from collections import defaultdict
import sys

from rowhammer_tester.scripts.adapter.payload_generators import generate_payload, generate_trr_test_payload, get_range_from_rows
from rowhammer_tester.scripts.adapter.utils import LoopingInterruptibleThread
from rowhammer_tester.scripts.playbook.row_mappings import RowMapping
from rowhammer_tester.scripts.utils import RemoteClient, get_litedram_settings, setup_inverters, DRAMAddressConverter, \
    get_generated_defs, hw_memset, execute_payload, hw_memtest

import re

action_pattern = re.compile(r'HAMMER[(]\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*[)]')

DEBUG = False


# Data patterns written to memory before hammering. Each entry is
# (pattern_data, inversion_divisor, inversion_mask):
#   - pattern_data: the 32-bit word written to every column of a row;
#   - inversion_divisor: rows are grouped modulo this value (0 = no inversion);
#   - inversion_mask: within a group, rows whose index bit is set are inverted.
# Row layouts for the first four rows are shown alongside each entry.
ROW_PATTERNS = {
    'all_1':              (2 ** 32 - 1,                    0, 0),
    'all_0':              (0,                              0, 0),
    #                          0 11..1   1 00..0   2 11..1   3 00..0
    'striped':            (2 ** 32 - 1,                    2, 0b10),
    #                          0 00..0   1 11..1   2 00..0   3 11..1
    'striped_i':          (2 ** 32 - 1,                    2, 0b01),
    #                          0 1010..  1 0101..  2 1010..  3 0101..
    'alternate_10':       (int('10' * (32 // 2), 2),       2, 0b10),
    #                          0 0101..  1 1010..  2 0101..  3 1010..
    'alternate_01':       (int('01' * (32 // 2), 2),       2, 0b10),
    #                          0 0011..  1 1100..  2 0011..  3 1100..
    'alternate_0011':     (int('0011' * (32 // 4), 2),     2, 0b10),
    #                          0 1100..  1 0011..  2 1100..  3 0011..
    'alternate_1100':     (int('1100' * (32 // 4), 2),     2, 0b10),
    #                          0 00001111..  1 11110000..  2 00001111..  3 11110000..
    'alternate_00001111': (int('00001111' * (32 // 8), 2), 2, 0b10),
    #                          0 11110000..  1 00001111..  2 11110000..  3 00001111..
    'alternate_11110000': (int('11110000' * (32 // 8), 2), 2, 0b10),
}


# Models hammering action
class HammerAction:
    def __init__(self, row, reads, bitflips):
        self.row = row
        self.reads = reads
        self.bitflips = bitflips

    @staticmethod
    def from_string(action: str):
        parsed_action = re.search(action_pattern, action)
        if parsed_action is None:
            raise ValueError(f'Malformed hammer action: {action!r} '
                             '(expected HAMMER(<row>, <reads>, <bitflips>))')

        row: int = int(parsed_action.group(1))
        reads: int = int(parsed_action.group(2))
        bitflips: int = int(parsed_action.group(3))

        return HammerAction(row, reads, bitflips)

    def __eq__(self, other):
        return isinstance(other, HammerAction) and \
        ( self.row == other.row and
          self.reads == other.reads and
          self.bitflips == other.bitflips )

    def __hash__(self):
        return hash((self.row, self.reads, self.bitflips))

    def __repr__(self):
        return f'HAMMER({self.row}, {self.reads}, {self.bitflips})'


# Executes actions on FPGA
class HwExecutor:

    def __init__(self):
        # Keep track of row addresses and last actions/payload executed
        # so that repeated tests are done efficiently
        self._addresses_cache = {}
        self._last_test_data = {}
        # last_actions = None
        # self._last_payload = None

        # Open connection to FPGA
        self._wb = RemoteClient()
        self._wb.open()

        # Get DRAM settings, address converter, and FPGA system clock
        self._settings = get_litedram_settings()
        self._converter = DRAMAddressConverter.load()
        self._sys_clk_freq = float(get_generated_defs()['SYS_CLK_FREQ'])

        # Initialise pattern data to all zeroes
        self._pattern_data = 0
        self._row_pattern = None

        # Set default hammering mode to interleaving (most effective)
        self.hammering_mode = 'interleaving'

        # Set up row pattern (see the row_pattern property below). This is
        # done after _wb is open, because setting it programs the inverters.
        self.row_pattern = 'all_0'
        # Neighbour distance
        self.row_check_distance = 1
        # Bank to use
        self.bank = 0
        # Set logical to physical row mapping; default is the identity
        self.row_mapping = RowMapping.get_by_name('TrivialRowMapping')


    # Counts "1" in bit string
    @staticmethod
    def bitcount(x):
        return bin(x).count('1')  # seems faster than operations on integers


    # Returns number of bit flips: val is the read value and ref is the expected one
    @classmethod
    def bitflips(cls, val, ref):
        return cls.bitcount(val ^ ref)


    # Returns addresses corresponding to given row
    def _addresses_per_row(self, bank, row):
        # Calculate the addresses lazily and cache them
        if row not in self._addresses_cache:
            addresses = [
                self._converter.encode_bus(bank=bank, col=col, row=row)
                for col in range(2 ** self._settings.geom.colbits)
            ]
            self._addresses_cache[row] = addresses
        return self._addresses_cache[row]


    # Returns addresses corresponding to row_sequence plus neighbouring rows
    def _get_memory_range(self, row_sequence):
        # Convert from logical to physical row
        row_physical = [self.row_mapping.logical_to_physical(row) for row in row_sequence]
        # Add additional rows according to 'row_check_distance'
        min_row = min(row_physical)
        max_row = max(row_physical)

        for d in range(1, self.row_check_distance + 1):
            row_above = min_row - d
            row_below = max_row + d

            if row_above >= 0:
                row_physical.append(row_above)

            if row_below <= 2 ** self._settings.geom.rowbits - 1:
                row_physical.append(row_below)

        #  print(row_physical)
        return get_range_from_rows(self._wb, self._settings, row_physical)
 

    # Converts memory addresses where bit flips occurred to bank, row and column
    def _decode_errors(self, errors):
        dma_data_width = self._settings.phy.dfi_databits * self._settings.phy.nphases
        dma_data_bytes = dma_data_width // 8

        row_errors = defaultdict(list)
        for e in errors:
            addr = self._wb.mems.main_ram.base + e.offset * dma_data_bytes
            bank, row, col = self._converter.decode_bus(addr)
            base_addr = min(self._addresses_per_row(bank, row))
            row_errors[row].append(((addr - base_addr) // 4, e.data, e.expected))

        return dict(row_errors)


    # Computes number of bit flips per row and returns dictionary of the form
    #   row_flip[r] = <number of bit flips in row r>
    def _process_errors(self, row_errors):
        row_errors_logical = {}
        row_flip = {}

        for row in row_errors:
            row_errors_logical[self.row_mapping.physical_to_logical(row)] = (row, row_errors[row])

        for logical_row in sorted(row_errors_logical.keys()):
            row, errors = row_errors_logical[logical_row]
            if len(errors) > 0:
                row_flip[logical_row] = sum(self.bitflips(value, expected) for addr, value, expected in errors)

        return row_flip


    # Current data pattern name (see ROW_PATTERNS)
    @property
    def row_pattern(self):
        return self._row_pattern

    # Sets up the FPGA to write a specific data pattern before hammering.
    # Programming the inverters requires an open connection, so _wb must
    # already be set when this is assigned.
    @row_pattern.setter
    def row_pattern(self, value):
        try:
            pattern_data, inversion_divisor, inversion_mask = ROW_PATTERNS[value]
        except KeyError:
            raise ValueError(
                f'Illegal pattern {value!r}; choose one of: '
                f'{", ".join(ROW_PATTERNS)}') from None

        self._pattern_data = pattern_data
        setup_inverters(self._wb, inversion_divisor, inversion_mask)
        self._row_pattern = value


    def _process_payload(self, row_sequence, payload):
        offset, size = self._get_memory_range(row_sequence)

        hw_memset(self._wb, offset, size, [self._pattern_data], print_progress=False)
        execute_payload(self._wb, payload, False)
        errors = hw_memtest(self._wb, offset, size, [self._pattern_data], print_progress=False)
        row_errors = self._decode_errors(errors)
        return self._process_errors(row_errors)


    def execute_trr_test(self, actions, rounds, refreshes):
         # print('Executing', actions)
        row_sequence = [a.row for a in actions]
        read_counts = [a.reads for a in actions]

        cur_test_data = {'type': 'hammering', 'actions': actions, 'rounds': rounds, 'refreshes': refreshes}

        # Check if test is a repetition and, if so, use cached payload
        payload = None
        if cur_test_data.items() <=  self._last_test_data.items():
            # print('Repetition!')
            payload = self._last_test_data['payload']
        else:
            payload = generate_trr_test_payload(
                row_sequence=row_sequence,
                read_counts=read_counts,
                rounds=rounds,
                refreshes=refreshes,
                mode=self.hammering_mode,
                timings=self._settings.timing,
                bankbits=self._settings.geom.bankbits,
                bank=self.bank,
                payload_mem_size=self._wb.mems.payload.size,
                verbose=DEBUG,
                sys_clk_freq=self._sys_clk_freq)
            self._last_test_data = cur_test_data | {"payload": payload}

        return self._process_payload(row_sequence, payload)       


    # Converts actions to payload and executes it
    # Returns dictionary mapping rows to bit flips (keys are only rows where flips occurred)
    def execute_hammering_test(self, actions):
        # print('Executing', actions)
        row_sequence = [a.row for a in actions]
        read_counts = [a.reads for a in actions]

        cur_test_data = {'type': 'hammering', 'actions': actions}
        # Check if test is a repetition and, if so, use cached payload
        payload = None
        if  cur_test_data.items() <= self._last_test_data.items():
            # print('Repetition!')
            payload = self._last_test_data['payload']
        else:
            payload = generate_payload(
                row_sequence=row_sequence,
                read_counts=read_counts,
                mode=self.hammering_mode,
                timings=self._settings.timing,
                bankbits=self._settings.geom.bankbits,
                bank=self.bank,
                payload_mem_size=self._wb.mems.payload.size,
                refresh=False,
                verbose=DEBUG,
                sys_clk_freq=self._sys_clk_freq)
            self._last_test_data = cur_test_data | {'payload': payload}

        return self._process_payload(row_sequence, payload)
        # print('Row sequence: ', row_sequence)
        # row_sequence = [a.row for a in actions]


    # Closes connection to FPGA. Safe to call more than once.
    def stop(self):
        wb = getattr(self, '_wb', None)
        if wb is not None:
            wb.close()
            self._wb = None

    # Context-manager support so callers can use `with HwExecutor() as hw:`
    # and get deterministic cleanup instead of relying on __del__.
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()

    # Best-effort cleanup if the caller forgot to call stop(). __del__ runs
    # during garbage collection, where exceptions are only printed, so guard
    # against a half-initialised object and swallow shutdown errors.
    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass


# How to detect presence of TRR?
# - Compare results with and without refresh over several rounds: if we get
#
#       bit flips with refresh ~ bit flips without refresh
#
#   at least once, we know that TRR is not present
# - Test with low number of accesses followed by refreshes, because refreshes have
#   higher chances to correct bit flips

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == '--debug':
        DEBUG = True

    with HwExecutor() as hw_exec:
        hw_exec.row_pattern = 'striped'
        hw_exec.hammering_mode = 'interleaving'
        actions = [HammerAction(i, 8000, 0) for i in [2, 4, 5000, 5002, 8000, 8002]]
        ref_count = [0, 2, 5]
        rounds = 6
        iterations = 5

        def iteration(n_ref):
            print(f'{n_ref} ref:\t', hw_exec.execute_trr_test(actions=actions, rounds=rounds, refreshes=n_ref))

        test_thread = LoopingInterruptibleThread(function=iteration,
                                                 iterator=ref_count,
                                                 repetitions=iterations)
        test_thread.start()
        test_thread.join()


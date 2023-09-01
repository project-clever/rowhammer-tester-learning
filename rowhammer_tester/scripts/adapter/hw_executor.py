import sys
from collections import Counter, defaultdict
from rowhammer_tester.gateware.payload_executor import Decoder, Encoder, OpCode

from rowhammer_tester.scripts.adapter.utils import generate_payload, generate_trr_test_payload, get_range_from_rows
from rowhammer_tester.scripts.playbook.row_mappings import RowMapping,TrivialRowMapping
from rowhammer_tester.scripts.utils import RemoteClient, get_expected_execution_cycles, get_litedram_settings, setup_inverters, DRAMAddressConverter, \
    get_generated_defs, hw_memset, execute_payload, hw_memtest

import json
import itertools
import re
from timeit import default_timer as timer

action_pattern = re.compile(r'HAMMER[(]\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*[)]')


# Models hammering action
class HammerAction:
    def __init__(self, row, reads, bitflips):
        self.row = row
        self.reads = reads
        self.bitflips = bitflips

    @staticmethod
    def from_string(action: str):
        parsed_action = re.search(action_pattern, action)

        row: int = int(parsed_action.group(1))
        reads: int = int(parsed_action.group(2))
        bitflips: int = int(parsed_action.group(3))

        return HammerAction(row, reads, bitflips)
    
    def __eq__(self, other):
        return isinstance(other, HammerAction) and \
        ( self.row == other.row and
          self.reads == other.reads and
          self.bitflips == other.bitflips )

    def __repr__(self):
        return f'HAMMER({self.row}, {self.reads}, {self.bitflips})'


# Executes actions on FPGA
class HwExecutor:

    def __init__(self):
        # Keep track of row addresses and last actions/payload executed
        # so that repeated tests are done efficiently
        self._addresses_cache = {}
        self._last_actions = None
        self._last_payload = None

        # Open connection to FPGA
        self._wb = RemoteClient()
        self._wb.open()

        # Get DRAM settings, address converter, and FPGA system clock
        self._settings = get_litedram_settings()
        self._converter = DRAMAddressConverter.load()
        self._sys_clk_freq = float(get_generated_defs()['SYS_CLK_FREQ'])

        # Intialise pattern data to all zeroes
        self._pattern_data = 0


        # Set up row pattern (see __seattr__ below)
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


    # Sets up FPGA to handle specific data pattern
    def __setattr__(self, key, value):
        if key == 'row_pattern':
            inversion_divisor = 0
            inversion_mask = 0

            match value:
                case 'all_1':
                    self._pattern_data = 2 ** 32 - 1
                case 'all_0':
                    self._pattern_data = 0
                case 'striped':
                    self._pattern_data = 2 ** 32 - 1
                    inversion_divisor = 2
                    inversion_mask = 0b10
                case _:
                    raise ValueError('Illegal pattern provided')

            setup_inverters(self._wb, inversion_divisor, inversion_mask)

        super(HwExecutor, self).__setattr__(key, value)

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

        # Check if test is a repetition and, if so, use cached payload
        payload = None
        if actions == self._last_actions:
            # print('Repetition!')
            payload = self._last_payload
        else:
            payload = generate_trr_test_payload(
                row_sequence=row_sequence,
                read_counts=read_counts,
                rounds=rounds,
                refreshes=refreshes,
                timings=self._settings.timing,
                bankbits=self._settings.geom.bankbits,
                bank=self.bank,
                payload_mem_size=self._wb.mems.payload.size,
                verbose=False,
                sys_clk_freq=self._sys_clk_freq)
            self._last_actions = actions
            self._last_payload = payload

        return self._process_payload(row_sequence, payload)       


    # Converts actions to payload and executes it
    # Returns dictionary mapping rows to bit flips (keys are only rows where flips occurred)
    def execute_hammering_test(self, actions):
        # print('Executing', actions)
        row_sequence = [a.row for a in actions]
        read_counts = [a.reads for a in actions]

        # Check if test is a repetition and, if so, use cached payload
        payload = None
        if actions == self._last_actions:
            # print('Repetition!')
            payload = self._last_payload
        else:
            payload = generate_payload(
                row_sequence=row_sequence,
                read_counts=read_counts,
                timings=self._settings.timing,
                bankbits=self._settings.geom.bankbits,
                bank=self.bank,
                payload_mem_size=self._wb.mems.payload.size,
                refresh=True,
                verbose=True,
                sys_clk_freq=self._sys_clk_freq)
            self._last_actions = actions
            self._last_payload = payload

        return self._process_payload(row_sequence, payload)
        # print('Row sequence: ', row_sequence)
        # row_sequence = [a.row for a in actions]


    # Closes connection to FPGA
    def stop(self):
        self._wb.close()

    # Stops when object is destroyed
    def __del__(self):
        self.stop()


if __name__ == "__main__":
    hw_exec = HwExecutor()
    hw_exec.row_pattern = 'striped'
    actions =  [HammerAction(i, 5000, 0) for i in range(0,8,2)]

    # actions = [HammerAction(0, 10000, 0), HammerAction(2, 10000, 1),
    #            HammerAction(0,10000,1), HammerAction(2, 10000, 1),
    #            HammerAction(0, 10000, 1), HammerAction(2, 10000, 1),
    #            HammerAction(0, 10000, 1), HammerAction(2, 10000, 1)]
    
    # actions1 = [HammerAction(0, 10000, 0), HammerAction(0, 10000, 1),
    #            HammerAction(0,10000,1), HammerAction(0, 10000, 1),
    #            HammerAction(2, 10000, 1), HammerAction(2, 10000, 1),
    #            HammerAction(2, 10000, 1), HammerAction(2, 10000, 1)]


    print(hw_exec.execute_trr_test(actions, rounds=10,refreshes=1))
    print(actions)
    # print(hw_exec.execute(actions1))
    # end = timer()
    # print(end - start)

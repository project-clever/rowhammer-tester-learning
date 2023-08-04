import sys
from collections import defaultdict

from rowhammer_tester.scripts.playbook.lib import generate_payload_from_row_list, get_range_from_rows
from rowhammer_tester.scripts.playbook.row_mappings import RowMapping,TrivialRowMapping
from rowhammer_tester.scripts.utils import RemoteClient, get_litedram_settings, setup_inverters, DRAMAddressConverter, \
    get_generated_defs, hw_memset, execute_payload, hw_memtest

import json
import re
from timeit import default_timer as timer

action_pattern = re.compile(r'HAMMER[(]\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*[)]')


class HammerAction:
    def __init__(self, row, reads, bitflips):
        self.row = row
        self.reads = reads
        self.biflips = bitflips

    @staticmethod
    def from_string(action: str):
        parsed_action = re.search(action_pattern, action)

        row: int = int(parsed_action.group(1))
        reads: int = int(parsed_action.group(2))
        bitflips: int = int(parsed_action.group(3))

        return HammerAction(row, reads, bitflips)

class HwExecutor:

    def __init__(self):
        self._addresses_cache = {}
        self._wb = RemoteClient()
        self._wb.open()

        self._settings = get_litedram_settings()
        self._converter = DRAMAddressConverter.load()
        self._pattern_data = 0
        self._sys_clk_freq = float(get_generated_defs()['SYS_CLK_FREQ'])

        self.row_pattern = 'all_0'
        self.row_check_distance = 1
        self.bank = 0
        # Set logical to physical row mapping; initially the identity
        self.row_mapping = RowMapping.get_by_name('TrivialRowMapping')

    @staticmethod
    def bitcount(x):
        return bin(x).count('1')  # seems faster than operations on integers

    @classmethod
    def bitflips(cls, val, ref):
        return cls.bitcount(val ^ ref)

    def _addresses_per_row(self, bank, row):
        # Calculate the addresses lazily and cache them
        if row not in self._addresses_cache:
            addresses = [
                self._converter.encode_bus(bank=bank, col=col, row=row)
                for col in range(2 ** self._settings.geom.colbits)
            ]
            self._addresses_cache[row] = addresses
        return self._addresses_cache[row]

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

        return get_range_from_rows(self._wb, self._settings, row_physical)

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

    def execute(self, actions):
        row_sequence = [a.row for a in actions]
        read_count = max([a.reads for a in actions])

        payload = generate_payload_from_row_list(
            read_count=read_count,
            row_sequence=row_sequence,
            timings=self._settings.timing,
            bankbits=self._settings.geom.bankbits,
            bank=self.bank,
            payload_mem_size=self._wb.mems.payload.size,
            refresh=False,
            verbose=False,
            sys_clk_freq=self._sys_clk_freq)

        # print('Row sequence: ', row_sequence)
        offset, size = self._get_memory_range(row_sequence)
        # print('Memory range: ', offset, size)
        hw_memset(self._wb, offset, size, [self._pattern_data], print_progress=False)
        execute_payload(self._wb, payload, False)
        errors = hw_memtest(self._wb, offset, size, [self._pattern_data], print_progress=False)
        row_errors = self._decode_errors(errors)
        return self._process_errors(row_errors)
        # Check all the rows for now

    def stop(self):
        self._wb.close()

    def __del__(self):
        self.stop()


# if __name__ == "__main__":
#     hw_exec = HwExecutor()
#     hw_exec.row_pattern = 'striped'
#     actions = [ HammerAction(0, 100000, 0), HammerAction(2, 100000, 0)]
#     start = timer()
#     print(hw_exec.execute(actions))
#     end = timer()
#     print(end - start)

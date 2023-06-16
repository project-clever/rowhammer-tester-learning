import json
import time

from rowhammer_tester.scripts.playbook.row_generators.row_seq import RowSequenceGenerator
from rowhammer_tester.scripts.playbook.payload_generators.row_list import RowListPayloadGenerator
from rowhammer_tester.scripts.utils import (DRAMAddressConverter, get_litedram_settings)

class LoggedRowListPayloadGenerator(RowListPayloadGenerator):

    def initialize(self, config):
        super().initialize(config)

        self.log_dir = self.module_config.get("log_dir", None)
        self.bank = self.module_config.get("bank", 0)

        if self.log_dir:
            self.err_summary = []
            self.converter = DRAMAddressConverter.load()
            self._addresses_per_row = {}


    def addresses_per_row(self, row, settings):
        """Returns a list of column addresses for a given row"""

        # Calculate the addresses lazily and cache them
        if row not in self._addresses_per_row:
            addresses = [
                self.converter.encode_bus(bank=self.bank, col=col, row=row)
                for col in range(2**settings.geom.colbits)
            ]
            self._addresses_per_row[row] = addresses
        return self._addresses_per_row[row]
    
    def process_errors(self, settings, row_errors):
        super().process_errors(settings, row_errors)

        if self.log_dir:
            """ JSON log file describes the list of iterations. Each consists of a dictionary where keys are logical row number, and values are row flips descriptors. Row flips descriptors have the following format:
                    - logical row number
                    - physical_row: physical row number
                    - col: dictionary indexed by columns, each containing the list of bitflip locations for that column
                    - biflips: total number of bitflips for the row
            """
            err_desc = {}
            for row in row_errors:
                if len(row_errors[row]) > 0:
                    flips = sum(
                        self.bitflips(value, expected) for addr, value, expected in row_errors[row])
                cols = {}
                for addr, value, expected in row_errors[row]:
                    base_addr = min(self.addresses_per_row(row, settings))
                    actual_addr = base_addr + 4 * addr
                    bank, _row, col = self.converter.decode_bus(actual_addr)
                    expr = f'{(value ^ expected):#0{len(bin(expected))}b}'
                    bitflips = [i for i, c in enumerate(expr[2:]) if c == '1']
                    cols[col] = bitflips
                    assert row == _row
                err_desc[self.row_mapping.physical_to_logical(row)] = {'physical_row': _row, 'col': cols, 'bitflips': flips}
            self.err_summary.append(err_desc)
    
    def summarize(self):
        if self.log_dir:
            with open("{}/error_summary_{}.json".format(self.log_dir, time.time()), "w") as write_file:
                json.dump(self.err_summary, write_file, indent=4)
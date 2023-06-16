import time
import json
from collections import (OrderedDict, defaultdict)
from rowhammer_tester.scripts.utils import DRAMAddressConverter
from rowhammer_tester.scripts.playbook.payload_generators import PayloadGenerator
from rowhammer_tester.scripts.playbook.lib import generate_idle_payload
from rowhammer_tester.scripts.utils import validate_keys
from rowhammer_tester.scripts.playbook.row_mappings import (
    RowMapping, TrivialRowMapping, TypeARowMapping, TypeBRowMapping)


class IdlePayloadGenerator(PayloadGenerator):
    _valid_module_keys = set(
        [
            "idle_time", "max_iteration", "row_mapping", "verbose", "log_dir", "bank"
        ])

    def initialize(self, config):
        self.module_config = config["payload_generator_config"]
        assert validate_keys(self.module_config, self._valid_module_keys)

        row_mapping_name = self.module_config["row_mapping"]
        self.row_mapping = RowMapping.get_by_name(row_mapping_name)
        self.bank = self.module_config.get("bank", 0)

        self.idle_time = self.module_config["idle_time"]
        self.max_iteration = self.module_config["max_iteration"]
        self.verbose = self.module_config["verbose"]
        
        self.log_dir = self.module_config.get("log_dir", None)

        if self.log_dir:
            self.err_summary = []
            self.converter = DRAMAddressConverter.load()
            self._addresses_per_row = {}

        self.iteration = 0

    def get_memtest_range(self, wb, settings):
        return PayloadGenerator.get_memtest_range(self, wb, settings)

    def get_memset_range(self, wb, settings):
        return PayloadGenerator.get_memset_range(self, wb, settings)

    def get_payload(self, *, settings, bank, payload_mem_size, sys_clk_freq=None):
        return generate_idle_payload(
            idle_time=self.idle_time, 
            timings=settings.timing,
            bankbits=settings.geom.bankbits,
            bank=bank,
            payload_mem_size=payload_mem_size,
            verbose=True,
            sys_clk_freq=sys_clk_freq)

    @staticmethod
    def bitcount(x):
        return bin(x).count('1')  # seems faster than operations on integers

    @classmethod
    def bitflips(cls, val, ref):
        return cls.bitcount(val ^ ref)

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

    def update_log(self, settings, row_errors):
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

    def process_errors(self, settings, row_errors):
        row_errors_logical = {}
        for row in row_errors:
            row_errors_logical[self.row_mapping.physical_to_logical(row)] = (row, row_errors[row])
        for logical_row in sorted(row_errors_logical.keys()):
            row, errors = row_errors_logical[logical_row]
            if len(errors) > 0:
                print(
                    "Bit-flips for row {:{n}}: {}".format(
                        logical_row,
                        sum(self.bitflips(value, expected) for addr, value, expected in errors),
                        n=len(str(2**settings.geom.rowbits - 1))))

        if self.log_dir:                        
            self.update_log(settings, row_errors)

        self.iteration += 1



    def done(self):
        return self.iteration >= self.max_iteration

    
    def summarize(self):
        if self.log_dir:
            with open("{}/error_summary_{}.json".format(self.log_dir, time.time()), "w") as write_file:
                json.dump(self.err_summary, write_file, indent=4)
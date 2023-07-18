#!/usr/bin/env python3


import argparse
from collections import defaultdict
from rowhammer_tester.scripts.playbook.playbook import (addresses_per_row, decode_errors) 
from rowhammer_tester.scripts.playbook.payload_generators import PayloadGenerator
from rowhammer_tester.scripts.playbook.payload_generators.idle import IdlePayloadGenerator
from rowhammer_tester.scripts.utils import (
    RemoteClient, setup_inverters, get_litedram_settings, hw_memset, hw_memtest, execute_payload, 
    DRAMAddressConverter, get_generated_defs)

_addresses_per_row = {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--idle_time', type=float, default=3.2, help='Idle time to test specific retention time.')
    parser.add_argument('--iterations', type=int, default=2, help='Idle time to test specific retention time.')
    args = parser.parse_args()

    config = {
        "payload_generator" : "IdlePayloadGenerator",
	    "row_pattern" : 0,
	    "payload_generator_config" : {
		    "row_mapping" : "TrivialRowMapping",
		    "map_generator" : "EvenRowGenerator",
		    "idle_time" : args.idle_time,
            "max_iteration": args.iterations,
		    "verbose" : False,
	}
    }

    pg = PayloadGenerator.get_by_name(config["payload_generator"])
    pg.initialize(config)
    wb = RemoteClient()
    wb.open()
    settings = get_litedram_settings()
    inversion_divisor = 0
    inversion_mask = 0
    row_pattern = config.get("row_pattern", 0)
    setup_inverters(wb, inversion_divisor, inversion_mask)

    while not pg.done():
        offset, size = pg.get_memset_range(wb, settings)
        hw_memset(wb, offset, size, [row_pattern])
        converter = DRAMAddressConverter.load()
        bank = 0
        sys_clk_freq = float(get_generated_defs()['SYS_CLK_FREQ'])
        payload = pg.get_payload(
            settings=settings,
            bank=bank,
            payload_mem_size=wb.mems.payload.size,
            sys_clk_freq=sys_clk_freq)

        execute_payload(wb, payload)
        offset, size = pg.get_memtest_range(wb, settings)
        errors = hw_memtest(wb, offset, size, [row_pattern])
        row_errors = decode_errors(wb, settings, converter, bank, errors)
        pg.process_errors(settings, row_errors)


    pg.summarize()
    wb.close()


if __name__ == "__main__":
    main()


#!/usr/bin/env python3

import argparse
import random
from collections import defaultdict
from rowhammer_tester.scripts.utils import memread
from rowhammer_tester.scripts.playbook.playbook import decode_errors 
from rowhammer_tester.scripts.playbook.payload_generators import PayloadGenerator
from rowhammer_tester.scripts.utils import (
    RemoteClient, setup_inverters, get_litedram_settings, hw_memset, hw_memtest, execute_payload, 
    DRAMAddressConverter, get_generated_defs)


rows_retention_times = {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-id','--idle', type=float, default=2, help='Idle time to test specific retention time.')
    parser.add_argument('-it', '--iterations', type=int, default=1, help='Number of times to repeat the test.')
    parser.add_argument('--nrows', type=int, default=1, help='Number of rows with similar retention times to process.')
    parser.add_argument('--trow', type=int, help='Check whether a specific row has a greater retention time.')
    parser.add_argument('--log', type=bool, default=False, help='Enable log saving.')
    parser.add_argument('--test', type=float, help='A test in retition time is increased until specified time.')

    args = parser.parse_args()
    config = {
        "payload_generator" : "IdlePayloadGenerator",
        "row_pattern" : 0,
        "payload_generator_config" : {
            "row_mapping" : "TrivialRowMapping",
            "row_generator": "RowSequenceGenerator",
            "idle_time" : args.idle,
            "max_iteration": args.iterations,
            "verbose" : False,
        }
    }

    if args.test is not None:
        global rows_retention_times
        cursor = args.idle
        while cursor <= args.test:
            config["payload_generator_config"]["idle_time"] = cursor
            fire(config, args)
            cursor += 0.1

    fire(config, args)


def add_entry(row_dict, row_number, idle_time):
    if row_number not in row_dict:
        row_dict[row_number] = idle_time


def add_row_retention_time(row_errors, config):
    global rows_retention_times
    for row in row_errors:
        add_entry(rows_retention_times, row, config["payload_generator_config"]["idle_time"])
    print("Row Times:",rows_retention_times)


def analyze_data(data, args, max_row):
    if args.trow is not None:
        if args.trow not in data:
            print("The Row {0} has a retention time >= {1}".format(args.trow, args.idle))
        else:
            print("The Row {0} has a retention time <= {1}".format(args.trow, args.idle))
    else:
        selected_rows = [x for x in random.sample(range(0, max_row), args.nrows) if x not in data]
        print("Rows Retention Times Greater Than {0}: {1}".format(args.idle, selected_rows))


def fire(config, args):
    settings = get_litedram_settings()
    max_row = 2 ** (getattr(settings.geom, "rowbits") ) - 1
    inversion_mask = 0
    inversion_divisor = 0
    pg = PayloadGenerator.get_by_name(config["payload_generator"])
    pg.initialize(config)
    wb = RemoteClient()
    wb.open()
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
        if args.test is not None:
            add_row_retention_time(row_errors, config)
        else:
            analyze_data(row_errors, args, max_row)
        pg.iteration += 1

    if args.log:
        pg.summarize()
    wb.close()

if __name__ == "__main__":
    main()

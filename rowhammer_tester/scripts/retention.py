#!/usr/bin/env python3
import argparse
import time
import json
from collections import defaultdict
from rowhammer_tester.scripts.playbook.payload_generators import PayloadGenerator
from rowhammer_tester.scripts.playbook.lib import generate_idle_payload
from rowhammer_tester.scripts.utils import (
    RemoteClient, setup_inverters, get_litedram_settings, hw_memset, hw_memtest, execute_payload, 
    DRAMAddressConverter, get_generated_defs)


settings = get_litedram_settings()
converter = DRAMAddressConverter.load()

max_col=2 ** settings.geom.colbits
max_row=2 ** settings.geom.rowbits
max_bank=2 ** settings.geom.bankbits

dma_data_width = settings.phy.dfi_databits * settings.phy.nphases
nbytes = dma_data_width // 8




def range_action(range_str):
    try:
        start, end = map(int, range_str.split('-'))
        return start, end
    except ValueError:
        raise argparse.ArgumentTypeError("Row Range Must Be In The Format: 'start-end'")



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--idle', type=float, default=2, help='Idle time for testing specific retention time.')
    parser.add_argument('--pattern', type=int, default=1, help='Specific pattern to test (parity must be even).')
    parser.add_argument('--iterations', type=int, default=1, help='Number of iterations to perform the retention test.')
    parser.add_argument('--debug', type=bool,default=False, help='Target a specific row for retention testing.')
    parser.add_argument('--row', type=int, help='Target a specific row for retention testing.')
    parser.add_argument('--row-range', type=range_action, help='Target a range of rows in the format "start-end" for retention testing.')
    parser.add_argument('--log', type=bool, default=False, help='Enable log saving.')
    parser.add_argument('--log-dir', type=str, default=".", help='Set logging directory.')
    parser.add_argument('--cell-check', action='store_true', help="Checks All Columns Within A Sepecific Row Whether It's An AntiCell Or TrueCell. NOT RELIABLE") # Still Need Work



    
    args = parser.parse_args()

    output = {}
    try:
        wb = RemoteClient()
        wb.open()
        if args.cell_check:
            cell_check(wb, args)
        elif args.row: 
            for iterations in range(args.iterations):
                fill_mem(wb, args.row, args.pattern)
                idle(wb, args)
                output["Iteration {0}".format(iterations+1)] = check_row(wb, args.row, args.pattern)
        elif args.row_range:
            start_row, end_row = args.row_range
            range_length = end_row - start_row
            for iterations in range(args.iterations):
                fill_mem(wb, start_row, args.pattern, rows=range_length)
                idle(wb, args)
                output["Iteration {0}".format(iterations+1)] = check_row(wb, start_row, args.pattern, rows=range_length)

        else:
            print("Something Went Wrong. Check The Arguments Provided.")
    finally:
        if output:
            if args.debug:
                print(str(output).replace(",", "\n"))
            if args.log:
                with open("{}/test_summary_{}.json".format(args.log_dir, time.strftime("%d-%m-%Y_%H-%M", time.localtime(int(time.time())))), "w") as write_file:
                    json.dump(output, write_file, indent=4)
        wb.close()


def cell_check(wb, args): # Some columns are within both.
    output = {}
    true_cells = []
    anti_cells = []
    
    if args.row:
        row_range = 1
        row = args.row
    elif args.row_range:
        start_row, end_row = args.row_range
        row_range = end_row - start_row
        row = start_row
    else:
        print("Make Sure You Have A Row/Row Range Set!")
        quit()

    addr = converter.encode_bus(bank=0, col=0, row=row)
    #True-cell Test
    offset = addr - wb.mems.main_ram.base
    size = (max_col * max_bank)*8 * row_range
    pattern = 2 ** 32 - 1
    hw_memset(wb, offset, size, [pattern]) # Only fill the columns you want to test
    idle(wb, args)
    errors = hw_memtest(wb, offset, size, [pattern]) 
    for row in errors:
        addr = wb.mems.main_ram.base + row.offset * dma_data_width // 8
        bank, row, col = converter.decode_bus(addr)
        true_cells.append("Row: {1}  Column:{0}".format(col, row))
    true_cells.sort()
    
    #Anti-cell Test
    addr = converter.encode_bus(bank=0, col=0, row=row)
    pattern = 0
    hw_memset(wb, offset, size, [pattern]) # Only fill the columns you want to test
    idle(wb, args)
    errors = hw_memtest(wb, offset, size, [pattern]) 
    for row in errors:
        addr = wb.mems.main_ram.base + row.offset * dma_data_width // 8
        bank, row, col = converter.decode_bus(addr)
        anti_cells.append("Row: {1}  Column:{0}".format(col, row))
    anti_cells.sort()

    union_set = set(true_cells) & set(anti_cells)
    print("Weird columns",union_set)
    output["True Cell"] = true_cells
    output["Anti Cell"] = anti_cells
    print(output)


def idle(wb, args):
    sys_clk_freq = float(get_generated_defs()['SYS_CLK_FREQ'])
    payload = generate_idle_payload(
        idle_time=args.idle, 
        timings=settings.timing,
        bankbits=settings.geom.bankbits,
        bank=0,
        payload_mem_size=wb.mems.payload.size,
        verbose=False,
    sys_clk_freq=sys_clk_freq)
    execute_payload(wb, payload)
            

def check_row(wb, trow, pattern, rows=1):
    addr = converter.encode_bus(bank=0, col=0, row=trow)
    size = (max_col * max_bank)*8 * rows # Multiply by 8 because its not a bit >:(
    offset = addr - wb.mems.main_ram.base
    errors = hw_memtest(wb, offset, size, [pattern]) 
    error_rows = []
    for row in errors:
        addr = wb.mems.main_ram.base + row.offset * dma_data_width // 8
        bank, row, col = converter.decode_bus(addr)
        error_rows.append(row)
    print("Bitflips Occured In The Following Rows:  \n{0}".format(error_rows))
    return error_rows


def fill_mem(wb, row, pattern, rows=1):
    addr = converter.encode_bus(bank=0, col=0, row=row)
    hw_memset(wb, addr - wb.mems.main_ram.base, (cols * max_bank)* 8 * rows, [pattern])

if __name__ == "__main__":
    main()

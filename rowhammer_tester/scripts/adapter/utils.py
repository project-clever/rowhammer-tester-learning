from math import ceil
from rowhammer_tester.gateware.payload_executor import Encoder, OpCode, Decoder
from rowhammer_tester.scripts.utils import RemoteClient, get_expected_execution_cycles, get_litedram_settings, DRAMAddressConverter, \
    get_generated_defs, hw_memset, execute_payload, hw_memtest


# Returns range of memory addresses corresponding to the list of rows "row_nums"
def get_range_from_rows(wb, settings, row_nums):
    conv = DRAMAddressConverter.load()
    min_row = min(row_nums)
    max_row = max(row_nums) + 1
    start = conv.encode_bus(bank=0, row=min_row, col=0)
    if max_row < 2 ** settings.geom.rowbits:
        end = conv.encode_bus(bank=0, row=max_row, col=0)
    else:
        end = wb.mems.main_ram.base + wb.mems.main_ram.size

    return start - wb.mems.main_ram.base, end - start

# Computes LCM
def least_common_multiple(x, y):
    gcd = x
    rem = y
    while (rem):
        gcd, rem = rem, gcd % rem

    return (x * y) // gcd

# Prints out payload info such as size, expected exec time and list of instructions
def print_payload_info(payload,
                       payload_mem_size,
                    #    read_count,
                    #    row_sequence,
                       refreshes,
                       sys_clk_freq):
    expected_cycles = get_expected_execution_cycles(payload)
    print(
        '  Payload size = {:5.2f}KB / {:5.2f}KB'.format(
            4 * len(payload) / 2 ** 10, payload_mem_size / 2 ** 10))
    # count = '{:.3f}M'.format(read_count /
    #                          1e6) if read_count > 1e6 else '{:.3f}K'.format(read_count / 1e3)
    # print('  Payload per-row toggle count = {}  x{} rows'.format(count, len(row_sequence)))
    time = ''
    if sys_clk_freq is not None:
        time = ' = {:.3f} ms'.format(1 / sys_clk_freq * expected_cycles * 1e3)
    print('  Expected execution time = {} cycles'.format(expected_cycles) + time)
    print(f'  Refreshes: {refreshes}')
    
    for instruction in payload:
        op, *args = map(lambda p: p[1], instruction._parts)
        print(op, *map(hex, args), sep="\t")


# FPGA code defines a memory area where payloads are stored before being executed
# This checks that the payload fits into that memory area
def check_payload(payload, payload_mem_size):
    if len(payload) > payload_mem_size // 4:
        explanation = f'Memory required for payload executor instructions ({len(payload) * 4} bytes) exceeds available payload memory ({payload_mem_size} bytes)'
        raise MemoryError(explanation)


# Encode a single loop by repeating accesses to rows in an interleaving fashion
def encode_one_loop(*, unrolled, rolled, row_sequence, timings, encoder, bank, payload, refresh=False):
    tras = timings.tRAS
    trp = timings.tRP
    trefi = timings.tREFI
    trfc = timings.tRFC

    local_refreshes = 0

    if refresh:
        payload.append(encoder.I(OpCode.REF, timeslice=trfc))
        local_refreshes = 1
        accum = trfc + 1

    for idx in range(unrolled):
        for row in row_sequence:
            if refresh:
                if accum + tras + trp > trefi:
                    payload.append(encoder.I(OpCode.REF, timeslice=trfc))
                    # Invariant: time between the beginning of two refreshes
                    # is is less than tREFI.
                    accum = trfc
                    local_refreshes += 1
                accum += tras + trp
            payload.extend(
                [
                    encoder.I(
                        OpCode.ACT, timeslice=tras, address=encoder.address(bank=bank, row=row)),
                    encoder.I(OpCode.PRE, timeslice=trp,
                             address=encoder.address(col=1 << 10)),  # all
                ])
    jump_target = 2 * unrolled * len(row_sequence) + local_refreshes
    # + 1
    assert jump_target < 2 ** Decoder.LOOP_JUMP
    payload.append(encoder.I(OpCode.LOOP, count=rolled, jump=jump_target))

    return local_refreshes * (rolled + 1)


# Split a long loop into shorter ones according to max jump length of LOOP opcode
def encode_long_loop(*, unrolled, rolled, **kwargs):
    # fill payload so that we have >= desired read_count
    count_max = 2 ** Decoder.LOOP_COUNT - 1
    n_loops = ceil(rolled / (count_max + 1))
    refreshes = 0

    for outer_idx in range(n_loops):
        if outer_idx == 0:
            loop_count = ceil(rolled) % (count_max + 1)
            # print(f'Loop count: {loop_count}')
            # print(f'Unrolled: {unrolled}')
            if loop_count == 0:
                loop_count = count_max
            else:
                loop_count -= 1
        else:
            loop_count = count_max

        refreshes += encode_one_loop(unrolled=unrolled, rolled=loop_count, **kwargs)

    return refreshes


# 0: ACT row1
# 1: PRE row1
# 2: ACT row2
# 3: PRE row2
# 4: LOOP 50000 5

# 32 => 2 11 19 

# Encodes hammering each row in a sequence for a fixed number of times
def encode_one_readcount(
        *,
        row_sequence,
        read_count,
        timings,
        encoder,
        bank,
        payload,
        refresh=False):

    tras = timings.tRAS
    trp = timings.tRP
    trefi = timings.tREFI
    trfc = timings.tRFC

    acts_per_interval = (trefi - trfc) // (trp + tras)
    max_acts_in_loop = (2 ** Decoder.LOOP_JUMP - 1) // 2
    repeatable_unit = min(
        least_common_multiple(acts_per_interval, len(row_sequence)), max_acts_in_loop)
    assert repeatable_unit >= len(row_sequence)
    repetitions = repeatable_unit // len(row_sequence)

    read_count_quotient = read_count // repetitions
    read_count_remainder = read_count % repetitions
    
    refreshes = encode_long_loop(
        unrolled=repetitions,
        rolled=read_count_quotient,
        row_sequence=row_sequence,
        timings=timings,
        encoder=encoder,
        bank=bank,
        payload=payload,
        refresh=refresh)
    refreshes += encode_long_loop(
        unrolled=1,
        rolled=read_count_remainder,
        row_sequence=row_sequence,
        timings=timings,
        encoder=encoder,
        bank=bank,
        payload=payload,
        refresh=refresh)

    return refreshes


# Generates low-level code hammering each row in row_sequence = [R1, ..., Rn] a number
# of times given by read_counts = [C1, ..., Cn]. Namely: Rk is hammered Ck times.
# The 'mode' argument determines how hammering is implemented:
#   - interleaving => rows are hammered in a round-robin fashion
#   - sequential   => rows are hammered exactly as indicated, i.e., R1 for C1 times, then 
#                     R2 for C2 times and so on.
def encode_hammering(
        *,
        row_sequence,
        read_counts,
        mode='sequential',
        payload,
        timings,
        bankbits,
        bank,
        refresh=False):
    encoder = Encoder(bankbits=bankbits)

    refreshes = 0

    # Attaches read counts to corresponding rows
    rows_with_counts = list(zip(row_sequence, read_counts))

    match mode:
        case 'interleaving':

            read_count = min(read_counts)
            first_count = True
            # Go through read counts in ascending order and generate payload for that
            # specific read count. Subtract from rows_with_counts to keep track
            # of remaining accesses, removing rows for which read counts have reached zero.
            # Example for row_sequence = [1, 3, 5], read_counts = [100, 200, 300]:
            #   Iteration 1. [1, 3, 5] x 100 times
            #   Iteration 2. [3, 5] x 100 times
            #   Iteartion 3. [5] x 100 times

            while len(rows_with_counts) > 0:
                if first_count:
                    first_count = False
                else:
                    # Generate next sequence to hammer and corresponding read count
                    row_sequence = [row for row, _ in rows_with_counts]
                    read_count = min([count for _ , count in rows_with_counts])

                # print(f'Seq: {row_sequence}')
                # print(f'Count: {read_count}')

                # Encode hammering "row_sequence" for "read_count" times
                refreshes += encode_one_readcount(row_sequence=row_sequence,
                                    read_count=read_count,
                                    timings=timings,
                                    encoder=encoder,
                                    bank=bank,
                                    payload=payload,
                                    refresh=refresh)
                

                # Update rows_with_counts so that it now contains read counts
                # that are left to do. Rows whose read count has reached zero are removed
                rows_with_counts = [(row, count - read_count) for row, count in rows_with_counts if count > read_count]

        case 'sequential':
            # Hammer sequentially. Example for row_sequence = [1, 3, 5], read_counts = [100, 200, 300]:            
            #   Iteration 1. 1 x 100 times
            #   Iteration 2. 2 x 200 times
            #   Iteartion 3. 5 x 300 times
            for row, count in rows_with_counts:
                print(count, row)
                refreshes += encode_one_readcount(row_sequence=[row],
                                    read_count=count,
                                    timings=timings,
                                    encoder=encoder,
                                    bank=bank,
                                    payload=payload,
                                    refresh=refresh)
                
        case _: 
            raise ValueError('Incorrect hammering mode')
        
    return refreshes




def generate_payload(
        *,
        row_sequence,
        read_counts,
        mode='sequential',
        timings,
        bankbits,
        bank,
        payload_mem_size,
        refresh=False,
        verbose=False,
        sys_clk_freq=None):
    encoder = Encoder(bankbits=bankbits)

    trefi = timings.tREFI
    trfc = timings.tRFC

    # First instruction after mode transition should be a NOOP that waits until tRFC is satisfied
    # As we include REF as first instruction we actually wait tREFI here
    payload = [encoder.I(OpCode.NOOP, timeslice=max(1, trfc - 2, trefi - 2))]

    issued_refreshes = encode_hammering(row_sequence=row_sequence,
                                        read_counts=read_counts,
                                        mode=mode,
                                        payload=payload,
                                        timings=timings,
                                        bankbits=bankbits,
                                        bank=bank,
                                        refresh=refresh)

        # print(f'Rows and count: {rows_with_counts}')


    # MC refresh timer is reset on mode transition, so issue REF now, this way it will be in sync with MC
    payload.append(encoder.I(OpCode.NOOP, timeslice=1))
    payload.append(encoder.I(OpCode.NOOP, timeslice=0))  # STOP

    if verbose:
        print_payload_info(payload, payload_mem_size, issued_refreshes, sys_clk_freq)

    # Check that payload fits into payload memory
    check_payload(payload, payload_mem_size)

    # print(f'Expected cycles: {get_expected_execution_cycles(payload)}')

    # Encode payload
    return encoder(payload)

# 0, 2, 3 => 50k => refresh
# 1. 5k => refresh
# 2. 5k => refresh


def generate_trr_test_payload(
        *,
        row_sequence,
        read_counts,
        rounds=1,
        refreshes=1,
        mode='sequential',
        timings,
        bankbits,
        bank,
        payload_mem_size,
        verbose=False,
        sys_clk_freq=None):
    encoder = Encoder(bankbits=bankbits)

    trefi = timings.tREFI
    trfc = timings.tRFC

    # First instruction after mode transition should be a NOOP that waits until tRFC is satisfied
    # As we include REF as first instruction we actually wait tREFI here
    payload = [encoder.I(OpCode.NOOP, timeslice=max(1, trfc - 2, trefi - 2))]

    # Encode one round
    one_round = []
    issued_refreshes = encode_hammering(row_sequence=row_sequence,
                     read_counts=read_counts,
                     mode=mode,
                     payload=one_round,
                     timings=timings,
                     bankbits=bankbits,
                     bank=bank,
                     refresh=False)


    # Append refreshes at the end of round
    one_round.extend([encoder.I(OpCode.REF, timeslice=trfc)] * refreshes)

    # Generate payload
    payload = [encoder.I(OpCode.NOOP, timeslice=max(1, trfc - 2, trefi - 2))]

    payload.extend(one_round * rounds)
    issued_refreshes += refreshes * rounds

    # MC refresh timer is reset on mode transition, so issue REF now, this way it will be in sync with MC
    payload.append(encoder.I(OpCode.NOOP, timeslice=1))
    payload.append(encoder.I(OpCode.NOOP, timeslice=0))  # STOP

    if verbose:
        print_payload_info(payload, payload_mem_size, issued_refreshes, sys_clk_freq)

    # Check that payload fits into payload memory
    check_payload(payload, payload_mem_size)
    

    # Encode payload
    return encoder(payload)


# Test hammering one row ~400k times
def test_payload_generation(wb, settings, encoder, payload, bank, row, sys_clk_freq):
    tras = settings.timing.tRAS
    trp = settings.timing.tRP
    
    max_reps = 2 ** Decoder.LOOP_COUNT - 1
    print(max_reps)
    n_loops = 100
    # encoder = Encoder(settings.geom.bankbits)
    for l in range(n_loops):
        # for row in row_sequence:
        payload.extend(
            [
                encoder.I(
                    OpCode.ACT, timeslice=tras, address=encoder.address(bank=bank, row=row)),
                encoder.I(OpCode.PRE, timeslice=trp,
                            address=encoder.address(col=1 << 10)),  # all
            ])
        jump_target = 2
        payload.append(encoder.I(OpCode.LOOP, count=max_reps, jump=jump_target))

    payload.append(encoder.I(OpCode.NOOP, timeslice=1))
    payload.append(encoder.I(OpCode.NOOP, timeslice=0))  # STOP

    print_payload_info(payload, wb.mems.payload.size, 100 * (max_reps + 1), [row], sys_clk_freq)
    return payload


# This is just for testing purposes
if __name__ == '__main__':
    wb = RemoteClient()
    wb.open()
    settings = get_litedram_settings()
    payload = []
    sys_clk_freq = float(get_generated_defs()['SYS_CLK_FREQ'])
    row_sequence = [0, 2]
    read_counts = [50000] * len(row_sequence)

    wb.regs.controller_settings_refresh.write(0)

    # payload = generate_trr_test_payload(row_sequence= row_sequence,
    #                         read_counts=read_counts,
    #                         rounds=5,
    #                         refreshes=1,
    #                         mode='interleaving',
    #                         timings=settings.timing, 
    #                         bankbits=settings.geom.bankbits, 
    #                         bank=0, 
    #                         payload_mem_size=wb.mems.payload.size, 
    #                         verbose=True,
    #                         sys_clk_freq=sys_clk_freq)
    

    payload = generate_payload(row_sequence=row_sequence,
                               read_counts=read_counts,
                               mode='interleaving',
                               timings=settings.timing,
                               bankbits=settings.geom.bankbits,
                               bank=0,
                               payload_mem_size=wb.mems.payload.size,
                               verbose=True,
                               refresh=False,
                               sys_clk_freq=sys_clk_freq)

    wb.regs.controller_settings_refresh.write(1)
    check_payload(payload=payload, payload_mem_size=wb.mems.payload.size)
    # row_sequence = [a.row for a in actions]
    offset, size = 0x0, wb.mems.main_ram.size
    #self._get_memory_range(row_sequence)
    # print('Memory range: ', offset, size)
    pattern_data=2**32-1
    hw_memset(wb, offset, size, [pattern_data], print_progress=True)
    execute_payload(wb, payload, False)
    errors = hw_memtest(wb, offset, size, [pattern_data], print_progress=True)
    wb.close()
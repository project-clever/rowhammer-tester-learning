# Local changes

Changes made on top of upstream `antmicro/rowhammer-tester` (base commit `9e0561b`), newest first.
Each entry gives the problem, the change and how it was checked.

## 2026-10-12: Oracle robustness and Python 3.12 review

**Files:** `rowhammer_tester/scripts/adapter/{adapter.py, hw_executor.py, utils.py}`,
new `rowhammer_tester/scripts/adapter/tests/test_oracle_logic.py`

**Problem.** A review of the automata-learning oracle against Python 3.12.3 found
no incompatibilities but several latent bugs and fragile constructs:

1. `HwExecutor.__setattr__` intercepted every attribute assignment to handle
   `row_pattern`, and the pattern cases read `self._pattern_data`/`self._wb` by
   order of assignment in `__init__`; reordering or a non-`all_0` default would
   crash.
2. `HammerAction` defined `__eq__` but not `__hash__`, so instances were
   unhashable (fine today, a trap for any future set/dict use).
3. `HammerAction.from_string` raised an opaque `AttributeError` on malformed
   input, which came straight from the socket, killing the handler thread.
4. `LoopingInterruptibleThread.__init__` called `signal.signal` unconditionally,
   which fails off the main thread and silently hijacks the process SIGINT.
5. `HwExecutor.__del__` called `_wb.close()` with no guard against a
   half-initialised object or shutdown-time errors.

**Change.** Pattern definitions moved to a module-level `ROW_PATTERNS` table and
`row_pattern` became a property (`__setattr__` override removed). `HammerAction`
gained `__hash__`; `from_string` now raises a clear `ValueError`, which the
server catches and returns to the learner as `{"error": ...}` instead of
dropping the connection. The SIGINT handler is now opt-in
(`install_sigint_handler`, main-thread-only) and the thread gained a `stop()`
method. `HwExecutor` gained context-manager support and a guarded `stop()`/
`__del__`. Debug `print(vars(...))` replaced by a logger call; dead code removed.

**Checked.** New offline unit tests (14 cases) pass under Python 3.12.3: query
parsing including the malformed-input path, action equality/hashing, the
`ROW_PATTERNS` values, the `row_pattern` setter, and the thread's run/stop and
signal-handler guards. Not yet exercised on the board.

## 2026-10-12: `spd_eeprom.py read` works on ZCU104

**Files:** `rowhammer_tester/scripts/spd_eeprom.py`, new `spd/MTA4ATF51264HZ-3G2J1.bin`

**Problem.** `spd_eeprom.py read` could not dump a DDR4 SO-DIMM's SPD on the ZCU104,
so `--from-spd` builds were impossible. There were two bugs in the console path that selects the
I2C switch (0x74, channel 7) before reading the SPD:

1. `" ".join(args)` received integers (`[0x74, 0x80]`) and raised `TypeError`.
2. The BIOS command is `i2c_write <slaveaddr7bit> <addr> <addr_size> [<data>...]`. Without
   `addr_size` it only printed its usage message, so the switch was never selected.

**Change.** Integer arguments are formatted with `hex()` before building the BIOS command,
and the ZCU104 switch command is now `i2c_write 0x74 0x80 0x1`.

**Checked.** `spd_eeprom.py read` saved a 512-byte dump from the installed module. It decodes as
the Micron `4ATF51264HZ-3G2J1` (manufacturer 0x802c): DDR4 SO-DIMM, 1 rank, x16 devices,
8 banks, 65,536 rows, 1,024 columns. The dump is stored in `spd/` for `--from-spd` builds.

## 2026-10-12: ZCU104 EtherBone server acknowledges writes

**Files:** `firmware/zcu104/etherbone/etherbone.c`

**Problem.** `make srv` followed by any script failed with `TimeoutError` in
`litex_server` → `comm_udp.py` `write()`. The pinned LiteX (`eadfa2f2`) `CommUDP.write`
waits for a write acknowledgement, because LiteEth always sends one. The ZCU104 EtherBone
server on the PS Linux only replied to probes and reads. Write-only packets got no reply,
so every write timed out. Reads and probes were unaffected.

**Change.** `etherbone_process_packet` now answers write-only packets the way LiteEth does:
a packet with `nr=1` and one record with `rcount = wcount`, `wcount = 0`,
`base_ret_addr = 0` and all read addresses 0. Packets with reads are handled as before.

**Deployment.** The rootfs on the SD card was not rebuilt. The server was cross-compiled
statically, because the board uses uClibc:

```sh
cd firmware/zcu104/etherbone
make LDFLAGS=-static
```

The result, `build/zcu104_etherbone`, was copied to `/bin/zcu104_etherbone` on the board
and the service restarted with `/etc/init.d/S90zcu104_etherbone`. The original binary is kept on the
board as `/bin/zcu104_etherbone.orig`. To revert, copy it back and restart the service.
A future buildroot build of the rootfs picks up the change from the source automatically.

**Checked.** Single-word and multi-word writes through LiteX's `CommUDP.write` returned
acknowledgements, and a read-back of `ctrl_scratch` returned the written value.

## 2026-09-28: ZCU104 IDELAYCTRL `SIM_DEVICE` set to `ULTRASCALE_PLUS`

**Files:** `rowhammer_tester/targets/zcu104.py`

**Problem.** With Vivado 2026.1, `make build` failed at `write_bitstream` with 22
`DRC ADEF-911 SIM_DEVICE_arch_mismatch` errors. LiteX's `USIDELAYCTRL` (and `USPIDELAYCTRL`)
hard-code `SIM_DEVICE="ULTRASCALE"`, but the ZCU104 is UltraScale+. Older Vivado versions
reported this as a critical warning only.

**Change.** After creating `USIDELAYCTRL`, the CRG rewrites the `SIM_DEVICE` parameter of
the `IDELAYCTRL` instance to `"ULTRASCALE_PLUS"`. It reads the instance from
`self.idelayctrl._fragment.specials`, a private Migen attribute, because `Module.specials`
cannot be read back. The LiteX submodule is unchanged. `SIM_DEVICE` only affects
simulation, so the hardware behaviour is unchanged.

**Checked.** The generated Verilog contains `.SIM_DEVICE("ULTRASCALE_PLUS")`, the bitstream
builds, and on the board the BIOS completes DRAM calibration with `Memtest OK`.

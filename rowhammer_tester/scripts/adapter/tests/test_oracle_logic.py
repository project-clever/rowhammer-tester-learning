"""Offline unit tests for the oracle logic that does not touch the board.

The adapter modules import the rowhammer_tester framework, whose
``scripts/utils`` discovers generated board files at import time and fails
without a built target. To keep these tests runnable offline (and in CI), we
install lightweight stand-ins for the framework modules into ``sys.modules``
before importing ``hw_executor``. The stubs cover only the names the adapter
imports; the pure logic under test (query parsing, action equality, the
ROW_PATTERNS table and the row_pattern setter) does not depend on real
hardware.

Run with:
    python3 -m unittest rowhammer_tester.scripts.adapter.tests.test_oracle_logic
or directly:
    python3 rowhammer_tester/scripts/adapter/tests/test_oracle_logic.py
"""
import os
import sys
import types
import unittest


def _install_framework_stubs():
    """Register minimal stand-ins so `import hw_executor` succeeds offline."""

    def mod(name):
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    if 'rowhammer_tester.scripts.utils' not in sys.modules:
        u = mod('rowhammer_tester.scripts.utils')

        class RemoteClient:
            def open(self): ...
            def close(self): ...

        class DRAMAddressConverter:
            @staticmethod
            def load():
                return DRAMAddressConverter()

        u.RemoteClient = RemoteClient
        u.DRAMAddressConverter = DRAMAddressConverter
        u.get_litedram_settings = lambda: None
        u.get_expected_execution_cycles = lambda payload: 0
        u.get_generated_defs = lambda: {'SYS_CLK_FREQ': '1e8'}
        u.setup_inverters = lambda wb, div, mask: None
        u.hw_memset = lambda *a, **k: None
        u.execute_payload = lambda *a, **k: None
        u.hw_memtest = lambda *a, **k: []

    if 'rowhammer_tester.scripts.playbook.row_mappings' not in sys.modules:
        rm = mod('rowhammer_tester.scripts.playbook.row_mappings')

        class RowMapping:
            subclasses = {}

            @staticmethod
            def get_by_name(name):
                return RowMapping.subclasses[name]()

        class TrivialRowMapping(RowMapping):
            def logical_to_physical(self, r):
                return r

            def physical_to_logical(self, r):
                return r

        RowMapping.subclasses['TrivialRowMapping'] = TrivialRowMapping
        rm.RowMapping = RowMapping
        rm.TrivialRowMapping = TrivialRowMapping

    if 'rowhammer_tester.gateware.payload_executor' not in sys.modules:
        pe = mod('rowhammer_tester.gateware.payload_executor')

        class OpCode:
            NOOP = 0; ACT = 1; PRE = 2; REF = 3; LOOP = 4

        class Decoder:
            LOOP_JUMP = 12; LOOP_COUNT = 13

        class Encoder:
            def __init__(self, bankbits=0):
                self.bankbits = bankbits

        pe.OpCode = OpCode
        pe.Decoder = Decoder
        pe.Encoder = Encoder


# The adapter modules import each other by bare name (they are run as scripts
# from the adapter directory), so put that directory on the path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_install_framework_stubs()

import hw_executor as hwe  # noqa: E402
from hw_executor import HammerAction, ROW_PATTERNS  # noqa: E402


class TestHammerActionParsing(unittest.TestCase):
    def test_wellformed(self):
        a = HammerAction.from_string('HAMMER(54, 10000, 3)')
        self.assertEqual((a.row, a.reads, a.bitflips), (54, 10000, 3))

    def test_whitespace_tolerated(self):
        a = HammerAction.from_string('HAMMER( 7 , 8000 , 0 )')
        self.assertEqual((a.row, a.reads, a.bitflips), (7, 8000, 0))

    def test_malformed_raises_valueerror(self):
        for bad in ['', 'HAMMER()', 'HAMMER(1,2)', 'nonsense', 'HAMMER(a,b,c)']:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    HammerAction.from_string(bad)


class TestHammerActionEquality(unittest.TestCase):
    def test_eq(self):
        self.assertEqual(HammerAction(1, 2, 3), HammerAction(1, 2, 3))
        self.assertNotEqual(HammerAction(1, 2, 3), HammerAction(1, 2, 4))
        self.assertNotEqual(HammerAction(1, 2, 3), 'not an action')

    def test_hashable_and_consistent(self):
        a, b = HammerAction(1, 2, 3), HammerAction(1, 2, 3)
        self.assertEqual(hash(a), hash(b))
        self.assertEqual(len({a, b}), 1)
        self.assertIn(a, {b})


class TestRowPatterns(unittest.TestCase):
    def test_all_patterns_fit_32_bits(self):
        for name, (data, _div, _mask) in ROW_PATTERNS.items():
            with self.subTest(name=name):
                self.assertGreaterEqual(data, 0)
                self.assertLess(data, 2 ** 32)

    def test_known_values(self):
        self.assertEqual(ROW_PATTERNS['all_0'][0], 0)
        self.assertEqual(ROW_PATTERNS['all_1'][0], 2 ** 32 - 1)
        self.assertEqual(ROW_PATTERNS['alternate_10'][0], 0xAAAAAAAA)
        self.assertEqual(ROW_PATTERNS['alternate_01'][0], 0x55555555)
        self.assertEqual(ROW_PATTERNS['alternate_10'][0] ^ ROW_PATTERNS['alternate_01'][0],
                         2 ** 32 - 1)
        self.assertEqual(ROW_PATTERNS['alternate_0011'][0], 0x33333333)
        self.assertEqual(ROW_PATTERNS['alternate_1100'][0], 0xCCCCCCCC)
        self.assertEqual(ROW_PATTERNS['striped'][1:], (2, 0b10))
        self.assertEqual(ROW_PATTERNS['striped_i'][1:], (2, 0b01))

    def test_expected_pattern_set(self):
        expected = {
            'all_1', 'all_0', 'striped', 'striped_i',
            'alternate_10', 'alternate_01', 'alternate_0011', 'alternate_1100',
            'alternate_00001111', 'alternate_11110000',
        }
        self.assertEqual(set(ROW_PATTERNS), expected)


class TestRowPatternSetterLogic(unittest.TestCase):
    """Drive the row_pattern setter without constructing HwExecutor, which
    would open a board connection. setup_inverters is replaced so we can see
    how it is called."""

    def setUp(self):
        self.inverter_calls = []
        self._orig = hwe.setup_inverters
        hwe.setup_inverters = lambda wb, div, mask: self.inverter_calls.append((div, mask))

    def tearDown(self):
        hwe.setup_inverters = self._orig

    def _make_stub(self):
        obj = hwe.HwExecutor.__new__(hwe.HwExecutor)
        obj._wb = object()
        obj._pattern_data = None
        obj._row_pattern = None
        return obj

    def test_valid_pattern_sets_data_and_programs_inverters(self):
        obj = self._make_stub()
        hwe.HwExecutor.row_pattern.fset(obj, 'striped')
        self.assertEqual(obj.row_pattern, 'striped')
        self.assertEqual(obj._pattern_data, 2 ** 32 - 1)
        self.assertEqual(self.inverter_calls, [(2, 0b10)])

    def test_invalid_pattern_raises_and_lists_choices(self):
        obj = self._make_stub()
        with self.assertRaises(ValueError) as ctx:
            hwe.HwExecutor.row_pattern.fset(obj, 'does_not_exist')
        self.assertIn('all_0', str(ctx.exception))
        self.assertIsNone(obj._row_pattern)



class TestLoopingInterruptibleThread(unittest.TestCase):
    """utils.LoopingInterruptibleThread is self-contained (no framework)."""

    def setUp(self):
        import importlib
        self.utils = importlib.import_module('utils')

    def test_runs_all_items_and_repetitions(self):
        seen = []
        t = self.utils.LoopingInterruptibleThread(function=seen.append,
                                                  iterator=[10, 20], repetitions=3)
        t.start(); t.join()
        self.assertEqual(seen, [10, 10, 10, 20, 20, 20])

    def test_stop_interrupts_early(self):
        import time
        seen = []

        def slow(x):
            seen.append(x); time.sleep(0.05)

        t = self.utils.LoopingInterruptibleThread(function=slow,
                                                  iterator=list(range(100)), repetitions=1)
        t.start()
        time.sleep(0.07)
        t.stop()
        t.join(timeout=2)
        self.assertFalse(t.is_alive())
        self.assertLess(len(seen), 100)

    def test_sigint_handler_rejected_off_main_thread(self):
        import threading
        result = {}

        def build():
            try:
                self.utils.LoopingInterruptibleThread(function=lambda x: None,
                                                      iterator=[1],
                                                      install_sigint_handler=True)
                result['err'] = None
            except RuntimeError as e:
                result['err'] = str(e)

        h = threading.Thread(target=build); h.start(); h.join()
        self.assertIsNotNone(result['err'])
        self.assertIn('main thread', result['err'])

    def test_no_signal_handler_by_default_off_main_thread(self):
        import threading
        result = {}

        def build():
            try:
                self.utils.LoopingInterruptibleThread(function=lambda x: None, iterator=[1])
                result['ok'] = True
            except Exception as e:  # pragma: no cover
                result['ok'] = False; result['e'] = repr(e)

        h = threading.Thread(target=build); h.start(); h.join()
        self.assertTrue(result.get('ok'), result.get('e'))


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""普查工具的牙齿与"不误杀"（第二百三十一刀）。

工具本身不判"这是不是真问题"（它做不到，见模块 docstring），但**不许**连
"什么算一次读取"都判错：docstring 里提到 `data/…` 不是读取；被 patch 到临时路径的
零参调用在静态上无法区分（故工具只报、不做闸）。
"""

import unittest

from tests.live_read_census import census, hits_in_source


class TeethTest(unittest.TestCase):
    def test_flags_a_literal_live_path_read(self):
        src = ('def test_x(self):\n'
               '    ledger = json.loads(open("data/trading_ledger.json").read())\n'
               '    self.assertTrue(ledger)\n')
        hits = hits_in_source(src)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("data/trading_ledger.json", hits[0][3])

    def test_flags_a_zero_arg_reader_call(self):
        src = ('def test_y(self):\n'
               '    rows = load_ledger()\n'
               '    self.assertTrue(rows)\n')
        self.assertEqual([h[3] for h in hits_in_source(src)], ["零参读取 load_ledger"])

    def test_does_not_flag_a_docstring_mention(self):
        """docstring 里写「绝不碰生产 data/llm_models.json」不是一次读取。"""
        src = ('def test_z(self):\n'
               '    """写入必须落在沙箱，绝不能碰生产 data/llm_models.json。"""\n'
               '    self.assertTrue(True)\n')
        self.assertEqual(hits_in_source(src), [], "docstring 不该被当成读取")

    def test_does_not_flag_reads_with_explicit_temp_path(self):
        """显式传路径（临时目录）不是"零参读生产"。"""
        src = ('def test_w(self):\n'
               '    rows = load_ledger(tmp / "ledger.json")\n'
               '    self.assertTrue(rows is None or rows)\n')
        self.assertEqual(hits_in_source(src), [])

    def test_only_test_functions_are_scanned(self):
        src = ('def helper():\n'
               '    return load_ledger()\n')
        self.assertEqual(hits_in_source(src), [], "非 test_ 函数不是用例")


class RepoCensusTest(unittest.TestCase):
    def test_census_has_signal_and_is_not_empty(self):
        rows = census()
        self.assertGreaterEqual(len(rows), 10,
                                f"普查命中过少({len(rows)}) ⇒ 判据可能坏了；"
                                "它本该报出全部可疑处（含假阳性）供人判读")


if __name__ == "__main__":
    unittest.main()

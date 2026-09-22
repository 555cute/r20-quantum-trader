"""提示词渲染视图：**"系统段 / 用户段"按 `【USER PROMPT` 标记切分**（第二百九十一刀，开新面 prompt_views.py）。

★ 为什么值得单独开一刀：探针显示 `_split_snapshot` 与 `rendered_snapshots` **从未被执行过**
（`6/11 = 54.5%`）。全仓唯一碰它的用例

    tests/core/test_router_prompt_library.py:51
    "rendered_snapshots": mock.Mock(return_value={"snap": 1}),

把它打桩打掉了。于是 `【USER PROMPT` 这个**切分契约**（前端编辑器靠它把「系统提示词」
与「用户提示词」分成两个输入框）在没有测试的情况下裸奔 —— 标记一旦写错，编辑器会把
整份文件塞进其中一个框。

| 语义 | 口径 |
|---|---|
| ★ **切分标记是字面量 `【USER PROMPT`** | 找到 ⇒ 之前是 `system`、从标记起是 `user`；找不到 ⇒ `system=""` 且**整份内容都算 `user`**（宁可多给用户看，也不能把内容悄悄丢掉）|
| ★ **文件不存在 ⇒ 三字段空串** | 不是抛错、也不是 `None`：前端拿到的是可直接渲染的空结构 |
| ★ **`updated` 是 mtime 的整数字符串** | 前端靠它判断"这份快照是不是变了"；用 `int()` 截断（不做本地时区格式化）|
| ★ **解码失败不许炸** | `errors="replace"` —— 盘上的文件混进坏字节时给个带替换符的结果，而不是把整个编辑器打不开 |
| ★ **两份快照固定叫 trading / evolution** | 键名是前端契约；各自读 `ai_brain_last_prompt.txt` / `self_improvement_last_prompt.txt` |
| ★ **模板里的 `{{...}}` 是插槽，不是代码** | 这两段长模板靠 `{{变量}}` 被下游替换，故断言插槽真的在（防止有人把花括号"顺手"改掉）|
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from r20_backend import prompt_views as PV


class SplitSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "snapshot.txt"

    def _write(self, text, encoding="utf-8"):
        self.path.write_text(text, encoding=encoding)
        return self.path

    def test_a_missing_file_degrades_to_empty_fields(self):
        self.assertEqual(PV._split_snapshot(self.path),
                         {"system": "", "user": "", "updated": ""})

    def test_a_directory_raises_instead_of_degrading(self):
        """🐞 实测到的不对称：**文件不存在**会优雅降级成空字段，但**路径是目录**
        （或权限不足等其它 `OSError`）会**直接把 `IsADirectoryError` 抛出去** ——
        函数体内没有 try/except 兜住 `read_text`。本仓另一处 `load_council_config`
        有过同样形态的 `IsADirectoryError` 事故（见其提交记录），故按实际行为钉住。"""
        directory = Path(self.tmp.name) / "a_dir"
        directory.mkdir()
        with self.assertRaises(IsADirectoryError):
            PV._split_snapshot(directory)

    def test_the_marker_splits_system_and_user(self):
        self._write("系统指令\n【USER PROMPT】\n用户内容\n")
        out = PV._split_snapshot(self.path)
        self.assertEqual(out["system"], "系统指令")
        self.assertEqual(out["user"], "【USER PROMPT】\n用户内容")

    def test_without_the_marker_everything_is_user(self):
        """宁可整份都给用户看，也不能把内容悄悄丢掉。"""
        self._write("只有一段内容\n")
        out = PV._split_snapshot(self.path)
        self.assertEqual(out["system"], "")
        self.assertEqual(out["user"], "只有一段内容")

    def test_a_marker_at_the_very_start_leaves_system_empty(self):
        self._write("【USER PROMPT】只有用户段")
        out = PV._split_snapshot(self.path)
        self.assertEqual(out["system"], "")
        self.assertEqual(out["user"], "【USER PROMPT】只有用户段")

    def test_the_marker_is_matched_case_sensitively(self):
        self._write("系统\n【user prompt】小写不算\n")
        out = PV._split_snapshot(self.path)
        self.assertEqual(out["system"], "")
        self.assertIn("小写不算", out["user"])

    def test_the_split_happens_at_the_first_marker(self):
        self._write("系统\n【USER PROMPT】先\n【USER PROMPT】后\n")
        out = PV._split_snapshot(self.path)
        self.assertEqual(out["system"], "系统")
        self.assertIn("【USER PROMPT】后", out["user"])

    def test_both_segments_are_stripped(self):
        self._write("\n\n系统\n\n【USER PROMPT】\n\n用户\n\n")
        out = PV._split_snapshot(self.path)
        self.assertEqual(out["system"], "系统")
        self.assertEqual(out["user"], "【USER PROMPT】\n\n用户")

    def test_an_empty_file_yields_empty_segments_but_a_real_mtime(self):
        self._write("")
        out = PV._split_snapshot(self.path)
        self.assertEqual(out["system"], "")
        self.assertEqual(out["user"], "")
        self.assertNotEqual(out["updated"], "")

    def test_updated_is_the_modified_time_as_an_integer_string(self):
        self._write("x")
        out = PV._split_snapshot(self.path)
        self.assertEqual(out["updated"], str(int(self.path.stat().st_mtime)))
        self.assertTrue(out["updated"].isdigit())

    def test_updated_tracks_the_current_file(self):
        self._write("x")
        first = PV._split_snapshot(self.path)["updated"]
        import os
        os.utime(self.path, (2_000_000_000, 2_000_000_000))
        self.assertEqual(PV._split_snapshot(self.path)["updated"], "2000000000")
        self.assertNotEqual(first, "2000000000")

    def test_undecodable_bytes_are_replaced_not_raised(self):
        """`errors="replace"`：坏字节变成替换符，编辑器仍拿得到内容而不是崩掉。"""
        self.path.write_bytes(b"system\xff\xfe" + "【USER PROMPT】好".encode("utf-8"))
        out = PV._split_snapshot(self.path)
        self.assertIn("【USER PROMPT】", out["user"])
        self.assertNotEqual(out["system"], "")
        self.assertIn("\ufffd", out["system"])

    def test_the_return_shape_is_exactly_three_strings(self):
        self._write("x")
        out = PV._split_snapshot(self.path)
        self.assertEqual(set(out), {"system", "user", "updated"})
        self.assertTrue(all(isinstance(v, str) for v in out.values()))


class RenderedSnapshotsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self._start = mock.patch.object(PV, "DATA", self.data).start()
        self.addCleanup(mock.patch.stopall)

    def _snapshot(self, name, text):
        (self.data / name).write_text(text, encoding="utf-8")

    def test_both_keys_are_present_even_when_both_files_are_missing(self):
        out = PV.rendered_snapshots()
        self.assertEqual(set(out), {"trading", "evolution"})
        self.assertEqual(out["trading"]["system"], "")
        self.assertEqual(out["evolution"]["user"], "")

    def test_each_key_reads_its_own_file(self):
        self._snapshot("ai_brain_last_prompt.txt", "交易系统\n【USER PROMPT】交易用户")
        self._snapshot("self_improvement_last_prompt.txt", "进化系统\n【USER PROMPT】进化用户")
        out = PV.rendered_snapshots()
        self.assertEqual(out["trading"]["system"], "交易系统")
        self.assertEqual(out["trading"]["user"], "【USER PROMPT】交易用户")
        self.assertEqual(out["evolution"]["system"], "进化系统")
        self.assertEqual(out["evolution"]["user"], "【USER PROMPT】进化用户")

    def test_the_two_snapshots_do_not_bleed_into_each_other(self):
        self._snapshot("ai_brain_last_prompt.txt", "只有交易")
        out = PV.rendered_snapshots()
        self.assertEqual(out["trading"]["user"], "只有交易")
        self.assertEqual(out["evolution"]["user"], "")

    def test_it_reads_from_the_module_data_dir(self):
        """键名与文件名都是前端契约，改成别的名字编辑器就读不到了。"""
        self._snapshot("ai_brain_last_prompt.txt", "A")
        self._snapshot("self_improvement_last_prompt.txt", "B")
        out = PV.rendered_snapshots()
        self.assertEqual(out["trading"]["user"], "A")
        self.assertEqual(out["evolution"]["user"], "B")


class TemplateTests(unittest.TestCase):
    def test_the_real_data_dir_is_under_the_repo_root(self):
        self.assertEqual(PV.DATA, PV.ROOT / "data")
        self.assertTrue(str(PV.ROOT).endswith("r20"))

    def test_the_evolution_template_exposes_its_slots(self):
        for slot in ("timestamp_beijing", "existing_memory_markdown", "total",
                     "wins", "losses", "win_rate", "total_net", "total_fees",
                     "target_instruments", "closed_trades_json"):
            with self.subTest(slot=slot):
                self.assertIn("{{" + slot + "}}", PV.EVOLUTION_USER_TEMPLATE)

    def test_the_evolution_template_demands_strict_json_output(self):
        self.assertIn("change_status", PV.EVOLUTION_USER_TEMPLATE)
        self.assertIn("NO_CHANGE", PV.EVOLUTION_USER_TEMPLATE)

    def test_the_trading_template_declares_its_sections(self):
        for section in ("当前决策时间戳与市场时效", "账户当前持仓与风险敞口全景",
                        "在途未成交限价挂单", "R20 启发式实战认知与长期记忆",
                        "全标的池原生行情、技术指标与筹码矩阵"):
            with self.subTest(section=section):
                self.assertIn(section, PV.TRADING_USER_TEMPLATE)

    def test_the_trading_template_keeps_the_hard_gate_disclaimer(self):
        """编辑器的"用户提示词"框不许改到 P0 与执行层硬门禁 —— 模板里明写了这条。"""
        self.assertIn("P0 与执行层硬门禁仍由 System Prompt 和执行器锁定",
                      PV.TRADING_USER_TEMPLATE)

    def test_neither_template_contains_the_split_marker(self):
        """切分标记只由下游拼接产生；模板里自带会把切分点提前。"""
        self.assertNotIn("【USER PROMPT", PV.EVOLUTION_USER_TEMPLATE)
        self.assertNotIn("【USER PROMPT", PV.TRADING_USER_TEMPLATE)

    def test_the_templates_are_non_trivial(self):
        self.assertGreater(len(PV.EVOLUTION_USER_TEMPLATE), 500)
        self.assertGreater(len(PV.TRADING_USER_TEMPLATE), 300)


if __name__ == "__main__":
    unittest.main()

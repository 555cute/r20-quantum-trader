"""周期披露与防抖状态的**契约**（第二百一十五刀）。

两条本会话反复出现的语义在这里都有明确落点：

1. **读不到 ≠ 没有**：`_load_watchdog_state` 刻意区分两种"空" —— 文件不存在 ⇒ `{}`
   （首次运行，合法空态）；**不可读/损坏 ⇒ `None`**（调用方拿到 `None` 必须不写单）。
   把这两者混为一谈，正是"读不出来当成没有缺口"那一类缺陷。
2. **报告器不得成为新的单点故障**：`cycle_disclosure_payload` 绝不抛（非 dict 的巡检报告、
   `None` 集合、含 `None` 的列表一律宽容）；`write_cycle_disclosure_snapshot` 失败只返回 False。

另外 `cycle_disclosure_summary` 是**唯一**把载荷渲染成一行的地方（第 51 刀定的单一事实源），
它必须每轮都出现，且把"未开闸"这种"没在跑的保护"也写出来（不是错误，但必须被看见）。
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.trader.cycle_stages import (_load_watchdog_state, _save_watchdog_state,
                                         cycle_disclosure_payload,
                                         cycle_disclosure_summary,
                                         write_cycle_disclosure_snapshot)


class DisclosurePayloadTest(unittest.TestCase):
    def test_broken_venues_are_deduped_and_sorted(self):
        p = cycle_disclosure_payload(broken_venues=["gate", "binance", "gate", "", None])
        self.assertEqual(p["broken_venues"], ["binance", "gate"])
        self.assertEqual(p["broken_venue_count"], 2)
        self.assertFalse(p["clean"], "有坏所就不算干净")

    def test_clean_cycle(self):
        p = cycle_disclosure_payload()
        self.assertTrue(p["clean"])
        self.assertEqual(p["broken_venue_count"], 0)
        self.assertEqual(cycle_disclosure_summary(p), "[周期披露] 本轮无跳过/未核验项")

    def test_non_dict_watchdog_report_is_tolerated(self):
        """巡检报告不是 dict（或压根没跑）⇒ 不许抛、也不许编造数字。"""
        for bad in (None, "字符串", 42, ["列表"]):
            with self.subTest(report=bad):
                p = cycle_disclosure_payload(watchdog_report=bad)
                self.assertEqual(p["watchdog_errors"], 0)
                self.assertEqual(p["watchdog_critical"], 0)

    def test_entries_blocked_is_disclosed_and_not_clean(self):
        p = cycle_disclosure_payload(entries_blocked=True)
        self.assertFalse(p["clean"], "禁本轮新开仓不算干净周期")
        self.assertIn("禁本轮新开仓", cycle_disclosure_summary(p))

    def test_shape_violations_are_counted_with_a_head(self):
        p = cycle_disclosure_payload(shape_violations=["a", "b", "c", "d", "e"])
        self.assertEqual(p["shape_violation_count"], 5)
        self.assertEqual(p["shape_violation_head"], ["a", "b", "c"], "只截前三作为摘要")
        line = cycle_disclosure_summary(p)
        self.assertIn("数据形状违规=5", line)
        self.assertIn("共5条", line, "超过三条要写总数，否则读者以为只有三条")

    def test_watchdog_counts_and_disabled_flag_are_disclosed(self):
        p = cycle_disclosure_payload(watchdog_report={"errors": [{}, {}], "critical": [{}]},
                                     watchdog_enabled=False)
        line = cycle_disclosure_summary(p)
        self.assertIn("错误=2", line)
        self.assertIn("严重缺口=1", line)
        self.assertIn("跨所保护巡检未开闸", line,
                      "没在跑的保护也是事实，必须被看见（不是错误，但也不能沉默）")

    def test_summary_tolerates_non_dict_payload(self):
        self.assertEqual(cycle_disclosure_summary(None), "[周期披露] 本轮无跳过/未核验项")
        self.assertEqual(cycle_disclosure_summary("字符串"), "[周期披露] 本轮无跳过/未核验项")


class WatchdogStateContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20-wd-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "watchdog.json"

    def test_missing_file_is_a_legit_empty_state(self):
        self.assertEqual(_load_watchdog_state(str(self.path)), {},
                         "文件不存在 = 首次运行 = 合法空态（不是 None）")

    def test_corrupt_file_is_undecidable_not_empty(self):
        self.path.write_text("{ 坏 JSON", encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = _load_watchdog_state(str(self.path))
        self.assertIsNone(out, "读不出来必须返回 None（调用方据此不写单），绝不当成没有缺口")
        self.assertIn("读取失败", buf.getvalue(), "而且必须真的出声")

    def test_non_dict_gaps_is_undecidable(self):
        self.path.write_text(json.dumps({"gaps": ["不是字典"]}), encoding="utf-8")
        self.assertIsNone(_load_watchdog_state(str(self.path)))

    def test_single_bad_timestamp_is_dropped_with_a_trace(self):
        self.path.write_text(json.dumps({"gaps": {"BTC": 1700000000, "ETH": "坏时间"}}),
                             encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = _load_watchdog_state(str(self.path))
        self.assertEqual(out, {"BTC": 1700000000.0}, "坏的那条丢掉、好的仍可用")
        self.assertIn("ETH", buf.getvalue(), "丢掉哪一条要留痕")

    def test_save_is_atomic_and_roundtrips(self):
        self.assertTrue(_save_watchdog_state(str(self.path), {"BTC": 1.0}))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("updated_at", raw, "快照要带写入时间（用来算新鲜度）")
        self.assertEqual(_load_watchdog_state(str(self.path)), {"BTC": 1.0})

    def test_save_caps_the_number_of_entries(self):
        big = {f"K{i}": float(i) for i in range(600)}
        self.assertTrue(_save_watchdog_state(str(self.path), big))
        saved = json.loads(self.path.read_text(encoding="utf-8"))["gaps"]
        self.assertEqual(len(saved), 500, "防抖状态要有上限，避免无限膨胀")

    def test_save_failure_returns_false_without_raising(self):
        with patch("scripts.trader.cycle_stages.os.replace",
                   side_effect=OSError("replace boom")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                ok = _save_watchdog_state(str(self.path), {"BTC": 1.0})
        self.assertFalse(ok, "写失败只返回 False，绝不抛（否则拖垮整个周期）")
        self.assertIn("写入失败", buf.getvalue())

    def test_snapshot_write_returns_false_on_failure(self):
        def boom(path, body):
            raise OSError("atomic boom")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok = write_cycle_disclosure_snapshot(path=str(self.path), payload={"clean": True},
                                                _atomic_write_json=boom)
        self.assertFalse(ok, "可观测性失败绝不拖垮周期")
        self.assertIn("快照写入失败", buf.getvalue())

    def test_snapshot_write_adds_freshness_stamp(self):
        captured = {}

        def fake(path, body):
            captured.update(body)

        self.assertTrue(write_cycle_disclosure_snapshot(path=str(self.path),
                                                       payload={"clean": True},
                                                       _atomic_write_json=fake))
        self.assertIn("written_at_ms", captured)
        self.assertGreater(captured["written_at_ms"], 1_600_000_000_000,
                           "毫秒级时间戳：让「很久没更新」与「本轮很干净」在指标上可区分")


if __name__ == "__main__":
    unittest.main()

"""台账/追踪器读取的**披露纪律**，以及台账行的**守卫不一致**（第二百四十三刀）。

先打印再动笔。实测三件事：

| 实测 | 结论 |
|---|---|
| `sync_full_ledger.py` 写台账用 `sorted(..., reverse=True)` | 台账文件是 **最新在前** ⇒ `trades_table = valid[:60]` 就是**最新 60 笔** ✓（上一刀把"取前 60"列为待议 ⇒ **本刀结案：不是缺陷**）|
| `load_json_dict_disclosed` 缺文件 ⇒ `({}, '')` **不吵**；坏文件 ⇒ `({}, 原因)` **且打印 warn**：「读不出来…（相关字段将显示为空，**请勿据此判断"没有数据"**）」| ★ **区分"没有"与"读不到"** —— 正是本项目「读不到 ≠ 没有」的落地 |
| `load_signal_journal_by_inst` 坏文件 ⇒ `{}`，**静默** | ✗ 与隔壁**不对称**（追踪器那条会披露）⇒ 记入待议 13 |

★ 另外发现一处**守卫不一致**（本刀按现状钉住，并列为待议 25）：

- 加标签的循环写了 `if isinstance(_t, dict)` —— 有守卫；
- **过滤**循环却直接 `t.get(...)` —— **没有守卫**。
⇒ 台账文件里只要有一行**不是 dict**（半写/损坏的常见形态），`AttributeError` 会从
`load_ledger_lifecycle_trades` 抛出去，而它是 `update_cache_cycle` 的第 7 段
⇒ **整个看板缓存周期会失败**（不只是台账表少几行）。本刀用 `assertRaises` 把**当前行为**钉住，
**不声称它是对的**。
"""

import json
import os
import tempfile
import time
import unittest
from unittest import mock

from r20_backend.dashboard_payload import ledger_view as LV
from r20_backend.dashboard_payload.readers import load_json_dict_disclosed

OLD_RESET = "2020-01-01 00:00:00"


class DisclosureTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def test_missing_file_is_silent_and_empty(self):
        """缺文件 = **没有** ⇒ 空字典 + 空原因，**不吵**。"""
        out, source = load_json_dict_disclosed(os.path.join(self.dir.name, "nope.json"))
        self.assertEqual((out, source), ({}, ""))

    def test_corrupt_file_discloses_and_says_do_not_read_it_as_absent(self):
        """坏文件 = **读不到** ⇒ 空字典 + 原因 + 打印，并明说**别当成没有数据**。"""
        bad = os.path.join(self.dir.name, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{ 不是 json")
        with mock.patch("builtins.print") as fake_print:
            out, source = load_json_dict_disclosed(bad)
        self.assertEqual(out, {})
        self.assertTrue(source, "必须给出原因")
        printed = " ".join(str(c) for c in fake_print.call_args_list)
        self.assertIn("请勿据此判断", printed, "要明确告诉读者：读不到 ≠ 没有")

    def test_position_trackers_go_through_the_disclosing_reader(self):
        """★ 追踪器走 `load_json_dict_disclosed`（第 52 刀：与 factors 版收敛到**同一实现**）。"""
        with mock.patch.object(LV, "load_json_dict_disclosed",
                               return_value=({"BTC-USDT-SWAP_long": {}}, "原因")) as reader:
            out = LV.load_position_trackers(self.dir.name)
        self.assertEqual(out, {"BTC-USDT-SWAP_long": {}})
        reader.assert_called_once()
        self.assertTrue(str(reader.call_args.args[0]).endswith("position_trackers.json"))


class SignalJournalTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "signal_journal.json")

    def _write(self, payload):
        with open(self.path, "w", encoding="utf-8") as f:
            if isinstance(payload, str):
                f.write(payload)
            else:
                json.dump(payload, f)

    def test_records_are_grouped_by_name_then_inst(self):
        self._write([{"name": "BTC-USDT-SWAP", "v": 1},
                     {"inst": "ETH-USDT-SWAP", "v": 2},
                     {"name": "BTC-USDT-SWAP", "v": 3},
                     {"name": "", "v": 4}])
        out = LV.load_signal_journal_by_inst(self.dir.name)
        self.assertEqual(sorted(out), ["BTC-USDT-SWAP", "ETH-USDT-SWAP"])
        self.assertEqual([r["v"] for r in out["BTC-USDT-SWAP"]], [1, 3], "同名归到一组且保序")
        self.assertEqual(out["ETH-USDT-SWAP"][0]["v"], 2, "没有 name 时用 inst")

    def test_missing_file_is_empty(self):
        self.assertEqual(LV.load_signal_journal_by_inst(self.dir.name), {})

    def test_corrupt_file_is_silently_empty_unlike_its_neighbour(self):
        """✗ 记录现状：坏文件 ⇒ `{}` **且静默**（追踪器那条会披露）⇒ 待议 13。

        本用例只把**不对称**钉住：同一层里两个读取器，一个披露、一个不披露。
        """
        self._write("{ 坏")
        with mock.patch("builtins.print") as fake_print:
            out = LV.load_signal_journal_by_inst(self.dir.name)
        self.assertEqual(out, {}, "现状：读不到 ⇒ 当空")
        self.assertFalse(fake_print.called, "现状：连一句 warn 都没有（这正是待议点）")


class NonDictRowTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ledger = os.path.join(self.dir.name, "trading_ledger.json")

    def _write(self, rows):
        with open(self.ledger, "w", encoding="utf-8") as f:
            json.dump(rows, f)

    def test_a_non_dict_row_currently_crashes_the_whole_loader(self):
        """★ 待议 25：**过滤循环没有 isinstance 守卫** ⇒ 脏行让整个装载抛 `AttributeError`。

        加标签的循环有守卫、过滤循环没有 —— **同一函数里两套标准**。
        本用例钉的是**当前行为**（会抛），**不是**在说这是期望行为。
        """
        self._write([{"inst": "BTC-USDT-SWAP", "status": "holding", "open_time": time.time()},
                     "junk"])
        with self.assertRaises(AttributeError):
            LV.load_ledger_lifecycle_trades(self.ledger, self.dir.name, False, OLD_RESET)

    def test_pure_dict_rows_are_unaffected(self):
        self._write([{"inst": "BTC-USDT-SWAP", "status": "holding", "open_time": time.time()}])
        valid, table = LV.load_ledger_lifecycle_trades(self.ledger, self.dir.name, False,
                                                       OLD_RESET)
        self.assertEqual(len(valid), 1)
        self.assertEqual(table, valid)


class CalculusFallbackTest(unittest.TestCase):
    """★ 两段**重复**的 calculus 兜底（代码里同一段写了两遍）都要能走到。

    第一段：`status == "holding"` 且 tracker 里没有快照时；
    第二段：以上都不成立、且 journal 因果匹配也没命中时（跨所台账无 journal 历史）。
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ledger = os.path.join(self.dir.name, "trading_ledger.json")
        with open(os.path.join(self.dir.name, "calculus_snapshot.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"instruments": [{"name": "BTC-USDT-SWAP", "calculus": {"z": 1}}]}, f)

    def _run(self, row):
        with open(self.ledger, "w", encoding="utf-8") as f:
            json.dump([row], f)
        with mock.patch("scripts.trader.signal_snapshot.build_signal_snapshot",
                        return_value={"velocity": 9.0}) as builder:
            valid, table = LV.load_ledger_lifecycle_trades(self.ledger, self.dir.name, False,
                                                           OLD_RESET)
        return valid, table, builder

    def test_holding_row_without_a_tracker_uses_calculus(self):
        valid, table, builder = self._run({"inst": "BTC-USDT-SWAP", "status": "holding",
                                           "side": "多", "open_time": time.time(),
                                           "open_px": 100.0})
        builder.assert_called_once()
        self.assertEqual(builder.call_args.kwargs.get("data_dir"), self.dir.name)
        self.assertIn("entry_snapshot", table[0])
        self.assertNotEqual(table[0]["snapshot_observability"], "NONE")

    def test_closed_row_without_journal_uses_calculus_too(self):
        valid, table, builder = self._run({"inst": "BTC-USDT-SWAP", "status": "closed",
                                           "side": "多", "open_time": time.time(),
                                           "close_time": time.time(), "close_px": 101.0})
        builder.assert_called_once()
        self.assertIn("entry_snapshot", table[0])

    def test_unknown_instrument_leaves_the_row_unobservable(self):
        valid, table, builder = self._run({"inst": "DOGE-USDT-SWAP", "status": "closed",
                                           "side": "多", "open_time": time.time(),
                                           "close_time": time.time()})
        builder.assert_not_called()
        self.assertNotIn("entry_snapshot", table[0])
        self.assertEqual(table[0]["snapshot_observability"], "NONE")


if __name__ == "__main__":
    unittest.main()

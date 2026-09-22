"""`ledger_view.py` 收口：兜底分支与静默吞（第二百四十四刀）。

先打印这 8 行再动笔。它们全是**异常/兜底**分支，共同主题是「**出错了就别硬撑，但也不该假装没事**」：

| 行 | 行为 | 评价 |
|---|---|---|
| 96 | journal 记录的 `entryTime` 解析不出 ⇒ 跳过该候选 | ✓ 对的（不猜）|
| 145 | `os.path.getmtime` 抛错 ⇒ `pass`，仍认为**需要同步** | ✓ 保守（宁可同步一次）|
| 167 | 触发同步失败 ⇒ `pass` | ⚠️ **静默**：看板不受影响，但没人知道台账没刷新 |
| 174 | 台账 JSON 坏 ⇒ `pass`，当作空表 | ⚠️ **静默当空**（待议 13 同族）|
| 194 | 同级目录没 journal、`workspace_dir/data` 有 ⇒ 用后者 | ✓ 路径回退 |
| 196 | 两者都没有 ⇒ 退到 `workspace_dir` | ✓ |
| 236 / 262 | 两段 calculus 兜底里构造快照抛错 ⇒ `pass` | ✓ 该行最终标注 `NONE`（**不可观测就写不可观测**，不编造）|
"""

import json
import os
import tempfile
import time
import unittest
from unittest import mock

from r20_backend.dashboard_payload import ledger_view as LV

OLD_RESET = "2020-01-01 00:00:00"


class JournalCandidateTest(unittest.TestCase):
    def test_an_unparseable_candidate_time_is_skipped(self):
        """★ 第 96 行：候选记录的 `entryTime` 解析不出来 ⇒ 跳过，不猜。"""
        journal = {"BTC-USDT-SWAP": [
            {"side": "long", "entryTime": "不是时间", "snapshot": {"which": "bad"}},
            {"side": "long", "entryTime": time.time(), "snapshot": {"which": "good"}},
        ]}
        out = LV.match_trade_snapshot(journal, "BTC-USDT-SWAP", time.time(), "多")
        self.assertEqual(out, {"which": "good"}, "跳过坏候选，用能解析的那条")


class LedgerTailTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ledger = os.path.join(self.dir.name, "trading_ledger.json")

    def _write(self, content, age_seconds=0):
        with open(self.ledger, "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(self.ledger, (old, old))

    def _sync_ready(self):
        os.makedirs(os.path.join(self.dir.name, "scripts"), exist_ok=True)
        with open(os.path.join(self.dir.name, "scripts", "sync_full_ledger.py"), "w",
                  encoding="utf-8") as f:
            f.write("# stub\n")

    def test_getmtime_failure_still_asks_for_a_sync(self):
        """★ 第 145 行：连文件时间都读不到 ⇒ **保守地**认为需要同步。"""
        self._sync_ready()
        self._write([], age_seconds=600)
        with mock.patch("r20_backend.spawn.run_script") as runner, \
                mock.patch("os.path.getmtime", side_effect=OSError("权限")):
            with mock.patch.dict("os.environ", {"R20_LEDGER_SYNC_DISABLED": ""}):
                LV.load_ledger_lifecycle_trades(self.ledger, self.dir.name, True, OLD_RESET)
        runner.assert_called_once()

    def test_a_failing_sync_does_not_break_the_dashboard(self):
        """★ 第 167 行：同步抛错被吞 ⇒ 装载照常返回（静默现状，已在待议里）。"""
        self._sync_ready()
        self._write([{"inst": "BTC-USDT-SWAP", "status": "holding",
                      "open_time": time.time()}], age_seconds=600)
        with mock.patch("r20_backend.spawn.run_script", side_effect=RuntimeError("起不来")):
            with mock.patch.dict("os.environ", {"R20_LEDGER_SYNC_DISABLED": ""}):
                valid, table = LV.load_ledger_lifecycle_trades(
                    self.ledger, self.dir.name, True, OLD_RESET)
        self.assertEqual(len(table), 1, "同步失败不拖垮看板")

    def test_a_corrupt_ledger_is_treated_as_empty(self):
        """★ 第 174 行：台账 JSON 坏 ⇒ 当作**空表**（静默，待议 13 同族）。"""
        self._write("{ 坏掉的 json")
        with mock.patch("builtins.print") as fake_print:
            valid, table = LV.load_ledger_lifecycle_trades(
                self.ledger, self.dir.name, False, OLD_RESET)
        self.assertEqual((valid, table), ([], []))
        self.assertFalse(fake_print.called, "现状：一声不响")

    def test_journal_is_looked_up_in_the_workspace_data_dir(self):
        """★ 第 194 行：同级目录没有 journal、`workspace_dir/data` 有 ⇒ 用后者。"""
        sub = os.path.join(self.dir.name, "data")
        os.makedirs(sub, exist_ok=True)
        ledger = os.path.join(sub, "trading_ledger.json")
        with open(ledger, "w", encoding="utf-8") as f:
            json.dump([], f)
        ws_data = os.path.join(self.dir.name, "data")
        os.makedirs(ws_data, exist_ok=True)   # 同级 == ws/data 时两者重合
        os.makedirs(os.path.join(self.dir.name, "ws", "data"), exist_ok=True)
        with open(os.path.join(self.dir.name, "ws", "data", "signal_journal.json"), "w",
                  encoding="utf-8") as f:
            json.dump([], f)
        with mock.patch.object(LV, "load_signal_journal_by_inst",
                               return_value={}) as journal_reader:
            LV.load_ledger_lifecycle_trades(ledger, os.path.join(self.dir.name, "ws"),
                                            False, OLD_RESET)
        self.assertTrue(journal_reader.called)
        self.assertTrue(str(journal_reader.call_args.args[0]).endswith("ws/data"),
                        "回退到 workspace_dir/data")

    def test_relative_ledger_path_falls_back_to_the_workspace_dir(self):
        """★ 第 196 行：`ledger_file` 没有目录部分且 ws/data 也没 journal ⇒ 退到 workspace_dir。"""
        with mock.patch.object(LV, "load_signal_journal_by_inst",
                               return_value={}) as journal_reader:
            with mock.patch("os.path.exists", return_value=False):
                LV.load_ledger_lifecycle_trades("trading_ledger.json", self.dir.name,
                                                False, OLD_RESET)
        self.assertEqual(journal_reader.call_args.args[0], self.dir.name)


class CalculusFailureTest(unittest.TestCase):
    """★ 第 236 / 262 行：两段 calculus 兜底**构造快照失败**时 ⇒ 该行标注不可观测。"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ledger = os.path.join(self.dir.name, "trading_ledger.json")
        with open(os.path.join(self.dir.name, "calculus_snapshot.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"instruments": [{"name": "BTC-USDT-SWAP", "calculus": {}}]}, f)

    def _run(self, row):
        with open(self.ledger, "w", encoding="utf-8") as f:
            json.dump([row], f)
        with mock.patch("scripts.trader.signal_snapshot.build_signal_snapshot",
                        side_effect=RuntimeError("构造不出来")):
            valid, table = LV.load_ledger_lifecycle_trades(self.ledger, self.dir.name,
                                                           False, OLD_RESET)
        return table

    def test_holding_row_construction_failure_is_unobservable(self):
        table = self._run({"inst": "BTC-USDT-SWAP", "status": "holding", "side": "多",
                           "open_time": time.time(), "open_px": 100.0})
        self.assertNotIn("entry_snapshot", table[0])
        self.assertEqual(table[0]["snapshot_observability"], "NONE",
                         "兜底也失败 ⇒ 老老实实写不可观测，不编造")

    def test_closed_row_construction_failure_is_unobservable(self):
        table = self._run({"inst": "BTC-USDT-SWAP", "status": "closed", "side": "多",
                           "open_time": time.time(), "close_time": time.time()})
        self.assertNotIn("entry_snapshot", table[0])
        self.assertEqual(table[0]["snapshot_observability"], "NONE")


if __name__ == "__main__":
    unittest.main()

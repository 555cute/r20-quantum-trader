"""台账装载：**同步闸门**、过滤/截断、逐单快照取证（第二百四十二刀）。

先打印目标行再动笔（上一刀的教训）。实测要点：

| 语义 | 口径 |
|---|---|
| ★ **持仓中永远保留** | 过滤条件是「`close_time`/`open_time`/`time` 任一 ≥ reset」**或** `status == "holding"` ⇒ 持仓中的行**不受 reset 影响** |
| 截断 | `trades_table = valid[:LEDGER_TRADES_MAX]`（取**前** 60，按文件原始顺序）|
| ★ **同步闸门（批E 测试封闭闸）** | `autosync_enabled=False` ⇒ **不触发**同步；`R20_LEDGER_SYNC_DISABLED ∈ {1,true,yes}` ⇒ 也不触发；台账文件 **60 秒内**改过 ⇒ 不需要同步 |
| ★ **审计批7 的修复** | 触发时走 `r20_backend.spawn.run_script(..., timeout=45, label="sync_full_ledger")` —— 旧实现是 `python3` **shell 串**（这台主机**根本没有该可执行文件**，rc=127 被 `capture_output` **吞**）⇒ **服务器侧台账刷新从未生效**；旧 `timeout=10s` 还**短于**真实三所全史拉取（约 20-30s）⇒ **必然静默超时** |
| ★ 快照优先序 | 行内 `_SNAPSHOT_KEYS`（**非空** dict）＞ `holding` 行的 tracker `signal_snapshot` ＞ `calculus_snapshot.json` ＞ `match_trade_snapshot`（journal）＞ calculus 兜底 |
| ★ **缺席即缺席** | 一行都拿不到证据 ⇒ **不写 `entry_snapshot`**，但**一定**写 `snapshot_observability="NONE"` |
"""

import json
import os
import tempfile
import time
import unittest
from unittest import mock

from r20_backend.dashboard_payload import ledger_view as LV

OLD_RESET = "2020-01-01 00:00:00"
FUTURE_RESET = "2030-01-01 00:00:00"


def _row(**kw):
    row = {"inst": "BTC-USDT-SWAP", "side": "多",
           "open_time": time.time() - 3600, "close_time": time.time() - 1800,
           "status": "closed"}
    row.update(kw)
    return row


class LedgerLoaderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ledger = os.path.join(self.dir.name, "trading_ledger.json")

    def _write(self, rows, age_seconds=0):
        with open(self.ledger, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(self.ledger, (old, old))

    def _run(self, reset=OLD_RESET, autosync=False, env=None):
        ctx = mock.patch.dict("os.environ", env or {}, clear=False)
        ctx.start()
        self.addCleanup(ctx.stop)
        with mock.patch("r20_backend.spawn.run_script") as runner:
            out = LV.load_ledger_lifecycle_trades(self.ledger, self.dir.name,
                                                  autosync, reset)
        return out[0], out[1], runner   # (valid, table, runner)

    def test_holding_rows_survive_any_reset(self):
        self._write([_row(status="holding", open_time=time.time() - 86400 * 300),
                     _row(open_time=time.time() - 86400 * 300,
                          close_time=time.time() - 86400 * 300)])
        valid, table, _r = self._run(reset=FUTURE_RESET)
        self.assertEqual(len(valid), 1, "旧持仓仍然有效（holding 不受 reset 影响）")
        self.assertEqual(valid[0]["status"], "holding")
        self.assertEqual(table, valid)

    def test_rows_before_reset_are_dropped(self):
        self._write([_row(open_time=time.time() - 86400 * 300,
                          close_time=time.time() - 86400 * 300)])
        valid, _table, _r = self._run(reset=FUTURE_RESET)
        self.assertEqual(valid, [])

    def test_table_is_truncated_to_the_view_cap_keeping_the_file_order(self):
        self._write([_row(tag=i) for i in range(LV.LEDGER_TRADES_MAX + 3)])
        valid, table, _r = self._run()
        self.assertEqual(len(valid), LV.LEDGER_TRADES_MAX + 3, "valid 不截断")
        self.assertEqual(len(table), LV.LEDGER_TRADES_MAX)
        self.assertEqual([r["tag"] for r in table], list(range(LV.LEDGER_TRADES_MAX)),
                         "取**前** 60（按文件原始顺序）")

    def test_missing_ledger_file_is_empty_not_an_error(self):
        valid, table, _r = self._run()
        self.assertEqual((valid, table), ([], []))

    def test_disabled_autosync_never_spawns_a_sync(self):
        """★ 测试封闭闸：`autosync_enabled=False` ⇒ 一个子进程都不许起。"""
        self._write([_row()], age_seconds=600)
        _v, _t, runner = self._run(autosync=False)
        runner.assert_not_called()

    def test_recent_ledger_file_needs_no_sync(self):
        self._write([_row()])
        _v, _t, runner = self._run(autosync=True)
        runner.assert_not_called()

    def test_stale_ledger_triggers_the_fixed_sync_invocation(self):
        """★ 审计批7 的守卫：走 `run_script`（同解释器）且 **timeout=45**。"""
        os.makedirs(os.path.join(self.dir.name, "scripts"), exist_ok=True)
        script = os.path.join(self.dir.name, "scripts", "sync_full_ledger.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write("# stub\n")
        self._write([_row()], age_seconds=600)
        # ⚠️ 测试环境自身就置了 R20_LEDGER_SYNC_DISABLED（批E 封闭闸，见 tests/__init__.py）
        # ⇒ 想验证"同步真的会触发"，必须先把它置空串。
        _v, _t, runner = self._run(autosync=True, env={"R20_LEDGER_SYNC_DISABLED": ""})
        runner.assert_called_once()
        args, kwargs = runner.call_args
        self.assertEqual(args[0], script)
        self.assertEqual(kwargs.get("timeout"), 45, "旧的 10s 短于真实拉取（20-30s）必然静默超时")
        self.assertEqual(kwargs.get("label"), "sync_full_ledger")

    def test_env_flag_also_disables_the_sync(self):
        os.makedirs(os.path.join(self.dir.name, "scripts"), exist_ok=True)
        with open(os.path.join(self.dir.name, "scripts", "sync_full_ledger.py"),
                  "w", encoding="utf-8") as f:
            f.write("# stub\n")
        self._write([_row()], age_seconds=600)
        _v, _t, runner = self._run(autosync=True, env={"R20_LEDGER_SYNC_DISABLED": "YES"})
        runner.assert_not_called()

    def test_inline_snapshot_is_used_and_labelled(self):
        self._write([_row(signal_snapshot={"velocity": 1.0})])
        _valid, table, _r = self._run()
        row = table[0]
        self.assertIn("entry_snapshot", row, "行内非空快照 ⇒ 挂上去")
        self.assertNotEqual(row["snapshot_observability"], "NONE")

    def test_empty_inline_snapshot_is_not_evidence(self):
        self._write([_row(signal_snapshot={})])
        _valid, table, _r = self._run()
        row = table[0]
        self.assertNotIn("entry_snapshot", row, "**缺席即缺席**：空快照不写键")
        self.assertEqual(row["snapshot_observability"], "NONE",
                         "并明确标注不可观测（绝不编造）")

    def test_holding_row_falls_back_to_the_position_tracker(self):
        _t = _row(status="holding", inst="ETH-USDT-SWAP", side="空")
        self._write([_t])
        with open(os.path.join(self.dir.name, "position_trackers.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"ETH-USDT-SWAP_short": {"signal_snapshot": {"velocity": 3.0}}}, f)
        _valid, table, _r = self._run()
        row = table[0]
        self.assertIn("entry_snapshot", row, "持仓中 ⇒ 用 tracker 里的开仓快照")

    def test_labels_are_shared_between_valid_and_table_for_short_ledgers(self):
        """★ 未触发截断时两表是**同一批对象** ⇒ 标签对两者都可见（浅切片）。"""
        self._write([_row(signal_snapshot={"velocity": 2.0})])
        valid, table, _r = self._run()
        self.assertIs(valid[0], table[0])
        self.assertIn("entry_snapshot", valid[0])


if __name__ == "__main__":
    unittest.main()

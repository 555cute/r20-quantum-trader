"""周期编排三个阶段的行为契约（第二百一十六刀）。

全量探针显示 `scripts/trader/cycle_stages.py` 70.0%，未命中集中在编排阶段。本月这几处
（`fetch_universe_and_manage_positions` 的循环体、`persist_state_and_sync_ledger` 的台账自动同步、
`preflight_reconcile_and_housekeeping` 的整条主路径）**从未被执行过** —— 而它们是每轮巡检都要走的：

- **只对真有持仓的标的**做止盈止损管理（没仓的标的不许碰）；
- 台账自动同步只在开关打开时跑，且**两个脚本各自存在才跑各自**；
- 引擎未就绪/陈旧挂单清不掉 ⇒ **本周期中止**（`return None`），不带着盲区去下单；
- 对账失败 ⇒ fail-closed：**只禁本轮新增下单**（`entries_blocked`），持仓管理照常。
"""

import datetime as _dt
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.trader.cycle_stages import (fetch_universe_and_manage_positions,
                                        persist_state_and_sync_ledger,
                                        preflight_reconcile_and_housekeeping)


class _FakePool:
    def __init__(self, max_workers=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, fn, items):
        return [fn(i) for i in items]


class UniverseStageTest(unittest.TestCase):
    def _run(self, factors, *, stale=0):
        calls = {"managed": [], "saved": None, "pruned": None}
        trackers = {"keep": 1}

        def manage(f, curr_pos, tr, ts, actions):
            calls["managed"].append((f["symbol"], curr_pos["pos"]))

        # ⚠️ `all_positions` 是**字典**（按标的索引），不是列表 —— 我第一版传了 `[]`，
        # 桩里 `pos.get(...)` 当场 AttributeError。
        fetch_universe_and_manage_positions(
            all_positions={"BTC": {"pos": 1.0}}, real_pos_dict={"BTC": 1},
            timestamp_full="2026-09-21 12:00:00",
            usdt_available=1000.0, TARGET_INSTRUMENTS=["BTC", "ETH"],
            ThreadPoolExecutor=_FakePool,
            fetch_single_instrument_data=lambda item, pos, usdt: {
                "symbol": item, "position": pos.get(item)},
            load_trackers=lambda: trackers,
            manage_position_tp_and_trailing=manage,
            prune_trackers=lambda tr, real: (calls.__setitem__("pruned", (tr, real)), stale)[1],
            save_trackers=lambda tr: calls.__setitem__("saved", dict(tr)))
        return calls

    def test_only_positions_that_exist_are_managed(self):
        calls = self._run([{"symbol": "BTC", "position": {"pos": 1.0}},
                           {"symbol": "ETH", "position": None}])
        self.assertEqual([c[0] for c in calls["managed"]], ["BTC"],
                         f"没有持仓的标的不许做止盈止损管理：{calls['managed']}")

    def test_stale_tracker_cleanup_is_disclosed(self):
        calls = self._run([], stale=3)
        self.assertIsNotNone(calls["saved"], "无论有没有动作都要落盘 trackers")
        self.assertEqual(calls["pruned"][1] and calls["pruned"][0], {"keep": 1})


class PersistStageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20-persist-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        self.written = {}
        self.ran = []

    def _run(self, *, autosync, scripts=(), raise_on=None):
        for name in scripts:
            (self.root / "scripts" / name).write_text("# stub", encoding="utf-8")
            (self.root / "scripts" / name).chmod(0o644)

        def run_captured(path, **kw):
            if raise_on and path.endswith(raise_on):
                raise RuntimeError("sync boom")
            self.ran.append(os.path.basename(path))

        log = self.root / "trader.log"
        persist_state_and_sync_ledger(
            _xv_total=2, active_pos_count=1, all_factors=[], cb_active=False, cb_reason="",
            executed_actions=["开了 1 单"], long_count=1, short_count=0,
            timestamp_full="2026-09-21 12:00:00", DATA_DIR=str(self.root),
            LEDGER_AUTOSYNC_ENABLED=autosync, LOG_FILE=str(log),
            MAX_CONCURRENT_POSITIONS=5, WORKSPACE_DIR=str(self.root), __version__="8.2.0",
            _atomic_write_json=lambda path, body: self.written.update({"path": path, "body": body}),
            _run_captured=run_captured,
            build_state_payload=lambda **kw: {"ok": True},
            evaluate_asset_signal=lambda f: (1.0, "HOLD", [], "tag", ""), os=os)
        return log

    def test_state_is_written_atomically_and_log_appended(self):
        log = self._run(autosync=False)
        self.assertTrue(self.written["path"].endswith("trading_state.json"),
                        f"状态要写进 DATA_DIR：{self.written['path']}")
        self.assertIn("巡检完成", log.read_text(encoding="utf-8"))
        self.assertEqual(self.ran, [], "开关关闭时一个同步脚本都不许跑")

    def test_autosync_runs_only_the_scripts_that_exist(self):
        log = self._run(autosync=True, scripts=("sync_full_ledger.py",))
        self.assertEqual(self.ran, ["sync_full_ledger.py"],
                         f"只该跑存在的那一个：{self.ran}")

    def test_autosync_runs_both_when_both_exist(self):
        self._run(autosync=True, scripts=("sync_full_ledger.py", "db_manager.py"))
        self.assertEqual(self.ran, ["sync_full_ledger.py", "db_manager.py"])

    def test_autosync_failure_only_warns(self):
        """同步脚本炸了只告警：巡检结果与状态落盘不受影响。"""
        log = self._run(autosync=True, scripts=("sync_full_ledger.py",),
                        raise_on="sync_full_ledger.py")
        self.assertTrue(self.written, "状态仍必须落盘")
        self.assertIn("巡检完成", log.read_text(encoding="utf-8"))


class _Env:
    def __init__(self, configured=True, mode="demo"):
        self.configured = configured
        self.mode = mode


class PreflightStageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20-pre-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        self.ran = []

    def _run(self, *, configured=True, reconcile_ok=True, orders_ok=True, harvester=True,
             harvester_raises=False):
        if harvester:
            (self.root / "scripts" / "news_sentiment_harvester.py").write_text("# stub", encoding="utf-8")

        def run_captured(path, **kw):
            if harvester_raises:
                raise RuntimeError("harvest boom")
            self.ran.append(os.path.basename(path))

        return preflight_reconcile_and_housekeeping(
            WORKSPACE_DIR=str(self.root), _run_captured=run_captured,
            clean_stale_open_orders=lambda keep_ord_ids=None: (
                (True, "") if orders_ok else (False, "查不动陈旧挂单")),
            current_environment=lambda: _Env(configured=configured),
            datetime=_dt, load_trackers=lambda: {"t": 1}, os=os,
            reconcile_pending_orders=lambda trackers=None: (
                (True, ["ord-1"]) if reconcile_ok else (False, ["ord-1"])))

    def test_unconfigured_engine_aborts_the_cycle(self):
        self.assertIsNone(self._run(configured=False),
                          "引擎未就绪 ⇒ 本周期中止（不许带着盲区下单）")

    def test_happy_path_returns_entries_flag_and_timestamp(self):
        out = self._run()
        self.assertIsNotNone(out)
        entries_blocked, ts = out
        self.assertFalse(entries_blocked)
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
                         "必须用北京时间算出的绝对时间戳（下游日志/台账共用）")
        self.assertEqual(self.ran, ["news_sentiment_harvester.py"])

    def test_reconcile_failure_blocks_entries_but_not_position_management(self):
        out = self._run(reconcile_ok=False)
        self.assertIsNotNone(out, "对账失败**不中止**周期（持仓管理照常）")
        entries_blocked, _ = out
        self.assertTrue(entries_blocked, "对账失败 ⇒ fail-closed：只禁本轮新增下单")

    def test_unverifiable_stale_orders_abort_the_cycle(self):
        self.assertIsNone(self._run(orders_ok=False),
                          "陈旧挂单清不掉 ⇒ 本周期中止（不许边挂边下单）")

    def test_missing_harvester_is_skipped(self):
        out = self._run(harvester=False)
        self.assertIsNotNone(out)
        self.assertEqual(self.ran, [])

    def test_harvester_failure_only_warns(self):
        out = self._run(harvester_raises=True)
        self.assertIsNotNone(out, "舆情采集失败不该中止交易周期")


if __name__ == "__main__":
    unittest.main()

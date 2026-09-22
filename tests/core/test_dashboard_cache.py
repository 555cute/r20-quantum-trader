"""看板缓存门面：**薄壳必须调用期解析、失败绝不覆盖 last-good、缓存自愈**（第二百七十六刀，开新面 dashboard_cache.py）。

先打印整个文件（556 行）再动笔。它是 legacy 整包的**纯库**（不持有 app），
主体是三类东西：① 十几个"薄壳"转调 `dashboard_payload/*`；② 大函数 `update_cache_cycle`；
③ 后台预热线程 + TTL 自愈缓存。

| 语义 | 口径 |
|---|---|
| ★ **薄壳要在调用期解析门面全局** | 每个薄壳把 `DATA_DIR`/`AI_MEMORY_MD_FILE`/`LEDGER_JSON_FILE` 等**模块常量**当参数传给 core；用例改门面常量后断言 core **收到新值**（这正是"结构优化阶段 2 / B2"的验收条件）|
| ★ **核心账户查询失败绝不写零** | `balance_ok`/`positions_ok` 任一为假时：有可用的 last-good 就**拷贝成 stale**（`is_stale=True`、`data_health.status` 在 `STALE`/`NOT_READY` 之间二选一、带上 `last_success_at` 与 `cache_age_seconds`），没有才退化成空壳 `OFFLINE`/`NOT_READY` |
| ★ **陈旧分支必须显式改 `is_stale`** | 第 197 刀的修复点：拷贝自上次成功载荷、原本带 `is_stale=False` ⇒ 不显式改真，前端永远看不出是旧数据 |
| ★ **`_fetch_json` 三态** | 成功 → `(True, data, "")`；`OKXNotConfigured` → `(False, None, NOT_READY 人话)`；其它异常 → `(False, None, "类型名: 消息")`，**绝不冒泡**（它在并发线程池里跑）|
| ★ **今日已实现以台账为单一事实源** | `valid_ledger_trades` 非空时用 `ledger_today_stats` 覆盖 bills 口径（`_today_stats_source = "ledger_multi_venue"`）；台账口径抛错则**回退 OKX bills** 并打 warn |
| ★ **后台预热线程可启可停** | `start_` 只在"没有线程或已死"时新建；`stop_` 只翻标志位；循环体前 0.5s 静默起步、异常**吞掉不中断**（否则一次网络抖动就永久停更）|
| ★ **TTL 自愈 + 双重检查** | `refresh_cache_if_needed` 先无锁快路径，未命中才拿锁，**锁内再查一次**（避免惊群）|

⚠️ 本模块在 **import 期**就有副作用（读 last-good 缓存 + 起后台线程），且 `CACHE_DATA`/`LAST_CACHE_TIME`
是**模块级可变全局**。用例基类逐个快照并还原，避免把全局状态泄漏给同批其它用例。
"""

import asyncio
import json
import sys
import types
import unittest
from unittest import mock

from fastapi.responses import JSONResponse

from r20_backend import dashboard_cache as DC


class _Base(unittest.TestCase):
    """快照并还原模块级可变全局（本模块 import 期就带副作用）。"""

    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self._saved = {name: getattr(DC, name) for name in
                       ("CACHE_DATA", "LAST_CACHE_TIME", "CACHE_LOCK",
                        "_BG_WORKER_THREAD", "_BG_WORKER_RUNNING")}
        for name, value in self._saved.items():
            self.addCleanup(setattr, DC, name, value)


class ConstantTests(unittest.TestCase):
    def test_directories_are_anchored_at_the_repo_root(self):
        self.assertTrue(DC.BASE_DIR.endswith("r20"))
        self.assertEqual(DC.DASHBOARD_DIR, DC.BASE_DIR + "/r20_backend")
        self.assertEqual(DC.WORKSPACE_DIR, DC.BASE_DIR)
        self.assertEqual(DC.DATA_DIR, DC.BASE_DIR + "/data")
        self.assertEqual(DC.LOGS_DIR, DC.BASE_DIR + "/logs")

    def test_data_files_all_live_under_data_dir(self):
        for name in ("LEDGER_JSON_FILE", "NEWS_SENTIMENT_FILE", "REVIEW_JOURNAL_FILE",
                     "REPORT_JSON_FILE", "POSITION_TRACKER_FILE", "SNAPSHOTS_JSON_FILE",
                     "STATE_JSON_FILE", "AI_DECISIONS_FILE", "AI_HISTORY_FILE",
                     "AI_LAST_PROMPT_FILE", "FACTOR_LIBRARY_FILE", "AI_MEMORY_MD_FILE",
                     "DASHBOARD_CACHE_FILE"):
            with self.subTest(constant=name):
                self.assertTrue(getattr(DC, name).startswith(DC.DATA_DIR + "/"))

    def test_log_file_lives_under_logs_dir(self):
        self.assertTrue(DC.LOG_FILE.startswith(DC.LOGS_DIR + "/"))

    def test_ledger_autosync_is_disabled_inside_the_test_sandbox(self):
        self.assertIs(DC.LEDGER_AUTOSYNC_ENABLED, False,
                      "tests/__init__.py 在 import 前置了 R20_LEDGER_SYNC_DISABLED=1")

    def test_target_instruments_are_preloaded(self):
        self.assertEqual(len(DC.TARGET_INSTRUMENTS), 9)

    def test_not_ready_text_is_human_readable(self):
        self.assertIn("NOT READY", DC._NOT_READY_TEXT)
        self.assertIn("未配置", DC._NOT_READY_TEXT)


class FetchJsonTests(unittest.TestCase):
    def test_success_returns_the_payload(self):
        self.assertEqual(DC._fetch_json(lambda a, b=2: (a, b), 1),
                         (True, (1, 2), ""))

    def test_not_configured_maps_to_the_human_message(self):
        import scripts.okx_rest as rest

        def _boom():
            raise rest.OKXNotConfigured("no key")

        self.assertEqual(DC._fetch_json(_boom), (False, None, DC._NOT_READY_TEXT))

    def test_other_exceptions_are_described_not_raised(self):
        def _boom():
            raise RuntimeError("OKX 51008 余额不足")

        self.assertEqual(DC._fetch_json(_boom),
                         (False, None, "RuntimeError: OKX 51008 余额不足"))

    def test_args_and_kwargs_are_forwarded(self):
        seen = {}

        def _fn(*args, **kwargs):
            seen.update(args=args, kwargs=kwargs)
            return "ok"

        DC._fetch_json(_fn, 1, 2, limit=100)
        self.assertEqual(seen, {"args": (1, 2), "kwargs": {"limit": 100}})


class ThinShellTests(_Base):
    """薄壳的验收条件：core 必须收到**调用时**的门面常量，而不是 import 时的旧值。"""

    def test_load_trading_memory_md_passes_the_current_paths(self):
        core = self._start(mock.patch.object(DC, "_core_load_trading_memory_md",
                                             return_value="md"))
        self._start(mock.patch.object(DC, "AI_MEMORY_MD_FILE", "/new/memory.md"))
        self._start(mock.patch.object(DC, "DATA_DIR", "/new/data"))
        self.assertEqual(DC.load_trading_memory_md(), "md")
        core.assert_called_once_with("/new/memory.md", "/new/data")

    def test_memory_freshness_note_passes_the_current_data_dir(self):
        core = self._start(mock.patch.object(DC, "_core__memory_freshness_note",
                                             return_value="note"))
        self._start(mock.patch.object(DC, "DATA_DIR", "/new/data"))
        self.assertEqual(DC._memory_freshness_note(), "note")
        core.assert_called_once_with("/new/data")

    def test_load_position_trackers_passes_the_current_file(self):
        core = self._start(mock.patch.object(DC, "_core_load_position_trackers",
                                             return_value={"a": 1}))
        self._start(mock.patch.object(DC, "POSITION_TRACKER_FILE", "/new/trackers.json"))
        self.assertEqual(DC.load_position_trackers(), {"a": 1})
        core.assert_called_once_with("/new/trackers.json")

    def test_enrich_position_risk_fields_passes_the_current_file(self):
        core = self._start(mock.patch.object(DC, "_core_enrich_position_risk_fields"))
        self._start(mock.patch.object(DC, "POSITION_TRACKER_FILE", "/new/trackers.json"))
        DC.enrich_position_risk_fields(["p"], {"t": 1})
        core.assert_called_once_with("/new/trackers.json", ["p"], {"t": 1})

    def test_enrich_position_risk_fields_tolerates_omitted_trackers(self):
        core = self._start(mock.patch.object(DC, "_core_enrich_position_risk_fields"))
        DC.enrich_position_risk_fields(["p"])
        self.assertIsNone(core.call_args[0][2])

    def test_load_local_factor_library_passes_the_current_file(self):
        core = self._start(mock.patch.object(DC, "_core__load_local_factor_library"))
        self._start(mock.patch.object(DC, "FACTOR_LIBRARY_FILE", "/new/factors.json"))
        DC._load_local_factor_library()
        core.assert_called_once_with("/new/factors.json")

    def test_build_factors_from_local_files_passes_every_path(self):
        core = self._start(mock.patch.object(DC, "_core__build_factors_from_local_files"))
        self._start(mock.patch.object(DC, "FACTOR_LIBRARY_FILE", "/f.json"))
        self._start(mock.patch.object(DC, "AI_DECISIONS_FILE", "/d.json"))
        self._start(mock.patch.object(DC, "STATE_JSON_FILE", "/s.json"))
        DC._build_factors_from_local_files(["p"], "ts")
        core.assert_called_once_with("/f.json", "/d.json", "/s.json", ["p"], "ts")

    def test_build_ai_health_passes_the_current_data_dir(self):
        core = self._start(mock.patch.object(DC, "_core_build_ai_health"))
        self._start(mock.patch.object(DC, "DATA_DIR", "/new/data"))
        DC.build_ai_health([{"a": 1}])
        core.assert_called_once_with("/new/data", [{"a": 1}])

    def test_load_persisted_dashboard_cache_passes_the_current_file(self):
        core = self._start(mock.patch.object(DC, "_core_load_persisted_dashboard_cache"))
        self._start(mock.patch.object(DC, "DASHBOARD_CACHE_FILE", "/new/cache.json"))
        DC.load_persisted_dashboard_cache()
        core.assert_called_once_with("/new/cache.json")

    def test_persist_dashboard_cache_passes_file_and_dir(self):
        core = self._start(mock.patch.object(DC, "_core_persist_dashboard_cache"))
        self._start(mock.patch.object(DC, "DASHBOARD_CACHE_FILE", "/new/cache.json"))
        self._start(mock.patch.object(DC, "DATA_DIR", "/new/data"))
        DC.persist_dashboard_cache({"k": 1})
        core.assert_called_once_with("/new/cache.json", "/new/data", {"k": 1})

    def test_load_cross_venue_data_passes_dir_and_decisions_file(self):
        core = self._start(mock.patch.object(DC, "_core__load_cross_venue_data"))
        self._start(mock.patch.object(DC, "DATA_DIR", "/new/data"))
        self._start(mock.patch.object(DC, "AI_DECISIONS_FILE", "/new/decisions.json"))
        DC._load_cross_venue_data()
        core.assert_called_once_with("/new/data", "/new/decisions.json")

    def test_inject_local_data_into_stale_forwards_every_seam(self):
        core = self._start(mock.patch.object(DC, "_core__inject_local_data_into_stale"))
        self._start(mock.patch.object(DC, "NEWS_SENTIMENT_FILE", "/n.json"))
        self._start(mock.patch.object(DC, "AI_HISTORY_FILE", "/h.json"))
        self._start(mock.patch.object(DC, "REPORT_JSON_FILE", "/r.json"))
        self._start(mock.patch.object(DC, "AI_LAST_PROMPT_FILE", "/p.txt"))
        self._start(mock.patch.object(DC, "LOG_FILE", "/l.log"))
        self._start(mock.patch.object(DC, "LEDGER_JSON_FILE", "/led.json"))
        DC._inject_local_data_into_stale({"stale": 1}, ["pos"], "ts")
        args = core.call_args[0]
        self.assertEqual(args[6:], ("/n.json", "/h.json", "/r.json", "/p.txt",
                                    "/l.log", "/led.json", {"stale": 1}, ["pos"], "ts"))
        self.assertEqual(len(args), 15, "9 个 seam + 6 个路径/载荷")

    def test_inject_local_data_forwards_the_shell_functions_not_the_cores(self):
        core = self._start(mock.patch.object(DC, "_core__inject_local_data_into_stale"))
        DC._inject_local_data_into_stale({}, [], "ts")
        args = core.call_args[0]
        self.assertIs(args[0], DC._load_local_factor_library)
        self.assertIs(args[1], DC._load_cross_venue_data)
        self.assertIs(args[2], DC._load_portfolio_risk_data)
        self.assertIs(args[3], DC._build_factors_from_local_files)
        self.assertIs(args[4], DC.build_ai_health)
        self.assertIs(args[5], DC.load_trading_memory_md)


class CacheLockTests(_Base):
    def test_lock_is_created_lazily_and_reused(self):
        self._start(mock.patch.object(DC, "CACHE_LOCK", None))
        first = DC.get_cache_lock()
        self.assertIsInstance(first, asyncio.Lock)
        self.assertIs(DC.get_cache_lock(), first)

    def test_existing_lock_is_returned_as_is(self):
        sentinel = object()
        self._start(mock.patch.object(DC, "CACHE_LOCK", sentinel))
        self.assertIs(DC.get_cache_lock(), sentinel)


class BackgroundWorkerTests(_Base):
    def test_stop_only_flips_the_flag(self):
        self._start(mock.patch.object(DC, "_BG_WORKER_RUNNING", True))
        DC.stop_dashboard_background_worker()
        self.assertIs(DC._BG_WORKER_RUNNING, False)

    def test_start_spawns_a_daemon_thread_when_none_exists(self):
        self._start(mock.patch.object(DC, "_BG_WORKER_THREAD", None))
        self._start(mock.patch.object(DC, "_BG_WORKER_RUNNING", False))
        thread = self._start(mock.patch.object(DC.threading, "Thread"))
        DC.start_dashboard_background_worker()
        self.assertIs(DC._BG_WORKER_RUNNING, True)
        self.assertIs(DC._BG_WORKER_THREAD, thread.return_value)
        thread.assert_called_once_with(target=DC._dashboard_background_worker_loop,
                                       daemon=True, name="dashboard_cache_worker")
        thread.return_value.start.assert_called_once_with()

    def test_start_reuses_a_living_thread(self):
        alive = mock.Mock()
        alive.is_alive.return_value = True
        self._start(mock.patch.object(DC, "_BG_WORKER_THREAD", alive))
        thread = self._start(mock.patch.object(DC.threading, "Thread"))
        DC.start_dashboard_background_worker()
        thread.assert_not_called()

    def test_start_replaces_a_dead_thread(self):
        dead = mock.Mock()
        dead.is_alive.return_value = False
        self._start(mock.patch.object(DC, "_BG_WORKER_THREAD", dead))
        thread = self._start(mock.patch.object(DC.threading, "Thread"))
        DC.start_dashboard_background_worker()
        thread.assert_called_once()

    def test_loop_pauses_first_then_cycles_and_stops_on_flag(self):
        self._start(mock.patch.object(DC, "_BG_WORKER_RUNNING", True))
        cycles = []
        self._start(mock.patch.object(DC, "update_cache_cycle",
                                      side_effect=lambda: cycles.append(1)))
        sleeps = []

        def _sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 2:                 # 跑完第一圈之后退出
                DC._BG_WORKER_RUNNING = False

        self._start(mock.patch.object(DC.time, "sleep", side_effect=_sleep))
        DC._dashboard_background_worker_loop()
        self.assertEqual(sleeps, [0.5, 2.0], "起步 0.5s，之后每圈 2s")
        self.assertEqual(len(cycles), 1)

    def test_loop_survives_an_exploding_cycle(self):
        """一次网络抖动不能让预热线程永久停更。"""
        self._start(mock.patch.object(DC, "_BG_WORKER_RUNNING", True))
        self._start(mock.patch.object(DC, "update_cache_cycle",
                                      side_effect=RuntimeError("网络炸了")))
        calls = {"n": 0}

        def _sleep(seconds):
            calls["n"] += 1
            if calls["n"] >= 3:
                DC._BG_WORKER_RUNNING = False

        self._start(mock.patch.object(DC.time, "sleep", side_effect=_sleep))
        DC._dashboard_background_worker_loop()      # 不得抛
        self.assertEqual(calls["n"], 3, "异常被吞掉后循环继续")


def _account_state(*, private_not_ready=False, balance_ok=True, positions_ok=True,
                   positions=None, trackers=None):
    return (private_not_ready, 1000.0, balance_ok, 500.0, 1, [{"ordId": "o"}],
            [{"ordId": "o"}], positions if positions is not None else [{"instId": "BTC"}],
            positions_ok, 0, 2000.0, 12.5, trackers or {}, 3.0)


def _local_reads():
    return {"adaptive_cfg": {"a": 1}, "ai_history_list": [{"h": 1}],
            "ai_last_prompt_text": "prompt", "ai_memory_md_content": "mem",
            "disk_free_gb": 42.0, "factor_lib_snapshot": {"f": 1},
            "news_data": {"n": 1}, "review_data": {"r": 1}, "snapshots_list": [{"s": 1}]}


class UpdateCycleFailureTests(_Base):
    """核心账户查询失败：绝不把 last-good 覆盖成零。"""

    def setUp(self):
        super().setUp()
        self._start(mock.patch.object(DC, "CACHE_DATA", None))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 0))

    def _wire(self, **kwargs):
        self._start(mock.patch.object(DC, "collect_core_account_state",
                                      return_value=_account_state(**kwargs)))
        self.enrich = self._start(mock.patch.object(DC, "enrich_position_risk_fields"))
        self.inject = self._start(mock.patch.object(DC, "persist_dashboard_cache"))
        return self

    def test_degraded_without_last_good_yields_an_offline_shell(self):
        self._start(mock.patch.object(DC, "_is_meaningful_dashboard_snapshot",
                                      return_value=False))
        self._wire(balance_ok=False)
        DC.update_cache_cycle()
        self.assertEqual(DC.CACHE_DATA["data_health"]["status"], "OFFLINE")
        self.assertIs(DC.CACHE_DATA["data_health"]["partial"], True)
        self.assertIsNone(DC.CACHE_DATA["data_health"]["message"])
        self.assertEqual(DC.CACHE_DATA["positions_summary"]["total"], 0)
        self.assertEqual(DC.CACHE_DATA["account"], {})
        self.assertGreater(DC.LAST_CACHE_TIME, 0)

    def test_not_ready_without_last_good_says_why(self):
        self._start(mock.patch.object(DC, "_is_meaningful_dashboard_snapshot",
                                      return_value=False))
        self._wire(balance_ok=False, private_not_ready=True)
        DC.update_cache_cycle()
        self.assertEqual(DC.CACHE_DATA["data_health"]["status"], "NOT_READY")
        self.assertEqual(DC.CACHE_DATA["data_health"]["message"], DC._NOT_READY_TEXT)

    def test_degraded_with_last_good_replays_it_as_stale(self):
        previous = {"timestamp": "2026-09-01 08:00:00 (北京时间)", "is_stale": False,
                    "positions_summary": {"items": [{"instId": "BTC"}]}}
        self._start(mock.patch.object(DC, "CACHE_DATA", previous))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 1000.0))
        self._start(mock.patch.object(DC, "_is_meaningful_dashboard_snapshot",
                                      return_value=True))
        self._start(mock.patch.object(DC.time, "time", return_value=1042.0))
        self._wire(balance_ok=False)
        inserted = []
        self._start(mock.patch.object(DC, "_inject_local_data_into_stale",
                                      side_effect=lambda *a: inserted.append(a)))
        DC.update_cache_cycle()

        stale = DC.CACHE_DATA
        self.assertIs(stale["is_stale"], True, "第 197 刀的修复点：必须显式改真")
        self.assertEqual(stale["data_health"]["status"], "STALE")
        self.assertIs(stale["data_health"]["partial"], True)
        self.assertEqual(stale["data_health"]["last_success_at"],
                         "2026-09-01 08:00:00 (北京时间)")
        self.assertEqual(stale["data_health"]["cache_age_seconds"], 42.0)
        self.assertIs(previous["is_stale"], False,
                      "原始 last-good 载荷本身不得被就地改动（stale 是它的拷贝）")
        self.assertEqual(inserted[0][1], [{"instId": "BTC"}],
                         "本地数据要注入到**陈旧仓位行**上")
        self.enrich.assert_called_once_with([{"instId": "BTC"}], {})

    def test_stale_without_a_previous_success_has_no_cache_age(self):
        self._start(mock.patch.object(DC, "CACHE_DATA",
                                      {"timestamp": "t", "positions_summary": {}}))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 0))
        self._start(mock.patch.object(DC, "_is_meaningful_dashboard_snapshot",
                                      return_value=True))
        self._wire(positions_ok=False)
        self._start(mock.patch.object(DC, "_inject_local_data_into_stale"))
        DC.update_cache_cycle()
        self.assertIsNone(DC.CACHE_DATA["data_health"]["cache_age_seconds"])

    def test_not_ready_stale_uses_the_human_message(self):
        self._start(mock.patch.object(DC, "CACHE_DATA",
                                      {"timestamp": "t", "positions_summary": {}}))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 1.0))
        self._start(mock.patch.object(DC, "_is_meaningful_dashboard_snapshot",
                                      return_value=True))
        self._wire(positions_ok=False, private_not_ready=True)
        self._start(mock.patch.object(DC, "_inject_local_data_into_stale"))
        DC.update_cache_cycle()
        self.assertEqual(DC.CACHE_DATA["data_health"]["status"], "NOT_READY")
        self.assertEqual(DC.CACHE_DATA["data_health"]["message"], DC._NOT_READY_TEXT)


class UpdateCycleLiveTests(_Base):
    """核心账户查询成功：完整 live 载荷路径。"""

    def setUp(self):
        super().setUp()
        self._start(mock.patch.object(DC, "CACHE_DATA", None))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 0))
        self._start(mock.patch.object(DC, "collect_core_account_state",
                                      return_value=_account_state()))
        self.algo = self._start(mock.patch.object(DC, "_core_collect_algo_protection"))
        self.cross = self._start(mock.patch.object(DC, "_core_collect_cross_venue_positions",
                                                   return_value=(2, 1, 7.5)))
        self.reset = self._start(mock.patch.object(DC, "_core_read_reset_initial_state",
                                                   return_value=("2026-01-01", 1000.0)))
        self.bills = self._start(mock.patch.object(DC.okx_rest, "bills",
                                                   return_value=[{"billId": "b"}]))
        self.metrics = self._start(mock.patch.object(DC, "aggregate_bills_and_metrics",
                                                     return_value=tuple(range(22))))
        self.lines = self._start(mock.patch.object(DC, "read_text_lines",
                                                   return_value=["log"]))
        self.factors = self._start(mock.patch.object(DC, "_core_build_factors_list",
                                                     return_value=(["f"], {"s": 1})))
        self.ledger = self._start(mock.patch.object(DC, "_core_load_ledger_lifecycle_trades",
                                                    return_value=(None, [])))
        self.local = self._start(mock.patch.object(DC, "_core_load_local_reads",
                                                   return_value=_local_reads()))
        self.sidecars = self._start(mock.patch.object(DC, "_core_merge_all_integrity_sidecars"))
        self.payload = self._start(mock.patch.object(DC, "_core_build_live_cache_payload",
                                                     return_value={"built": True}))
        self.persist = self._start(mock.patch.object(DC, "persist_dashboard_cache"))
        self.load_instruments = self._start(mock.patch.object(DC, "load_instruments",
                                                              return_value=["BTC", "ETH"]))
        self._start(mock.patch.object(DC, "_global_env_axis", return_value="demo"))

    def _patch_llm(self, runtime=None, exc=None):
        from r20_backend import llm_manager
        if exc is not None:
            return self._start(mock.patch.object(llm_manager, "get_active_llm_runtime",
                                                 side_effect=exc))
        return self._start(mock.patch.object(llm_manager, "get_active_llm_runtime",
                                             return_value=runtime or {}))

    def test_happy_path_builds_and_persists_the_live_payload(self):
        self._patch_llm({"model": "gpt-x", "provider_name": "p", "reasoning_effort": "low",
                         "api_format": "openai_chat"})
        from scripts import calculus_engine
        regime = self._start(mock.patch.object(calculus_engine, "detect_macro_market_regime",
                                               return_value={"regime": "bull"}))
        DC.update_cache_cycle()
        self.assertEqual(DC.CACHE_DATA["built"], True)
        self.assertEqual(DC.CACHE_DATA["llm_runtime"],
                         {"model": "gpt-x", "provider_name": "p",
                          "reasoning_effort": "low", "api_format": "openai_chat"})
        self.assertEqual(DC.CACHE_DATA["market_regime"], {"regime": "bull"})
        regime.assert_called_once_with(["f"])
        self.persist.assert_called_once_with(DC.CACHE_DATA)
        self.assertGreater(DC.LAST_CACHE_TIME, 0)
        self.assertEqual(self.algo.call_args[0][0], [{"instId": "BTC"}])

    def test_llm_runtime_defaults_come_from_the_environment_on_failure(self):
        self._patch_llm(exc=RuntimeError("llm 配置坏了"))
        self._start(mock.patch.object(DC.os, "environ",
                                      {"LLM_MODEL": "env-model",
                                       "LLM_REASONING_EFFORT": "medium"}))
        with mock.patch("scripts.calculus_engine.detect_macro_market_regime",
                        side_effect=RuntimeError("no factors")):
            DC.update_cache_cycle()
        self.assertEqual(DC.CACHE_DATA["llm_runtime"],
                         {"model": "env-model", "provider_name": "默认",
                          "reasoning_effort": "medium", "api_format": "openai_chat"})

    def test_missing_market_regime_module_is_tolerated(self):
        self._patch_llm({})
        from scripts import calculus_engine
        self._start(mock.patch.object(calculus_engine, "detect_macro_market_regime",
                                      side_effect=ImportError("没有这个函数")))
        with mock.patch.dict(sys.modules, {"calculus_engine": None}):
            DC.update_cache_cycle()
        self.assertNotIn("market_regime", DC.CACHE_DATA)

    def test_legacy_single_spelling_import_is_the_fallback(self):
        """双拼写铁律：`scripts.calculus_engine` 拿不到时退到顶层 `calculus_engine`。"""
        self._patch_llm({})
        from scripts import calculus_engine
        self._start(mock.patch.object(calculus_engine, "detect_macro_market_regime",
                                      side_effect=ImportError("scripts. 下拿不到")))
        legacy = types.ModuleType("calculus_engine")
        legacy.detect_macro_market_regime = lambda factors: {"regime": "legacy",
                                                             "factors": factors}
        with mock.patch.dict(sys.modules, {"calculus_engine": legacy}):
            DC.update_cache_cycle()
        self.assertEqual(DC.CACHE_DATA["market_regime"],
                         {"regime": "legacy", "factors": ["f"]})

    def test_bills_failure_is_recorded_and_the_cycle_continues(self):
        self._start(mock.patch.object(DC.okx_rest, "bills",
                                      side_effect=RuntimeError("bills 挂了")))
        self._patch_llm({})
        DC.update_cache_cycle()
        errors = self.payload.call_args[1]["source_errors"]
        self.assertIn("bills: RuntimeError: bills 挂了", errors)

    def test_reset_time_is_forwarded_to_the_ledger_and_stats_layers(self):
        self._start(mock.patch.object(DC, "_core_read_reset_initial_state",
                                      return_value=("2026-05-05", 2500.0)))
        self._patch_llm({})
        DC.update_cache_cycle()
        self.assertEqual(self.ledger.call_args[0][3], "2026-05-05")
        self.assertEqual(self.metrics.call_args[1]["initial_capital_val"], 2500.0)
        self.assertEqual(self.metrics.call_args[1]["reset_time_str"], "2026-05-05")

    def test_ledger_today_stats_override_the_bills_figures(self):
        from r20_backend.execution import circuit_breaker
        self._start(mock.patch.object(DC, "_core_load_ledger_lifecycle_trades",
                                      return_value=([{"trade_id": 1}], [{"row": 1}])))
        stats = self._start(mock.patch.object(
            circuit_breaker, "ledger_today_stats",
            return_value={"realized_gross": 100.0, "fees_paid": 5.0,
                          "net_realized": 95.0, "win_trades": 3, "loss_trades": 1,
                          "win_rate": 75.0}))
        self._patch_llm({})
        DC.update_cache_cycle()
        kwargs = self.payload.call_args[1]
        self.assertEqual(kwargs["_today_stats_source"], "ledger_multi_venue")
        self.assertEqual(kwargs["today_net_realized_pnl"], 95.0)
        self.assertEqual(kwargs["today_fees"], 5.0)
        self.assertEqual(kwargs["today_win_rate"], 75.0)
        self.assertEqual(stats.call_args[0][:2], ([{"trade_id": 1}], "demo"))
        self.assertRegex(stats.call_args[0][2], r"^\d{4}-\d{2}-\d{2}$")

    def test_ledger_today_stats_failure_falls_back_to_bills(self):
        from r20_backend.execution import circuit_breaker
        self._start(mock.patch.object(DC, "_core_load_ledger_lifecycle_trades",
                                      return_value=([{"trade_id": 1}], [])))
        self._start(mock.patch.object(circuit_breaker, "ledger_today_stats",
                                      side_effect=ValueError("台账口径炸了")))
        self._patch_llm({})
        DC.update_cache_cycle()
        self.assertEqual(self.payload.call_args[1]["_today_stats_source"],
                         "okx_bills_degraded")

    def test_env_axis_failure_degrades_to_an_empty_axis(self):
        from r20_backend.execution import circuit_breaker
        self._start(mock.patch.object(DC, "_global_env_axis",
                                      side_effect=RuntimeError("环境轴坏了")))
        self._start(mock.patch.object(DC, "_core_load_ledger_lifecycle_trades",
                                      return_value=([{"trade_id": 1}], [])))
        stats = self._start(mock.patch.object(
            circuit_breaker, "ledger_today_stats",
            return_value={"realized_gross": 0, "fees_paid": 0, "net_realized": 0,
                          "win_trades": 0, "loss_trades": 0, "win_rate": 0}))
        self._patch_llm({})
        DC.update_cache_cycle()
        self.assertEqual(stats.call_args[0][1], "")

    def test_ledger_rows_lookup_is_optional(self):
        from scripts.trader import venue_protection
        reader = self._start(mock.patch.object(venue_protection, "read_ledger_rows",
                                               return_value=[{"r": 1}]))
        self._patch_llm({})
        DC.update_cache_cycle()
        self.assertEqual(self.cross.call_args[1]["ledger_rows"], [{"r": 1}])
        reader.assert_called_once_with(DC.LEDGER_JSON_FILE)

    def test_ledger_rows_lookup_failure_yields_none(self):
        from scripts.trader import venue_protection
        self._start(mock.patch.object(venue_protection, "read_ledger_rows",
                                      side_effect=RuntimeError("读不到台账")))
        self._patch_llm({})
        DC.update_cache_cycle()
        self.assertIsNone(self.cross.call_args[1]["ledger_rows"])

    def test_integrity_sidecars_run_after_the_local_reads(self):
        order = []
        self._start(mock.patch.object(DC, "_core_load_local_reads",
                                      side_effect=lambda *a, **k: (order.append("local"),
                                                                   _local_reads())[1]))
        self._start(mock.patch.object(DC, "_core_merge_all_integrity_sidecars",
                                      side_effect=lambda *a, **k: order.append("sidecar")))
        self._patch_llm({})
        DC.update_cache_cycle()
        self.assertEqual(order, ["local", "sidecar"])


class RefreshCacheTests(_Base):
    def test_fresh_cache_short_circuits_without_the_lock(self):
        self._start(mock.patch.object(DC, "CACHE_DATA", {"cached": True}))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 1000.0))
        self._start(mock.patch.object(DC.time, "time", return_value=1001.0))
        get_lock = self._start(mock.patch.object(DC, "get_cache_lock"))
        out = asyncio.run(DC.refresh_cache_if_needed(3.0))
        self.assertEqual(out, {"cached": True})
        get_lock.assert_not_called()

    def test_empty_cache_forces_a_cycle(self):
        self._start(mock.patch.object(DC, "CACHE_DATA", None))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 0))
        self.cycle = self._start(mock.patch.object(DC, "update_cache_cycle",
                                                   side_effect=lambda: setattr(
                                                       DC, "CACHE_DATA", {"fresh": True})))
        out = asyncio.run(DC.refresh_cache_if_needed(3.0))
        self.assertEqual(out, {"fresh": True})

    def test_stale_cache_goes_through_the_lock_and_the_executor(self):
        self._start(mock.patch.object(DC, "CACHE_DATA", {"old": True}))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 1000.0))
        self._start(mock.patch.object(DC.time, "time", return_value=2000.0))
        self.cycle = self._start(mock.patch.object(DC, "update_cache_cycle",
                                                   side_effect=lambda: setattr(
                                                       DC, "CACHE_DATA", {"new": True})))
        out = asyncio.run(DC.refresh_cache_if_needed(3.0))
        self.assertEqual(out, {"new": True})
        self.cycle.assert_called_once()

    def test_double_check_skips_the_cycle_when_another_coroutine_refreshed(self):
        """锁内再查一次：等锁期间别人已经刷新过，就不要重复跑。"""
        self._start(mock.patch.object(DC, "CACHE_DATA", {"old": True}))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 1000.0))
        clock = iter([2000.0, 1001.0])   # 锁外看是旧的；等锁期间别人已刷新 ⇒ 锁内看是新的
        self._start(mock.patch.object(DC.time, "time", side_effect=lambda: next(clock)))
        self.cycle = self._start(mock.patch.object(DC, "update_cache_cycle"))
        out = asyncio.run(DC.refresh_cache_if_needed(3.0))
        self.assertEqual(out, {"old": True})
        self.cycle.assert_not_called()


class EndpointTests(_Base):
    def test_get_all_data_returns_a_fresh_snapshot_with_no_browser_cache(self):
        self._start(mock.patch.object(DC, "CACHE_DATA", {"snapshot": 1}))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 1000.0))
        self._start(mock.patch.object(DC.time, "time", return_value=1001.0))
        response = asyncio.run(DC.get_all_data())
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.headers["cache-control"],
                         "public, max-age=0, s-maxage=2, stale-while-revalidate=5")
        self.assertIn(b'"slim"', response.body, "默认返回瘦身载荷")

    def test_get_all_data_full_returns_the_raw_payload(self):
        self._start(mock.patch.object(DC, "CACHE_DATA", {"snapshot": 1}))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 1000.0))
        self._start(mock.patch.object(DC.time, "time", return_value=1001.0))
        response = asyncio.run(DC.get_all_data(full=True))
        self.assertEqual(json.loads(response.body), {"snapshot": 1})

    def test_get_all_data_refreshes_when_the_snapshot_is_older_than_five_seconds(self):
        self._start(mock.patch.object(DC, "CACHE_DATA", {"snapshot": 1}))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 1000.0))
        self._start(mock.patch.object(DC.time, "time", return_value=1010.0))
        refresh = self._start(mock.patch.object(DC, "refresh_cache_if_needed",
                                                new=mock.AsyncMock(return_value={"snapshot": 2})))
        response = asyncio.run(DC.get_all_data(full=True))
        refresh.assert_awaited_once_with(1.5)
        self.assertEqual(json.loads(response.body), {"snapshot": 2})

    def test_get_all_data_refreshes_when_there_is_no_snapshot(self):
        self._start(mock.patch.object(DC, "CACHE_DATA", None))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 0))
        self._start(mock.patch.object(DC, "refresh_cache_if_needed",
                                      new=mock.AsyncMock(return_value={"snapshot": 3})))
        response = asyncio.run(DC.get_all_data(full=True))
        self.assertEqual(json.loads(response.body), {"snapshot": 3})

    def test_get_overview_uses_its_own_ttl_and_is_not_slimmed(self):
        self._start(mock.patch.object(DC, "CACHE_DATA", {"snapshot": 1}))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 1000.0))
        self._start(mock.patch.object(DC.time, "time", return_value=1001.0))
        response = asyncio.run(DC.get_overview())
        self.assertEqual(json.loads(response.body), {"snapshot": 1})
        self.assertEqual(response.headers["cache-control"],
                         "public, max-age=1, s-maxage=3, stale-while-revalidate=5")

    def test_get_overview_refreshes_after_twelve_seconds(self):
        self._start(mock.patch.object(DC, "CACHE_DATA", {"snapshot": 1}))
        self._start(mock.patch.object(DC, "LAST_CACHE_TIME", 1000.0))
        self._start(mock.patch.object(DC.time, "time", return_value=1020.0))
        refresh = self._start(mock.patch.object(DC, "refresh_cache_if_needed",
                                                new=mock.AsyncMock(return_value={"snapshot": 4})))
        response = asyncio.run(DC.get_overview())
        refresh.assert_awaited_once_with(2.5)
        self.assertEqual(json.loads(response.body), {"snapshot": 4})


if __name__ == "__main__":
    unittest.main()

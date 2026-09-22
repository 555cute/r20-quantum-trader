"""`dashboard_cache.py` 收尾：门面薄壳、`_fetch_json` 四态、宏观兜底、后台工人、锁内双检（第二百四十六刀）。

先打印 22 行再动笔。

| 组 | 语义 |
|---|---|
| ★ **门面薄壳（9 个）** | 每个薄壳都把**自己的**路径常量/全局喂给对应 `_core_*` —— 这正是「**名字即语义**」的落点：`load_position_trackers()` 必须读 `POSITION_TRACKER_FILE`，不能串到别的文件 |
| ★ `_fetch_json` 四态 | 成功 ⇒ `(True, 值, "")`；**`OKXNotConfigured` ⇒ `(False, None, NOT READY 人话)`**；其它异常 ⇒ `(False, None, "异常类名: 消息")`。**异常绝不冒泡**（并发线程池里）|
| 宏观兜底 | `scripts.calculus_engine` 不可用时退到 `calculus_engine`；两条都不通 ⇒ **载荷里干脆没有 `market_regime` 这个键**（缺席即缺席）|
| 后台工人 | 循环里 `update_cache_cycle()` 抛错 ⇒ **吞掉继续**（`pass`）；`stop_dashboard_background_worker()` 只负责把运行标志置 `False` |
| ★ 锁内双检 | `refresh_cache_if_needed` 取到锁后**再查一次**新鲜度 ⇒ 并发请求不会一起打交易所（防惊群）|
"""

import asyncio
import sys
import types
import time
import unittest
from unittest import mock

from r20_backend import dashboard_cache as DC


class FacadeShellTest(unittest.TestCase):
    """★ 门面薄壳：断言"**它把哪个常量喂给了谁**"。"""

    CASES = [
        ("load_trading_memory_md", (), "_core_load_trading_memory_md", "AI_MEMORY_MD_FILE"),
        ("_memory_freshness_note", (), "_core__memory_freshness_note", "DATA_DIR"),
        ("load_position_trackers", (), "_core_load_position_trackers",
         "POSITION_TRACKER_FILE"),
        ("enrich_position_risk_fields", ([{"i": 1}], {}),
         "_core_enrich_position_risk_fields", "POSITION_TRACKER_FILE"),
        ("_load_local_factor_library", (), "_core__load_local_factor_library",
         "FACTOR_LIBRARY_FILE"),
        ("build_ai_health", ([],), "_core_build_ai_health", "DATA_DIR"),
        ("_load_cross_venue_data", (), "_core__load_cross_venue_data", "DATA_DIR"),
    ]

    def test_each_shell_forwards_its_own_path_constant(self):
        for shell_name, args, core_name, const_name in self.CASES:
            with self.subTest(shell=shell_name):
                core = mock.Mock(return_value={"sentinel": shell_name})
                with mock.patch.object(DC, core_name, core):
                    out = getattr(DC, shell_name)(*args)
                self.assertEqual(out, {"sentinel": shell_name}, "薄壳原样返回核心结果")
                self.assertIs(core.call_args.args[0], getattr(DC, const_name),
                              f"{shell_name} 必须把 {const_name} 喂给它")

    def test_build_factors_from_local_files_forwards_the_factor_library(self):
        core = mock.Mock(return_value=([], {}))
        with mock.patch.object(DC, "_core__build_factors_from_local_files", core):
            DC._build_factors_from_local_files([{"p": 1}], "2026-09-21 10:00:00")
        self.assertIs(core.call_args.args[0], DC.FACTOR_LIBRARY_FILE)

    def test_inject_local_data_into_stale_forwards_callables_not_results(self):
        """★ 传给核心的是**函数**（延迟解析），不是它们的返回值。"""
        core = mock.Mock(return_value=None)
        with mock.patch.object(DC, "_core__inject_local_data_into_stale", core):
            DC._inject_local_data_into_stale({"stale": 1}, [], "2026-09-21 10:00:00")
        first = core.call_args.args[0]
        self.assertTrue(callable(first), "要注入的是可调用对象")
        self.assertIs(first, DC._load_local_factor_library)


class FetchJsonTest(unittest.TestCase):
    def test_success(self):
        self.assertEqual(DC._fetch_json(lambda: {"a": 1}), (True, {"a": 1}, ""))

    def test_not_configured_says_not_ready_not_a_traceback(self):
        """★ 未配置凭证 ⇒ 给「NOT READY」人话，不是 traceback。"""
        # ⚠️ `okx_rest` 是**懒子模块**：`from r20_backend import okx_rest` 会 ImportError
        # （第 229 刀踩过同一个坑）⇒ 从已经导入它的模块里取。
        not_configured = DC.okx_rest.OKXNotConfigured

        def _boom():
            raise not_configured("没配 key")
        ok, data, err = DC._fetch_json(_boom)
        self.assertIs(ok, False)
        self.assertIsNone(data)
        self.assertEqual(err, DC._NOT_READY_TEXT)
        self.assertNotIn("OKXNotConfigured", err)

    def test_other_errors_expose_type_and_message(self):
        def _boom():
            raise ValueError("上游 502")
        ok, data, err = DC._fetch_json(_boom)
        self.assertIs(ok, False)
        self.assertIsNone(data)
        self.assertEqual(err, "ValueError: 上游 502", "类名 + 消息，便于页面呈现")


class MacroRegimeFallbackTest(unittest.TestCase):
    def _cycle(self):
        core = (False, 100.0, True, 100.0, 1, [], [], [{"instId": "BTC"}], True, 0,
                100.0, 0.0, {}, 0.0)
        local = {"adaptive_cfg": {}, "ai_history_list": [], "ai_last_prompt_text": "",
                 "ai_memory_md_content": "", "disk_free_gb": 1.0,
                 "factor_lib_snapshot": {}, "news_data": {}, "review_data": {},
                 "snapshots_list": []}
        stubs = {
            "collect_core_account_state": mock.Mock(return_value=core),
            "_core_collect_algo_protection": mock.Mock(return_value=None),
            "_core_collect_cross_venue_positions": mock.Mock(
                side_effect=lambda p_, pend, l, s_, u, **kw: (l, s_, u)),
            "_core_read_reset_initial_state": mock.Mock(return_value=("", 1000.0)),
            "_fetch_json": mock.Mock(return_value=(True, [], "")),
            "aggregate_bills_and_metrics": mock.Mock(return_value=tuple([0.0] * 22)),
            "read_text_lines": mock.Mock(return_value=[]),
            "_core_build_factors_list": mock.Mock(return_value=(["因子"], {})),
            "_core_load_ledger_lifecycle_trades": mock.Mock(return_value=([], [])),
            "_core_load_local_reads": mock.Mock(return_value=dict(local)),
            "_core_merge_all_integrity_sidecars": mock.Mock(return_value=None),
            "_core_build_live_cache_payload": mock.Mock(return_value={}),
            "persist_dashboard_cache": mock.Mock(return_value=None),
        }
        for name, stub in stubs.items():
            p = mock.patch.object(DC, name, stub)
            p.start()
            self.addCleanup(p.stop)

    def test_falls_back_to_the_second_spelling(self):
        """★ 兜底拼写 `calculus_engine` 在测试环境里**不存在** ⇒ 塞一个合成模块进去。

        （实测：`import calculus_engine` ⇒ ModuleNotFoundError。所以我不能"import 它再 patch"，
        只能在 `sys.modules` 里放一个假模块 —— 这也正好证明兜底走的是**模块导入**这条路径。）
        """
        fake = types.ModuleType("calculus_engine")
        fake.detect_macro_market_regime = lambda factors: {"regime": "牛", "n": len(factors)}
        self._cycle()
        DC.CACHE_DATA = {}
        with mock.patch.dict(sys.modules, {"scripts.calculus_engine": None,
                                           "calculus_engine": fake}):
            DC.update_cache_cycle()
        self.assertEqual(DC.CACHE_DATA["market_regime"], {"regime": "牛", "n": 1})

    def test_both_spellings_missing_means_no_key_at_all(self):
        """★ 两条导入都不通 ⇒ **不塞假值**，载荷里根本没有这个键。"""
        self._cycle()
        DC.CACHE_DATA = {}
        with mock.patch.dict(sys.modules, {"scripts.calculus_engine": None,
                                           "calculus_engine": None}):
            DC.update_cache_cycle()
        self.assertNotIn("market_regime", DC.CACHE_DATA)


class BackgroundWorkerTest(unittest.TestCase):
    def setUp(self):
        self.saved = DC._BG_WORKER_RUNNING
        self.addCleanup(self._restore)

    def _restore(self):
        DC._BG_WORKER_RUNNING = self.saved

    def test_a_failing_cycle_is_swallowed_and_the_loop_stops_on_the_flag(self):
        calls = []

        def _flip():
            calls.append(1)
            DC._BG_WORKER_RUNNING = False   # 让它跑完一轮就退出
            raise RuntimeError("周期炸了")
        DC._BG_WORKER_RUNNING = True
        with mock.patch.object(DC, "update_cache_cycle", side_effect=_flip), \
                mock.patch("r20_backend.dashboard_cache.time.sleep", return_value=None):
            DC._dashboard_background_worker_loop()   # 不许把异常抛出来
        self.assertEqual(len(calls), 1)

    def test_stop_only_flips_the_flag(self):
        DC._BG_WORKER_RUNNING = True
        DC.stop_dashboard_background_worker()
        self.assertIs(DC._BG_WORKER_RUNNING, False)


class RefreshDoubleCheckTest(unittest.TestCase):
    def setUp(self):
        self.saved = (DC.CACHE_DATA, DC.LAST_CACHE_TIME)
        self.addCleanup(self._restore)

    def _restore(self):
        DC.CACHE_DATA, DC.LAST_CACHE_TIME = self.saved

    def test_the_inner_check_prevents_a_stampede(self):
        """★ 取到锁后**再查一次**：若此刻已经新鲜，就直接返回，**不跑周期**。

        ⚠️ 第一版我用 `mock.patch(..., side_effect=[...])` 去喂 `time.time`，
        结果**假锁的 `__aenter__` 也去消费那个序列** ⇒ 第二次检查根本没走到
        （探针仍在 496 报未命中）。**正确做法**：不 patch 时钟，改用**真的时间** ——
        进来时把 `LAST_CACHE_TIME` 设成 0（陈旧），让假锁在 `__aenter__` 里把它刷新成"现在"，
        于是内层检查必然命中。
        """
        ran = []

        class _Lock:
            async def __aenter__(self):
                DC.LAST_CACHE_TIME = time.time()   # 模拟"另一个请求刚把缓存刷新了"
                return self

            async def __aexit__(self, *a):
                return False

        with mock.patch.object(DC, "get_cache_lock", return_value=_Lock()), \
                mock.patch.object(DC, "update_cache_cycle",
                                  side_effect=lambda: ran.append(1)):
            DC.CACHE_DATA = {"cached": True}
            DC.LAST_CACHE_TIME = 0.0          # 外层检查：陈旧 ⇒ 取锁
            out = asyncio.run(DC.refresh_cache_if_needed(ttl_seconds=3.0))
        self.assertEqual(out, {"cached": True}, "内层检查命中 ⇒ 直接返回缓存")
        self.assertEqual(ran, [], "**不**再跑周期（防惊群）")


class OverviewFreshTest(unittest.TestCase):
    def setUp(self):
        self.saved = (DC.CACHE_DATA, DC.LAST_CACHE_TIME)
        self.addCleanup(self._restore)

    def _restore(self):
        DC.CACHE_DATA, DC.LAST_CACHE_TIME = self.saved

    def test_fresh_overview_is_served_from_memory(self):
        refresh = mock.AsyncMock(return_value={"fresh": True})
        with mock.patch.object(DC, "refresh_cache_if_needed", refresh):
            DC.CACHE_DATA = {"只看内存": 1}
            DC.LAST_CACHE_TIME = time.time()
            asyncio.run(DC.get_overview())
        refresh.assert_not_awaited()
        self.assertEqual(DC.CACHE_DATA, {"只看内存": 1})


if __name__ == "__main__":
    unittest.main()

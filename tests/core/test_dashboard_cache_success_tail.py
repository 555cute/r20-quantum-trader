"""`update_cache_cycle` 成功收尾与 `refresh_cache_if_needed`（第二百三十八刀）。

| 语义 | 口径 |
|---|---|
| ★ `llm_runtime` 正常 | 取活跃运行时四字段（`provider_name` 缺省「默认」、`api_format` 缺省 `openai_chat`）|
| ★ `llm_runtime` 降级 | `get_active_llm_runtime()` 抛错 ⇒ 回落到**环境变量**（`LLM_MODEL`/`LLM_REASONING_EFFORT`），不炸整轮 |
| ★ `market_regime` | 双拼写导入（`scripts.calculus_engine` → `calculus_engine`）都失败 ⇒ **载荷里干脆没有这个键**（**缺席即缺席**，不塞假值）|
| ★ 落盘与时钟 | 成功收尾调用 `persist_dashboard_cache(CACHE_DATA)` 并更新 `LAST_CACHE_TIME` |
| ★ TTL 与双检 | `refresh_cache_if_needed`：TTL 内直接返回；过期则**取锁**后在锁内**再查一次**（防惊群），再交给 `SYNC_EXECUTOR` 跑一次同步周期 |
"""

import asyncio
import types
import unittest
from unittest import mock

from r20_backend import dashboard_cache as DC


class SuccessTailTest(unittest.TestCase):
    def setUp(self):
        self.persisted = []
        p = mock.patch.object(DC, "persist_dashboard_cache",
                              side_effect=lambda payload: self.persisted.append(payload))
        p.start()
        self.addCleanup(p.stop)
        # 只关心收尾：把周期前半段整体短路成一个已知载荷
        p2 = mock.patch.object(DC, "collect_core_account_state",
                               side_effect=RuntimeError("前置阶段不参与本用例"))
        p2.start()
        self.addCleanup(p2.stop)

    def _drive_tail(self, runtime_raises=False):
        """把周期前半段全部打桩，只留**收尾**真跑（含 `llm_runtime` 与落盘）。"""
        core = (False, 100.0, True, 100.0, 1, [], [], [{"instId": "BTC"}], True, 0,
                100.0, 0.0, {}, 0.0)
        local = {"adaptive_cfg": {}, "ai_history_list": [], "ai_last_prompt_text": "",
                 "ai_memory_md_content": "", "disk_free_gb": 1.0, "factor_lib_snapshot": {},
                 "news_data": {}, "review_data": {}, "snapshots_list": []}
        bills = tuple([0.0] * 13 + [11.0, 22.0, 9, -33.0, -44.0, 55.0, 7, -66.0, -77.0])
        patches = {
            "collect_core_account_state": mock.Mock(return_value=core),
            "_core_collect_algo_protection": mock.Mock(return_value=None),
            "_core_collect_cross_venue_positions": mock.Mock(
                side_effect=lambda p_, pend, l, s_, u, **kw: (l, s_, u)),
            "_core_read_reset_initial_state": mock.Mock(return_value=("", 1000.0)),
            "_fetch_json": mock.Mock(return_value=(True, ["B"], "")),
            "aggregate_bills_and_metrics": mock.Mock(return_value=bills),
            "read_text_lines": mock.Mock(return_value=[]),
            "_core_build_factors_list": mock.Mock(return_value=([], {})),
            "_core_load_ledger_lifecycle_trades": mock.Mock(return_value=([], [])),
            "_core_load_local_reads": mock.Mock(return_value=dict(local)),
            "_core_merge_all_integrity_sidecars": mock.Mock(return_value=None),
            "_core_build_live_cache_payload": mock.Mock(return_value={}),
            "okx_rest": types.SimpleNamespace(bills="b"),
        }
        for name, stub in patches.items():
            q = mock.patch.object(DC, name, stub)
            q.start()
            self.addCleanup(q.stop)
        extras = [
            mock.patch("r20_backend.llm_manager.get_active_llm_runtime",
                       side_effect=RuntimeError("读不到") if runtime_raises
                       else mock.Mock(return_value={"model": "M", "provider_name": "P",
                                                    "reasoning_effort": "high",
                                                    "api_format": "openai_chat"})),
            mock.patch("scripts.trader.venue_protection.read_ledger_rows", return_value=[]),
            mock.patch("r20_backend.execution.circuit_breaker.ledger_today_stats",
                       return_value={"realized_gross": 0.0, "fees_paid": 0.0,
                                     "net_realized": 0.0, "win_trades": 0, "loss_trades": 0,
                                     "win_rate": 0.0}),
        ]
        for q in extras:
            q.start()
            self.addCleanup(q.stop)
        DC.CACHE_DATA = {}
        try:
            DC.update_cache_cycle()
            return None
        except Exception as exc:
            return exc

    def test_llm_runtime_failure_falls_back_to_environment_not_to_a_crash(self):
        """★ 真跑收尾：活跃运行时读不到 ⇒ 用**环境变量**兜底（不是让整轮失败）。"""
        with mock.patch.dict("os.environ", {"LLM_MODEL": "env-model",
                                            "LLM_REASONING_EFFORT": "medium"}):
            err = self._drive_tail(runtime_raises=True)
        self.assertIsNone(err, "兜底后整轮不应抛错（若抛，说明兜底没生效）")
        info = DC.CACHE_DATA["llm_runtime"]
        self.assertEqual(info["model"], "env-model")
        self.assertEqual(info["reasoning_effort"], "medium")
        self.assertEqual(info["provider_name"], "默认")
        self.assertEqual(info["api_format"], "openai_chat")

    def test_successful_tail_persists_the_payload(self):
        """★ 成功收尾必须**落盘**（否则重启后看板回到空白）。"""
        err = self._drive_tail()
        self.assertIsNone(err)
        self.assertTrue(self.persisted, "persist_dashboard_cache 必须被调用")
        self.assertIs(self.persisted[-1], DC.CACHE_DATA, "落盘的就是刚构建的载荷")


class RefreshIfNeededTest(unittest.TestCase):
    def setUp(self):
        self.calls = 0

        def _cycle():
            self.calls += 1
        p = mock.patch.object(DC, "update_cache_cycle", side_effect=_cycle)
        self.cycle = p.start()
        self.addCleanup(p.stop)
        self.saved = (DC.LAST_CACHE_TIME, DC.CACHE_DATA)
        self.addCleanup(self._restore)

    def _restore(self):
        DC.LAST_CACHE_TIME, DC.CACHE_DATA = self.saved

    def test_fresh_cache_returns_without_running_the_cycle(self):
        import time as _t
        DC.LAST_CACHE_TIME = _t.time()
        DC.CACHE_DATA = {"cached": True}
        out = asyncio.run(DC.refresh_cache_if_needed(ttl_seconds=3.0))
        self.assertEqual(out, {"cached": True})
        self.assertEqual(self.calls, 0, "TTL 内**不得**跑周期")

    def test_empty_cache_forces_a_cycle_even_within_ttl(self):
        import time as _t
        DC.LAST_CACHE_TIME = _t.time()
        DC.CACHE_DATA = {}
        DC.CACHE_DATA = {}
        out = asyncio.run(DC.refresh_cache_if_needed(ttl_seconds=3.0))
        self.assertEqual(self.calls, 1, "载荷为空 ⇒ 即使时间新鲜也要跑（否则前端永远空白）")

    def test_expired_ttl_runs_the_cycle_on_the_sync_executor(self):
        DC.LAST_CACHE_TIME = 0.0
        DC.CACHE_DATA = {"old": 1}
        with mock.patch("asyncio.get_running_loop") as loop:
            loop.return_value.run_in_executor = mock.AsyncMock(return_value=None)
            asyncio.run(DC.refresh_cache_if_needed(ttl_seconds=3.0))
        self.assertEqual(loop.return_value.run_in_executor.call_args.args[0],
                         DC.SYNC_EXECUTOR, "阻塞周期必须丢给同步执行器")


if __name__ == "__main__":
    unittest.main()

"""今日 KPI 的**单一事实源**：台账可用则覆盖 OKX 单所 bills（第二百三十七刀）。

背景（代码注释里的真机实锤）：bills 聚合是 **OKX 单所视野** —— binance/gate 当日平仓
（实锤：**SUI +27.63**）**前台永远看不见**，与三所合并的台账/熔断对不上。
所以：台账可用 ⇒ 以 `ledger_today_stats` 覆盖（与熔断锚点**逐字同式**：
`net=Σ行pnl`，`fees 已含行内`；**funding 单列不混净值**）；bills 口径退化为
**台账缺失/损坏时的单所降级兜底**，并且 —— ★ **降级必须自曝**（`_today_stats_source`）。

| 语义 | 口径 |
|---|---|
| ★ 自曝字段 | 初值 `"okx_bills_degraded"`；台账覆盖成功 ⇒ `"ledger_multi_venue"` |
| ★ 覆盖字段 | `realized_gross` / `fees_paid` / `net_realized` / `win_trades` / `loss_trades` / `win_rate` 六项逐字覆盖 |
| ★ **funding 不混** | `today_funding` **不被台账覆盖**（保持 bills 口径）|
| 环境轴 | `_global_env_axis()` 抛错 ⇒ 用空串（保守纳入），仍走台账覆盖 |
| 台账失败 | ⇒ `print("[KPI] warn …")` 出声 + **保持** `okx_bills_degraded`（不假装已覆盖）|
| bills 失败 | ⇒ `source_errors` 追加 `bills: <原因>` **且** `bills_data=[]` |
| ★ 顺序即语义 | `source_errors` 的条目顺序就是前端展示顺序：**bills 错误先入，旁车合并随后** |
| 台账行读不到 | ⇒ 交给跨所归属层的是 `ledger_rows=None`（**读不到不产生证据**）|
"""

import types
import unittest
from unittest import mock

from r20_backend import dashboard_cache as DC

CORE_TUPLE = (False, 100.0, True, 100.0, 1, [], [], [{"instId": "BTC"}], True, 0,
              100.0, 0.0, {}, 0.0)
LOCAL = {"adaptive_cfg": {}, "ai_history_list": [], "ai_last_prompt_text": "",
         "ai_memory_md_content": "", "disk_free_gb": 1.0, "factor_lib_snapshot": {},
         "news_data": {}, "review_data": {}, "snapshots_list": []}
BILLS = [100.0, 0.0, 0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, [], {}, 0.0,
         11.0, 22.0, 9, -33.0, -44.0, 55.0, 7, -66.0, -77.0]      # 22 项
LEDGER_STATS = {"realized_gross": 111.0, "fees_paid": 22.0, "net_realized": 89.0,
                "win_trades": 3, "loss_trades": 1, "win_rate": 75.0}


class KpiSingleSourceTest(unittest.TestCase):
    def setUp(self):
        self.payload_kwargs = {}
        self.attr_kwargs = {}
        self.sidecar_seen = None
        self.warnings = []

    def _run(self, *, ledger_trades, ledger_rows=None, ledger_rows_raise=False,
             bills_ok=True, ts_raises=False, env_axis_raises=False):
        def _cross(positions, pending, long_count, short_count, total_pos_upl, **kw):
            self.attr_kwargs = kw
            return (long_count, short_count, total_pos_upl)

        def _payload(**kw):
            self.payload_kwargs = kw
            return {}

        def _sidecars(source_errors, data_dir, **kw):
            self.sidecar_seen = list(source_errors)
            source_errors.append("sidecar: ai 连续失败")

        def _bills(*a, **kw):
            return (bills_ok, ["B"], "上游 502")

        def _aggregate(**kw):
            return tuple(BILLS)

        def _ledger_rows(*a, **kw):
            if ledger_rows_raise:
                raise RuntimeError("台账读不到")
            return ledger_rows

        def _env_axis():
            if env_axis_raises:
                raise RuntimeError("环境轴不可用")
            return "live"

        def _ts(rows, env, day):
            if ts_raises:
                raise RuntimeError("口径失败")
            return dict(LEDGER_STATS)

        patches = {
            "collect_core_account_state": mock.Mock(return_value=CORE_TUPLE),
            "_core_collect_cross_venue_positions": mock.Mock(side_effect=_cross),
            "_core_read_reset_initial_state": mock.Mock(return_value=("", 1000.0)),
            "_fetch_json": mock.Mock(side_effect=_bills),
            "aggregate_bills_and_metrics": mock.Mock(side_effect=_aggregate),
            "read_text_lines": mock.Mock(return_value=[]),
            "_core_build_factors_list": mock.Mock(return_value=([], {})),
            "_core_load_ledger_lifecycle_trades": mock.Mock(return_value=(ledger_trades, [])),
            "_core_load_local_reads": mock.Mock(return_value=dict(LOCAL)),
            "_core_merge_all_integrity_sidecars": mock.Mock(side_effect=_sidecars),
            "_core_build_live_cache_payload": mock.Mock(side_effect=_payload),
            # ★ 同一个 `_fetch_json` 桩被多个消费者用：算法保护腿那条路会把
            # 返回元组当可迭代对象逐项 `.get`（实测 AttributeError / `_sl_legs[0]` IndexError）
            # ⇒ 这一支直接打桩，避免用"给 bills 造的形状"去喂另一个消费者。
            "_core_collect_algo_protection": mock.Mock(return_value=None),
            "_global_env_axis": mock.Mock(side_effect=_env_axis),
            "okx_rest": types.SimpleNamespace(bills="bills-fn"),
        }
        for name, stub in patches.items():
            p = mock.patch.object(DC, name, stub)
            p.start()
            self.addCleanup(p.stop)
        extras = [
            mock.patch("scripts.trader.venue_protection.read_ledger_rows",
                       side_effect=_ledger_rows),
            mock.patch("r20_backend.execution.circuit_breaker.ledger_today_stats",
                       side_effect=_ts),
            mock.patch("r20_backend.llm_manager.get_active_llm_runtime",
                       return_value={"model": "M"}),
        ]
        for p in extras:
            p.start()
            self.addCleanup(p.stop)
        try:
            DC.update_cache_cycle()
        except Exception as exc:          # 后半段未读 ⇒ 只关心已捕获的调用参数
            self.tail_error = exc
            if not self.payload_kwargs:   # ★ 载荷都没到 ⇒ **把真实原因打出来**，不要静默
                import traceback
                print("TAIL_TRACE:", traceback.format_exc().splitlines()[-6:])
        return self.payload_kwargs

    def test_ledger_overrides_the_single_venue_view_and_self_declares(self):
        kw = self._run(ledger_trades=[{"row": 1}])
        self.assertEqual(kw["_today_stats_source"], "ledger_multi_venue",
                         "台账可用 ⇒ 自曝来源为**三所台账**")
        self.assertEqual(kw["today_realized_gross"], 111.0)
        self.assertEqual(kw["today_fees"], 22.0)
        self.assertEqual(kw["today_net_realized_pnl"], 89.0)
        self.assertEqual((kw["today_win_trades"], kw["today_loss_trades"]), (3, 1))
        self.assertEqual(kw["today_win_rate"], 75.0)

    def test_funding_is_not_mixed_into_the_ledger_net(self):
        """★ `funding` 单列：台账覆盖**不碰** `today_funding`（保持 bills 的 22.0）。"""
        kw = self._run(ledger_trades=[{"row": 1}])
        self.assertEqual(kw["today_funding"], 22.0, "funding 仍来自 bills 口径")

    def test_without_ledger_trades_the_source_self_declares_degradation(self):
        kw = self._run(ledger_trades=[])
        self.assertEqual(kw["_today_stats_source"], "okx_bills_degraded",
                         "降级**自曝**（前端能看出这是 OKX 单所口径）")
        self.assertEqual(kw["today_net_realized_pnl"], -33.0, "此时用 bills 的值")

    def test_a_failing_ledger_stats_keeps_the_degraded_label_and_warns(self):
        with mock.patch("builtins.print") as fake_print:
            kw = self._run(ledger_trades=[{"row": 1}], ts_raises=True)
        self.assertEqual(kw["_today_stats_source"], "okx_bills_degraded",
                         "口径失败 ⇒ **不假装**已覆盖")
        self.assertTrue(any("[KPI] warn" in str(c) for c in fake_print.call_args_list),
                        "要出声")

    def test_env_axis_failure_falls_back_to_the_empty_axis(self):
        kw = self._run(ledger_trades=[{"row": 1}], env_axis_raises=True)
        self.assertEqual(kw["_today_stats_source"], "ledger_multi_venue",
                         "环境轴不可用也仍走台账（空串 = 保守纳入）")

    def test_bills_failure_is_recorded_and_data_is_emptied(self):
        kw = self._run(ledger_trades=[], bills_ok=False)
        errors = kw["source_errors"]
        self.assertIn("bills: 上游 502", errors)
        self.assertTrue(any("B" not in str(x) for x in errors if "bills" in str(x)))

    def test_source_errors_order_is_bills_first_then_sidecars(self):
        """★ 顺序即语义：前端按 `source_errors` 顺序展示 ⇒ bills 错误必须**在前**。"""
        self._run(ledger_trades=[], bills_ok=False)
        self.assertEqual(self.sidecar_seen, ["bills: 上游 502"],
                         "旁车合并时，bills 错误已在列表里（顺序 = 展示顺序）")

    def test_unreadable_ledger_rows_become_none_not_evidence(self):
        """★ 台账行读不到 ⇒ 交给归属层 `ledger_rows=None`（**不产生证据**，腿留在不可判定）。"""
        self._run(ledger_trades=[], ledger_rows_raise=True)
        self.assertIsNone(self.attr_kwargs.get("ledger_rows"))

    def test_ledger_rows_are_forwarded_as_evidence_when_readable(self):
        self._run(ledger_trades=[], ledger_rows=[{"row": 9}])
        self.assertEqual(self.attr_kwargs.get("ledger_rows"), [{"row": 9}])
        self.assertIn("source_errors", self.attr_kwargs, "错误表按名字透传")


if __name__ == "__main__":
    unittest.main()

"""指标中枢：**取数失败必须可见、不可判定绝不发 0、HELP/TYPE 每族只许一次**（第二百六十四刀，开新面 metrics.py）。

先打印整个文件（552 行）再动笔。本模块是 Prometheus exposition 的唯一出口，
四条设计约束（只读零副作用 / fail-soft 且失败可见 / 绝不泄露内容 / 取数与渲染分离）
决定了下面这些**必须钉死**的口径：

| 语义 | 口径 |
|---|---|
| ★ **失败必须可见** | 任一源读不到 ⇒ `r20_metrics_source_ok{source="…"} 0`（**不**让整页 500，也**不**静默全绿）；`required` 标签区分必需源与可选源 |
| ★ **不可判定 ≠ 0** | 行情流无 tick ⇒ **不发** `tick_age`；watchdog 未开闸 ⇒ **不发** `watchdog_errors`；孤儿腿读不到 ⇒ 只发 `readable 0`、**计数为 None 不发**；风控键取不到 ⇒ **跳过不补 0** |
| ★ **只输出有限数** | `NaN`/`Inf`/非数值一律降级为 `None` ⇒ 整条样本不发（部分抓取器遇 NaN 会丢整次抓取）|
| ★ **每族 HELP/TYPE 只一次** | 同 metric name 出现第二条 `# HELP` 会让 Prometheus **丢弃整次抓取** ⇒ 先按族聚合再逐族输出（多标的/多场所时尤其要验）|
| 结构纪律 | `build_snapshot` 每个源独立 try，失败只影响该源；`render_prometheus` 是纯函数（同一快照同一输出）|
| 转义 | label 值里的 `\\`、`"`、换行必须转义（否则文本格式直接解析失败）|
"""

import json
import math
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from r20_backend import metrics as M


class HelperTests(unittest.TestCase):
    def test_label_values_escape_backslash_quote_and_newline(self):
        self.assertEqual(M._label_value('a\\b"c\nd'), 'a\\\\b\\"c\\nd')
        self.assertEqual(M._label_value(None), "")

    def test_only_finite_numbers_survive(self):
        self.assertEqual(M._number("3.5"), 3.5)
        self.assertEqual(M._number(True), 1.0)
        self.assertIsNone(M._number(float("nan")))
        self.assertIsNone(M._number(float("inf")))
        self.assertIsNone(M._number("abc"))
        self.assertIsNone(M._number(None))

    def test_format_prints_integers_without_a_decimal_point(self):
        self.assertEqual(M._fmt(3.0), "3")
        self.assertEqual(M._fmt(3.25), "3.25")
        self.assertIsNone(M._fmt("nope"))
        self.assertIsNone(M._fmt(float("nan")))

    def test_utc_seconds_parses_only_the_exact_contract(self):
        self.assertIsNone(M._parse_utc_seconds(""))
        self.assertIsNone(M._parse_utc_seconds("2026-09-22"))
        self.assertIsNone(M._parse_utc_seconds("not-a-date"))
        self.assertEqual(M._parse_utc_seconds("1970-01-01 00:00:01"), 1.0)


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-metrics-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write(self, name, text):
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    # ── 场所健康 ─────────────────────────────────────────
    def test_venue_health_missing_corrupt_or_non_dict_is_none(self):
        self.assertIsNone(M.collect_venue_health(self.tmp))
        self._write("venue_health.json", "{not json")
        self.assertIsNone(M.collect_venue_health(self.tmp))
        self._write("venue_health.json", "[1,2]")
        self.assertIsNone(M.collect_venue_health(self.tmp))

    def test_venue_health_normalises_the_venues_field(self):
        self._write("venue_health.json", json.dumps({
            "updated_utc": "2026-09-22 10:00:00", "package_count": 12,
            "venues": {"okx": {"ok": ["BTC"]}}}))
        out = M.collect_venue_health(self.tmp)
        self.assertEqual(out["package_count"], 12)
        self.assertIn("okx", out["venues"])

    def test_venue_health_non_dict_venues_becomes_an_empty_map(self):
        self._write("venue_health.json", json.dumps({"venues": ["bad"]}))
        self.assertEqual(M.collect_venue_health(self.tmp)["venues"], {})

    # ── 模型统计 ─────────────────────────────────────────
    def test_model_stats_fail_soft_and_shape_checked(self):
        store = mock.Mock()
        store.model_stats.side_effect = RuntimeError("db 挂了")
        self.assertIsNone(M.collect_model_stats(store))
        store.model_stats.side_effect = None
        store.model_stats.return_value = [1, 2]
        self.assertIsNone(M.collect_model_stats(store), "非 dict 汇总一律当没有")
        store.model_stats.return_value = {"total_calls": 3}
        self.assertEqual(M.collect_model_stats(store), {"total_calls": 3})

    # ── 风控旋钮 ─────────────────────────────────────────
    def test_risk_limits_missing_keys_are_skipped_not_zeroed(self):
        out = M.collect_risk_limits(types.SimpleNamespace(MAX_LEVERAGE=5))
        self.assertEqual(out, {"max_leverage": 5.0})

    def test_risk_limits_all_missing_returns_none(self):
        self.assertIsNone(M.collect_risk_limits(types.SimpleNamespace()))

    def test_risk_limits_coerce_booleans_and_drop_non_finite(self):
        out = M.collect_risk_limits(types.SimpleNamespace(
            MAX_LEVERAGE=True, MIN_LEVERAGE=False, MAX_MARGIN_EQUITY_RATIO=float("nan")))
        self.assertEqual(out, {"max_leverage": 1.0, "min_leverage": 0.0})

    def test_risk_limits_module_resolution_failure_is_none(self):
        """两条 import 路径都不通 ⇒ None（绝不因为读不到常量就补 0）。"""
        import builtins
        real_import = builtins.__import__

        def _blocked(name, *args, **kwargs):
            if name in ("scripts", "risk_constants", "scripts.risk_constants"):
                raise ImportError(f"blocked: {name}")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=_blocked):
            self.assertIsNone(M.collect_risk_limits())

    def test_risk_limits_default_call_reads_the_real_module(self):
        out = M.collect_risk_limits()
        self.assertIsNotNone(out)
        self.assertIn("max_leverage", out)

    # ── 快照文件类 ───────────────────────────────────────
    def test_cycle_disclosure_tolerates_missing_and_corrupt(self):
        self.assertIsNone(M.collect_cycle_disclosure(self.tmp / "nope.json"))
        self._write("cycle_disclosure.json", "{bad")
        self.assertIsNone(M.collect_cycle_disclosure(self.tmp / "cycle_disclosure.json"))
        self._write("cycle_disclosure.json", "[1]")
        self.assertIsNone(M.collect_cycle_disclosure(self.tmp / "cycle_disclosure.json"))

    def test_cycle_disclosure_returns_a_dict_payload(self):
        self._write("cycle_disclosure.json", json.dumps({"broken_venue_count": 2}))
        self.assertEqual(M.collect_cycle_disclosure(self.tmp / "cycle_disclosure.json"),
                         {"broken_venue_count": 2})

    def test_market_data_health_loader_failure_is_none(self):
        import scripts.market_data_health as MDH
        with mock.patch.object(MDH, "load_snapshot", side_effect=RuntimeError("坏了")):
            self.assertIsNone(M.collect_market_data_health(self.tmp / "x.json"))

    def test_market_data_health_falsy_payload_is_none(self):
        import scripts.market_data_health as MDH
        with mock.patch.object(MDH, "load_snapshot", return_value={}):
            self.assertIsNone(M.collect_market_data_health(self.tmp / "x.json"))
        with mock.patch.object(MDH, "load_snapshot",
                               return_value={"calls": {"k": 1}}):
            self.assertEqual(M.collect_market_data_health(self.tmp / "x.json"),
                             {"calls": {"k": 1}})

    def test_market_stream_health_loader_failure_is_none(self):
        import scripts.market_stream as MS
        with mock.patch.object(MS, "load_snapshot", side_effect=RuntimeError("坏了")):
            self.assertIsNone(M.collect_market_stream_health(self.tmp / "x.json"))

    # ── 孤儿保护腿 ───────────────────────────────────────
    def test_orphans_require_a_dict_cache_with_a_position_list(self):
        self.assertIsNone(M.collect_protection_orphans(None))
        self.assertIsNone(M.collect_protection_orphans([]))
        self.assertIsNone(M.collect_protection_orphans({"positions": "nope"}))
        self.assertIsNone(M.collect_protection_orphans({"positions": []}))

    def test_orphans_unreadable_venue_publishes_none_counts(self):
        out = M.collect_protection_orphans({"positions": [
            {"venue": "okx", "protectionOrphans": {"readable": False}}]})
        self.assertEqual(out["okx"]["readable"], False)
        self.assertIsNone(out["okx"]["candidates"])
        self.assertIsNone(out["okx"]["ledger_evidence"])

    def test_orphans_readable_venue_counts_lists_and_flags_evidence(self):
        out = M.collect_protection_orphans({"positions": [
            {"venue": "Binance", "protectionOrphans": {
                "readable": True, "attributed": [1, 2], "unattributed": [3],
                "sideMismatch": [4], "sizeMismatch": [5, 6],
                "foreignCount": 7, "unparsedCount": "x", "ledgerRows": "ok"}}]})
        info = out["binance"]
        self.assertEqual(info["candidates"], 2)
        self.assertEqual(info["unattributed"], 1)
        self.assertEqual(info["side_mismatch"], 1)
        self.assertEqual(info["size_mismatch"], 2)
        self.assertEqual(info["foreign"], 7)
        self.assertIsNone(info["unparsed"], "非 int 的计数一律 None，不硬转")
        self.assertTrue(info["ledger_evidence"])

    def test_orphans_rows_without_venue_or_readable_flag_are_skipped(self):
        out = M.collect_protection_orphans({"positions": [
            "not-a-row",
            {"protectionOrphans": {"readable": True, "attributed": []}},
            {"venue": "okx", "protectionOrphans": {"readable": "yes"}},
            {"venue": "gate"},   # 没有 protectionOrphans
        ]})
        self.assertIsNone(out)


class BuildSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-metrics-build-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_injected_sources_are_marked_ok_and_time_is_injectable(self):
        snap = M.build_snapshot(data_dir=self.tmp, venue_health={"a": 1},
                                model_stats={"b": 2}, risk_limits={"c": 3.0},
                                market_data_health={"d": 4},
                                market_stream_health={"e": 5},
                                cycle_disclosure={"f": 6},
                                protection_orphans={"okx": {"readable": True}},
                                now=1234.0)
        self.assertEqual(snap["generated_at"], 1234.0)
        self.assertEqual(snap["sources"], {"venue_health": True, "model_calls": True,
                                           "risk_limits": True, "market_data": True,
                                           "market_stream": True, "cycle_disclosure": True,
                                           "protection_orphans": True})

    def test_missing_sources_are_marked_not_ok_instead_of_crashing(self):
        with mock.patch.dict(sys.modules, {
                "r20_backend.dependencies": None,
                "r20_gateway.publisher": None,
                "r20_gateway.store": None,
                "r20_backend.dashboard_cache": None}):
            snap = M.build_snapshot(data_dir=self.tmp, risk_limits=None)
        self.assertFalse(snap["sources"]["venue_health"])
        self.assertFalse(snap["sources"]["model_calls"])
        self.assertFalse(snap["sources"]["market_data"])
        self.assertFalse(snap["sources"]["market_stream"])
        self.assertFalse(snap["sources"]["cycle_disclosure"])
        self.assertFalse(snap["sources"]["protection_orphans"])
        self.assertTrue(snap["sources"]["risk_limits"], "本机风控常量可读 ⇒ 该源应为 ok")
        self.assertEqual(snap["venue_health"], {}, "缺失源以空字典占位，渲染侧自然不发")

    def test_empty_venue_health_file_is_not_ok(self):
        snap = M.build_snapshot(data_dir=self.tmp, venue_health=None,
                                model_stats={}, risk_limits={}, market_data_health={},
                                market_stream_health={}, cycle_disclosure={},
                                protection_orphans={})
        self.assertFalse(snap["sources"]["venue_health"])

    def test_file_sources_default_to_the_injected_data_dir_when_missing(self):
        snap = M.build_snapshot(data_dir=self.tmp, venue_health={}, model_stats={},
                                risk_limits={}, market_data_health=None,
                                market_stream_health=None, cycle_disclosure=None,
                                protection_orphans={}, now=5.0)
        self.assertFalse(snap["sources"]["market_data"])
        self.assertFalse(snap["sources"]["market_stream"])
        self.assertFalse(snap["sources"]["cycle_disclosure"])

    def test_model_calls_source_reads_the_gateway_store(self):
        import r20_gateway.publisher as PUB
        import r20_gateway.store as STORE
        gateway_store = mock.Mock(return_value="STORE")
        with mock.patch.object(PUB, "DB_PATH", "dbpath"), \
                mock.patch.object(STORE, "GatewayStore", gateway_store), \
                mock.patch.object(M, "collect_model_stats",
                                  return_value={"total_calls": 1}) as collector:
            snap = M.build_snapshot(data_dir=self.tmp, venue_health={},
                                    model_stats=None, risk_limits={},
                                    market_data_health={}, market_stream_health={},
                                    cycle_disclosure={}, protection_orphans={})
        gateway_store.assert_called_once_with("dbpath")
        collector.assert_called_once_with("STORE")
        self.assertTrue(snap["sources"]["model_calls"])
        self.assertEqual(snap["model_stats"], {"total_calls": 1})

    def test_protection_orphans_read_from_the_dashboard_cache_in_memory(self):
        import r20_backend.dashboard_cache as dash
        cache = {"positions": [
            {"venue": "okx", "protectionOrphans": {
                "readable": True, "attributed": [1], "unattributed": [],
                "ledgerRows": "ok"}}]}
        with mock.patch.object(dash, "CACHE_DATA", cache, create=True):
            snap = M.build_snapshot(data_dir=self.tmp, venue_health={}, model_stats={},
                                    risk_limits={}, market_data_health={},
                                    market_stream_health={}, cycle_disclosure={},
                                    protection_orphans=None, now=10.0)
        self.assertTrue(snap["sources"]["protection_orphans"])
        self.assertEqual(snap["protection_orphans"]["okx"]["candidates"], 1)


class RenderBasicsTests(unittest.TestCase):
    def _snap(self, **over):
        snap = {"generated_at": 1000.0, "sources": {"venue_health": True},
                "venue_health": {}, "model_stats": {}, "risk_limits": {},
                "market_data_health": {}, "market_stream_health": {},
                "cycle_disclosure": {}, "protection_orphans": {}}
        snap.update(over)
        return snap

    def test_up_is_always_present_and_output_ends_with_one_newline(self):
        text = M.render_prometheus(self._snap())
        self.assertIn("r20_up 1", text)
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_source_ok_carries_the_required_label(self):
        text = M.render_prometheus(self._snap(sources={"venue_health": True,
                                                       "market_stream": False}))
        self.assertIn('r20_metrics_source_ok{source="venue_health",required="1"} 1', text)
        self.assertIn('r20_metrics_source_ok{source="market_stream",required="0"} 0', text)

    def test_help_and_type_are_emitted_once_per_family(self):
        text = M.render_prometheus(self._snap(risk_limits={"max_leverage": 5.0,
                                                           "min_leverage": 1.0,
                                                           "time_stop_hours": 12.0}))
        self.assertEqual(text.count("# HELP r20_risk_limit "), 1,
                         "同名第二条 HELP 会让 Prometheus 丢弃整次抓取")
        self.assertEqual(text.count("# TYPE r20_risk_limit "), 1)
        self.assertIn('r20_risk_limit{name="max_leverage"} 5', text)
        self.assertIn('r20_risk_limit{name="min_leverage"} 1', text)

    def test_non_finite_samples_are_dropped_entirely(self):
        text = M.render_prometheus(self._snap(model_stats={
            "total_calls": 5, "avg_duration_ms": float("inf")}))
        self.assertIn("r20_model_calls_total 5", text)
        self.assertNotIn("duration", text)
        self.assertNotIn("inf", text)

    def test_labels_are_escaped(self):
        text = M.render_prometheus(self._snap(risk_limits={'we"ird\\key': 1.0}))
        self.assertIn(r'{name="we\"ird\\key"}', text)

    def test_render_is_pure(self):
        snap = self._snap(model_stats={"total_calls": 1})
        self.assertEqual(M.render_prometheus(snap), M.render_prometheus(snap))


class RenderVenueAndModelTests(RenderBasicsTests):
    def test_venue_families_cover_counts_latency_and_testnet(self):
        text = M.render_prometheus(self._snap(venue_health={
            "updated_utc": "1970-01-01 00:00:01",
            "venues": {"okx": {"ok": ["BTC", "ETH"], "failed": {"X": 1},
                               "avg_ms": 12.5, "testnet": True},
                       "bad": "not-a-dict"}}))
        self.assertIn("r20_venue_health_updated_timestamp_seconds 1", text)
        self.assertIn('r20_venue_instruments_ok{venue="okx"} 2', text)
        self.assertIn('r20_venue_instruments_failed{venue="okx"} 1', text)
        self.assertIn('r20_venue_latency_avg_ms{venue="okx"} 12.5', text)
        self.assertIn('r20_venue_testnet{venue="okx"} 1', text)
        self.assertNotIn('venue="bad"', text, "非 dict 的场所行必须跳过")

    def test_unparsable_updated_utc_omits_the_timestamp_family(self):
        text = M.render_prometheus(self._snap(venue_health={"updated_utc": "??"}))
        self.assertNotIn("r20_venue_health_updated_timestamp_seconds", text)

    def test_model_counters_are_typed_as_counters(self):
        text = M.render_prometheus(self._snap(model_stats={
            "total_calls": 10, "successful_calls": 9, "avg_duration_ms": 83.5,
            "total_tokens": 1234}))
        self.assertIn("# TYPE r20_model_calls_total counter", text)
        self.assertIn("r20_model_calls_successful_total 9", text)
        self.assertIn("r20_model_call_duration_ms_avg 83.5", text)
        self.assertIn("r20_model_tokens_total 1234", text)


class RenderMarketDataTests(RenderBasicsTests):
    def test_market_data_metrics_convert_ms_and_compute_ages(self):
        text = M.render_prometheus(self._snap(
            generated_at=1000.0,
            market_data_health={
                "written_at_ms": 990_000.0,
                "calls": {"ws": 4}, "failed_calls": {"ws": 1},
                "latency": {"ws": {"p50_ms": 83.347, "p95_ms": 200.0}},
                "last_success_ms": {"ws": 900_000.0},
                "failures": {"by_kind": {"ws": 2}},
            }))
        self.assertIn("r20_market_data_snapshot_age_seconds 10", text)
        self.assertIn('r20_market_data_calls_total{kind="ws"} 4', text)
        self.assertIn('r20_market_data_call_failures_total{kind="ws"} 1', text)
        self.assertIn('r20_market_data_latency_p50_seconds{kind="ws"} 0.083347', text)
        self.assertIn('r20_market_data_latency_p95_seconds{kind="ws"} 0.2', text)
        self.assertIn('r20_market_data_last_success_age_seconds{kind="ws"} 100', text)
        self.assertIn('r20_market_data_failures_reported_total{kind="ws"} 2', text)

    def test_negative_age_is_clamped_to_zero(self):
        text = M.render_prometheus(self._snap(generated_at=1000.0,
                                              market_data_health={"written_at_ms": 2_000_000.0}))
        self.assertIn("r20_market_data_snapshot_age_seconds 0", text,
                      "时钟回拨造成的负年龄不得外泄")


class RenderStreamAndDisclosureTests(RenderBasicsTests):
    def test_market_stream_metrics_and_tick_age_omitted_when_unknown(self):
        text = M.render_prometheus(self._snap(generated_at=1000.0,
            market_stream_health={"written_at_ms": 995_000.0, "venues": {
                "okx": {"frames": 10, "ticks": 8, "parse_errors": 1, "errors": 2,
                        "tick_age_s": 3.5},
                "gate": {"frames": 1, "ticks": 0, "parse_errors": 0, "errors": 0}}}))
        self.assertIn("r20_market_stream_snapshot_age_seconds 5", text)
        self.assertIn('r20_market_stream_ticks_total{venue="okx"} 8', text)
        self.assertIn('r20_market_stream_parse_errors_total{venue="okx"} 1', text)
        self.assertIn('r20_market_stream_tick_age_seconds{venue="okx"} 3.5', text)
        self.assertNotIn('r20_market_stream_tick_age_seconds{venue="gate"}', text,
                         "从未收到 tick ⇒ 不发该序列（0 会被读成刚刚有数据）")

    def test_cycle_disclosure_gates_watchdog_error_counters(self):
        text = M.render_prometheus(self._snap(generated_at=1000.0, cycle_disclosure={
            "written_at_ms": 999_000.0, "broken_venue_count": 2,
            "shape_violation_count": 1, "entries_blocked": True,
            "watchdog_enabled": False, "watchdog_errors": 5, "watchdog_critical": 3}))
        self.assertIn("r20_cycle_disclosure_snapshot_age_seconds 1", text)
        self.assertIn("r20_cycle_disclosure_broken_venues 2", text)
        self.assertIn("r20_cycle_disclosure_shape_violations 1", text)
        self.assertIn("r20_cycle_disclosure_entries_blocked 1", text)
        self.assertIn("r20_cycle_disclosure_watchdog_enabled 0", text)
        self.assertNotIn("watchdog_errors", text,
                         "巡检没开闸 ⇒ 不发错误计数（没跑 ≠ 跑了没问题）")

    def test_cycle_disclosure_emits_watchdog_errors_when_enabled(self):
        text = M.render_prometheus(self._snap(cycle_disclosure={
            "watchdog_enabled": True, "watchdog_errors": 0, "watchdog_critical": 0}))
        self.assertIn("r20_cycle_disclosure_watchdog_enabled 1", text)
        self.assertIn("r20_cycle_disclosure_watchdog_errors 0", text)
        self.assertIn("r20_cycle_disclosure_watchdog_critical 0", text)

    def test_empty_cycle_disclosure_emits_nothing(self):
        text = M.render_prometheus(self._snap(cycle_disclosure={}))
        self.assertNotIn("r20_cycle_disclosure", text)


class RenderOrphanTests(RenderBasicsTests):
    def test_unreadable_venue_publishes_readable_zero_and_no_counts(self):
        text = M.render_prometheus(self._snap(protection_orphans={
            "okx": {"readable": False, "candidates": None,
                    "side_mismatch": None, "foreign": None}}))
        self.assertIn('r20_protection_orphans_readable{venue="okx"} 0', text)
        self.assertNotIn("r20_protection_orphan_candidates", text)
        self.assertNotIn("r20_protection_side_mismatch_legs", text)

    def test_readable_venue_publishes_all_distinct_families(self):
        text = M.render_prometheus(self._snap(protection_orphans={"okx": {
            "readable": True, "candidates": 2, "unattributed": 1,
            "side_mismatch": 3, "size_mismatch": 4, "foreign": 5, "unparsed": 6,
            "ledger_evidence": True}}))
        for line in ('r20_protection_orphans_readable{venue="okx"} 1',
                     'r20_protection_orphan_candidates{venue="okx"} 2',
                     'r20_protection_orphan_unattributed{venue="okx"} 1',
                     'r20_protection_side_mismatch_legs{venue="okx"} 3',
                     'r20_protection_size_mismatch_legs{venue="okx"} 4',
                     'r20_protection_foreign_legs{venue="okx"} 5',
                     'r20_protection_unparsed_legs{venue="okx"} 6',
                     'r20_protection_orphans_ledger_evidence{venue="okx"} 1'):
            self.assertIn(line, text)
        # 语义不同必须是两个指标名 ⇒ 每族仍只有一条 HELP
        self.assertEqual(text.count("# HELP r20_protection_side_mismatch_legs "), 1)
        self.assertEqual(text.count("# HELP r20_protection_size_mismatch_legs "), 1)

    def test_non_dict_orphan_rows_are_skipped(self):
        text = M.render_prometheus(self._snap(protection_orphans={"okx": "bad"}))
        self.assertNotIn("r20_protection_orphans_readable", text)


class EndToEndShapeTests(RenderBasicsTests):
    def test_rendered_document_is_parseable_shape(self):
        snap = M.build_snapshot(venue_health={"venues": {"okx": {"ok": ["BTC"]}}},
                                model_stats={"total_calls": 1},
                                risk_limits={"max_leverage": 5.0},
                                market_data_health={}, market_stream_health={},
                                cycle_disclosure={}, protection_orphans={},
                                now=1.0)
        lines = M.render_prometheus(snap).splitlines()
        self.assertTrue(any(line.startswith("# HELP ") for line in lines))
        self.assertTrue(any(line.startswith("r20_up ") for line in lines))
        self.assertTrue(all(line.startswith("#") or line.strip() for line in lines))
        # 每个非注释行都必须是 `name value` 或 `name{labels} value`
        for line in lines:
            if line.startswith("#"):
                continue
            self.assertRegex(line, r"^r20_[a-z0-9_]+\{?.*\}? -?[0-9.eE+]+$")


if __name__ == "__main__":
    unittest.main()

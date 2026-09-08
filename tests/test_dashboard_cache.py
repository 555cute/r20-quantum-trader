from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


import dashboard.app as dashboard
from scripts import evolution_shield as shield

class DashboardPersistentCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp.name) / "dashboard_last_good.json"
        self.patch = patch.object(dashboard, "DASHBOARD_CACHE_FILE", str(self.cache))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_persist_and_reload_meaningful_snapshot(self):
        payload = {"timestamp": "2026-09-02 20:00:00", "account": {"total_eq": 4100.0}, "factors": [{"name": "BTC"}]}
        dashboard.persist_dashboard_cache(payload)
        self.assertEqual(dashboard.load_persisted_dashboard_cache(), payload)
        if os.name == "posix":
            self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)

    def test_empty_account_is_never_saved_or_used_as_last_good(self):
        dashboard.persist_dashboard_cache({"account": {}})
        self.assertFalse(self.cache.exists())
        self.cache.write_text(json.dumps({"account": {}}), encoding="utf-8")
        self.assertEqual(dashboard.load_persisted_dashboard_cache(), {})

    def test_enriches_stale_positions_with_margin_and_tracker_stop(self):
        positions=[{"instId":"ETH-USDT-SWAP","side":"short","pos":0.3,"avgPx":2370,"markPx":2372,"lever":"5","protectionStatus":"unknown_stale"}]
        trackers={"ETH-USDT-SWAP_short":{"trailingStopPx":2410.31,"takeProfitPx":2290.59,"stage_desc":"持有监控中","strategy_tag":"阻力抛压","cloudProtection":{"verifiedAt":"2026-09-02 18:15:00","detail":"cloud OCO coverage verified (3/3)"}}}
        enriched=dashboard.enrich_position_risk_fields(positions,trackers)[0]
        self.assertEqual(enriched["notional_usdt"],711.6)
        self.assertEqual(enriched["margin_usdt"],142.32)
        self.assertEqual(enriched["marginSource"],"notional_div_leverage")
        self.assertEqual(enriched["displayStop"],2410.31)
        self.assertEqual(enriched["stopSource"],"local_tracker")
        self.assertEqual(enriched["protectionStatus"],"verification_stale")
        self.assertEqual(enriched["quantity_unit"],"base")
        self.assertEqual(enriched["ctVal"],"1")

    def test_exchange_margin_and_cloud_stop_take_priority(self):
        positions=[{"instId":"SOL-USDT-SWAP","posSide":"short","pos":6,"avgPx":98.4,"markPx":98.5,"lever":"3","imr":"201.25","exchangeSl":"100.06","protectionStatus":"fully_protected"}]
        trackers={"SOL-USDT-SWAP_short":{"trailingStopPx":101.0}}
        enriched=dashboard.enrich_position_risk_fields(positions,trackers)[0]
        self.assertEqual(enriched["margin_usdt"],201.25)
        self.assertEqual(enriched["marginSource"],"exchange_imr")
        self.assertEqual(enriched["displayStop"],100.06)
        self.assertEqual(enriched["stopSource"],"exchange_cloud")

    def test_meaningful_snapshot_predicate(self):
        self.assertFalse(dashboard._is_meaningful_dashboard_snapshot({}))
        self.assertFalse(dashboard._is_meaningful_dashboard_snapshot({"account": {}}))
        self.assertTrue(dashboard._is_meaningful_dashboard_snapshot({"account": {"total_eq": 0.0}}))

    def test_inject_local_data_into_stale_preserves_factor_library_and_factors(self):
        """When OKX is down, stale cache must still get fresh local factor_library + factors."""
        temp_dir = tempfile.mkdtemp()
        import shutil
        factor_lib = {"timestamp": 123, "instruments": [{"name": "BTC", "instId": "BTC-USDT-SWAP", "calculus_dynamics": {"velocity": -0.78}}]}
        trading_state = {"timestamp": 123, "instruments": [{"name": "BTC", "instId": "BTC-USDT-SWAP", "price": 77000, "rsi": 58.2}]}
        ai_decisions = {"BTC-USDT-SWAP": {"decision": {"action": "WAIT"}, "thought_process": {}, "smart_money": {"weighted_long_pct": 65.4}, "adx_1h": 31.7}}
        Path(temp_dir, "factor_library_snapshot.json").write_text(json.dumps(factor_lib), encoding="utf-8")
        Path(temp_dir, "trading_state.json").write_text(json.dumps(trading_state), encoding="utf-8")
        Path(temp_dir, "ai_brain_decisions.json").write_text(json.dumps(ai_decisions), encoding="utf-8")
        with patch.object(dashboard, "FACTOR_LIBRARY_FILE", str(Path(temp_dir, "factor_library_snapshot.json"))), \
             patch.object(dashboard, "STATE_JSON_FILE", str(Path(temp_dir, "trading_state.json"))), \
             patch.object(dashboard, "AI_DECISIONS_FILE", str(Path(temp_dir, "ai_brain_decisions.json"))):
            stale = {"account": {"total_eq": 4100}, "positions_summary": {"items": []}, "factors": []}
            dashboard._inject_local_data_into_stale(stale, [], "2026-09-02 22:00:00 (北京时间)")
        # factor_library should be injected from local file
        self.assertEqual(stale["factor_library"]["instruments"][0]["calculus_dynamics"]["velocity"], -0.78)
        # factors should be rebuilt from local trading_state + ai_brain_decisions
        self.assertTrue(len(stale["factors"]) > 0)
        f0 = stale["factors"][0]
        self.assertEqual(f0["name"], "BTC")
        self.assertEqual(f0["rsi"], 58.2)
        self.assertEqual(f0["smart_money"]["weighted_long_pct"], 65.4)
        self.assertEqual(f0["adx_1h"], 31.7)
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_identity_switch_discards_in_memory_account(self):
        dashboard.CACHE_DATA = {"account": {"total_eq": 4100.0}, "exchange": "okx", "runtime": {"identity": "okx:demo:old"}}
        dashboard._BOUND_IDENTITY = "okx:demo:old"
        class Env:
            exchange = "binance"
            mode = "demo"
            identity = "binance:demo:new"
            configured = False
            fingerprint = "new"
        try:
            with patch.object(dashboard, "selected_environment", return_value=Env), \
                 patch.object(dashboard, "load_persisted_dashboard_cache", return_value={}):
                dashboard.bind_account_scope()
            self.assertEqual(dashboard.CACHE_DATA, {})
            self.assertEqual(dashboard._BOUND_IDENTITY, "binance:demo:new")
        finally:
            dashboard.CACHE_DATA = {}
            dashboard._BOUND_IDENTITY = None


class DashboardPublicMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        data = Path(self.temp.name) / "data"
        data.mkdir()
        self.data = data
        self.structured = data / "structured_trading_memory.json"
        self.md = data / "AI_TRADING_MEMORY.md"
        self.legacy_json = data / "ai_trading_memory.json"
        self.patches = [
            patch.object(dashboard, "DATA_DIR", str(data)),
            patch.object(dashboard, "AI_MEMORY_MD_FILE", str(self.md)),
            patch.object(dashboard, "NEWS_SENTIMENT_FILE", str(data / "news_sentiment.json")),
            patch.object(shield, "DATA_DIR", data),
            patch.object(shield, "STRUCTURED_MEMORY_FILE", self.structured),
            patch.object(shield, "AI_MEMORY_MD_FILE", self.md),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.temp.cleanup)

    def _serve(self, cache=None):
        env = SimpleNamespace(exchange="okx", mode="demo", identity="okx:demo:mem")
        with patch.object(dashboard, "bind_account_scope", return_value=env), \
             patch.object(dashboard, "load_news_snapshot", return_value={"latest_news": []}):
            previous = dashboard.CACHE_DATA
            dashboard.CACHE_DATA = {} if cache is None else cache
            try:
                return dashboard.serve_cached_dashboard()
            finally:
                dashboard.CACHE_DATA = previous

    def _write_structured(self, lessons):
        self.structured.write_text(json.dumps({
            "schema_version": 1,
            "revision": "dashhash",
            "lessons": lessons,
        }), encoding="utf-8")

    def test_idle_serve_exposes_uninitialized_without_loading_markdown(self):
        payload = self._serve()
        self.assertEqual(payload["ai_trading_memory_status"], "uninitialized")
        self.assertEqual(payload["ai_trading_memory_md"], "")
        dumped = json.dumps(payload)
        self.assertNotIn("Traceback", dumped)

    def test_empty_authority_does_not_revive_stale_markdown_on_stale_inject(self):
        self._write_structured([])
        self.md.write_text("- OLD LEGACY MIRROR\n", encoding="utf-8")
        stale = {"account": {"total_eq": 4100}, "positions_summary": {"items": []}, "factors": []}
        dashboard._inject_local_data_into_stale(stale, [], "2026-09-08 12:00:00 (北京时间)")
        self.assertEqual(stale["ai_trading_memory_status"], "empty")
        self.assertEqual(stale["ai_trading_memory_md"], "")
        self.assertNotIn("OLD LEGACY", stale["ai_trading_memory_md"])

    def test_corrupt_authority_is_unavailable_without_exception_text(self):
        self.structured.write_text("{", encoding="utf-8")
        self.md.write_text("- OLD LEGACY MIRROR\n", encoding="utf-8")
        before = self.structured.read_bytes()
        payload = self._serve({"account": {"total_eq": 1.0}})
        self.assertEqual(payload["ai_trading_memory_status"], "unavailable")
        self.assertEqual(payload["ai_trading_memory_md"], "")
        dumped = json.dumps(payload)
        self.assertNotIn("damaged", dumped)
        self.assertNotIn("MemoryCorrupt", dumped)
        self.assertNotIn("Traceback", dumped)
        self.assertEqual(self.structured.read_bytes(), before)

    def test_missing_authority_legacy_markdown_ready_on_offline_payload(self):
        self.md.write_text("- usable legacy heuristic\n", encoding="utf-8")
        offline = {"account": {}, "data_health": {"status": "OFFLINE"}}
        dashboard._apply_trading_memory(offline)
        self.assertEqual(offline["ai_trading_memory_status"], "ready")
        self.assertIn("usable legacy heuristic", offline["ai_trading_memory_md"])

    def test_projector_exception_becomes_unavailable_without_leak(self):
        with patch.object(shield, "project_public_trading_memory", side_effect=RuntimeError("secret boom")):
            markdown, status = dashboard.load_trading_memory_payload()
        self.assertEqual(status, "unavailable")
        self.assertEqual(markdown, "")
        self.assertNotIn("secret boom", markdown)

if __name__ == "__main__":
    unittest.main()

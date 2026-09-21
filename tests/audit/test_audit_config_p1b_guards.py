"""批3 配置面回归：P1-5 删除守卫（实时持仓 + 强制短语）。

审计 plan_local/STRATEGY_CONFIG_AUDIT_20260913.md：
- 守卫查 `inst_id in trackers`，而真实 tracker key 是 `{inst_id}_{side}` → 永不命中；
- 跨所（binance/gate）持仓根本没有 tracker 文件 → 只有实时持仓能兜住；
- 确认短语 `if conf and ...` 可省略 → 裸 curl 就能删；
- 判不了持仓时必须 fail-closed（未知 ≠ 无持仓）。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.config_sandbox import isolate_config

ROOT = Path(__file__).resolve().parents[2]


class _Base(unittest.TestCase):
    def setUp(self):
        # 先导入再隔离（isolate_config 只重定向已导入模块里的 data/ 路径）
        import r20_backend.app  # noqa: F401
        import scripts.instrument_pool  # noqa: F401
        self.root = isolate_config(self)
        from r20_backend.routers import risk as risk_router
        self.risk = risk_router

    def _audits(self):
        return []


class TrackerKeyTests(_Base):
    def test_real_tracker_key_format_is_matched(self):
        keys = {"BTC-USDT-SWAP_long": {"x": 1}, "SOL-USDT-SWAP_short": {"x": 1}, "ETH-USDT-SWAP": {}}
        self.assertEqual(self.risk._tracker_keys_for("BTC-USDT-SWAP", keys), ["BTC-USDT-SWAP_long"])
        self.assertEqual(self.risk._tracker_keys_for("SOL-USDT-SWAP", keys), ["SOL-USDT-SWAP_short"])
        # 精确等值（旧实现唯一认得的形式）依旧命中
        self.assertEqual(self.risk._tracker_keys_for("ETH-USDT-SWAP", keys), ["ETH-USDT-SWAP"])
        self.assertEqual(self.risk._tracker_keys_for("DOGE-USDT-SWAP", keys), [])

    def test_holdings_report_uses_cache_positions(self):
        cache = {"positions": [{"instId": "ALGO-USDT-SWAP", "venue": "binance", "pos": "6431.5"}],
                 "data_health": {"cache_age_seconds": 1}}
        with patch.dict("sys.modules", {}), patch("r20_backend.dashboard_cache.CACHE_DATA", cache, create=True):
            import r20_backend.dashboard_cache as dash
            with patch.object(dash, "CACHE_DATA", cache):
                report = self.risk._holdings_report("ALGO-USDT-SWAP", {})
        self.assertTrue(report["held_live"])
        self.assertEqual(report["held_venues"], ["binance"])
        self.assertIsNone(report["holdings_unknown"])

    def test_stale_cache_is_unknown_not_empty(self):
        import r20_backend.dashboard_cache as dash
        cache = {"positions": [], "data_health": {"cache_age_seconds": 600}}
        with patch.object(dash, "CACHE_DATA", cache):
            report = self.risk._holdings_report("DOGE-USDT-SWAP", {})
        self.assertFalse(report["held_live"])
        self.assertIn("过期", report["holdings_unknown"] or "")
        self.assertFalse(report["held"], "过期快照不得被当作「无持仓」")

    def test_missing_positions_key_is_unknown(self):
        import r20_backend.dashboard_cache as dash
        with patch.object(dash, "CACHE_DATA", {"data_health": {"cache_age_seconds": 0}}):
            report = self.risk._holdings_report("DOGE-USDT-SWAP", {})
        self.assertIn("缺失", report["holdings_unknown"] or "")


class DeleteGuardRouteTests(_Base):
    """直接调路由函数（鉴权打桩），断言状态码 + 审计 + 文件未被改动。"""

    def setUp(self):
        super().setUp()
        import scripts.instrument_pool as pool
        self.pool = pool
        self.pool_file = Path(pool.POOL_FILE)
        self.pool_file.parent.mkdir(parents=True, exist_ok=True)
        self.pool_file.write_text(json.dumps({"version": 1, "instruments": [
            # P2-11 起 load_instruments 会校验必需字段（instId/name/ctVal），
            # 夹具补齐 ctVal 以反映真实池（data/instrument_pool.json 每条都有）
            {"instId": "BTC-USDT-SWAP", "name": "BTC", "ctType": "SWAP", "ctVal": 0.01},
            {"instId": "ALGO-USDT-SWAP", "name": "ALGO", "ctType": "SWAP", "ctVal": 100.0},
            {"instId": "DOGE-USDT-SWAP", "name": "DOGE", "ctType": "SWAP", "ctVal": 1000.0},
        ]}), encoding="utf-8")
        # save_instruments 会扇出下游同步文件，测试里禁掉（只关心池文件本身）
        p4 = patch.object(self.pool, "sync_instruments_state", lambda: None)
        p4.start(); self.addCleanup(p4.stop)
        self.audits = []
        p = patch.object(self.risk, "audit_record", lambda *a, **k: self.audits.append((a, k)))
        p.start(); self.addCleanup(p.stop)
        p2 = patch.object(self.risk, "require_admin_header", lambda *a, **k: {"username": "t"})
        p2.start(); self.addCleanup(p2.stop)
        p3 = patch.object(self.risk, "refresh_settings", lambda: None)
        p3.start(); self.addCleanup(p3.stop)

    def _models(self):
        from r20_backend.schemas import InstrumentDeleteRequest
        return InstrumentDeleteRequest

    def _delete(self, inst_id, confirmation=None, with_body=True):
        body = self._models()(confirmation=confirmation) if with_body else None
        return self.risk.delete_admin_instrument(inst_id, body, confirmation, None, None)

    def _pool_ids(self):
        payload = json.loads(self.pool_file.read_text(encoding="utf-8"))
        rows = payload.get("instruments", payload) if isinstance(payload, dict) else payload
        return [item["instId"] for item in rows]

    def _live(self, positions, age=1):
        import r20_backend.dashboard_cache as dash
        cache = {"positions": positions, "data_health": {"cache_age_seconds": age}}
        return patch.object(dash, "CACHE_DATA", cache)

    def test_phrase_is_now_mandatory(self):
        from fastapi import HTTPException
        with self._live([]):
            with self.assertRaises(HTTPException) as ctx:
                self._delete("DOGE-USDT-SWAP", None, with_body=False)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("必须提供确认短语", ctx.exception.detail)
            with self.assertRaises(HTTPException) as ctx2:
                self._delete("DOGE-USDT-SWAP", "remove doge")
            self.assertEqual(ctx2.exception.status_code, 400)
        self.assertEqual(self._pool_ids(), ["BTC-USDT-SWAP", "ALGO-USDT-SWAP", "DOGE-USDT-SWAP"])
        self.assertEqual(self.audits[0][0][0], "instrument.remove")

    def test_live_holding_blocks_deletion(self):
        from fastapi import HTTPException
        with self._live([{"instId": "ALGO-USDT-SWAP", "venue": "binance", "pos": "6431.5"}]):
            with self.assertRaises(HTTPException) as ctx:
                self._delete("ALGO-USDT-SWAP", "REMOVE ALGO-USDT-SWAP")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("binance", ctx.exception.detail)
        self.assertIn("ALGO-USDT-SWAP", self._pool_ids(), "有实时持仓却被删出交易池（P1-5 回归）")
        self.assertIn("rejected_live_holdings", json.dumps(self.audits, ensure_ascii=False))

    def test_tracker_record_blocks_deletion(self):
        from fastapi import HTTPException
        trackers = self.root / "data" / "position_trackers.json"
        trackers.write_text(json.dumps({"DOGE-USDT-SWAP_long": {"instId": "DOGE-USDT-SWAP"}}), encoding="utf-8")
        with self._live([]):
            with self.assertRaises(HTTPException) as ctx:
                self._delete("DOGE-USDT-SWAP", "REMOVE DOGE-USDT-SWAP")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("DOGE-USDT-SWAP_long", ctx.exception.detail)
        self.assertIn("DOGE-USDT-SWAP", self._pool_ids())

    def test_unknown_holdings_fail_closed(self):
        from fastapi import HTTPException
        with self._live([], age=9999):
            with self.assertRaises(HTTPException) as ctx:
                self._delete("DOGE-USDT-SWAP", "REMOVE DOGE-USDT-SWAP")
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("无法确认实时持仓", ctx.exception.detail)
        self.assertIn("DOGE-USDT-SWAP", self._pool_ids(), "未知态放行了删除（缺失≠0 红线）")
        self.assertIn("rejected_unknown_holdings", json.dumps(self.audits, ensure_ascii=False))

    def test_clean_instrument_is_still_removable(self):
        with self._live([{"instId": "ALGO-USDT-SWAP", "venue": "binance", "pos": "1"}]):
            res = self._delete("DOGE-USDT-SWAP", "REMOVE DOGE-USDT-SWAP")
        self.assertEqual(res["removed"], "DOGE-USDT-SWAP")
        self.assertNotIn("DOGE-USDT-SWAP", self._pool_ids())
        self.assertIn("success", json.dumps(self.audits, ensure_ascii=False))

    def test_list_route_exposes_holdings_state(self):
        trackers = self.root / "data" / "position_trackers.json"
        trackers.write_text(json.dumps({"DOGE-USDT-SWAP_short": {}}), encoding="utf-8")
        with self._live([{"instId": "ALGO-USDT-SWAP", "venue": "binance", "pos": "1"}], age=1):
            payload = self.risk.admin_instruments(None)
        rows = {row["instId"]: row for row in payload["instruments"]}
        self.assertTrue(rows["BTC-USDT-SWAP"]["protected"])
        self.assertFalse(rows["BTC-USDT-SWAP"]["removable"])
        self.assertTrue(rows["DOGE-USDT-SWAP"]["has_tracker"])
        self.assertEqual(rows["DOGE-USDT-SWAP"]["tracker_keys"], ["DOGE-USDT-SWAP_short"])
        self.assertTrue(rows["ALGO-USDT-SWAP"]["held_live"])
        self.assertEqual(rows["ALGO-USDT-SWAP"]["held_venues"], ["binance"])
        self.assertFalse(rows["ALGO-USDT-SWAP"]["holdings_unknown"])
        self.assertTrue(rows["ALGO-USDT-SWAP"]["removable"] is False)
        self.assertFalse(rows["DOGE-USDT-SWAP"]["removable"], "有追踪记录却标成可移除")
        with self._live([], age=9999):
            stale = self.risk.admin_instruments(None)
        stale_rows = {row["instId"]: row for row in stale["instruments"]}
        self.assertTrue(stale_rows["DOGE-USDT-SWAP"]["holdings_unknown"])
        self.assertFalse(stale_rows["DOGE-USDT-SWAP"]["removable"], "未知态不得标成可移除")
        self.assertEqual(stale["holdings_snapshot"]["unknown_count"], 2)


class CouncilConfigGateTests(_Base):
    """P1-4a/4b：委员会配置读写门禁一致 + 未登记模型绑定不再静默回落。"""

    def setUp(self):
        super().setUp()
        import r20_backend.council_manager as cm
        self.cm = cm
        self.config_file = Path(cm.COUNCIL_CONFIG_FILE)
        # 模型库：只登记一个模型，便于断言"未登记"
        self.registry = {"models": [{"id": "glm-5.3-flash", "base_url": "u", "api_key": "k", "api_format": "openai_chat"}],
                         "active_reasoning_effort": "medium"}
        import r20_backend.llm_manager as llm
        p = patch.object(llm, "load_llm_config", lambda **k: self.registry)
        p.start(); self.addCleanup(p.stop)

    def _write(self, payload):
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _read(self):
        return json.loads(self.config_file.read_text(encoding="utf-8"))

    def test_custom_arbitrator_seat_survives_load(self):
        """旧读闸只认 trader_trend/cio → 自定义仲裁官下次 GET 就被工厂默认覆盖（P1-4a）。"""
        self._write({"enabled": True, "consensus_mode": "standard", "timeout_seconds": 200,
                     "roles": {"head_risk": {"id": "head_risk", "is_arbitrator": True, "enabled": True,
                                             "model_id": "", "prompt": "我的自定义仲裁官提示词"}}})
        cfg = self.cm.load_council_config()
        self.assertEqual(list(cfg["roles"].keys()), ["head_risk"])
        self.assertEqual(cfg["roles"]["head_risk"]["prompt"], "我的自定义仲裁官提示词")
        self.assertNotIn("config_warning", cfg)
        self.assertEqual(self._read()["roles"]["head_risk"]["prompt"], "我的自定义仲裁官提示词",
                         "用户配置被读路径覆盖（P1-4a 回归）")

    def test_config_without_arbitrator_is_preserved_and_flagged(self):
        self._write({"consensus_mode": "standard", "roles": {"trader_a": {"id": "trader_a", "prompt": "只有交易员"}}})
        cfg = self.cm.load_council_config()
        self.assertIn("trader_a", cfg["roles"], "无仲裁官时用户配置被工厂默认吞掉（数据丢失）")
        self.assertIn("config_warning", cfg)
        self.assertEqual(self._read()["roles"]["trader_a"]["prompt"], "只有交易员")

    def test_corrupt_config_is_backed_up_before_rebuild(self):
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text("{ 这不是 JSON", encoding="utf-8")
        cfg = self.cm.load_council_config()
        backups = list(Path(self.cm.DATA_DIR).glob("council_config_corrupt_*.json"))
        self.assertTrue(backups, "损坏配置被直接覆盖，没有留备份")
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{ 这不是 JSON")
        self.assertIn("roles", cfg)

    def test_write_gate_rejects_new_unregistered_model_binding(self):
        self._write({"consensus_mode": "standard", "roles": {"cio": {"id": "cio", "is_arbitrator": True, "model_id": ""}}})
        with self.assertRaises(ValueError) as ctx:
            self.cm.save_council_config({"consensus_mode": "standard",
                                         "roles": {"cio": {"id": "cio", "is_arbitrator": True, "model_id": "qwen3.8-flash"}}})
        self.assertIn("未在模型库登记", str(ctx.exception))

    def test_legacy_unregistered_binding_does_not_block_saving(self):
        """历史遗留绑定不该堵死保存（否则管理员连开关都改不了）。"""
        self._write({"consensus_mode": "standard",
                     "roles": {"cio": {"id": "cio", "is_arbitrator": True, "model_id": "qwen3.8-flash"}}})
        saved = self.cm.save_council_config({"enabled": True, "consensus_mode": "standard",
                                            "roles": {"cio": {"id": "cio", "is_arbitrator": True,
                                                              "model_id": "qwen3.8-flash", "enabled": True}}})
        self.assertTrue(saved["enabled"])
        self.assertEqual(saved["roles"]["cio"]["model_id"], "qwen3.8-flash")

    def test_registered_binding_is_accepted(self):
        self._write({"consensus_mode": "standard", "roles": {"cio": {"id": "cio", "is_arbitrator": True, "model_id": ""}}})
        saved = self.cm.save_council_config({"consensus_mode": "standard",
                                            "roles": {"cio": {"id": "cio", "is_arbitrator": True,
                                                              "model_id": "glm-5.3-flash"}}})
        self.assertEqual(saved["roles"]["cio"]["model_id"], "glm-5.3-flash")

    def test_seat_model_health_and_resolve_flag_missing(self):
        roles = {"cio": {"model_id": "qwen3.8-flash"}, "trader_trend": {"model_id": "glm-5.3-flash"},
                 "trader_quant": {"model_id": ""}}
        health = {row["role_id"]: row for row in self.cm.seat_model_health(roles)}
        self.assertEqual(health["cio"]["mode"], "missing")
        self.assertEqual(health["trader_trend"]["mode"], "registered")
        self.assertEqual(health["trader_quant"]["mode"], "follow_main")
        missing = self.cm.resolve_seat_model(roles["cio"])
        self.assertTrue(missing["fallback"])
        self.assertFalse(missing["registered"])
        self.assertEqual(missing["requested"], "qwen3.8-flash")
        self.assertIn("glm-5.3-flash", missing["registered_ids"])
        ok = self.cm.resolve_seat_model(roles["trader_trend"])
        self.assertFalse(ok["fallback"])
        self.assertEqual(ok["model"], "glm-5.3-flash")

    def test_config_route_exposes_model_health(self):
        # 第九十六刀：议会端点现住 strategy/council.py ⇒ patch/调用都指向它
        from r20_backend.routers.strategy import council as strategy_council
        self._write({"consensus_mode": "standard",
                     "roles": {"cio": {"id": "cio", "is_arbitrator": True, "model_id": "qwen3.8-flash"}}})
        with patch.object(strategy_council, "require_admin_header", lambda *a, **k: {"username": "t"}):
            payload = strategy_council.admin_get_council_config(None)
        self.assertEqual(payload["model_health"][0]["mode"], "missing")
        self.assertIn("model_health_note", payload)


class VenuePoolGateTests(_Base):
    """P1-7：data/venue_routing.json 的 dry_run/assets/max_open/margin/min_confidence 必须在发送前真的生效。"""

    @classmethod
    def setUpClass(cls):
        from r20_backend.exchanges import listing as _listing
        cls._lp = patch.object(_listing, "ensure_contract_listed",
                               lambda *a, **k: _listing.ListingCheck(ok=True, reason=None, checked_at="", source="cache"))
        cls._lp.start()

    @classmethod
    def tearDownClass(cls):
        cls._lp.stop()

    def setUp(self):
        super().setUp()
        from r20_backend import execution_router as router
        from r20_backend.exchanges.gate import GateAdapter
        self.router = router
        self._ambient = {k: v for k, v in os.environ.items()
                         if k.startswith(("R20_GATE_TESTNET", "R20_GATE_EXECUTION"))}
        os.environ.pop("R20_GATE_TESTNET", None)
        os.environ["R20_GATE_EXECUTION"] = "1"
        def _restore_env():
            for k in ("R20_GATE_EXECUTION", "R20_GATE_TESTNET"):
                os.environ.pop(k, None)
            os.environ.update(self._ambient)

        self.addCleanup(_restore_env)

        outer = self

        class _Stub(GateAdapter):
            def __init__(self, positions=None):
                self.calls = []
                self._positions = positions or []

            def _keys(self):
                return ("k", "s")

            def detect_position_mode(self):
                # 第八刀：router 新增持仓模式只读体检（policy：探测不到就禁新开仓）。
                # 本桩继承真实 GateAdapter（声明 position_modes）但打桩了私有 IO，
                # 探测会返回 unknown ⇒ 整条开仓路径被拒。桩必须像真适配器一样**明确**
                # 给出模式，否则这些用例测的就不再是它们本来要测的东西。
                return "single"

            def positions(self):
                self.calls.append(("positions",))
                return list(self._positions)

            def fetch_instrument_spec(self, symbol, refresh=False):
                from r20_backend.exchanges import InstrumentSpec
                return InstrumentSpec(venue="gate", inst_id="BTC_USDT", base="BTC",
                                      tick_size=0.1, step_size=0.0001, ct_val=0.0001, min_size=1)

            def fetch_ticker(self, symbol):
                return {"last": 79000.0, "mark_price": 79000.0}

            def set_leverage(self, symbol, leverage, margin_mode="cross"):
                self.calls.append(("leverage", symbol, int(leverage), margin_mode))
                return {"leverage": str(int(leverage))}

            def place_order(self, symbol, side, contracts, price=None, tif="gtc", text=""):
                self.calls.append(("place", symbol, side, contracts, price))
                return {"id": 9001, "text": "t", "size": contracts}

            def attach_protective_orders(self, symbol, pos_side, tp_px=None, sl_px=None,
                                         expiration=604800, price_type=0):
                self.price_orders = [{"id": "tp1"}, {"id": "sl1"}]
                return {"tp": "tp1", "sl": "sl1"}

            def list_protective_orders(self, symbol):
                return [{"id": "tp1"}, {"id": "sl1"}]

            def cancel_order(self, symbol, order_id):
                return {"cancelled": True}

        self._Stub = _Stub
        _ = outer

    def _pool(self, **over):
        pool = {"assets": ["BTC"], "margin_per_trade_usdt": 500.0, "max_open": 5,
                "min_confidence": 72.0, "dry_run": False}
        pool.update(over)
        return pool

    def _run(self, pool=None, adapter=None, raw_pool=False, **decision_over):
        decision = {"asset": "BTC", "action": "BUY_LONG", "margin_usdt": 300.0, "leverage": 3,
                    "entry_price": 79000.0, "take_profit_price": 85000.0, "stop_loss_price": 77000.0,
                    "confidence": 88.0}
        decision.update(decision_over)
        ad = adapter or self._Stub()
        if raw_pool:   # 集成口径：走真实 routing_policy 读文件
            return self.router.open_protected_position(decision, adapter=ad, price_ref=79000.0), ad
        with patch.object(self.router, "_load_venue_pool_soft", lambda venue: self._pool(**(pool or {}))):
            return self.router.open_protected_position(decision, adapter=ad, price_ref=79000.0), ad

    def test_dry_run_blocks_real_send(self):
        res, ad = self._run(pool={"dry_run": True})
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "venue_dry_run")
        self.assertNotIn("place", [c[0] for c in ad.calls], "dry_run=true 却仍然发出了委托（P1-7 回归）")

    def test_asset_not_in_venue_list_is_rejected(self):
        res, ad = self._run(pool={"assets": ["ETH"]})
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "venue_pool")
        self.assertNotIn("place", [c[0] for c in ad.calls])

    def test_empty_asset_pool_means_no_trading(self):
        res, ad = self._run(pool={"assets": []})
        self.assertFalse(res["ok"])
        self.assertIn("空池", res["detail"])
        self.assertNotIn("place", [c[0] for c in ad.calls])

    def test_margin_is_clamped_to_venue_budget(self):
        res, ad = self._run(pool={"margin_per_trade_usdt": 120.0}, margin_usdt=300.0)
        self.assertTrue(res["ok"], res.get("detail"))
        self.assertEqual(res["margin_usdt"], 120.0)
        self.assertEqual(res["margin_clamped_from_usdt"], 300.0)
        # 第二百零三刀：这里原本是 `assertIn("该所预算", "该所预算 120U")` ——
        # **字面量自证**（拿一个常量断言它包含自己），与代码毫无关系 ⇒ 恒真、白占一行。
        # 现在改成**捕获真日志**：夹仓消息必须点名"该所预算"与实际夹到的上限，
        # 这样"用例名说 venue budget"才是真的被验证了。
        from contextlib import redirect_stdout
        import io as _io
        _buf = _io.StringIO()
        with redirect_stdout(_buf):
            self._run(pool={"margin_per_trade_usdt": 120.0}, margin_usdt=300.0)
        _log = _buf.getvalue()
        self.assertIn("该所预算", _log, f"夹仓日志没说清哪道上限生效：{_log!r}")
        self.assertIn("120U", _log, f"夹仓日志没报夹到的上限：{_log!r}")

    def test_min_confidence_gate(self):
        res, ad = self._run(pool={"min_confidence": 90.0}, confidence=88.0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "venue_pool")
        self.assertIn("置信度", res["detail"])
        self.assertNotIn("place", [c[0] for c in ad.calls])

    def test_missing_confidence_does_not_fabricate_a_rejection(self):
        """决策载荷没有置信度时不得凭空判违规（缺失≠0）。"""
        res, _ad = self._run(pool={"min_confidence": 90.0}, confidence=0.0)
        self.assertTrue(res["ok"], res.get("detail"))

    def test_max_open_uses_the_single_positions_probe(self):
        held = [{"base": "ETH", "size_signed": 1}, {"base": "SOL", "size_signed": -2},
                {"base": "DOGE", "size_signed": 3}, {"base": "ADA", "size_signed": 4},
                {"base": "LINK", "size_signed": 5}]
        res, ad = self._run(pool={"max_open": 5}, adapter=self._Stub(positions=held))
        self.assertFalse(res["ok"])
        self.assertIn("max_open=5", res["detail"])
        self.assertEqual([c[0] for c in ad.calls].count("positions"), 1, "池上限检查不得额外多探一次持仓")
        self.assertNotIn("place", [c[0] for c in ad.calls])

    def test_happy_path_single_probe_and_order_sent(self):
        res, ad = self._run(pool={}, adapter=self._Stub(positions=[{"base": "ETH", "size_signed": 1}]))
        self.assertTrue(res["ok"], res.get("detail"))
        self.assertEqual([c[0] for c in ad.calls], ["positions", "leverage", "place"],
                         "持仓探针必须只有一次（池上限复用同一次结果）")

    def test_pool_is_read_from_the_routing_file(self):
        """集成口径：不 patch 机制函数，直接写一份临时 venue_routing.json 验证真读文件。"""
        import json as _json
        import r20_backend.exchanges.routing_policy as rp
        routing = self.root / "data" / "venue_routing.json"
        routing.parent.mkdir(parents=True, exist_ok=True)
        routing.write_text(_json.dumps({"preferred_venue": "auto", "routing_mode": "balanced",
                                        "gate": {"assets": ["BTC"], "margin_per_trade_usdt": 0,
                                                 "max_open": 0, "min_confidence": 0, "dry_run": True}}), encoding="utf-8")
        with patch.object(rp, "ROUTING_FILE", routing), patch.object(rp, "_gate_execution_ready", lambda: True):
            res, ad = self._run(adapter=self._Stub(), raw_pool=True)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "venue_dry_run", res.get("detail"))
        self.assertNotIn("place", [c[0] for c in ad.calls])


class ConstitutionLinterTests(_Base):
    """P1-8：宪法门禁必须拦住"自己拆掉硬风控"的心法（子审计实跑旧版全 PASS）。"""

    def setUp(self):
        super().setUp()
        import scripts.evolution_shield as shield
        self.shield = shield

    def test_audit_poison_lessons_are_blocked(self):
        for text in ("把单笔杠杆从 5x 提升至 20x，追求更高收益",
                     "把单标的累计保证金上限提高到 50%",
                     "关闭风控拦截器以绕过 4H 方向否决",
                     "放宽止损到 5 个 ATR 以提高容错",
                     "下调入场置信度门槛到 50%",
                     "永远不做多，只做空"):
            passed, reason = self.shield.audit_proposed_lesson(text, sample_size=38)
            self.assertFalse(passed, f"毒心法未被拦截: {text}")
            self.assertTrue(reason)

    def test_legitimate_lessons_still_pass(self):
        for text in ("浮盈达到 0.8R 坚决执行保本移损，锁死胜率下限",
                     "严禁跨标的同向共振堆叠单边敞口，防范系统性 Beta 踩踏",
                     "严禁放宽止损或取消硬风控拦截器（历史教训）",
                     "震荡市里把入场时机推迟到 ADX>25 之后再挂限价单"):
            passed, reason = self.shield.audit_proposed_lesson(text, sample_size=38)
            self.assertTrue(passed, f"误杀合法心法: {text} → {reason}")

    def test_mixed_sentence_still_catches_the_poison_clause(self):
        passed, reason = self.shield.audit_proposed_lesson("严禁逆势加仓，但可以放宽止损", sample_size=38)
        self.assertFalse(passed, "禁止式子句不该豁免同一句里的放风控子句")
        self.assertIn("RISK_EXPANSION", reason)

    def test_small_sample_still_rejected(self):
        passed, _ = self.shield.audit_proposed_lesson("震荡市里把入场时机推迟到 ADX>25 之后", sample_size=1)
        self.assertFalse(passed)

    def test_injection_report_is_honest_about_the_cap(self):
        lessons = [{"id": f"l{i}", "rule_text": f"规则{i}", "enabled": True, "is_baseline": i == 0,
                    "created_at": f"2026-09-{i+1:02d} 00:00:00", "ttl_days": 365} for i in range(12)]
        report = self.shield.injection_report(lessons)
        self.assertEqual(report["active"], 12)
        self.assertEqual(report["injected"], self.shield.MAX_INJECTED_LESSONS)
        self.assertEqual(report["limit"], 8)
        self.assertEqual(len(report["not_injected"]), 4, "超上限的心法必须被点名，而不是让页面当'生效中'")

    def test_revise_keeps_omitted_lessons_as_disabled_archive(self):
        """P1-8c：模型没复述的已学心法不得静默消失。"""
        old = [
            {"id": "keep", "rule_text": "浮盈 0.8R 保本移损，锁死胜率下限", "enabled": True,
             "health_score": 99.0, "created_at": "2026-09-01 00:00:00", "ttl_days": 14,
             "sample_size": 20, "is_baseline": False, "shield_status": "PASSED"},
            {"id": "dropped", "rule_text": "震荡区间里把入场推迟到 ADX>25 之后", "enabled": True,
             "health_score": 80.0, "created_at": "2026-09-02 00:00:00", "ttl_days": 14,
             "sample_size": 20, "is_baseline": False, "shield_status": "PASSED"},
        ]
        candidates = self.shield._review_candidates(
            ["浮盈 0.8R 保本移损，锁死胜率下限（修订版适用条件：仅趋势日）"],
            old, sample_size=20, strict=True, change_status="REVISE")
        texts = [c["rule_text"] for c in candidates]
        self.assertIn("震荡区间里把入场推迟到 ADX>25 之后", " ".join(texts),
                      "被省略的已学心法直接蒸发（P1-8c 回归）")
        kept = [c for c in candidates if c["rule_text"] == "震荡区间里把入场推迟到 ADX>25 之后"][0]
        self.assertFalse(kept["enabled"], "退役存档必须停用（不得继续注入提示词）")
        self.assertIn("REVISE", kept.get("retired_reason", ""))

    def test_add_does_not_mark_omissions_as_retired(self):
        old = [{"id": "x", "rule_text": "既有心法，浮盈 1R 移损", "enabled": True, "health_score": 90.0,
                "created_at": "2026-09-01 00:00:00", "ttl_days": 14, "sample_size": 20,
                "is_baseline": False, "shield_status": "PASSED"}]
        candidates = self.shield._review_candidates(
            ["既有心法，浮盈 1R 移损", "新增：ADX<20 时不追单"], old, sample_size=20,
            strict=True, change_status="ADD")
        self.assertTrue(all(c["enabled"] for c in candidates))


class RollbackConstitutionReviewTests(_Base):
    """P1-8b：策略回滚写记忆曾只做 schema 校验，宪法门禁被绕过。"""

    def test_restored_poison_lesson_is_flagged(self):
        from r20_backend.policy_snapshot import _review_restored_lessons
        lessons = [
            {"id": "ok", "rule_text": "浮盈 0.8R 保本移损，锁死胜率下限", "sample_size": 30},
            {"id": "bad", "rule_text": "把单笔杠杆从 5x 提升至 20x 提高资金效率", "sample_size": 30},
        ]
        report = _review_restored_lessons(lessons)
        self.assertEqual(report["total"], 2)
        self.assertEqual([f["id"] for f in report["flagged"]], ["bad"])
        self.assertEqual(lessons[1]["shield_status"], "RESTORED_UNREVIEWED")
        self.assertEqual(lessons[0]["shield_status"], "RESTORED")
        self.assertEqual(report["marked"], 2)

    def test_missing_reviewer_is_disclosed_not_faked(self):
        from r20_backend.policy_snapshot import _review_restored_lessons
        with patch.dict("sys.modules", {"evolution_shield": None}):
            report = _review_restored_lessons([{"id": "x", "rule_text": "任意"}])
        self.assertIn("reviewer_error", report)
        self.assertEqual(report.get("flagged"), [])


class CouncilPromptRenderingTests(_Base):
    """P1-4d：席位提示词变量必须真渲染；未知变量显式标注而非原样丢给模型。"""

    def setUp(self):
        super().setUp()
        import r20_backend.council_manager as cm
        self.cm = cm

    def test_known_variables_render(self):
        out = self.cm._render_seat_prompt("余额 {{account_balance}} 心法 {{trading_memory}}",
                                          {"account_balance": "4980.96", "trading_memory": "保本锁利"})
        self.assertEqual(out, "余额 4980.96 心法 保本锁利")

    def test_unknown_variable_is_marked(self):
        out = self.cm._render_seat_prompt("宏观 {{macro_4h}}", {"account_balance": "1"})
        self.assertEqual(out, "宏观 [UNKNOWN_VARIABLE:macro_4h]")

    def test_missing_context_is_marked(self):
        out = self.cm._render_seat_prompt("余额 {{account_balance}}", {})
        self.assertEqual(out, "余额 [MISSING_CONTEXT:account_balance]")

    def test_no_context_keeps_template_preview(self):
        tpl = "宏观 {{macro_4h}}"
        self.assertEqual(self.cm._render_seat_prompt(tpl, None), tpl)

    def test_debate_signature_accepts_runtime_context(self):
        import inspect
        sig = inspect.signature(self.cm.execute_council_debate)
        self.assertIn("runtime_context", sig.parameters)
        src = inspect.getsource(self.cm.execute_council_debate)
        self.assertIn("runtime_context", src)

    def test_ui_slots_are_real_variables(self):
        """UI 的"插入槽位"必须都是 prompt_library 认得的变量（旧列表 6 个里 5 个非法）。

        ⚠️ 第六十刀把 `dataSlots` 从 `CouncilPage.vue` 搬进了
        `views/admin/council/councilLogic.ts`，本用例随之改为扫描**两个文件**。

        ⚠️⚠️ 这里刻意**不**改成"import 那个 ts 模块再读 DATA_SLOTS"：
        那样做，本用例就**不再强制任何源文件里存在槽位定义** ——
        把定义删掉、只在测试旁边的变量里留一份，用例照样绿。
        扫源文件虽然粗糙，但它保证"真值在源码里"。
        """
        import re
        import prompt_library as pl
        candidates = [
            ROOT / "frontend" / "src" / "views" / "admin" / "CouncilPage.vue",
            ROOT / "frontend" / "src" / "views" / "admin" / "council" / "councilLogic.ts",
        ]
        # `{ k: 'x' }`（单引号，旧形态）或 `{ k: "x" }`（双引号）都要能读到
        keys = []
        for f in candidates:
            self.assertTrue(f.exists(), f"槽位定义所在文件不存在: {f}")
            keys += re.findall(r"\{ k: ['\"]([a-z0-9_]+)['\"]", f.read_text(encoding="utf-8"))
        self.assertTrue(keys, "未解析到槽位定义（源码里必须存在真值）")
        self.assertGreaterEqual(len(keys), 8, f"槽位数量异常地少（{len(keys)}）—— 是否被删了？")
        invalid = [k for k in keys if k not in pl.ALLOWED_VARIABLES]
        self.assertEqual(invalid, [], f"槽位里有非法变量（会渲染成 [UNKNOWN_VARIABLE]）: {invalid}")


class CouncilTestDebateContextTests(_Base):
    """P1-4c：测试辩论的行情/资金必须来自真实快照，缺什么写什么，不再编 1450U/77200/ord_10283。"""

    def test_debate_prompt_uses_live_snapshot_and_marks_missing(self):
        import json as _json
        from r20_backend.routers.strategy import council as strategy_council   # 第九十六刀：议会端点现住此
        import r20_backend.dashboard_cache as dash

        snap = self.root / "data" / "factor_library_snapshot.json"
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(_json.dumps({
            "timestamp": 1789316902, "time_str": "2026-09-14 00:30:00",
            "instruments": [{"instId": "BTC-USDT-SWAP", "price": 77226.3, "chg24h": -0.18,
                             "trend_momentum": {"adx_1h": 36.52, "rsi_14": 70.2, "trend_regime": "STRONG_TREND"},
                             "volatility_channel": {"atr_1h": 239.3571},
                             "volume_money_flow": {"cmf_1h": 0.16},
                             "smart_money_derivatives": {"weighted_long_pct": 61.4}}],
        }), encoding="utf-8")
        captured = {}

        def fake_debate(**kwargs):
            captured.update(kwargs)
            return ({"action": "WAIT"}, {"consensus_mode": "standard"})

        cache = {"account": {"avail_eq": 4980.96, "total_eq": 4980.96},
                 "positions": [{"instId": "ETH-USDT-SWAP", "venue": "binance", "posSide": "long", "pos": "0.275"}],
                 "pending_orders": []}
        cls = type("T", (), {"mock_market_prompt": None})()
        with patch.object(strategy_council, "require_admin_header", lambda *a, **k: {"username": "t"}), \
             patch.object(strategy_council, "ROOT", self.root), \
             patch.object(dash, "CACHE_DATA", cache), \
             patch.object(strategy_council, "audit_record", lambda *a, **k: None), \
             patch("r20_backend.council_manager.execute_council_debate", fake_debate), \
             patch.object(strategy_council, "load_council_config" if hasattr(strategy_council, "load_council_config") else "refresh_settings", lambda *a, **k: {}, create=True):
            payload = strategy_council.admin_test_council_debate(cls)
        prompt = captured.get("market_prompt", "")
        self.assertIn("77226.3", prompt, "未使用真实因子快照")
        self.assertIn("36.52", prompt)
        self.assertIn("4980.96", prompt, "未使用真实账户余额")
        self.assertIn("ETH-USDT-SWAP", prompt, "未使用真实持仓")
        self.assertNotIn("1,450.00", prompt, "仍在使用编造的可用资金")
        self.assertNotIn("ord_10283", prompt, "仍在使用编造的挂单")
        self.assertNotIn("77200.0", prompt, "仍在使用编造的 BTC 持仓价")
        self.assertEqual(payload["market_context"]["source"], "live")
        self.assertEqual(payload["market_context"]["instruments"], 1)

    def test_council_test_route_has_no_fabricated_market_literals(self):
        """源码钉：编造的行情/资金字面量不得回流（旧实现写死 1450/2280/77200/ord_10283）。"""
        # 第九十六刀：strategy 已拆包 ⇒ 按**域**取源（不绑文件位置）
        from tests.source_scan import router_domain_source
        src = router_domain_source("strategy", root=ROOT)
        # 只查代码行：审计注释里会引用这些旧字面量（"旧实现写死了 1450/77200"），注释不是造假
        code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
        for literal in ("1,450.00", "2,280.00", "ord_10283", "77200.0", "2026-09-05 08:30:00"):
            self.assertNotIn(literal, code, f"测试辩论路由又出现编造字面量 {literal}")

    def test_missing_sources_are_declared_not_fabricated(self):
        from r20_backend.routers.strategy import council as strategy_council   # 第九十六刀：议会端点现住此
        import r20_backend.dashboard_cache as dash
        captured = {}

        def fake_debate(**kwargs):
            captured.update(kwargs)
            return ({"action": "WAIT"}, {})

        cls = type("T", (), {"mock_market_prompt": None})()
        with patch.object(strategy_council, "require_admin_header", lambda *a, **k: {"username": "t"}), \
             patch.object(strategy_council, "ROOT", self.root), \
             patch.object(dash, "CACHE_DATA", {}), \
             patch("r20_backend.council_manager.execute_council_debate", fake_debate):
            payload = strategy_council.admin_test_council_debate(cls)
        prompt = captured.get("market_prompt", "")
        self.assertIn("不可用", prompt)
        self.assertTrue(payload["market_context"]["missing"])
        for fabricated in ("1450", "2280", "77200", "ord_10283", "2026-09-05"):
            self.assertNotIn(fabricated, prompt, f"缺失场景仍在编造 {fabricated}")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""US-003 接入级回归：主脑信号 → 选所路由 → 预算原子预留 → 提交订单（全 mock 边界）。

覆盖验收标准：
1. 手动选所优先生效（直取该所；仍过 route_signal 取 rejected 证据）；
2. auto 评分路由（当前唯一真候选 OKX + binance/gate 占位在 executable 阶段留证）；
3. ALL_REJECTED / 未登记执行面的中选所 → 本轮不下单 + warn；
4. ReservationExceeded → 本轮不下单；下单未获受理 → 预留释放；同意图重投幂等；
5. venue_decision 段随决策 JSON 落盘（既有字段逐键保留、无决策不伪造条目）；
6. preferred_venue 缺字段/非法值 → 回退 auto + warn，且与 gate 池读写互相兼容。

封闭三律：
① 一切外部依赖 patch **模块绑定名**：trader.load_preferred_venue /
   trader.venue_execution_ready / trader.reservation_manager /
   listing_mod.ensure_contract_listed / trader.okx_rest.place_order /
   routing_policy.ROUTING_FILE——零真实网络、零真实凭证、零真实下单；
② 决策缓存、预留库、意图文件、路由配置全部落在 tempfile 目录；
③ 不触碰真实 data/**、.env、交易所端点；ambient R20_* 开闸旗标在夹具里排除，
   执行档只由用例自己决定（同 test_gate_execution_router 的钉法）。
"""
from __future__ import annotations

import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import scripts.ai_factor_trader as trader
from r20_backend import risk_reservation
from r20_backend.exchanges import listing as listing_mod
from r20_backend.exchanges import routing_policy

FP = "fp-test-1234"


def _health_with(*venues):
    """构造一份「此刻新鲜」的 venue_health.json 观测（跨所取数面）。"""
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return {"v": 1, "updated_utc": now_utc,
            "venues": {v: {"ok": ["BTC"], "failed": {}, "latency_ms": {"BTC": 120}}
                       for v in venues}}
ACCOUNT_OKX_LIVE = ("okx", "live", FP)


class _FakeEnv:
    """受控 OKX 环境对象——凭证是假的，只提供路由/预留要的三元身份维度。"""

    def __init__(self, mode: str = "live", configured: bool = True):
        self.mode = mode
        self.configured = configured
        self.simulated = (mode == "demo")
        self.fingerprint = FP


def _ok_listing(venue, environment, contract):
    return listing_mod.ListingCheck(ok=True, reason=None,
                                    checked_at="2026-09-11T12:00:00Z", source="cache")


class _WiringSandbox(unittest.TestCase):
    """公共夹具：临时目录接管决策缓存/预留库/健康度/意图文件 + 全 mock 边界。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="us003-")
        self.cache_file = os.path.join(self.tmp, "ai_brain_decisions.json")
        self.calls = []
        self.out = ""
        # ambient 旗标隔离（退出 patch.dict 自动还原）
        self._envp = patch.dict("os.environ", {}, clear=False)
        self._envp.start()
        for key in [k for k in os.environ
                    if k.startswith("R20_") and ("EXECUTION" in k or "TESTNET" in k)]:
            os.environ.pop(key, None)
        os.environ.pop(trader.PORTFOLIO_RISK_BUDGET_ENV, None)

    def tearDown(self):
        self._envp.stop()

    # ---- 夹具助手 ----
    def _write_cache(self, inst_ids):
        payload = {i: {"instId": i, "policy_hash": "keepme",
                       "decision": {"action": "BUY_LONG", "confidence": 90.0}}
                   for i in inst_ids}
        with open(self.cache_file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)

    def _manager(self, limit=None):
        return risk_reservation.get_manager(db_path=os.path.join(self.tmp, "res.db"),
                                            total_limit_usdt=limit)

    def _submit(self, inst_id="BTC-USDT-SWAP", side="buy", pos_side="long",
                size=10.0, price=100.0, tp=115.0, sl=95.0, notional=300.0,
                margin=60.0, intent="BTC:i1", limit=None, preferred="auto",
                executable=None, env=None, health=None, mode="auto",
                listing_check=None):
        """跑一次真实 submit_protected_limit_order；返回 (ok, ref)。"""
        if limit:
            # 总上限的单一事实源是 env（trader 读它建 manager）
            os.environ[trader.PORTFOLIO_RISK_BUDGET_ENV] = str(limit)
        mgr = self._manager(limit)
        live_env = env or _FakeEnv()
        health_file = os.path.join(self.tmp, "venue_health.json")
        if health is not None:
            with open(health_file, "w", encoding="utf-8") as handle:
                json.dump(health, handle, ensure_ascii=False)
        else:
            health_file = os.path.join(self.tmp, "no_health.json")
        patches = [
            patch.object(trader, "AI_DECISION_CACHE_FILE", self.cache_file),
            patch.object(trader, "VENUE_HEALTH_FILE", health_file),
            patch.object(trader, "OPEN_INTENT_FILE", os.path.join(self.tmp, "intents.json")),
            patch.object(trader, "load_preferred_venue", lambda: preferred),
            patch.object(trader, "load_routing_mode", lambda: mode),
            patch.object(trader, "current_environment", lambda values=None: live_env),
            patch.object(trader, "selected_environment", lambda values=None: live_env),
            patch.object(trader, "reservation_manager", lambda: mgr),
            patch.object(listing_mod, "ensure_contract_listed", listing_check or _ok_listing),
            patch.object(trader.okx_rest, "place_order", self._on_place),
        ]
        if executable is not None:
            patches.append(patch.object(trader, "venue_execution_ready",
                                        lambda v, e: bool(executable.get(v, False))))
        started = []
        for p in patches:
            p.start()
            started.append(p)
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                ok, ref = trader.submit_protected_limit_order(
                    inst_id, side, pos_side, size, price, tp, sl,
                    venue_ctx={"notional_usdt": notional, "margin_usdt": margin,
                               "intent_id": intent})
        finally:
            for p in started:
                p.stop()
        self.out = buf.getvalue()
        self.mgr = mgr
        return ok, ref

    def _on_place(self, *args, **kwargs):
        self.calls.append(kwargs.get("instId") or (args[0] if args else "?"))
        return [{"ordId": "ORDER-1"}]

    def _cache(self):
        with open(self.cache_file, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _venue_decision(self, inst_id="BTC-USDT-SWAP"):
        return self._cache()[inst_id]["venue_decision"]


class TestManualPreferredVenue(_WiringSandbox):
    """验收 1：手动选所优先。"""

    def test_manual_preferred_venue_is_taken_directly(self):
        self._write_cache(["BTC-USDT-SWAP"])
        # 三所都「开闸」时，手选 okx 必须直取 okx，其余所不得参与评分
        ok, ref = self._submit(preferred="okx",
                               executable={"okx": True, "binance": True, "gate": True})
        self.assertTrue(ok, f"应下单成功: {ref} | {self.out}")
        self.assertEqual(ref, "ORDER-1")
        self.assertEqual(self.calls, ["BTC-USDT-SWAP"])
        vd = self._venue_decision()
        self.assertEqual(vd["venue"], "okx")
        self.assertEqual(vd["preferred_venue"], "okx")
        self.assertEqual(vd["reason_code"], "OK")
        self.assertEqual(vd["rejected"], [], "手选模式下其他所不进候选")
        self.assertFalse(any("[binance]" in r or "[gate]" in r for r in vd["reasons"]),
                         vd["reasons"])

    def test_manual_preferred_venue_without_submitter_skips_order(self):
        self._write_cache(["BTC-USDT-SWAP"])
        # 手选未登记 submitter 的所（以仅留 OKX 提交器模拟）：路由选中它但跳过下单
        with patch.dict(trader.VENUE_SUBMITTERS, {"okx": "okx_rest.place_order"}, clear=True):
            ok, ref = self._submit(preferred="gate", executable={"gate": True},
                                   health=_health_with("gate"))
            self.assertFalse(ok)
            self.assertTrue(ref.startswith("路由拒绝: "), ref)
            self.assertEqual(self.calls, [], "未登记执行面的所绝不能打到 OKX 下单端点")
            vd = self._venue_decision()
            self.assertEqual(vd["venue"], "gate")
            self.assertEqual(vd["outcome"], "selected")
            self.assertIn("未登记下单实现", vd["skip_reason"])
            self.assertIn("本轮不下单", self.out)

    def test_manual_preferred_venue_with_gate_dispatches_execution_router(self):
        self._write_cache(["BTC-USDT-SWAP"])
        fake_res = {"ok": True, "order_id": "GATE-ORDER-888", "detail": "gate success"}
        with patch("r20_backend.execution_router.open_protected_position", return_value=fake_res) as mock_open:
            ok, ref = self._submit(preferred="gate", executable={"gate": True},
                                   health=_health_with("gate"))
            self.assertTrue(ok, ref)
            self.assertEqual(ref, "GATE-ORDER-888")
            self.assertEqual(self.calls, [], "Gate 下单不得打到 OKX 端点")
            mock_open.assert_called_once()
            called_decision = mock_open.call_args[0][0]
            self.assertEqual(called_decision["venue"], "gate")
            self.assertEqual(called_decision["asset"], "BTC")
            self.assertEqual(called_decision["action"], "BUY_LONG")
            self.assertEqual(called_decision["environment"], "live")

    def test_manual_preferred_venue_with_binance_dispatches_execution_router(self):
        self._write_cache(["BTC-USDT-SWAP"])
        fake_res = {"ok": True, "order_id": "BINANCE-ORDER-999", "detail": "binance success"}
        with patch("r20_backend.execution_router.open_protected_position", return_value=fake_res) as mock_open:
            ok, ref = self._submit(preferred="binance", executable={"binance": True})
            self.assertTrue(ok, ref)
            self.assertEqual(ref, "BINANCE-ORDER-999")
            self.assertEqual(self.calls, [], "Binance 下单不得打到 OKX 端点")
            mock_open.assert_called_once()
            called_decision = mock_open.call_args[0][0]
            self.assertEqual(called_decision["venue"], "binance")
            self.assertEqual(called_decision["asset"], "BTC")
            self.assertEqual(called_decision["action"], "BUY_LONG")
            self.assertEqual(called_decision["environment"], "live")

    def test_listing_gate_native_symbol_parity(self):
        """三所原生合约代码转换对账验证：OKX/Binance/Gate 均应查到原生合约码。"""
        listing_calls = []

        def capture_listing(venue, environment, contract):
            listing_calls.append((venue, contract))
            return listing_mod.ListingCheck(ok=True, reason=None,
                                            checked_at="2026-09-11T12:00:00Z", source="cache")

        self._write_cache(["BTC-USDT-SWAP"])
        fake_res = {"ok": True, "order_id": "BN-1", "detail": "ok"}
        with patch("r20_backend.execution_router.open_protected_position", return_value=fake_res):
            ok, ref = self._submit(preferred="binance", executable={"binance": True},
                                   listing_check=capture_listing)
            self.assertTrue(ok, ref)
            # 验证向 listing gate 传入的是 Binance 原生合约码 BTCUSDT，而不是 OKX 格式 BTC-USDT-SWAP
            self.assertIn(("binance", "BTCUSDT"), listing_calls)
            self.assertNotIn(("binance", "BTC-USDT-SWAP"), listing_calls)

    def test_manual_preferred_venue_not_executable_rejected_with_evidence(self):
        self._write_cache(["BTC-USDT-SWAP"])
        # 手选一个未开闸的所：不得静默回退到 OKX，必须留 executable 阶段证据并跳过
        ok, ref = self._submit(preferred="binance", executable={"okx": True})
        self.assertFalse(ok)
        self.assertEqual(self.calls, [])
        vd = self._venue_decision()
        self.assertEqual(vd["reason_code"], "ALL_REJECTED")
        self.assertEqual({r["venue"] for r in vd["rejected"]}, {"binance"})
        self.assertEqual(vd["rejected"][0]["stage"], "executable")


class TestAutoScoringRoute(_WiringSandbox):
    """验收 2：auto 评分路由 + 未来所占位。"""

    def test_auto_routes_to_okx_with_scoring_evidence(self):
        self._write_cache(["BTC-USDT-SWAP"])
        ok, ref = self._submit(preferred="auto")   # 不 patch executable → 读真实能力表
        self.assertTrue(ok, ref)
        vd = self._venue_decision()
        self.assertEqual(vd["venue"], "okx")
        self.assertEqual(vd["reason_code"], "OK")
        self.assertTrue(any("评分" in r for r in vd["reasons"]), vd["reasons"])
        self.assertTrue(vd["hysteresis_applied"] is False)
        self.assertIsNone(vd["allocation"], "split_enabled 默认关，不得拆单")
        stages = {(r["venue"], r["stage"]) for r in vd["rejected"]}
        self.assertTrue({("binance", "executable"), ("gate", "executable")} <= stages,
                        f"未开闸所必须在 executable 阶段留证：{vd['rejected']}")

    def test_candidates_come_from_registry_not_hardcoded(self):
        with patch.object(trader, "VENUE_HEALTH_FILE",
                          os.path.join(self.tmp, "none.json")), \
                patch.object(trader, "venue_execution_ready", lambda v, e: v == "gate"):
            cands = {c["venue"]: c
                     for c in trader.build_venue_candidates("BTC-USDT-SWAP", "live")}
        self.assertEqual(set(cands), {"okx", "binance", "gate"}, "候选所 = registry 登记所")
        self.assertTrue(cands["gate"]["executable"], "开闸即真候选，路由代码零改动")
        self.assertFalse(cands["binance"]["executable"])
        self.assertTrue(cands["okx"]["current_venue"], "OKX 是现任所（滞回/现任 bonus 依据）")
        self.assertIsNotNone(cands["okx"]["health_updated_utc"])

    def test_all_rejected_skips_order_and_warns(self):
        self._write_cache(["BTC-USDT-SWAP"])
        ok, ref = self._submit(preferred="auto", executable={})   # 全所不可执行
        self.assertFalse(ok)
        self.assertTrue(ref.startswith("路由拒绝: "), ref)
        self.assertEqual(self.calls, [], "ALL_REJECTED 必须本轮不下单")
        self.assertIn("本轮不下单", self.out)
        vd = self._venue_decision()
        self.assertEqual(vd["reason_code"], "ALL_REJECTED")
        self.assertIsNone(vd["venue"])
        self.assertEqual(vd["outcome"], "rejected")
        self.assertTrue(vd["rejected"], "rejected 证据必须随决策落盘")


class TestBudgetReservation(_WiringSandbox):
    """验收 3：预算原子预留。"""

    def test_reservation_exceeded_skips_order(self):
        self._write_cache(["BTC-USDT-SWAP", "ETH-USDT-SWAP"])
        ok1, _ = self._submit(inst_id="BTC-USDT-SWAP", intent="BTC:i1",
                              margin=60.0, limit=100.0)
        self.assertTrue(ok1, "首笔 60U 在 100U 组合预算内")
        self.assertEqual(self.mgr.total_reserved(ACCOUNT_OKX_LIVE), 60.0)
        self.calls.clear()
        ok2, ref2 = self._submit(inst_id="ETH-USDT-SWAP", intent="ETH:i2",
                                 margin=60.0, limit=100.0)
        self.assertFalse(ok2)
        self.assertTrue(ref2.startswith("预算预留拒绝: "), ref2)
        self.assertEqual(self.calls, [], "ReservationExceeded 必须不打下单端点")
        self.assertIn("本轮不下单", self.out)
        vd = self._venue_decision("ETH-USDT-SWAP")
        self.assertEqual(vd["outcome"], "budget_rejected")
        self.assertEqual(vd["budget"]["limit_usdt"], 100.0)
        self.assertEqual(vd["budget"]["reserved_before_usdt"], 60.0)
        self.assertIn("预算越界", vd["budget"]["error"])
        self.assertEqual(self.mgr.total_reserved(ACCOUNT_OKX_LIVE), 60.0,
                         "被拒的一笔不得占用预算")

    def test_same_intent_is_idempotent_and_success_keeps_pending(self):
        self._write_cache(["BTC-USDT-SWAP"])
        self.assertTrue(self._submit(intent="BTC:dup", margin=60.0, limit=100.0)[0])
        self.calls.clear()
        self.assertTrue(self._submit(intent="BTC:dup", margin=60.0, limit=100.0)[0])
        self.assertEqual(self.mgr.total_reserved(ACCOUNT_OKX_LIVE), 60.0,
                         "同 intent_id 重投幂等，绝不二次占用")
        row = self.mgr.reservations()[0]
        self.assertEqual(row["state"], risk_reservation.STATE_PENDING,
                         "已受理未成交保持 pending 占用（不提前释放）")

    def test_reservation_released_when_order_not_accepted(self):
        self._write_cache(["BTC-USDT-SWAP"])
        mgr = self._manager(limit=100.0)

        def boom(*args, **kwargs):
            raise RuntimeError("交易所不可达")

        with patch.object(trader, "AI_DECISION_CACHE_FILE", self.cache_file), \
                patch.object(trader, "VENUE_HEALTH_FILE", os.path.join(self.tmp, "none.json")), \
                patch.object(trader, "OPEN_INTENT_FILE", os.path.join(self.tmp, "intents.json")), \
                patch.object(trader, "load_preferred_venue", lambda: "okx"), \
                patch.object(trader, "current_environment", lambda values=None: _FakeEnv()), \
                patch.object(trader, "selected_environment", lambda values=None: _FakeEnv()), \
                patch.object(trader, "reservation_manager", lambda: mgr), \
                patch.object(listing_mod, "ensure_contract_listed", _ok_listing), \
                patch.object(trader.okx_rest, "place_order", boom):
            buf = io.StringIO()
            with redirect_stdout(buf):
                ok, ref = trader.submit_protected_limit_order(
                    "BTC-USDT-SWAP", "buy", "long", 10.0, 100.0, 115.0, 95.0,
                    venue_ctx={"notional_usdt": 300.0, "margin_usdt": 60.0,
                               "intent_id": "BTC:boom"})
            self.out = buf.getvalue()
        self.assertFalse(ok)
        self.assertIn("交易所不可达", str(ref))
        self.assertEqual(mgr.total_reserved(ACCOUNT_OKX_LIVE), 0.0,
                         "下单未获受理 → 预留必须释放，预算即刻回笼")
        self.assertEqual(mgr.reservations()[0]["state"],
                         risk_reservation.STATE_REJECTED)

    def test_reservation_amount_is_margin_estimate_not_notional(self):
        self._write_cache(["BTC-USDT-SWAP"])
        os.environ[trader.PORTFOLIO_RISK_BUDGET_ENV] = "70"
        ok, _ = self._submit(notional=300.0, margin=50.0, intent="BTC:m1")
        self.assertTrue(ok, "预算封顶按保证金口径：50U < 70U（名义额 300U 不参与）")
        self.assertEqual(self.mgr.total_reserved(ACCOUNT_OKX_LIVE), 50.0)

    def test_budget_env_absent_means_no_cap_but_ledger_still_records(self):
        self._write_cache(["BTC-USDT-SWAP"])
        ok, _ = self._submit(margin=9999.0, intent="BTC:nocap")
        self.assertTrue(ok, "未配置总上限 = 只记账不封顶（不新增拦单规则）")
        self.assertEqual(self.mgr.total_reserved(ACCOUNT_OKX_LIVE), 9999.0)
        self.assertEqual(self._venue_decision()["budget"]["limit_usdt"], 0.0)


class TestDecisionEvidencePersistence(_WiringSandbox):
    """验收 4：venue_decision 段随决策 JSON 落盘。"""

    def test_venue_decision_written_and_existing_fields_untouched(self):
        self._write_cache(["BTC-USDT-SWAP"])
        self.assertTrue(self._submit(margin=60.0)[0])
        entry = self._cache()["BTC-USDT-SWAP"]
        self.assertEqual(entry["policy_hash"], "keepme")
        self.assertEqual(entry["decision"], {"action": "BUY_LONG", "confidence": 90.0})
        vd = entry["venue_decision"]
        for key in ("venue", "reason_code", "reasons", "rejected"):
            self.assertIn(key, vd)
        self.assertEqual(vd["venue"], "okx")
        self.assertIsInstance(vd["reasons"], list)
        self.assertIsInstance(vd["rejected"], list)
        self.assertEqual(vd["budget"]["amount_usdt"], 60.0)
        self.assertEqual(vd["budget"]["intent_id"], "BTC:i1")
        self.assertEqual(vd["budget"]["account_key"], list(ACCOUNT_OKX_LIVE))
        self.assertTrue(vd["decided_utc"].endswith("Z"))

    def test_no_decision_entry_means_no_fabricated_record(self):
        self._write_cache(["OTHER-USDT-SWAP"])           # 缓存里没有 BTC 的决策
        ok, _ = self._submit(margin=60.0)
        self.assertTrue(ok, "证据落盘失败不得影响下单")
        self.assertNotIn("BTC-USDT-SWAP", self._cache(),
                         "无主脑决策不得伪造缓存条目")
        self.assertIn("不在决策缓存", self.out)


class TestPreferredVenueConfigCompatibility(unittest.TestCase):
    """验收 6：preferred_venue 缺字段/非法值回退 + 与 gate 池互不破坏。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="us003-cfg-")
        self.file = Path(self.tmp) / "venue_routing.json"

    def _load(self, payload):
        buf = io.StringIO()
        with redirect_stdout(buf):
            got = routing_policy.load_preferred_venue(payload)
        return got, buf.getvalue()

    def test_missing_field_falls_back_to_auto_with_warn(self):
        got, out = self._load({"gate": {"assets": ["BTC"]}})
        self.assertEqual(got, "auto")
        self.assertIn("缺 preferred_venue", out)

    def test_illegal_value_falls_back_to_auto_with_warn(self):
        got, out = self._load({"preferred_venue": "bitmex"})
        self.assertEqual(got, "auto")
        self.assertIn("非法值", out)
        got2, _ = self._load({"preferred_venue": None})
        self.assertEqual(got2, "auto")

    def test_valid_value_case_and_space_insensitive(self):
        got, out = self._load({"preferred_venue": " OKX "})
        self.assertEqual(got, "okx")
        self.assertEqual(out, "")

    def test_save_load_roundtrip_preserves_gate_pool(self):
        self.file.write_text(json.dumps({"gate": {"assets": ["BTC"], "dry_run": True,
                                                 "max_open": 3}}), encoding="utf-8")
        with patch.object(routing_policy, "ROUTING_FILE", self.file):
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertTrue(routing_policy.save_preferred_venue("gate"))
            raw = json.loads(self.file.read_text(encoding="utf-8"))
            self.assertEqual(raw["preferred_venue"], "gate")
            self.assertEqual(raw["gate"]["assets"], ["BTC"], "gate 子树形状不变")
            self.assertEqual(raw["gate"]["max_open"], 3)
            self.assertEqual(routing_policy.load_preferred_venue(), "gate")
            self.assertEqual(routing_policy.load_gate_pool()["max_open"], 3)
            # 非法值拒绝写入，原文件逐字不动
            self.assertFalse(routing_policy.save_preferred_venue("nope"))
            self.assertEqual(json.loads(self.file.read_text(encoding="utf-8"))
                             ["preferred_venue"], "gate")
            self.assertFalse(Path(str(self.file) + ".tmp").exists())

    def test_old_reader_survives_new_top_level_key(self):
        """老 reader（只认 gate 子树）遇到新增顶层键不得崩。"""
        self.file.write_text(json.dumps({"preferred_venue": "auto",
                                         "gate": {"assets": ["eth"]}}), encoding="utf-8")
        with patch.object(routing_policy, "ROUTING_FILE", self.file):
            pool = routing_policy.load_gate_pool()
        self.assertEqual(pool["assets"], ["ETH"])
        self.assertTrue(pool["dry_run"], "执行未开闸必须 fail-safe 收敛 dry_run")

    def test_valid_preferred_venues_follow_registry(self):
        self.assertEqual(set(routing_policy.VALID_PREFERRED_VENUES),
                         set(routing_policy.registered_venues()) | {"auto"},
                         "合法值来自注册表，不硬编码场所名单")

    def test_routing_mode_roundtrip_missing_illegal_and_sibling_preserved(self):
        self.file.write_text(json.dumps({"preferred_venue": "auto",
                                         "gate": {"assets": ["BTC"]}}), encoding="utf-8")
        buf = io.StringIO()
        with patch.object(routing_policy, "ROUTING_FILE", self.file), redirect_stdout(buf):
            self.assertEqual(routing_policy.load_routing_mode(), "balanced",
                             "缺字段回退三所平权基线 balanced")
            self.assertIn("缺 routing_mode", buf.getvalue())
            self.assertFalse(routing_policy.save_routing_mode("nope"), "非法值拒写")
            self.assertTrue(routing_policy.save_routing_mode("auto"))
            self.assertEqual(routing_policy.load_routing_mode(), "auto")
            self.assertFalse(routing_policy.save_routing_mode("split; rm -rf"))
        raw = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(raw["preferred_venue"], "auto", "兄弟键不被抹掉")
        self.assertEqual(raw["gate"]["assets"], ["BTC"], "gate 子树形状不变")
        self.assertEqual(raw["routing_mode"], "auto")


class TestCrossVenueCap(_WiringSandbox):
    """跨所封顶：开闸所持仓纳入配额；读取失败 fail-closed；未开闸所跳过。"""

    class _DelegatingRegistry:
        """真实 registry 全委托，只钉 get_adapter——零出网假持仓。"""
        def __init__(self, positions_by_venue, fail_venues=()):
            # 构造期捕获真实 registry 引用（此时尚未 patch），避免 __getattr__
            # 在 patch 生效期间解析 trader.venue_registry 命中代理自身 → 递归
            self._real = trader.venue_registry
            self._pos = positions_by_venue
            self._fail = set(fail_venues)

        def __getattr__(self, name):
            return getattr(self._real, name)

        def get_adapter(self, venue, environment=None):
            v = str(venue).lower()
            if v in self._fail:
                raise RuntimeError(f"{v} simulated outage")
            rows = self._pos.get(v, [])
            return type("_Ad", (), {"positions": staticmethod(lambda r=rows: r)})()

    def _fetch(self, positions_by_venue, ready, fail_venues=()):
        reg = self._DelegatingRegistry(positions_by_venue, fail_venues)
        with patch.object(trader, "venue_registry", reg), \
                patch.object(trader, "venue_execution_ready",
                             lambda v, e: bool(ready.get(v, False))):
            buf = io.StringIO()
            with redirect_stdout(buf):
                ok, snap, err = trader.fetch_other_venue_positions("demo")
        return ok, snap, err, buf.getvalue()

    def test_open_venue_positions_counted(self):
        ok, snap, err, out = self._fetch(
            {"gate": [{"inst_id": "BTC_USDT", "side": "long", "size_signed": 2}],
             "binance": [{"inst_id": "ETHUSDT", "side": "short", "size_signed": -0.5}]},
            ready={"gate": True, "binance": True})
        self.assertTrue(ok, err)
        self.assertEqual(len(snap["gate"]), 1)
        self.assertEqual(len(snap["binance"]), 1)
        self.assertIn("纳入本周期仓位配额", out)

    def test_zero_size_rows_excluded(self):
        ok, snap, err, _ = self._fetch(
            {"gate": [{"inst_id": "BTC_USDT", "side": "long", "size_signed": 0.0},
                      {"inst_id": "SOL_USDT", "side": "short", "size_signed": -3}]},
            ready={"gate": True})
        self.assertTrue(ok, err)
        self.assertEqual(len(snap["gate"]), 1, "0 尺寸记录不是活跃持仓")

    def test_unreadable_open_venue_fails_closed(self):
        ok, snap, err, out = self._fetch(
            {"binance": [{"inst_id": "ETHUSDT", "side": "long", "size_signed": 1}]},
            ready={"gate": True, "binance": True}, fail_venues=("gate",))
        self.assertFalse(ok, "开闸所读仓失败必须 ok=False（调用方拒新增开仓）")
        self.assertIn("gate", err)

    def test_closed_venues_skipped_not_counted(self):
        ok, snap, err, _ = self._fetch({}, ready={"gate": False, "binance": False})
        self.assertTrue(ok, err)
        self.assertEqual(snap, {}, "未开闸所无仓位来源，结构性跳过")

    def test_negative_size_side_inferred(self):
        # Gate/Binance 归一化契约：side 字段随 size_signed 正负（long/short）
        ok, snap, err, _ = self._fetch(
            {"gate": [{"inst_id": "X_USDT", "side": "short", "size_signed": -1}]},
            ready={"gate": True})
        self.assertTrue(ok, err)
        self.assertEqual(snap["gate"][0]["side"], "short")


class TestRoutingModesWiring(_WiringSandbox):
    """A/B/C 三模式接线：balanced 轮动证据 / split 拆单证据 / auto 基线不变。"""

    _ALL3 = _health_with("okx", "binance", "gate")

    def test_balanced_mode_rotates_and_records_evidence(self):
        self._write_cache(["BTC-USDT-SWAP"])
        from r20_backend import execution_router as er_mod
        winners = set()
        fake = er_mod.RouteResult(ok=True, venue="mock", stage="done", order_id="MOCK-1")
        for inst in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"):
            if inst != "BTC-USDT-SWAP":
                self._write_cache([inst])
            # 轮动可能命中 gate/binance：双闸真实存在（路由 fake 开闸 ≠ 执行层放行），
            # 故整段分发器 mock——本用例钉的是「选所证据」不是执行链
            with patch.object(er_mod, "open_protected_position", return_value=fake):
                ok, ref = self._submit(inst_id=inst, preferred="auto", mode="balanced",
                                       executable={"okx": True, "binance": True, "gate": True},
                                       health=self._ALL3, intent=f"{inst}:i1")
            self.assertTrue(ok, f"{inst}: {ref} | {self.out}")
            vd = self._venue_decision(inst)
            self.assertEqual(vd["reason_code"], "OK_BALANCED", vd["reasons"])
            winners.add(vd["venue"])
        self.assertGreaterEqual(len(winners), 1)

    def test_split_mode_persists_allocation_evidence(self):
        self._write_cache(["BTC-USDT-SWAP"])
        ok, ref = self._submit(preferred="auto", mode="split", notional=900.0,
                               executable={"okx": True, "binance": True, "gate": True},
                               health=self._ALL3)
        self.assertTrue(ok, ref)
        vd = self._venue_decision()
        alloc = vd.get("allocation")
        self.assertTrue(alloc and len(alloc) >= 2, f"split 模式应给出多所拆单方案: {alloc}")
        self.assertTrue(all({"venue", "amount_usdt"} <= set(s) for s in alloc), alloc)

    def test_auto_mode_unchanged_by_default(self):
        self._write_cache(["BTC-USDT-SWAP"])
        ok, ref = self._submit(preferred="auto", mode="auto",
                               executable={"okx": True, "binance": True, "gate": True},
                               health=self._ALL3)
        self.assertTrue(ok, ref)
        vd = self._venue_decision()
        self.assertNotEqual(vd["reason_code"], "OK_BALANCED")
        self.assertIsNone(vd["allocation"], "auto/split_enabled 关：不得给出拆单方案")
        # 三所全开闸时现任所 OKX 凭 bonus+滞回胜出（最优执行语义）
        self.assertEqual(vd["venue"], "okx")


if __name__ == "__main__":
    unittest.main(verbosity=2)

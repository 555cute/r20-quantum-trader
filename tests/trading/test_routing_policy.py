"""选所路由与预算原子预留（trader/routing_policy.py）收口 —— 第 313 刀。

本模块是**下单前的最后一道决策闸**：选所 → 接线校验 → 预算预留，任何一步不过就
`ok=False`（调用方本轮不下单）。它没有专属的行为测试文件（既有引用者都是审计门、
接线测试与抽取门），所以本刀补一个。

钉的两条主线：

1. **每一处失败都必须 fail-closed 且可解释** —— 手选场所没登记、场所没下单实现、
   预留层不可用、跨所总预算超限、预留被拒、预留层异常：六条路径各自有独立的
   `outcome` / `skip_reason` / `error`，并一律**落盘 `venue_decision` 证据**。
   静默放行或静默跳过都会让"本轮为什么没下单"事后无法复盘。
2. **跨所合算总预算的幂等豁免** —— 同 `(account_key, intent)` 重提不是新增占用，
   否则网络重试会被总闸误杀（这条在源码注释里写得很细）。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.trader import routing_policy as rp  # noqa: E402


class _ReservationExceeded(Exception):
    pass


class _FakeRiskReservation:
    ReservationExceeded = _ReservationExceeded


class _Mgr:
    def __init__(self, *, gross=0.0, rows=None, rows_exc=None, reserve_exc=None,
                 record=None):
        self.gross = gross
        self._rows = rows or []
        self.rows_exc = rows_exc
        self.reserve_exc = reserve_exc
        self.record = record if record is not None else {"state": "pending"}
        self.reserved: list = []

    def gross_exposure(self, environment):
        return self.gross

    def reservations(self, account_key):
        if self.rows_exc is not None:
            raise self.rows_exc
        return self._rows

    def reserve(self, account_key, intent, amount, state=None):
        if self.reserve_exc is not None:
            raise self.reserve_exc
        self.reserved.append((account_key, intent, amount, state))
        return self.record


class _Router:
    def __init__(self, decision):
        self.decision = decision
        self.calls: list = []

    def RouterConfig(self, **kw):        # noqa: N802 - 模拟真实类名
        self.config = SimpleNamespace(**kw)
        return self.config

    def route_signal(self, signal, candidates, budget_view=None, config=None):
        self.calls.append({"signal": signal, "candidates": candidates,
                           "budget_view": budget_view, "config": config})
        return self.decision


def _decision(venue="okx", reason_code="SELECTED", rejected=None, reasons=None,
              hysteresis_applied=False, allocation=None):
    return SimpleNamespace(venue=venue, reason_code=reason_code,
                           rejected=rejected or [], reasons=reasons or [],
                           hysteresis_applied=hysteresis_applied,
                           allocation=allocation)


class _Harness(unittest.TestCase):
    def setUp(self):
        self.preferred = "auto"
        self.candidates = [{"venue": "okx", "current_venue": True, "executable": True}]
        self.budget_total = 0.0
        self.decision = _decision()
        self.mgr = _Mgr()
        self.mgr_exc = None
        self.persisted: list = []
        self.printed: list = []
        self.router = _Router(self.decision)

    def _factory(self):
        if self.mgr_exc is not None:
            raise self.mgr_exc
        return self.mgr

    def _persist(self, inst_id, payload):
        self.persisted.append((inst_id, payload))

    def _run(self, **over):
        p = patch.object(rp, "print", lambda *a, **k: self.printed.append(" ".join(map(str, a))))
        p.start()
        self.addCleanup(p.stop)
        kw = {
            "_decision_payload": rp._decision_payload,
            "_rejection_focus_reason": rp._rejection_focus_reason,
            "build_venue_candidates": lambda inst, env: list(self.candidates),
            "estimate_margin_usdt": rp.estimate_margin_usdt,
            "load_preferred_venue": lambda: self.preferred,
            "load_routing_mode": lambda: "auto",
            "persist_venue_decision": self._persist,
            "portfolio_budget_guard": rp.portfolio_budget_guard,
            "portfolio_risk_budget_usdt": lambda: self.budget_total,
            "reservation_manager": self._factory,
            "VENUE_SUBMITTERS": {"okx": "submitter"},
            "current_environment": lambda: SimpleNamespace(mode="live", fingerprint="FP"),
            "risk_reservation": _FakeRiskReservation,
            "venue_router": self.router,
        }
        # 位置参数与注入缝必须分开：混在一起会
        # `TypeError: got multiple values for argument 'size'`
        positional = {"side": "buy", "size": 1.0, "price": 100.0,
                      "notional_usdt": 0.0, "margin_usdt": 0.0, "intent_id": ""}
        for field in ("side", "size", "price", "notional_usdt", "margin_usdt", "intent_id"):
            if field in over:
                positional[field] = over.pop(field)
        # ★ router 必须**每次新建** —— `_Router` 会快照 decision，
        #   setUp 里建一次的话，用例改 `self.decision` 就不生效了
        kw.update(over)          # 注入缝的覆盖（harness 字段已在上面摘掉）
        self.router = _Router(self.decision)
        kw["venue_router"] = self.router
        return rp.route_and_reserve_signal(
            "BTC-USDT-SWAP", positional["side"], positional["size"], positional["price"],
            notional_usdt=positional["notional_usdt"],
            margin_usdt=positional["margin_usdt"],
            intent_id=positional["intent_id"], **kw)


class PortfolioRiskBudgetTests(unittest.TestCase):
    def _budget(self, raw):
        with patch.dict(os.environ, {"R20_X": raw}):
            return rp.portfolio_risk_budget_usdt(PORTFOLIO_RISK_BUDGET_ENV="R20_X")

    def test_valid_value_is_parsed(self):
        self.assertEqual(self._budget("750.5"), 750.5)

    def test_negative_value_is_clamped_to_zero(self):
        self.assertEqual(self._budget("-100"), 0.0)

    def test_unset_variable_is_zero(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("R20_X", None)
            self.assertEqual(rp.portfolio_risk_budget_usdt(PORTFOLIO_RISK_BUDGET_ENV="R20_X"),
                             0.0)

    def test_empty_string_is_zero(self):
        self.assertEqual(self._budget(""), 0.0)

    def test_unparsable_value_is_zero_not_a_crash(self):
        # ★ 第 64 行 —— 后台风控页填了非法字符不许把整轮交易打挂
        for raw in ("abc", "1,000", "--"):
            with self.subTest(raw=raw):
                self.assertEqual(self._budget(raw), 0.0)


class EstimateMarginTests(unittest.TestCase):
    def test_explicit_margin_wins(self):
        self.assertEqual(rp.estimate_margin_usdt(1000.0, 250.0), 250.0)

    def test_falls_back_to_one_third_of_notional(self):
        self.assertEqual(rp.estimate_margin_usdt(900.0), 300.0)

    def test_zero_or_negative_margin_falls_back(self):
        self.assertEqual(rp.estimate_margin_usdt(900.0, 0.0), 300.0)
        self.assertEqual(rp.estimate_margin_usdt(900.0, -5.0), 300.0)

    def test_negative_notional_is_clamped(self):
        self.assertEqual(rp.estimate_margin_usdt(-100.0), 0.0)

    def test_unparsable_inputs_are_treated_as_zero(self):
        self.assertEqual(rp.estimate_margin_usdt(None), 0.0)


class RejectionFocusTests(unittest.TestCase):
    """ALL_REJECTED 时挑「最该解释本次跳过」的那条理由。"""

    def _focus(self, rejected, candidates=(), preferred="auto"):
        return rp._rejection_focus_reason(_decision(rejected=rejected), list(candidates),
                                          preferred)

    def test_manual_preferred_venue_wins(self):
        rejected = [{"venue": "okx", "stage": "executable", "reason": "okx 原因"},
                    {"venue": "gate", "stage": "listing", "reason": "gate 原因"}]
        self.assertEqual(self._focus(rejected, preferred="gate"), "gate 原因")

    def test_current_venue_is_second_priority(self):
        rejected = [{"venue": "okx", "stage": "listing", "reason": "okx"},
                    {"venue": "gate", "stage": "listing", "reason": "gate"}]
        candidates = [{"venue": "gate", "current_venue": True}]
        self.assertEqual(self._focus(rejected, candidates), "gate")

    def test_unmatched_venue_is_skipped_via_continue(self):
        # ★ 第 107 行 `continue` —— wanted 里排在前面的场所**没有淘汰记录**时要继续找下一个
        rejected = [{"venue": "okx", "stage": "listing", "reason": "okx 原因"}]
        candidates = [{"venue": "gate", "current_venue": True}, {"venue": "okx"}]
        self.assertEqual(self._focus(rejected, candidates), "okx 原因")

    def test_substantive_stage_beats_the_executable_stage(self):
        # 「未开闸只是结构性事实，listing/precision/freshness 才是真因」
        # ⚠️ 这条**只在该场所落进 `wanted` 时才成立** ⇒ 必须给一个匹配的 wanted
        rejected = [{"venue": "okx", "stage": "executable", "reason": "未开闸"},
                    {"venue": "okx", "stage": "listing", "reason": "未上架"}]
        self.assertEqual(self._focus(rejected, preferred="okx"), "未上架")

    def test_only_executable_stage_available_is_used(self):
        rejected = [{"venue": "okx", "stage": "executable", "reason": "未开闸"}]
        self.assertEqual(self._focus(rejected, preferred="okx"), "未开闸")

    def test_the_blind_fallback_does_not_apply_the_substantive_preference(self):
        # ★ 实测细节：wanted 为空（preferred=auto 且无候选）时**直接跳到第 110 行**，
        #   那里的兜底是 `rows[0]` —— **不**再做"实质性阶段优先"的挑拣。
        #   于是同一批记录会因"有没有匹配的 wanted"给出**不同**的理由。
        rejected = [{"venue": "okx", "stage": "executable", "reason": "未开闸"},
                    {"venue": "okx", "stage": "listing", "reason": "未上架"}]
        self.assertEqual(self._focus(rejected), "未开闸")
        self.assertEqual(self._focus(rejected, preferred="okx"), "未上架")

    def test_falls_back_to_the_first_record_when_no_venue_matches(self):
        # ★ 第 110 行 —— wanted 全部落空 ⇒ 退回第一条记录
        rejected = [{"venue": "other", "stage": "listing", "reason": "兜底原因"}]
        self.assertEqual(self._focus(rejected, preferred="gate"), "兜底原因")

    def test_falls_back_to_a_placeholder_when_there_are_no_records_at_all(self):
        self.assertEqual(self._focus([]), "无候选所")

    def test_missing_reason_does_not_produce_the_string_None(self):
        # `or ""` / `or "无候选所"` —— 不许把 None 变成字面量 "None"
        self.assertEqual(self._focus([{"venue": "other"}]), "无候选所")
        # 命中 wanted 但理由为空 ⇒ 空串（不是 "None"、也不是占位语）
        self.assertEqual(self._focus([{"venue": "okx"}], preferred="okx"), "")


class PortfolioBudgetGuardTests(unittest.TestCase):
    def test_zero_budget_means_no_cap(self):
        self.assertIsNone(rp.portfolio_budget_guard(0.0, 999.0, 999.0))
        self.assertIsNone(rp.portfolio_budget_guard(-5.0, 999.0, 999.0))

    def test_within_budget_passes(self):
        self.assertIsNone(rp.portfolio_budget_guard(1000.0, 500.0, 400.0))

    def test_exactly_at_the_budget_passes(self):
        self.assertIsNone(rp.portfolio_budget_guard(1000.0, 500.0, 500.0))

    def test_over_budget_is_rejected_with_the_numbers_in_the_message(self):
        # 文案是**逐项**列出已占 / 本笔 / 总额，不含合计
        reason = rp.portfolio_budget_guard(1000.0, 800.0, 300.0, "live")
        self.assertIn("已占 800.00U", reason)
        self.assertIn("本笔 300.00U", reason)
        self.assertIn("总预算 1000.00U", reason)
        self.assertIn("live", reason)

    def test_float_tolerance_is_one_e_minus_nine(self):
        self.assertIsNone(rp.portfolio_budget_guard(1000.0, 0.0, 1000.0 + 1e-10))
        self.assertIsNotNone(rp.portfolio_budget_guard(1000.0, 0.0, 1000.0 + 1e-6))

    def test_zero_margin_never_trips_the_guard(self):
        self.assertIsNone(rp.portfolio_budget_guard(1000.0, 9999.0, 0.0))

    def test_unparsable_numbers_fail_closed(self):
        reason = rp.portfolio_budget_guard("junk", 0.0, 0.0)
        self.assertIn("不可解析", reason)
        self.assertIn("fail-closed", reason)


class RouteAndReserveTests(_Harness, unittest.TestCase):
    def test_success_returns_the_reservation_handle(self):
        out = self._run()
        self.assertTrue(out["ok"])
        self.assertEqual(out["venue"], "okx")
        self.assertIs(out["reservation"]["manager"], self.mgr)
        self.assertEqual(out["reservation"]["account_key"], ("okx", "live", "FP"))
        # `estimate_margin_usdt` 把保证金**取整到 4 位**（不是裸的 100/3）
        self.assertEqual(out["reservation"]["amount_usdt"], round(100.0 / 3.0, 4))
        self.assertEqual(len(self.persisted), 1)

    def test_signal_payload_is_built_from_the_arguments(self):
        self._run(notional_usdt=0.0, size=2.0, price=50.0)
        signal = self.router.calls[0]["signal"]
        self.assertEqual(signal["inst_id"], "BTC-USDT-SWAP")
        self.assertEqual(signal["symbol_canonical"], "BTC")
        self.assertEqual(signal["side"], "long")
        self.assertEqual(signal["size_usdt"], 100.0)

    def test_sell_side_is_normalized_to_short(self):
        for side in ("sell", "SHORT", "short"):
            with self.subTest(side=side):
                self._run(side=side)
                self.assertEqual(self.router.calls[0]["signal"]["side"], "short")

    def test_explicit_notional_wins_over_size_times_price(self):
        self._run(notional_usdt=1234.0, size=2.0, price=50.0)
        self.assertEqual(self.router.calls[0]["signal"]["size_usdt"], 1234.0)

    def test_budget_view_is_explicitly_none(self):
        # 路由层**不重复执行**预算硬筛（口径=保证金，与 notional 混用会双重误杀）
        self._run()
        self.assertIsNone(self.router.calls[0]["budget_view"])

    def test_split_mode_enables_split_generation(self):
        self._run(load_routing_mode=lambda: "split")
        self.assertTrue(self.router.calls[0]["config"].split_enabled)

    def test_non_split_modes_disable_split_generation(self):
        for mode in ("auto", "balanced"):
            with self.subTest(mode=mode):
                self._run(load_routing_mode=lambda m=mode: m)
                self.assertFalse(self.router.calls[0]["config"].split_enabled)

    # ---- 六条 fail-closed 路径 -------------------------------------------
    def test_manual_venue_not_in_registry_gets_a_synthetic_candidate(self):
        # ★ 第 177 行：手选场所没登记 ⇒ 造一个**不可执行**候选，让 route_signal 照常
        #   产出 executable/listing 的 rejected 证据（可解释不因手选而失效）
        self.preferred = "gate"
        self.decision = _decision(venue=None, reason_code="ALL_REJECTED",
                                  rejected=[{"venue": "gate", "stage": "listing",
                                             "reason": "未登记"}])
        out = self._run()
        candidates = self.router.calls[0]["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["venue"], "gate")
        self.assertFalse(candidates[0]["executable"])
        self.assertFalse(candidates[0]["current_venue"])
        self.assertIsNone(candidates[0]["health_updated_utc"])
        self.assertEqual(candidates[0]["environment"], "live")
        self.assertFalse(out["ok"])
        self.assertIn("未登记", out["error"])

    def test_manual_venue_in_registry_filters_the_candidate_list(self):
        self.preferred = "gate"
        self.candidates = [{"venue": "okx"}, {"venue": "gate", "executable": True}]
        self._run()
        self.assertEqual([c["venue"] for c in self.router.calls[0]["candidates"]], ["gate"])

    def test_rejected_route_persists_evidence_and_returns_not_ok(self):
        self.decision = _decision(venue=None, reason_code="ALL_REJECTED",
                                  rejected=[{"venue": "okx", "stage": "listing",
                                             "reason": "未上架"}])
        out = self._run()
        self.assertFalse(out["ok"])
        self.assertIn("未上架", out["error"])
        self.assertIsNone(out["venue"])
        self.assertEqual(self.persisted[0][1]["outcome"], "rejected")
        self.assertIn("ALL_REJECTED", self.persisted[0][1]["skip_reason"])

    def test_no_candidates_is_also_rejected(self):
        self.decision = _decision(venue=None, reason_code="NO_CANDIDATES")
        out = self._run()
        self.assertFalse(out["ok"])
        self.assertIn("无候选所", out["error"])

    def test_venue_without_a_registered_submitter_fails_closed(self):
        self.decision = _decision(venue="binance")
        out = self._run()
        self.assertFalse(out["ok"])
        self.assertIn("未登记下单实现", out["error"])
        self.assertEqual(out["venue"], "binance")
        self.assertIsNone(out["decision"]["executed_venue"])

    def test_unavailable_reservation_layer_fails_closed_before_routing_out(self):
        # ★ 第 190/191 + 229 行：`reservation_manager()` 抛 ⇒ mgr=None ⇒ 选中所之后仍拒绝
        self.mgr_exc = RuntimeError("预留库打不开")
        out = self._run()
        self.assertFalse(out["ok"])
        self.assertIn("预留层不可用", out["error"])
        self.assertIn("fail-closed", out["error"])
        self.assertIsNone(out["reservation"])
        self.assertEqual(len(self.persisted), 1, "拒绝也要落盘证据")
        self.assertTrue(any("预留层不可用" in line for line in self.printed))

    def test_gross_exposure_is_not_even_read_when_the_budget_is_zero(self):
        self.budget_total = 0.0
        calls = []
        self.mgr.gross_exposure = lambda env: calls.append(env) or 0.0
        self._run()
        self.assertEqual(calls, [], "0=无顶 ⇒ 不必查跨所占用")

    def test_gross_exposure_is_read_when_the_budget_is_positive(self):
        self.budget_total = 1000.0
        calls = []
        self.mgr.gross_exposure = lambda env: calls.append(env) or 100.0
        self._run()
        self.assertEqual(calls, ["live"])

    def test_portfolio_budget_exceeded_persists_a_budget_block(self):
        self.budget_total = 100.0
        self.mgr.gross = 90.0
        out = self._run()
        self.assertFalse(out["ok"])
        self.assertEqual(out["decision"]["outcome"], "portfolio_budget_exceeded")
        budget = out["decision"]["budget"]
        self.assertEqual(budget["limit_usdt"], 100.0)
        self.assertEqual(budget["reserved_before_usdt"], 90.0)
        self.assertEqual(budget["gross_exposure_before_usdt"], 90.0)

    def test_prior_reservation_for_the_same_intent_is_credited_back(self):
        # ★ 幂等豁免：同 (account_key, intent) 重提不是新增占用，否则重试会被总闸误杀
        self.budget_total = 100.0
        self.mgr.gross = 90.0
        self.mgr._rows = [{"intent_id": "INT-1", "amount_usdt": 40.0}]
        out = self._run(intent_id="INT-1")
        self.assertTrue(out["ok"], "扣回已占 40U 后 50U+33.3U 应放行")

    def test_a_different_intent_does_not_get_the_credit(self):
        self.budget_total = 100.0
        self.mgr.gross = 90.0
        self.mgr._rows = [{"intent_id": "OTHER", "amount_usdt": 40.0}]
        out = self._run(intent_id="INT-1")
        self.assertFalse(out["ok"])

    def test_only_the_first_matching_reservation_is_credited(self):
        self.budget_total = 100.0
        self.mgr.gross = 90.0
        self.mgr._rows = [{"intent_id": "INT-1", "amount_usdt": 90.0},
                          {"intent_id": "INT-1", "amount_usdt": 90.0}]
        # 只取第一条（break）⇒ 扣 90 ⇒ 只用 33.3 ⇒ 放行
        self.assertTrue(self._run(intent_id="INT-1")["ok"])

    def test_reservations_lookup_failure_is_swallowed(self):
        # ★ 第 248 行 `pass` —— 查不到历史占用不许拦轮（宁可保守按不豁免算）
        self.budget_total = 100.0
        self.mgr.gross = 0.0
        self.mgr.rows_exc = RuntimeError("读表失败")
        out = self._run(intent_id="INT-1")
        self.assertTrue(out["ok"])

    def test_reservation_exceeded_maps_to_budget_rejected(self):
        self.mgr.reserve_exc = _ReservationExceeded("超出子闸")
        out = self._run()
        self.assertFalse(out["ok"])
        self.assertIn("预算预留拒绝", out["error"])
        self.assertEqual(out["decision"]["outcome"], "budget_rejected")
        self.assertIn("超出子闸", out["decision"]["budget"]["error"])

    def test_unexpected_reservation_error_maps_to_budget_error(self):
        self.mgr.reserve_exc = ValueError("字段缺失")
        out = self._run()
        self.assertFalse(out["ok"])
        self.assertEqual(out["decision"]["outcome"], "budget_error")
        self.assertIn("字段缺失", out["decision"]["budget"]["error"])
        self.assertTrue(any("预留层异常" in line for line in self.printed))

    def test_reservation_exceeded_is_not_caught_by_the_generic_handler(self):
        # 子类关系：`ReservationExceeded` 必须在**前一个** except 里被接住
        self.assertTrue(issubclass(_ReservationExceeded, Exception))
        self.mgr.reserve_exc = _ReservationExceeded("x")
        self.assertEqual(self._run()["decision"]["outcome"], "budget_rejected")

    def test_intent_id_defaults_to_a_time_based_string(self):
        self._run()
        key = self.mgr.reserved[0][1]
        self.assertTrue(key.startswith("BTC-USDT-SWAP:buy:"))

    def test_explicit_intent_id_is_used_verbatim(self):
        self._run(intent_id="MY-INTENT")
        self.assertEqual(self.mgr.reserved[0][1], "MY-INTENT")

    def test_reserve_is_called_with_pending_state(self):
        self._run()
        self.assertEqual(self.mgr.reserved[0][3], "pending")

    def test_success_payload_records_the_record_state(self):
        self.mgr.record = {"state": "pending"}
        out = self._run()
        self.assertEqual(out["decision"]["budget"]["state"], "pending")

    def test_non_dict_record_does_not_crash(self):
        self.mgr.record = "not-a-dict"
        out = self._run()
        self.assertIsNone(out["decision"]["budget"]["state"])
        self.assertTrue(out["ok"])

    def test_success_print_mentions_the_manual_preference(self):
        self.preferred = "okx"
        self._run()
        self.assertTrue(any("手选优先 okx" in line for line in self.printed))
        self.assertTrue(any("预留保证金估算" in line for line in self.printed))

    def test_decision_payload_is_persisted_on_every_path(self):
        for setup in ("ok", "rejected", "no_submitter", "mgr_none"):
            with self.subTest(setup=setup):
                self.persisted.clear()
                self.decision = _decision()
                self.mgr_exc = None
                if setup == "rejected":
                    self.decision = _decision(venue=None, reason_code="ALL_REJECTED")
                elif setup == "no_submitter":
                    self.decision = _decision(venue="binance")
                elif setup == "mgr_none":
                    self.mgr_exc = RuntimeError("x")
                self._run()
                self.assertEqual(len(self.persisted), 1, setup)


class DecisionPayloadTests(unittest.TestCase):
    def test_payload_carries_every_documented_field(self):
        decision = _decision(venue="gate", reason_code="SELECTED",
                             rejected=[{"venue": "okx", "reason": "r"}],
                             reasons=["a"], hysteresis_applied=True,
                             allocation={"okx": 0.6})
        payload = rp._decision_payload(decision, "auto")
        self.assertEqual(payload["preferred_venue"], "auto")
        self.assertEqual(payload["venue"], "gate")
        self.assertEqual(payload["reason_code"], "SELECTED")
        self.assertEqual(payload["reasons"], ["a"])
        self.assertEqual(payload["rejected"], [{"venue": "okx", "reason": "r"}])
        self.assertTrue(payload["hysteresis_applied"])
        self.assertEqual(payload["allocation"], {"okx": 0.6})
        self.assertTrue(payload["decided_utc"].endswith("Z"))

    def test_rejected_records_are_copied_not_aliased(self):
        source = [{"venue": "okx"}]
        payload = rp._decision_payload(_decision(rejected=source), "auto")
        source[0]["venue"] = "mutated"
        self.assertEqual(payload["rejected"], [{"venue": "okx"}])

    def test_missing_lists_and_flags_are_normalized(self):
        payload = rp._decision_payload(SimpleNamespace(
            venue="okx", reason_code="R", rejected=None, reasons=None,
            hysteresis_applied=0, allocation=None), "auto")
        self.assertEqual(payload["reasons"], [])
        self.assertEqual(payload["rejected"], [])
        self.assertIs(payload["hysteresis_applied"], False)


if __name__ == "__main__":
    unittest.main()

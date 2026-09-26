"""统一执行路由·开仓侧：**多空几何是唯一尺、开仓必须双腿回读、缺一步就回滚**（第二百八十一刀，开新面 execution_router.py）。

先打印整个文件（720 行）再动笔。这是把大模型决策 JSON 变成场所原生受保护开仓的**总入口**，
文件头的四条铁律就是本刀要钉的东西。

| 语义 | 口径 |
|---|---|
| ★ **分发前物理校验不可绕过** | 数值有限且为正 → 杠杆两端夹取 → 保证金夹取 → **跨所敞口闸门** → TP 宽度平滑 → `validate_quote_geometry_and_rr` 单一事实源；任一步 Fail ⇒ `ok=False` + 明确 `stage` |
| ★ **开仓必须 100% 云端保护覆盖** | TP/SL 双腿挂成**并回读命中**才算成功；回读未见任一条 ⇒ `RuntimeError` ⇒ 走回滚 |
| ★ **回滚是双段清理** | ①已知 `legs` 逐腿 best-effort 撤；②枚举残留触发单，**只撤带 `r20` 前缀的本系统单**（用户手单绝不触碰）；③撤入场单。三段结果都写进 `detail`，撤单失败**不改判**平仓/开仓结论 |
| ★ **开闸由 registry 决定** | `require_execution` 未开闸一律抛 `ExchangeCapabilityError`（不吞、不转成 `ok=False`）；适配器自己抛的 `ExchangeCapabilityError` 也必须**原样冒泡**（三处都验）|
| ★ **所池门禁真的有消费者** | 审计 P1-7：`dry_run` / `assets`（空池=不发单）/ `min_confidence` / `max_open` 逐条拦下并写明原因；池**读取失败只告警**（加固不制造新阻塞点）|
| ★ **既有仓前置体检** | 探针失败 ⇒ 拒开；与调用方在管记录不符 ⇒ 拒开；无 `own_position` 时**不再默认宣称"外部仓"**，而是按台账判 `own/stale_closed/mismatch/ledger_unavailable` 并**在结果里写明判定** |
| ★ **跨所敞口说实话** | 敞口闸门拒开时，`detail` 必须追加"统计范围=…，未计=…"，并把这批场所写进 `counted_venues`/`skipped_venues`（风控名叫"跨所"就得让运维看见跨到了哪几所）|
| ★ **持仓模式不自动切换** | 只有该适配器**真的实现了只读探测**才体检；探测值不在声明域 ⇒ 拒；`entry_ready_position_modes` 是"载荷已在真机核验过"的子集，不在其中 ⇒ 拒并给出危害说明 |
| ★ **fail-open 的只有对账** | 合约存在性对账自身异常 ⇒ 放行（增强不是风控闸门）；而敞口读取失败 ⇒ **fail-closed 拒开** |
"""

import math
import os
import types
import unittest
from unittest import mock

from r20_backend import execution_router as ER
from r20_backend.exchanges import listing, registry, routing_policy
from scripts.trader import brackets, venue_protection


def _spec(tick=0.1, ct_val=1.0):
    return types.SimpleNamespace(tick_size=tick, ct_val=ct_val, min_sz=1.0)


class _FakeAdapter:
    """可控的场所适配器替身（只实现本模块真正会用到的那几个方法）。"""

    def __init__(self, venue="binance", environment="demo", **over):
        self.capabilities = types.SimpleNamespace(
            venue=venue,
            quantity_unit=over.get("quantity_unit", ""),
            position_modes=over.get("position_modes", ()),
            entry_ready_position_modes=over.get("entry_ready_position_modes", ()),
        )
        self.environment = environment
        self.spec = over.get("spec", _spec())
        self.ticker = over.get("ticker", {"mark_price": 100.0})
        self.contracts = over.get("contracts", 10)
        self.placed = over.get("placed", {"id": "ord-1"})
        self.legs = over.get("legs", {"tp": "tp-1", "sl": "sl-1"})
        self.open_orders = over.get("open_orders",
                                    [{"id": "tp-1"}, {"id": "sl-1"}])
        self.probe_value = over.get("probe_value")
        self.strict_attach = over.get("strict_attach", False)
        self.positions_raises = over.get("positions_raises")
        self.residue = over.get("residue", [])
        self.calls = []
        self.fail = over.get("fail", {})

    # -- 探针 ---------------------------------------------------------------
    def positions(self):
        self.calls.append("positions")
        if self.positions_raises:
            raise self.positions_raises
        return list(self.rows)

    # -- 只读 ---------------------------------------------------------------
    def native_symbol(self, asset):
        return f"{asset}USDT"

    def fetch_instrument_spec(self, asset):
        return self.spec

    def fetch_ticker(self, asset):
        self.calls.append("fetch_ticker")
        return self.ticker

    def quote_qty_to_native(self, notional, price, spec):
        return self.contracts

    def list_protective_orders(self, asset):
        self.calls.append("list_protective_orders")
        return list(self.open_orders)

    # -- 写 ---------------------------------------------------------------
    def set_leverage(self, asset, leverage, margin_mode="cross"):
        self.calls.append(("set_leverage", leverage, margin_mode))

    def place_order(self, asset, side, qty, price=None):
        self.calls.append(("place_order", asset, side, qty, price))
        return self.placed

    def attach_protective_orders(self, asset, side, *, tp_px, sl_px,
                                 expiration=None, contracts=None, **kwargs):
        if self.strict_attach and kwargs:
            raise TypeError("unexpected keyword argument")
        self.calls.append(("attach", kwargs.get("position_mode")))
        return self.legs

    def cancel_order(self, asset, order_id):
        self.calls.append(("cancel_order", order_id))


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.adapter = _FakeAdapter()
        self.adapter.rows = []
        self.decision = {"asset": "BTC", "action": "BUY_LONG", "margin_usdt": 150.0,
                         "leverage": 3, "entry_price": 100.0,
                         "take_profit_price": 110.0, "stop_loss_price": 95.0}
        self._start(mock.patch.object(ER, "get_adapter",
                                      return_value=self.adapter))
        self._start(mock.patch.object(ER, "is_sandbox_environment",
                                      side_effect=lambda e: str(e) in ("demo", "sandbox")))
        self.require = self._start(mock.patch.object(ER, "require_execution"))
        self._start(mock.patch.object(ER, "canonical_base",
                                      side_effect=lambda s: str(s or "").upper()))
        self.pool = self._start(mock.patch.object(ER, "_load_venue_pool_soft",
                                                  return_value={}))
        self.geom = self._start(mock.patch.object(ER, "validate_quote_geometry_and_rr",
                                                  return_value=(True, "", 2.5)))
        self._start(mock.patch.object(ER, "_check_total_exposure", return_value=None))
        self.lev = self._start(mock.patch.object(
            ER, "_clamp_leverage",
            side_effect=lambda **kw: (kw["leverage"], kw["decision"])))
        self.mar = self._start(mock.patch.object(
            ER, "_clamp_margin",
            side_effect=lambda **kw: (kw["margin"], kw["decision"], 0.0)))
        self._start(mock.patch.object(ER, "TOTAL_EXPOSURE_CAP", 0.0))
        self._start(mock.patch.object(brackets, "clamp_take_profit_width",
                                      side_effect=lambda **kw: kw["tp_px"]))
        self.listing = self._start(mock.patch.object(
            listing, "ensure_contract_listed",
            return_value=types.SimpleNamespace(ok=True, reason="")))
        self.own = self._start(mock.patch.object(ER, "own_position_records"))

    def _open(self, venue="binance", adapter=None, pass_adapter=False, **kwargs):
        ad = adapter or self.adapter
        self.adapter = ad
        self._start(mock.patch.object(ER, "get_adapter", return_value=ad))
        decision = {**self.decision, **kwargs.pop("decision", {})}
        if venue:
            decision.setdefault("venue", venue)
        price_ref = kwargs.pop("price_ref", 100.0)
        if pass_adapter:
            kwargs["adapter"] = ad
        return ER.open_protected_position(decision, price_ref=price_ref, **kwargs)


class FailHelperTests(unittest.TestCase):
    def test_fail_shape_and_extras(self):
        out = ER._fail("precheck", "详情", venue="gate", existing_size=3.0)
        self.assertIsInstance(out, ER.RouteResult)
        self.assertIsInstance(out, dict)
        self.assertIs(out["ok"], False)
        self.assertEqual(out["venue"], "gate")
        self.assertEqual(out["stage"], "precheck")
        self.assertEqual(out["detail"], "详情")
        self.assertEqual(out["existing_size"], 3.0)

    def test_default_venue_is_gate(self):
        self.assertEqual(ER._fail("x", "y")["venue"], "gate")

    def test_route_result_is_a_dict_subclass(self):
        self.assertEqual(dict(ER.RouteResult(ok=True)), {"ok": True})


class ExposureVenuesTests(_Base):
    def setUp(self):
        super().setUp()
        self.venues = self._start(mock.patch.object(registry, "registered_venues",
                                                    return_value=["binance", "gate", "okx"]))
        self.creds = self._start(mock.patch.object(registry, "venue_credentials",
                                                   return_value=("k", "s", "p")))

    def test_always_counts_the_current_venue(self):
        counted, skipped = ER._exposure_venues("binance", "demo")
        self.assertEqual(counted, ["binance", "gate", "okx"])
        self.assertEqual(skipped, [])

    def test_venue_is_not_duplicated(self):
        counted, _ = ER._exposure_venues("gate", "demo")
        self.assertEqual(counted.count("gate"), 1)

    def test_missing_credentials_are_skipped_with_a_reason(self):
        self.creds.side_effect = lambda v, e: ("", "", "") if v == "okx" else ("k", "s", "p")
        counted, skipped = ER._exposure_venues("binance", "demo")
        self.assertEqual(counted, ["binance", "gate"])
        self.assertEqual(skipped, ["okx(凭证未配置)"])

    def test_credential_read_failure_is_skipped_with_a_reason(self):
        def _creds(v, e):
            if v == "gate":
                raise RuntimeError("密文库坏了")
            return ("k", "s", "p")

        self.creds.side_effect = _creds
        counted, skipped = ER._exposure_venues("binance", "demo")
        self.assertEqual(counted, ["binance", "okx"])
        self.assertEqual(skipped, ["gate(凭证读取失败)"])

    def test_registry_enumeration_failure_falls_back_to_the_current_venue(self):
        self.venues.side_effect = RuntimeError("注册表坏了")
        counted, skipped = ER._exposure_venues("binance", "demo")
        self.assertEqual(counted, ["binance"])
        self.assertEqual(skipped, [])

    def test_environment_is_forwarded_to_the_credential_lookup(self):
        ER._exposure_venues("binance", "live")
        for call in self.creds.call_args_list:
            self.assertEqual(call[0][1], "live")


class VenuePoolSoftTests(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.load = self._start(mock.patch.object(routing_policy, "load_venue_pool"))

    def test_dict_is_returned(self):
        self.load.return_value = {"assets": ["BTC"]}
        self.assertEqual(ER._load_venue_pool_soft("gate"), {"assets": ["BTC"]})

    def test_non_dict_becomes_an_empty_pool(self):
        self.load.return_value = ["not", "a", "dict"]
        self.assertEqual(ER._load_venue_pool_soft("gate"), {})

    def test_failure_warns_and_returns_an_empty_pool(self):
        self.load.side_effect = RuntimeError("池文件坏了")
        with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()) as out:
            self.assertEqual(ER._load_venue_pool_soft("gate"), {})
        self.assertIn("所池门禁", out.getvalue())
        self.assertIn("仅告警", out.getvalue())


class OpenValidationTests(_Base):
    def test_missing_asset_is_rejected(self):
        out = self._open(decision={"asset": "", "name": ""})
        self.assertIs(out["ok"], False)
        self.assertEqual(out["stage"], "validate")
        self.assertIn("缺少 asset", out["detail"])
        self.assertEqual(self.adapter.calls, [], "校验未过不得触网")

    def test_name_is_an_acceptable_alias_for_asset(self):
        out = self._open(decision={"asset": None, "name": "ETH"})
        self.assertIs(out["ok"], True)

    def test_non_numeric_fields_are_rejected(self):
        out = self._open(decision={"margin_usdt": "很多"})
        self.assertEqual(out["stage"], "validate")
        self.assertIn("数值字段非法", out["detail"])

    def test_non_positive_or_non_finite_numbers_are_rejected(self):
        for field in ("margin_usdt", "leverage", "entry_price"):
            for bad in (0, -1, float("inf"), float("nan")):
                with self.subTest(field=field, value=bad):
                    out = self._open(decision={field: bad})
                    self.assertEqual(out["stage"], "validate")
                    self.assertIn("必须为有限正数", out["detail"])

    def test_tp_and_sl_are_left_to_the_geometry_gate(self):
        """TP/SL 允许为 0 —— 由 `validate_quote_geometry_and_rr` 统一裁决。"""
        self.geom.return_value = (False, "缺乏止盈", 0.0)
        out = self._open(decision={"take_profit_price": 0})
        self.assertEqual(out["stage"], "risk_gate")
        self.assertIn("缺乏止盈", out["detail"])

    def test_decision_venue_wins(self):
        self.assertEqual(self._open(venue="gate")["venue"], "gate")

    def test_injected_adapter_capabilities_are_the_second_choice(self):
        """⚠️ 第 158 行读的是 `adapter` **形参**（不是解析后的 `ad`）：
        只有调用方显式注入 adapter 时，capabilities.venue 才会被采纳。"""
        adapter = _FakeAdapter(venue="okx")
        adapter.rows = []
        out = self._open(adapter=adapter, pass_adapter=True, venue=None,
                         decision={"venue": ""})
        self.assertEqual(out["venue"], "okx")

    def test_without_an_injected_adapter_the_fallback_is_gate(self):
        """生产路径不预注入 adapter ⇒ 决策里没写 venue 时一律落到 `gate`。"""
        self.assertEqual(self._open(venue=None, decision={"venue": ""})["venue"], "gate")

    def test_sandbox_environments_are_normalised_for_gate(self):
        adapter = _FakeAdapter(venue="gate")
        adapter.rows = []
        self._open(venue="gate", adapter=adapter, environment="demo")
        self.assertEqual(self.adapter.environment, "demo")

    def test_sandbox_keeps_the_current_venue_for_exposure_accounting(self):
        self._start(mock.patch.object(ER, "get_adapter",
                                      side_effect=lambda v, environment=None: _FakeAdapter(venue=v)))
        self.adapter = _FakeAdapter(venue="binance")
        self.adapter.rows = []
        get = self._start(mock.patch.object(ER, "get_adapter",
                                           return_value=self.adapter))
        ER.open_protected_position({**self.decision, "venue": "binance"},
                                   price_ref=100.0, environment="sandbox")
        self.assertEqual(get.call_args[1]["environment"], "sandbox")


class OpenLeverageMarginTests(_Base):
    def test_leverage_bounds_come_from_env_then_constants(self):
        self._start(mock.patch.object(ER, "MIN_LEVERAGE", 2.0))
        self._start(mock.patch.object(ER, "MAX_LEVERAGE", 5.0))
        self._start(mock.patch.dict("os.environ", {"R20_MIN_LEVERAGE": "3",
                                                   "R20_MAX_LEVERAGE": "4"}))
        self._open()
        self.assertEqual(self.lev.call_args[1]["min_leverage"], 3.0)
        self.assertEqual(self.lev.call_args[1]["max_leverage"], 4.0)

    def test_inverted_env_bounds_are_collapsed(self):
        self._start(mock.patch.dict("os.environ", {"R20_MIN_LEVERAGE": "9",
                                                   "R20_MAX_LEVERAGE": "4"}))
        self._open()
        self.assertEqual(self.lev.call_args[1]["min_leverage"], 4.0)
        self.assertEqual(self.lev.call_args[1]["max_leverage"], 4.0)

    def test_margin_gate_receives_the_pool_and_global_caps(self):
        self.pool.return_value = {"assets": ["BTC"]}
        self._start(mock.patch.object(ER, "MAX_SINGLE_ASSET_MARGIN", 5000.0))
        self._start(mock.patch.object(ER, "MAX_MARGIN_EQUITY_RATIO", 0.25))
        self._open(max_margin_usdt=1000.0)
        kwargs = self.mar.call_args[1]
        self.assertEqual(kwargs["pool"], {"assets": ["BTC"]})
        self.assertEqual(kwargs["max_single_asset_margin"], 5000.0)
        self.assertEqual(kwargs["max_margin_equity_ratio"], 0.25)
        self.assertEqual(kwargs["max_margin_usdt"], 1000.0)

    def test_exposure_gate_uses_the_unclamped_margin(self):
        seen = {}
        self._start(mock.patch.object(
            ER, "_check_total_exposure",
            side_effect=lambda **kw: seen.update(kw) or None))
        self.mar.side_effect = None
        self.mar.return_value = (75.0, self.decision, 0.0)
        self._open()
        self.assertEqual(seen["margin"], 150.0, "敞口要按**未夹取**的原始保证金核算")


class OpenExposureGateTests(_Base):
    def _reject(self, fail_result):
        """统计范围写在 `_pos_cache` 里，而 cache 只在 `positions_reader` 被真正调用后才有值
        ⇒ 替身闸门必须先跑一次 reader，否则范围永远是"未统计"。"""
        def _gate(**kw):
            kw["positions_reader"]()
            return fail_result

        self._start(mock.patch.object(ER, "_check_total_exposure", side_effect=_gate))
        self._start(mock.patch.object(ER, "_exposure_venues",
                                      return_value=(["binance", "gate"],
                                                    ["okx(凭证未配置)"])))

    def test_rejection_reports_the_accounting_scope(self):
        self._reject(ER._fail("exposure", "跨所同向敞口超限"))
        out = self._open()
        self.assertIs(out["ok"], False)
        self.assertEqual(out["stage"], "exposure")
        self.assertIn("统计范围=binance/gate", out["detail"])
        self.assertIn("未计=okx(凭证未配置)", out["detail"])
        self.assertEqual(out["counted_venues"], ["binance", "gate"])
        self.assertEqual(out["skipped_venues"], ["okx(凭证未配置)"])

    def test_scope_note_omits_the_skipped_clause_when_nothing_was_skipped(self):
        def _gate(**kw):
            kw["positions_reader"]()
            return ER._fail("exposure", "超限")

        self._start(mock.patch.object(ER, "_check_total_exposure", side_effect=_gate))
        self._start(mock.patch.object(ER, "_exposure_venues",
                                      return_value=(["binance"], [])))
        out = self._open()
        self.assertIn("统计范围=binance", out["detail"])
        self.assertNotIn("未计=", out["detail"])
        self.assertEqual(out["skipped_venues"], [])

    def test_scope_note_degrades_when_nothing_was_read(self):
        self._start(mock.patch.object(ER, "_check_total_exposure",
                                      return_value=ER._fail("exposure", "超限")))
        out = self._open()
        self.assertIn("统计范围=—", out["detail"])

    def test_positions_reader_is_cross_venue_and_fails_closed(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "BTC", "size_signed": 2.0}]
        ok_adapter = _FakeAdapter(venue="gate")
        ok_adapter.rows = [{"base": "ETH", "size_signed": -1.0}]
        self._start(mock.patch.object(ER, "get_adapter",
                                      side_effect=lambda v, environment=None:
                                      adapter if v == "binance" else ok_adapter))
        self._start(mock.patch.object(ER, "_exposure_venues",
                                      return_value=(["binance", "gate"], [])))
        captured = {}

        def _gate(**kw):
            captured.update(kw)
            return kw["positions_reader"]()

        self._start(mock.patch.object(ER, "_check_total_exposure", side_effect=_gate))
        self.adapter = adapter
        ER.open_protected_position({**self.decision, "venue": "binance"},
                                   price_ref=100.0)
        self.assertEqual(captured["positions_reader"](),
                         [{"base": "BTC", "size_signed": 2.0, "venue": "binance"},
                          {"base": "ETH", "size_signed": -1.0, "venue": "gate"}])

    def test_a_venue_that_cannot_be_read_raises_fail_closed(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        broken = _FakeAdapter(venue="gate", positions_raises=RuntimeError("读不到"))
        self._start(mock.patch.object(ER, "get_adapter",
                                      side_effect=lambda v, environment=None:
                                      adapter if v == "binance" else broken))
        self._start(mock.patch.object(ER, "_exposure_venues",
                                      return_value=(["binance", "gate"], [])))
        captured = {}

        def _gate(**kw):
            captured.update(kw)
            return kw["positions_reader"]()

        self._start(mock.patch.object(ER, "_check_total_exposure", side_effect=_gate))
        self.adapter = adapter
        with self.assertRaises(RuntimeError) as ctx:
            ER.open_protected_position({**self.decision, "venue": "binance"},
                                       price_ref=100.0)
        self.assertIn("跨所敞口不可核算", str(ctx.exception))
        self.assertIn("fail-closed 拒开", str(ctx.exception))

    def test_positions_are_read_once_per_open(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        self.adapter = adapter
        self._start(mock.patch.object(ER, "get_adapter", return_value=adapter))
        ER.open_protected_position({**self.decision, "venue": "binance"},
                                   price_ref=100.0)
        self.assertEqual(adapter.calls.count("positions"), 1,
                         "敞口闸门与前置体检共用一次探针")


class OpenRiskGateTests(_Base):
    def test_geometry_rejection_carries_the_rr(self):
        self.geom.return_value = (False, "盈亏比不足", 1.2)
        out = self._open()
        self.assertEqual(out["stage"], "risk_gate")
        self.assertIn("物理风控拒绝: 盈亏比不足", out["detail"])
        self.assertEqual(out["rr"], 1.2)

    def test_tp_width_is_smoothed_and_written_back(self):
        seen = {}

        def _clamp(**kw):
            seen.update(kw)
            return 108.0

        self._start(mock.patch.object(brackets, "clamp_take_profit_width",
                                      side_effect=_clamp))
        decision = {**self.decision, "atr": 1.5}
        ER.open_protected_position(decision, price_ref=100.0)
        self.assertEqual(seen["is_long"], True)
        self.assertEqual(seen["atr"], 1.5)
        self.assertEqual(seen["limit_px"], 100.0)
        self.assertEqual(decision["take_profit_price"], 108.0)

    def test_tp_smoothing_failure_is_swallowed(self):
        self._start(mock.patch.object(brackets, "clamp_take_profit_width",
                                      side_effect=RuntimeError("括号模块坏了")))
        self.assertIs(self._open()["ok"], True)

    def test_precision_is_derived_from_the_entry_string(self):
        seen = {}
        self._start(mock.patch.object(brackets, "clamp_take_profit_width",
                                      side_effect=lambda **kw: seen.update(kw) or kw["tp_px"]))
        self._open(decision={"entry_price": 100.123})
        self.assertEqual(seen["prec"], 3)

    def test_short_decisions_are_flagged_as_not_long(self):
        seen = {}
        self._start(mock.patch.object(brackets, "clamp_take_profit_width",
                                      side_effect=lambda **kw: seen.update(kw) or kw["tp_px"]))
        self._open(decision={"action": "SELL_SHORT"})
        self.assertIs(seen["is_long"], False)

    def test_geometry_uses_the_smoothed_tp(self):
        self._start(mock.patch.object(brackets, "clamp_take_profit_width",
                                      return_value=108.0))
        self._open()
        self.assertEqual(self.geom.call_args[0][2], 108.0)


class OpenExecutionGateTests(_Base):
    def test_require_execution_is_called_with_the_adapter_environment(self):
        self._open()
        self.require.assert_called_once_with("binance", environment="demo")

    def test_a_refused_gate_propagates(self):
        from r20_backend.exchanges import ExchangeCapabilityError
        self.require.side_effect = ExchangeCapabilityError("未开闸")
        with self.assertRaises(ExchangeCapabilityError):
            self._open()

    def test_missing_environment_attribute_defaults_to_live(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        del adapter.environment
        self._open(adapter=adapter)
        self.require.assert_called_once_with("binance", environment="live")


class OpenVenuePoolTests(_Base):
    def test_dry_run_blocks_the_send(self):
        self.pool.return_value = {"dry_run": True}
        out = self._open()
        self.assertEqual(out["stage"], "venue_dry_run")
        self.assertIn("dry_run=true", out["detail"])
        self.assertEqual(self.adapter.calls, [])

    def test_empty_asset_list_blocks_the_send(self):
        self.pool.return_value = {"assets": []}
        out = self._open()
        self.assertEqual(out["stage"], "venue_pool")
        self.assertIn("准入币种清单为空", out["detail"])

    def test_asset_outside_the_pool_is_rejected(self):
        self.pool.return_value = {"assets": ["eth", "sol"]}
        out = self._open()
        self.assertEqual(out["stage"], "venue_pool")
        self.assertIn("BTC 不在 BINANCE 准入币种清单（ETH, SOL）", out["detail"])

    def test_pool_assets_are_case_insensitive(self):
        self.pool.return_value = {"assets": ["btc"]}
        self.assertIs(self._open()["ok"], True)

    def test_confidence_below_the_pool_floor_is_rejected(self):
        self.pool.return_value = {"assets": ["BTC"], "min_confidence": 80}
        out = self._open(decision={"confidence": 70})
        self.assertEqual(out["stage"], "venue_pool")
        self.assertIn("决策置信度 70 低于 BINANCE 门禁 80", out["detail"])

    def test_confidence_at_or_above_the_floor_passes(self):
        self.pool.return_value = {"assets": ["BTC"], "min_confidence": 80}
        self.assertIs(self._open(decision={"confidence": 80})["ok"], True)

    def test_absent_confidence_does_not_trigger_the_floor(self):
        self.pool.return_value = {"assets": ["BTC"], "min_confidence": 80}
        self.assertIs(self._open(decision={"confidence": None})["ok"], True)
        self.assertIs(self._open(decision={"confidence": "很高"})["ok"], True)

    def test_non_finite_pool_floor_is_ignored(self):
        self.pool.return_value = {"assets": ["BTC"], "min_confidence": float("nan")}
        self.assertIs(self._open(decision={"confidence": 1})["ok"], True)

    def test_max_open_is_checked_against_the_single_probe(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "ETH", "size_signed": 1.0},
                        {"base": "SOL", "size_signed": -2.0}]
        adapter.rows.append({"base": "XRP", "size_signed": 0.0})
        self.pool.return_value = {"assets": ["BTC"], "max_open": 2}
        out = self._open(adapter=adapter)
        self.assertEqual(out["stage"], "venue_pool")
        self.assertIn("已达池上限 max_open=2", out["detail"])

    def test_max_open_not_reached_passes(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "ETH", "size_signed": 1.0}]
        self.pool.return_value = {"assets": ["BTC"], "max_open": 2}
        self.assertIs(self._open(adapter=adapter)["ok"], True)


class OpenListingTests(_Base):
    def test_listing_rejection_blocks_the_send(self):
        self.listing.return_value = types.SimpleNamespace(ok=False, reason="已下架")
        out = self._open()
        self.assertEqual(out["stage"], "listing")
        self.assertIn("合约对账拒绝: 已下架", out["detail"])

    def test_listing_failure_is_fail_open(self):
        self.listing.side_effect = RuntimeError("目录拉不到")
        self.assertIs(self._open()["ok"], True)

    def test_listing_is_asked_for_the_native_symbol(self):
        self._open()
        self.assertEqual(self.listing.call_args[0], ("binance", "demo", "BTCUSDT"))


class OpenSizingTests(_Base):
    def test_missing_spec_is_rejected(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        adapter.spec = None
        out = self._open(adapter=adapter)
        self.assertEqual(out["stage"], "specs")
        self.assertIn("无法获取 BTC 合约规格", out["detail"])

    def test_unavailable_price_is_rejected(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        adapter.ticker = {"mark_price": 0}
        out = ER.open_protected_position({**self.decision, "venue": "binance"},
                                         adapter=adapter)
        self.assertEqual(out["stage"], "price")
        self.assertIn("现价不可得，禁止盲单", out["detail"])

    def test_ticker_last_is_the_second_choice(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        adapter.ticker = {"last": 42.0}
        self.assertIs(self._open(adapter=adapter)["ok"], True)

    def test_explicit_price_ref_skips_the_ticker(self):
        self._open(price_ref=100.0)
        self.assertNotIn("fetch_ticker", self.adapter.calls)

    def test_zero_contracts_is_rejected(self):
        adapter = _FakeAdapter(venue="binance", contracts=0)
        adapter.rows = []
        out = self._open(adapter=adapter)
        self.assertEqual(out["stage"], "sizing")
        self.assertIn("不足 BINANCE 最小下单量", out["detail"])

    def test_prices_are_quantized_to_the_tick(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        adapter._ = None
        # 本用例断言的是**限价**单的报单价已被对齐到 tick ⇒ 必须钉死下单模式。
        # `R20_ORDER_MODE` 是运行期可改的运维设置（后台可切市价单，且会写 `.env`）；
        # 不钉的话，运维一切到 market，这里就会因为"市价单本就不该带 px"而红。
        with mock.patch.dict(os.environ, {"R20_ORDER_MODE": "limit"}):
            out = ER.open_protected_position({**self.decision, "venue": "binance",
                                              "entry_price": 100.1234,
                                              "take_profit_price": 110.9876,
                                              "stop_loss_price": 95.4567},
                                             adapter=adapter, price_ref=100.0)
        self.assertIs(out["ok"], True)
        place = [c for c in adapter.calls if isinstance(c, tuple) and c[0] == "place_order"]
        self.assertEqual(place[0][4], 100.1)

    def test_contract_quantities_are_integers_for_contract_units(self):
        adapter = _FakeAdapter(venue="binance", contracts=7.9)
        adapter.rows = []
        out = self._open(adapter=adapter)
        self.assertEqual(out["contracts"], 7)
        self.assertEqual(out["size_signed"], 7)

    def test_short_positions_are_negative(self):
        adapter = _FakeAdapter(venue="binance", contracts=7.9)
        adapter.rows = []
        out = self._open(adapter=adapter, decision={"action": "SELL_SHORT"})
        self.assertEqual(out["size_signed"], -7)

    def test_base_asset_units_keep_the_fraction(self):
        adapter = _FakeAdapter(venue="binance", contracts=7.9, quantity_unit="base_asset")
        adapter.rows = []
        out = self._open(adapter=adapter)
        self.assertEqual(out["contracts"], 7.9)
        self.assertEqual(out["size_signed"], 7.9)

    def test_notional_is_margin_times_leverage(self):
        out = self._open()
        self.assertEqual(out["notional_usdt"], 450.0)


class OpenPrecheckTests(_Base):
    def test_position_probe_failure_is_rejected(self):
        adapter = _FakeAdapter(venue="binance", positions_raises=RuntimeError("探针炸了"))
        out = self._open(adapter=adapter)
        self.assertEqual(out["stage"], "precheck")
        self.assertIn("既有持仓探针失败", out["detail"])

    def test_capability_error_from_the_probe_propagates(self):
        from r20_backend.exchanges import ExchangeCapabilityError
        adapter = _FakeAdapter(venue="binance",
                               positions_raises=ExchangeCapabilityError("不支持"))
        with self.assertRaises(ExchangeCapabilityError):
            self._open(adapter=adapter)

    def test_dust_positions_are_ignored(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "BTC", "size_signed": 1e-12}]
        self.assertIs(self._open(adapter=adapter)["ok"], True)

    def test_other_assets_are_ignored(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "ETH", "size_signed": 5.0}]
        self.own.classify_exchange_position.return_value = {"verdict": "untracked",
                                                            "reason": "x"}
        self.assertIs(self._open(adapter=adapter)["ok"], True)

    def test_own_position_match_allows_the_open(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "BTC", "size_signed": 2.0, "side": "long"}]
        out = self._open(adapter=adapter,
                         own_position={"size_signed": 2.0, "side": "LONG"})
        self.assertIs(out["ok"], True)

    def test_own_position_mismatch_is_rejected(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "BTC", "size_signed": 5.0, "side": "long"}]
        out = self._open(adapter=adapter,
                         own_position={"size_signed": 2.0, "side": "long"})
        self.assertEqual(out["stage"], "precheck")
        self.assertIn("与**调用方在管记录**不符", out["detail"])
        self.assertEqual(out["own_verdict"], "mismatch")

    def test_side_mismatch_is_also_a_mismatch(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "BTC", "size_signed": 2.0, "side": "short"}]
        out = self._open(adapter=adapter,
                         own_position={"size_signed": 2.0, "side": "long"})
        self.assertEqual(out["own_verdict"], "mismatch")

    def _existing(self, verdict, reason="台账说"):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "BTC", "size_signed": 3.0, "side": "long"}]
        self.own.classify_exchange_position.return_value = {"verdict": verdict,
                                                            "reason": reason}
        return self._open(adapter=adapter)

    def test_own_verdict_blocks_a_duplicate_open(self):
        """本方已在管 ⇒ 本入口**拒开**（重复开仓=敞口翻倍），加仓要走持仓管理路径。"""
        out = self._existing("own", "台账 holding 行匹配")
        self.assertIs(out["ok"], False)
        self.assertEqual(out["stage"], "precheck")
        self.assertIn("本方已在管该仓", out["detail"])
        self.assertIn("不重复开仓", out["detail"])
        self.assertIn("加仓/减仓请走持仓管理路径", out["detail"])
        self.assertEqual(out["own_verdict"], "own")

    def test_stale_closed_is_reported_as_a_books_reality_mismatch(self):
        out = self._existing("stale_closed", "台账该行已 closed")
        self.assertEqual(out["stage"], "precheck")
        self.assertIn("账实不符", out["detail"])
        self.assertIn("须人工核对", out["detail"])
        self.assertEqual(out["own_verdict"], "stale_closed")

    def test_mismatch_verdict_is_reported(self):
        out = self._existing("mismatch", "量对不上")
        self.assertIn("本方记录与交易所不符", out["detail"])
        self.assertEqual(out["own_verdict"], "mismatch")

    def test_untracked_never_claims_the_position_is_external(self):
        out = self._existing("untracked", "台账无此合约")
        self.assertEqual(out["stage"], "precheck")
        self.assertIn("不宣称『外部仓』", out["detail"])
        self.assertEqual(out["own_verdict"], "untracked")

    def test_classifier_exception_is_recorded_as_unavailable_not_external(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "BTC", "size_signed": 3.0, "side": "long"}]
        self.own.classify_exchange_position.side_effect = RuntimeError("判定件坏了")
        out = self._open(adapter=adapter)
        self.assertIn("归属**不可判定**", out["detail"])
        self.assertEqual(out["own_verdict"], "ledger_unavailable")

    def test_ledger_load_failure_is_passed_as_none(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "BTC", "size_signed": 3.0, "side": "long"}]
        self.own.load_ledger.side_effect = RuntimeError("台账读不到")
        self.own.classify_exchange_position.return_value = {"verdict": "own",
                                                            "reason": "x"}
        out = self._open(adapter=adapter)
        self.assertIs(out["ok"], False, "读不到台账照样拒开，只是判成'不可判定'")
        self.assertIsNone(self.own.classify_exchange_position.call_args[1]["ledger"])

    def test_explicit_own_position_skips_the_ledger_entirely(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "BTC", "size_signed": 2.0, "side": "long"}]
        self._open(adapter=adapter, own_position={"size_signed": 2.0, "side": "long"})
        self.own.load_ledger.assert_not_called()

    def test_the_ledger_path_constant_is_read_at_call_time(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "BTC", "size_signed": 3.0, "side": "long"}]
        self.own.classify_exchange_position.return_value = {"verdict": "own",
                                                            "reason": "x"}
        self._start(mock.patch.object(ER, "OWN_POSITION_LEDGER_FILE", "/tmp/led.json"))
        self._open(adapter=adapter)
        self.own.load_ledger.assert_called_once_with("/tmp/led.json")

    def test_blank_ledger_constant_means_the_default_path(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = [{"base": "BTC", "size_signed": 3.0, "side": "long"}]
        self.own.classify_exchange_position.return_value = {"verdict": "own",
                                                            "reason": "x"}
        self._open(adapter=adapter)
        self.own.load_ledger.assert_called_once_with(None)


class OpenPositionModeTests(_Base):
    def _adapter(self, declared, ready=(), probe="net", has_probe=True):
        adapter = _FakeAdapter(venue="binance", position_modes=declared,
                               entry_ready_position_modes=ready)
        adapter.rows = []
        if has_probe:
            adapter.detect_position_mode = lambda: probe
        return adapter

    def test_no_declared_modes_skips_the_probe(self):
        adapter = self._adapter(())
        self.assertIs(self._open(adapter=adapter)["ok"], True)

    def test_declared_modes_without_a_probe_are_not_enforced(self):
        """实测教训：币安声明的是另一套词汇且没有探测 ⇒ 按"声明了就体检"会**停掉币安全部新开仓**。"""
        adapter = self._adapter(("net", "long_short"), has_probe=False)
        self.assertIs(self._open(adapter=adapter)["ok"], True)

    def test_detected_mode_outside_the_declared_domain_is_rejected(self):
        adapter = self._adapter(("net",), probe="dual_plus")
        out = self._open(adapter=adapter)
        self.assertEqual(out["stage"], "position_mode")
        self.assertIn("无法只读确认持仓模式", out["detail"])
        self.assertIn("不自动切换账户模式", out["detail"])
        self.assertEqual(out["position_mode"], "dual_plus")

    def test_detected_mode_not_entry_ready_is_rejected_with_a_hazard_note(self):
        adapter = self._adapter(("net", "dual_plus"), ready=("net",), probe="dual_plus")
        out = self._open(adapter=adapter)
        self.assertEqual(out["stage"], "position_mode")
        self.assertIn("拆仓语义", out["detail"])
        self.assertIn("仅在 net 下核验过", out["detail"])

    def test_unknown_mode_not_in_the_ready_subset_is_rejected(self):
        adapter = self._adapter(("net", "long_short"), ready=("net",), probe="long_short")
        out = self._open(adapter=adapter)
        self.assertIn("Hedge 对冲语义", out["detail"])

    def test_unknown_hazard_falls_back_to_a_generic_note(self):
        adapter = self._adapter(("net", "weird"), ready=("net",), probe="weird")
        out = self._open(adapter=adapter)
        self.assertIn("该模式下单/保护腿载荷未核验", out["detail"])

    def test_entry_ready_mode_passes(self):
        adapter = self._adapter(("net",), ready=("net",), probe="net")
        self.assertIs(self._open(adapter=adapter)["ok"], True)

    def test_probe_value_is_lowercased(self):
        adapter = self._adapter(("net",), ready=("net",), probe="  NET  ")
        self.assertIs(self._open(adapter=adapter)["ok"], True)

    def test_empty_probe_becomes_unknown(self):
        adapter = self._adapter(("net",), probe="")
        out = self._open(adapter=adapter)
        self.assertEqual(out["position_mode"], "unknown")

    def test_mode_is_forwarded_to_the_protective_legs(self):
        adapter = self._adapter(("net", "dual"), ready=("net", "dual"), probe="dual")
        self._open(adapter=adapter)
        attach = [c for c in adapter.calls if isinstance(c, tuple) and c[0] == "attach"]
        self.assertEqual(attach[0][1], "dual")


class OpenPlacementTests(_Base):
    def test_leverage_failure_is_rejected(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        adapter.set_leverage = mock.Mock(side_effect=RuntimeError("杠杆档位不支持"))
        out = self._open(adapter=adapter)
        self.assertEqual(out["stage"], "leverage")
        self.assertIn("设置杠杆失败", out["detail"])

    def test_capability_error_from_set_leverage_propagates(self):
        from r20_backend.exchanges import ExchangeCapabilityError
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        adapter.set_leverage = mock.Mock(side_effect=ExchangeCapabilityError("不支持"))
        with self.assertRaises(ExchangeCapabilityError):
            self._open(adapter=adapter)

    def test_margin_mode_is_forwarded(self):
        self._open(margin_mode="isolated")
        calls = [c for c in self.adapter.calls if isinstance(c, tuple)
                 and c[0] == "set_leverage"]
        self.assertEqual(calls[0][2], "isolated")

    def test_margin_mode_defaults_to_cross(self):
        self._open()
        calls = [c for c in self.adapter.calls if isinstance(c, tuple)
                 and c[0] == "set_leverage"]
        self.assertEqual(calls[0][2], "cross")

    def test_entry_failure_is_rejected(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        adapter.place_order = mock.Mock(side_effect=RuntimeError("被拒"))
        out = self._open(adapter=adapter)
        self.assertEqual(out["stage"], "entry")
        self.assertIn("入场委托提交失败", out["detail"])

    def test_capability_error_from_place_order_propagates(self):
        from r20_backend.exchanges import ExchangeCapabilityError
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        adapter.place_order = mock.Mock(side_effect=ExchangeCapabilityError("不支持"))
        with self.assertRaises(ExchangeCapabilityError):
            self._open(adapter=adapter)

    def test_order_id_aliases(self):
        for payload, expected in (({"id": "a"}, "a"), ({"order_id": "b"}, "b"),
                                  ({"text": "c"}, "c"), ({}, "")):
            with self.subTest(payload=payload):
                adapter = _FakeAdapter(venue="binance", placed=payload)
                adapter.rows = []
                out = self._open(adapter=adapter)
                self.assertEqual(out["order_id"], expected)


class OpenProtectionTests(_Base):
    def test_success_shape(self):
        out = self._open()
        self.assertIs(out["ok"], True)
        self.assertEqual(out["stage"], "done")
        self.assertEqual(out["asset"], "BTC")
        self.assertEqual(out["action"], "BUY_LONG")
        self.assertEqual(out["tp_id"], "tp-1")
        self.assertEqual(out["sl_id"], "sl-1")
        self.assertEqual(out["leverage"], 3.0)
        self.assertEqual(out["margin_usdt"], 150.0)
        self.assertIsNone(out["margin_clamped_from_usdt"])
        self.assertEqual(out["rr"], 2.5)
        self.assertIn("已回读验证", out["detail"])

    def test_clamped_margin_is_reported(self):
        self.mar.side_effect = None
        self.mar.return_value = (75.0, self.decision, 150.0)
        out = self._open()
        self.assertEqual(out["margin_usdt"], 75.0)
        self.assertEqual(out["margin_clamped_from_usdt"], 150.0)

    def test_readback_missing_a_leg_triggers_the_rollback(self):
        adapter = _FakeAdapter(venue="binance", open_orders=[{"id": "tp-1"}])
        adapter.rows = []
        out = self._open(adapter=adapter)
        self.assertEqual(out["stage"], "protective")
        self.assertIn("回读未见双腿触发单", out["detail"])
        self.assertIn("入场单已撤销", out["detail"])

    def test_attach_failure_triggers_the_rollback(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        adapter.attach_protective_orders = mock.Mock(side_effect=RuntimeError("挂腿被拒"))
        out = self._open(adapter=adapter)
        self.assertEqual(out["stage"], "protective")
        self.assertIn("保护单覆盖失败", out["detail"])

    def test_strict_adapters_get_no_position_mode_kwarg(self):
        """sandbox 等适配器没有 `**kwargs`，无条件传 position_mode 会 TypeError。"""
        adapter = _FakeAdapter(venue="binance", strict_attach=True,
                               position_modes=("net",),
                               entry_ready_position_modes=("net",))
        adapter.rows = []
        adapter.detect_position_mode = lambda: "net"
        self.assertIs(self._open(adapter=adapter)["ok"], True)

    def test_partial_legs_are_cancelled_individually(self):
        adapter = _FakeAdapter(venue="binance", legs={"tp": "tp-9", "sl": ""},
                               open_orders=[])
        adapter.rows = []
        out = self._open(adapter=adapter)
        self.assertEqual(out["stage"], "protective")
        self.assertIn("入场单已撤销", out["detail"])
        cancelled = [c[1] for c in adapter.calls if isinstance(c, tuple)
                     and c[0] == "cancel_order"]
        self.assertIn("tp-9", cancelled)
        self.assertIn("ord-1", cancelled)

    def test_leg_cancel_failure_is_recorded_without_changing_the_verdict(self):
        adapter = _FakeAdapter(venue="binance", open_orders=[])
        adapter.rows = []

        def _cancel(asset, oid):
            if oid == "tp-1":
                raise RuntimeError("撤不动")
            adapter.calls.append(("cancel_order", oid))

        adapter.cancel_order = _cancel
        out = self._open(adapter=adapter)
        self.assertIn("TP腿 tp-1 撤销失败", out["detail"])
        self.assertEqual(out["stage"], "protective")

    def test_only_r20_prefixed_residue_is_cancelled(self):
        residue = [{"id": "user-leg", "text": "user manual"},
                   {"id": "r20-orphan", "text": "r20 tp"},
                   {"id": "nested", "order": {"id": "r20-nested", "text": "R20 SL"}},
                   "not-a-dict",
                   {"id": "", "text": "r20-nope"}]
        adapter = _FakeAdapter(venue="binance", open_orders=[], residue=residue)
        adapter.rows = []
        adapter.list_protective_orders = mock.Mock(return_value=[])
        calls = {"n": 0}

        def _list(asset):
            calls["n"] += 1
            return [] if calls["n"] == 1 else residue

        adapter.list_protective_orders = _list
        out = self._open(adapter=adapter)
        cancelled = [c[1] for c in adapter.calls if isinstance(c, tuple)
                     and c[0] == "cancel_order"]
        self.assertIn("r20-orphan", cancelled)
        self.assertIn("nested", cancelled,
                      "嵌套行取的是外层 id（`row.get('id')` 优先于 `row['order']['id']`）")
        self.assertNotIn("user-leg", cancelled)
        self.assertNotIn("", cancelled)
        self.assertIn("孤儿触发单 r20-orphan 已撤", out["detail"])

    def test_residue_enumeration_failure_is_reported(self):
        adapter = _FakeAdapter(venue="binance")
        adapter.rows = []
        calls = {"n": 0}

        def _list(asset):
            calls["n"] += 1
            if calls["n"] == 1:
                return []                       # 回读未见双腿 ⇒ 进入回滚
            raise RuntimeError("枚举炸了")

        adapter.list_protective_orders = _list
        out = self._open(adapter=adapter)
        self.assertIn("孤儿触发单未能枚举", out["detail"])
        self.assertIn("OCO/到期/手动兜底", out["detail"])

    def test_entry_cancel_failure_is_reported(self):
        adapter = _FakeAdapter(venue="binance", open_orders=[], legs={})
        adapter.rows = []

        def _cancel(asset, oid):
            if oid == "ord-1":
                raise RuntimeError("撤不掉")
            adapter.calls.append(("cancel_order", oid))

        adapter.cancel_order = _cancel
        out = self._open(adapter=adapter)
        self.assertIn("入场单撤销失败", out["detail"])

    def test_empty_leg_ids_are_not_cancelled(self):
        for legs in ({"tp": None, "sl": "None"}, {"tp": "", "sl": ""}, {}):
            with self.subTest(legs=legs):
                adapter = _FakeAdapter(venue="binance", legs=legs, open_orders=[])
                adapter.rows = []
                out = self._open(adapter=adapter)
                self.assertNotIn("腿 None 撤销失败", out["detail"])


if __name__ == "__main__":
    unittest.main()

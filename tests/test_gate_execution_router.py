"""Gate 私有执行面 + ExecutionRouter 单测（全 mock：签名器用固定向量，HTTP 全打桩）。

测试封闭性：signed_request/_keys/环境开关全部 patch，零网络、零真实凭证、零下单可能。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import unittest
from unittest.mock import patch

from r20_backend import execution_router as router
from r20_backend.exchanges import ExchangeCapabilityError
from r20_backend.exchanges.gate import GateAdapter


class TestSigner(unittest.TestCase):
    """签名向量对照官方 SDK gen_sign 算法逐字节复算。"""

    def test_sign_string_layout(self):
        got = GateAdapter.sign_string("POST", "/api/v4/futures/usdt/orders", "",
                                      '{"contract":"BTC_USDT"}', "1700000000")
        body_hash = hashlib.sha512(b'{"contract":"BTC_USDT"}').hexdigest()
        self.assertEqual(got, f"POST\n/api/v4/futures/usdt/orders\n\n{body_hash}\n1700000000")

    def test_empty_body_hash_is_sha512_of_empty_string(self):
        got = GateAdapter.sign_string("GET", "/api/v4/futures/usdt/accounts", "x=1", "", "1")
        empty_hash = hashlib.sha512(b"").hexdigest()
        self.assertEqual(got, f"GET\n/api/v4/futures/usdt/accounts\nx=1\n{empty_hash}\n1")

    def test_sign_is_hmac_sha512_hex(self):
        ad = GateAdapter()
        msg = ad.sign_string("POST", "/p", "", "", "42")
        sig = ad.sign("POST", "/p", "", "", "42", "sekret")
        self.assertEqual(sig, hmac.new(b"sekret", msg.encode(), hashlib.sha512).hexdigest())
        self.assertEqual(len(sig), 128)


class _StubAdapter(GateAdapter):
    """打桩全部私有 IO 与规格/行情；记录调用序列，支持注入失败。"""

    def __init__(self, *, fail_attach=False, fail_verify=False, fail_place=False,
                 fail_leverage=False):
        self.calls = []
        self.price_orders = []
        self._fail = {"attach": fail_attach, "verify": fail_verify,
                      "place": fail_place, "leverage": fail_leverage}
        self._n = 0

    def _keys(self):
        return ("k", "s")

    def fetch_instrument_spec(self, symbol, refresh=False):
        from r20_backend.exchanges import InstrumentSpec
        return InstrumentSpec(venue="gate", inst_id="BTC_USDT", base="BTC",
                              tick_size=0.1, step_size=0.0001, ct_val=0.0001,
                              min_size=1)

    def fetch_ticker(self, symbol):
        return {"last": 79000.0, "mark_price": 79000.0}

    def set_leverage(self, symbol, leverage, margin_mode="cross"):
        self.calls.append(("leverage", symbol, leverage))
        if self._fail["leverage"]:
            raise RuntimeError("leverage refused")
        return {"leverage": str(int(leverage))}

    def place_order(self, symbol, side, contracts, price=None, tif="gtc", text=""):
        self.calls.append(("place", symbol, side, contracts, price))
        if self._fail["place"]:
            raise RuntimeError("entry rejected")
        signed = int(contracts) if side == "long" else -int(contracts)
        return {"id": 9001, "text": text or "t-x", "size": signed}

    def attach_protective_orders(self, symbol, pos_side, tp_px=None, sl_px=None,
                                 expiration=604800, price_type=0):
        self.calls.append(("attach", symbol, pos_side, tp_px, sl_px))
        if self._fail["attach"]:
            raise RuntimeError("price_orders down")
        self._n += 1
        legs = {}
        if tp_px:
            legs["tp"] = f"tp{self._n}"
            self.price_orders.append({"id": f"tp{self._n}"})
        if sl_px:
            legs["sl"] = f"sl{self._n}"
            self.price_orders.append({"id": f"sl{self._n}"})
        return legs

    def list_protective_orders(self, symbol):
        self.calls.append(("verify", symbol))
        if self._fail["verify"]:
            return []
        return list(self.price_orders)

    def cancel_order(self, symbol, order_id):
        self.calls.append(("cancel_entry", symbol, str(order_id)))
        return {"cancelled": True}

    def fast_close_position(self, symbol, text=""):
        self.calls.append(("close", symbol))
        return {"id": 9002, "closed": True}


def _decision(**over):
    # entry 79000 / tp 85000 / sl 77000 → R:R = 3.0（高于任何已配置底线）
    # 450U 名义 @79000、每张面值 0.0001 → 56.96 → 57 张
    d = {"asset": "BTC", "action": "BUY_LONG", "margin_usdt": 150.0, "leverage": 3,
         "entry_price": 79000.0, "take_profit_price": 85000.0, "stop_loss_price": 77000.0}
    d.update(over)
    return d


class TestRouter(unittest.TestCase):
    def test_execution_closed_by_default(self):
        """默认（env 未设）直接 fail-closed——不触任何 IO。"""
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ExchangeCapabilityError):
                router.open_protected_position(_decision(), adapter=_StubAdapter(),
                                               price_ref=79000.0)

    def test_open_long_full_sequence(self):
        ad = _StubAdapter()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.open_protected_position(_decision(), adapter=ad,
                                               price_ref=79000.0)
        self.assertTrue(r["ok"], r.get("detail"))
        self.assertEqual([c[0] for c in ad.calls], ["leverage", "place", "attach", "verify"])
        self.assertEqual(ad.calls[1], ("place", "BTC", "long", 57, 79000.0))
        self.assertEqual(r["tp_id"], "tp1")
        self.assertEqual(r["sl_id"], "sl1")
        self.assertGreaterEqual(r["rr"], 2.0)

    def test_short_signed_size(self):
        ad = _StubAdapter()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.open_protected_position(
                _decision(action="SELL_SHORT", take_profit_price=75000.0,
                          stop_loss_price=80500.0),
                adapter=ad, price_ref=79000.0)
        self.assertTrue(r["ok"], r.get("detail"))
        self.assertEqual(r["size_signed"], -57)
        self.assertEqual(ad.calls[1][2], "short")

    def test_geometry_rejected_before_any_execution(self):
        ad = _StubAdapter()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.open_protected_position(
                _decision(stop_loss_price=79500.0),  # 多头止损>入场，几何非法
                adapter=ad, price_ref=79000.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "risk_gate")
        self.assertEqual(ad.calls, [])   # 物理风控在任何 IO 之前

    def test_nan_margin_rejected(self):
        ad = _StubAdapter()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.open_protected_position(_decision(margin_usdt=float("nan")),
                                               adapter=ad, price_ref=79000.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "validate")
        self.assertEqual(ad.calls, [])

    def test_attach_failure_cancels_entry(self):
        ad = _StubAdapter(fail_attach=True)
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.open_protected_position(_decision(), adapter=ad, price_ref=79000.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "protective")
        self.assertIn(("cancel_entry", "BTC", "9001"), ad.calls)   # 绝不留裸仓
        self.assertIn("入场单已撤销", r["detail"])

    def test_verify_gap_cancels_entry(self):
        ad = _StubAdapter(fail_verify=True)
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.open_protected_position(_decision(), adapter=ad, price_ref=79000.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "protective")
        self.assertIn(("cancel_entry", "BTC", "9001"), ad.calls)

    def test_leverage_failure_stops_before_entry(self):
        ad = _StubAdapter(fail_leverage=True)
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.open_protected_position(_decision(), adapter=ad, price_ref=79000.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "leverage")
        self.assertNotIn("place", [c[0] for c in ad.calls])

    def test_min_notional_rejected(self):
        ad = _StubAdapter()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.open_protected_position(_decision(margin_usdt=0.5), adapter=ad,
                                               price_ref=79000.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "sizing")

    def test_price_aligned_to_tick(self):
        # tick 0.1 下 79000.04 必须对齐为 79000.0 再下单（防 Gate PRICE_INVALID）
        ad = _StubAdapter()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.open_protected_position(
                _decision(entry_price=79000.04, take_profit_price=85000.07,
                          stop_loss_price=77000.02), adapter=ad, price_ref=79000.0)
        self.assertTrue(r["ok"], r.get("detail"))
        self.assertEqual(ad.calls[1][4], 79000.0)   # place price 已对齐

    def test_price_ref_missing_falls_back_to_ticker(self):
        ad = _StubAdapter()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.open_protected_position(_decision(), adapter=ad)
        self.assertTrue(r["ok"], r.get("detail"))
        self.assertEqual(r["ref_price"], 79000.0)

    def test_close_position_requires_gate_open(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ExchangeCapabilityError):
                router.close_position("BTC", adapter=_StubAdapter())
        ad = _StubAdapter()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.close_position("BTC", adapter=ad)
        self.assertTrue(r["ok"])
        self.assertIn(("close", "BTC"), ad.calls)


class TestAdapterPayloadShapes(unittest.TestCase):
    """attach 双腿报文与 rule 映射（不打桩 signed_request，直接检查其入参）。"""

    def test_long_tp_sl_rules_and_bodies(self):
        ad = GateAdapter()
        sent = []

        def fake(method, path, params=None, body=None, timeout=15.0):
            sent.append((method, path, body))
            return {"id": f"po{len(sent)}"}
        with patch.object(ad, "_keys", lambda: ("k", "s")), \
                patch.object(ad, "signed_request", fake):
            legs = ad.attach_protective_orders("BTC", "long", tp_px=83000.0, sl_px=77000.0)
        self.assertEqual(sorted(legs), ["sl", "tp"])
        tp_body = next(b for (_, _, b) in sent if b["trigger"]["price"] == "83000.0")
        sl_body = next(b for (_, _, b) in sent if b["trigger"]["price"] == "77000.0")
        self.assertEqual(tp_body["trigger"]["rule"], 1)   # 多TP：价≥触发
        self.assertEqual(sl_body["trigger"]["rule"], 2)   # 多SL：价≤触发
        self.assertTrue(tp_body["initial"]["close"] and tp_body["initial"]["reduce_only"])
        self.assertEqual(tp_body["trigger"]["price_type"], 0)  # 最新价（非 mark！币安坑不复存在）

    def test_short_rules_mirror(self):
        self.assertEqual(GateAdapter.trigger_rule("short", "tp"), 2)
        self.assertEqual(GateAdapter.trigger_rule("short", "sl"), 1)
        self.assertEqual(GateAdapter.trigger_rule("long", "tp"), 1)
        self.assertEqual(GateAdapter.trigger_rule("long", "sl"), 2)

    def test_place_order_signed_size_and_market_ioc(self):
        ad = GateAdapter()
        seen = {}

        def fake(method, path, params=None, body=None, timeout=15.0):
            seen.update(method=method, path=path, body=body)
            return {"id": 1}
        with patch.object(ad, "_keys", lambda: ("k", "s")), \
                patch.object(ad, "signed_request", fake):
            ad.place_order("SOL", "short", 7, price=None)
        self.assertEqual(seen["body"]["size"], -7)
        self.assertEqual(seen["body"]["price"], "0")
        self.assertEqual(seen["body"]["tif"], "ioc")

    def test_partial_attach_rolls_back_first_leg(self):
        ad = GateAdapter()
        calls = []

        def fake(method, path, params=None, body=None, timeout=15.0):
            if method == "POST" and len([c for c in calls if c[0] == "POST"]) == 1:
                calls.append(("POST", path, body))
                raise RuntimeError("second leg boom")
            calls.append((method, path, body))
            if method == "DELETE":
                return {}
            return {"id": "po1"}
        with patch.object(ad, "_keys", lambda: ("k", "s")), \
                patch.object(ad, "signed_request", fake):
            with self.assertRaises(RuntimeError):
                ad.attach_protective_orders("BTC", "long", tp_px=83000.0, sl_px=77000.0)
        self.assertIn("DELETE", [c[0] for c in calls])   # 已挂腿被回滚


if __name__ == "__main__":
    unittest.main()

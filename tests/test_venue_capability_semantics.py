"""US-004 能力表真实语义契约测试（全 mock、零网络、零真实凭证）。

钉三件事（事实源 = plan_local/THREE_VENUE_API_FRESHNESS_AUDIT_20260910.md §2）：
1. 能力表逐所声明：order_id_type / native_amend / decimal_amount /
   position_modes / conditional_family / protection_semantics；
2. 请求契约：Binance Algo 双轨字段与互斥校验、Gate 原生 amend 与 amount 双许可、
   Gate id_string 字符串归一、OKX attachAlgo failCode 三态核验；
3. 门禁不回归：Binance 私有发送恒显式未实装（supports_orders=False 语义不动）。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

from r20_backend.exchanges.base import ExchangeCapabilityError, InstrumentSpec
from r20_backend.exchanges.binance import BinanceAdapter
from r20_backend.exchanges.gate import (AUTO_SIZE_CLOSE_LONG, AUTO_SIZE_CLOSE_SHORT,
                                        GateAPIError, GateAdapter)
from r20_backend.exchanges.okx import OKXPublicAdapter
from r20_backend import okx_trade_service as ots


class _Resp:
    """最小 urlopen 替身：with 上下文 + read()。"""
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ----------------------------------------------------------------------
# 1) 能力表声明（审计 §2 逐所）
# ----------------------------------------------------------------------
class TestCapabilityDeclarations(unittest.TestCase):
    def test_gate_semantic_fields(self):
        cap = GateAdapter.capabilities
        self.assertEqual(cap.order_id_type, "int64_id_string")
        self.assertTrue(cap.native_amend)            # price_orders/amend 原生改单
        self.assertTrue(cap.decimal_amount)          # 十进制张数（双许可后才发）
        self.assertIn("dual_plus", cap.position_modes)  # 拆仓模式声明在表
        self.assertEqual(cap.conditional_family, "independent_resource")
        self.assertIn("amend", cap.protection_semantics)

    def test_binance_algo_family_declared(self):
        cap = BinanceAdapter.capabilities
        self.assertEqual(cap.conditional_family, "algo_service")
        self.assertEqual(cap.order_id_type, "int64_precision_risk")
        self.assertFalse(cap.native_amend)           # 未核验原生改单，不宣称
        self.assertIn("openOrders", cap.protection_semantics)  # 普通挂单≠保护全集
        self.assertFalse(cap.supports_orders)        # 门禁不变

    def test_okx_attach_failcode_declared(self):
        cap = OKXPublicAdapter.capabilities
        self.assertEqual(cap.conditional_family, "attached")
        ps = cap.protection_semantics
        self.assertIn("failCode", ps)                # 受理≠生效，须核验
        self.assertIn("canceled", ps)                # 2026-08-20 直接终态语义
        self.assertIn("200", ps)                     # HTTP 200≠受保护字面钉住

    def test_new_fields_have_safe_defaults(self):
        # 既有构造点零破坏：US-004 新字段全部带默认值（frozen dataclass 增量演进）
        import dataclasses
        names = ("order_id_type", "native_amend", "decimal_amount",
                 "position_modes", "conditional_family", "protection_semantics")
        for f in dataclasses.fields(BinanceAdapter.capabilities):
            if f.name in names:
                self.assertTrue(f.default is not dataclasses.MISSING
                                or f.default_factory is not dataclasses.MISSING, f.name)


# ----------------------------------------------------------------------
# 2) Binance Algo 双轨契约（纯构造器 + fail-closed 发送）
# ----------------------------------------------------------------------
class TestBinanceAlgoContract(unittest.TestCase):
    def setUp(self):
        self.ad = BinanceAdapter()

    def test_build_algo_order_uses_algo_fields(self):
        req = self.ad.build_algo_order_request(
            symbol="BTCUSDT", side="SELL", type_="STOP_MARKET",
            trigger_price="78500.0", working_type="MARK_PRICE",
            quantity="0.05", client_algo_id="r20a1")
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["path"], "/fapi/v1/algoOrder")
        b = req["body"]
        self.assertEqual(b["triggerPrice"], "78500.0")     # 当前字段名
        self.assertEqual(b["clientAlgoId"], "r20a1")       # 当前字段名
        self.assertEqual(b["workingType"], "MARK_PRICE")   # 显式传入
        self.assertNotIn("stopPrice", b)                   # 普通订单旧字段禁传
        self.assertNotIn("newClientOrderId", b)

    def test_working_type_required_not_default(self):
        with self.assertRaises(ValueError):
            self.ad.build_algo_order_request(
                symbol="BTCUSDT", side="SELL", type_="STOP_MARKET",
                trigger_price="1", working_type=None, quantity="1")
        with self.assertRaises(ValueError):
            self.ad.build_algo_order_request(
                symbol="BTCUSDT", side="SELL", type_="STOP_MARKET",
                trigger_price="1", working_type="BOGUS", quantity="1")

    def test_close_position_exclusivity_fail_closed(self):
        mk = dict(symbol="BTCUSDT", side="SELL", type_="STOP_MARKET",
                  trigger_price="78000", working_type="CONTRACT_PRICE")
        # 合法：仅 closePosition
        req = self.ad.build_algo_order_request(close_position=True, **mk)
        self.assertEqual(req["body"]["closePosition"], "true")
        self.assertNotIn("quantity", req["body"])
        # 非法：与 quantity / reduceOnly 并传；非 *_MARKET 类型
        with self.assertRaises(ValueError):
            self.ad.build_algo_order_request(close_position=True, quantity="0.1", **mk)
        with self.assertRaises(ValueError):
            self.ad.build_algo_order_request(close_position=True, reduce_only=True, **mk)
        with self.assertRaises(ValueError):
            self.ad.build_algo_order_request(
                symbol="BTCUSDT", side="SELL", type_="STOP", working_type="CONTRACT_PRICE",
                trigger_price="78000", close_position=True)

    def test_hedge_mode_rejects_reduce_only(self):
        with self.assertRaises(ValueError):
            self.ad.build_algo_order_request(
                symbol="BTCUSDT", side="SELL", type_="STOP_MARKET",
                trigger_price="78000", working_type="MARK_PRICE",
                quantity="0.1", reduce_only=True, position_side="LONG")

    def test_private_algo_sends_are_fail_closed(self):
        for call in (lambda: self.ad.query_algo_order(algo_id="1"),
                     lambda: self.ad.current_all_algo_open_orders(symbol="BTCUSDT"),
                     lambda: self.ad.cancel_algo_order(algo_id="1"),
                     lambda: self.ad.cancel_all_algo_open_orders(symbol="BTCUSDT")):
            with self.assertRaises(ExchangeCapabilityError):
                call()

    def test_merged_protection_view_is_dual_source(self):
        view = self.ad.merged_protection_view(
            [{"orderId": 111, "symbol": "BTCUSDT"}],
            [{"algoId": 9007199254740993, "type": "STOP_MARKET"}])
        self.assertFalse(view["open_orders_only"])
        self.assertEqual(view["entries"][0]["orderId"], "111")           # str 归一
        self.assertEqual(view["conditional"][0]["algoId"],
                         "9007199254740993")                             # 大整数无损字符串
        self.assertEqual(view["protection_total"], 1)


# ----------------------------------------------------------------------
# 3) Gate：id_string 归一 / amount 双许可 / 原生 amend 棘轮
# ----------------------------------------------------------------------
class TestGateIdString(unittest.TestCase):
    def setUp(self):
        # 封死环境态：data/env_profiles.json 里持久化的沙盒探测结果不得影响本组断言，
        # 统一钉 live 默认域（resolve/has_env 均在构造期生效，必须在 GateAdapter() 之前 patch）。
        with patch("r20_backend.exchanges.env_profiles.resolve_base_url",
                   lambda venue, env, **kw: "https://api.gateio.ws"), \
             patch("r20_backend.exchanges.env_profiles.has_env", lambda *a, **k: True):
            self.ad = GateAdapter()

    def _request_capture(self, payload):
        seen = {}
        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            return _Resp(payload)
        return fake_urlopen, seen

    def test_id_string_preferred_and_str(self):
        fake, seen = self._request_capture(
            [{"id": 123, "id_string": "9007199254740993", "contract": "BTC_USDT"}])
        with patch.object(GateAdapter, "_keys", lambda s: ("k", "s")), \
             patch("r20_backend.exchanges.gate.urlopen", fake):
            rows = self.ad.signed_request("GET", "/api/v4/futures/usdt/orders")
        self.assertEqual(rows[0]["id"], "9007199254740993")   # id_string 优先
        self.assertIsInstance(rows[0]["id"], str)
        self.assertIn("api.gateio.ws", seen["url"])            # 只发钉死域（patch 钉 live 默认），无跨域

    def test_pure_int_id_also_stringified_no_float_roundtrip(self):
        fake, _ = self._request_capture({"id": 9007199254740993, "text": "t1"})
        with patch.object(GateAdapter, "_keys", lambda s: ("k", "s")), \
             patch("r20_backend.exchanges.gate.urlopen", fake):
            data = self.ad.signed_request("GET", "/api/v4/futures/usdt/orders")
        self.assertEqual(data["id"], "9007199254740993")      # 不经 float：Python int 精确保留


class TestGateDecimalAmount(unittest.TestCase):
    def setUp(self):
        self.ad = GateAdapter()

    def _spec(self, min_raw):
        return InstrumentSpec(venue="gate", inst_id="BTC_USDT", base="BTC",
                              tick_size=0.1, step_size=0.0001, ct_val=0.0001,
                              min_size=float(min_raw or 1),
                              raw={} if min_raw is None else {"order_size_min": min_raw})

    def test_permission_requires_capability_and_spec(self):
        with patch.object(self.ad, "fetch_instrument_spec", return_value=self._spec("0.01")):
            self.assertTrue(self.ad._decimal_amount_allowed("BTC"))
        with patch.object(self.ad, "fetch_instrument_spec", return_value=self._spec("1")):
            self.assertFalse(self.ad._decimal_amount_allowed("BTC"))   # int 合约拒
        with patch.object(self.ad, "fetch_instrument_spec", return_value=None):
            self.assertFalse(self.ad._decimal_amount_allowed("BTC"))   # 规格读不到拒（fail-closed）

    def test_amount_only_body_when_allowed(self):
        sent = {}
        def capture(method, path, params=None, body=None, timeout=15.0):
            sent["method"], sent["path"], sent["body"] = method, path, body
            return {"id": "7", "text": body.get("text")}
        with patch.object(self.ad, "_decimal_amount_allowed", return_value=True), \
             patch.object(self.ad, "signed_request", capture):
            self.ad.place_order("BTC", "short", 0, price=79000, amount="0.5")
        b = sent["body"]
        self.assertEqual(b["amount"], "-0.5")     # 带符号十进制字符串（short→负）
        self.assertNotIn("size", b)               # 与 size 并传易歧义 → 只发 amount

    def test_amount_rejected_without_permission_no_request(self):
        called = []
        with patch.object(self.ad, "_decimal_amount_allowed", return_value=False), \
             patch.object(self.ad, "signed_request",
                          lambda *a, **k: called.append(a)):
            with self.assertRaises(ExchangeCapabilityError):
                self.ad.place_order("BTC", "long", 0, price=79000, amount="0.5")
        self.assertEqual(called, [])              # 拒绝且零外发

    def test_int_path_unchanged(self):
        sent = {}
        def capture(method, path, params=None, body=None, timeout=15.0):
            sent["body"] = body
            return {"id": "8"}
        with patch.object(self.ad, "signed_request", capture):
            self.ad.place_order("BTC", "long", 5, price=79000)
        self.assertEqual(sent["body"]["size"], 5)
        self.assertNotIn("amount", sent["body"])


class TestGateNativeAmend(unittest.TestCase):
    def setUp(self):
        self.ad = GateAdapter()

    def test_amend_price_order_builds_put_contract(self):
        sent = {}
        def capture(method, path, params=None, body=None, timeout=15.0):
            sent["m"], sent["p"], sent["b"] = method, path, body
            return {"id": "42", "id_string": "42"}
        with patch.object(self.ad, "signed_request", capture):
            self.ad.amend_price_order(42, trigger_price="78123.4", price_type=0,
                                      amount="1.25")
        self.assertEqual(sent["m"], "PUT")
        self.assertEqual(sent["p"], "/api/v4/futures/usdt/price_orders/amend")
        self.assertEqual(sent["b"]["order_id"], "42")            # 字符串 ID
        self.assertEqual(sent["b"]["trigger_price"], "78123.4")  # 十进制字符串
        self.assertEqual(sent["b"]["price_type"], 0)
        self.assertEqual(sent["b"]["amount"], "1.25")

    def test_amend_auto_size_enum(self):
        with self.assertRaises(ExchangeCapabilityError):
            self.ad.amend_price_order("1", auto_size="close_long_dual_mode")  # 旧猜测值拒
        for good in (AUTO_SIZE_CLOSE_LONG, AUTO_SIZE_CLOSE_SHORT):
            sent = {}
            with patch.object(self.ad, "signed_request",
                              lambda m, p, params=None, body=None, timeout=15.0, s=sent:
                              s.__setitem__("b", body) or {"id": "1"}):
                self.ad.amend_price_order("1", auto_size=good)
            self.assertEqual(sent["b"]["auto_size"], good)

    def test_ratchet_prefers_native_amend(self):
        attach_calls = []
        with patch.object(self.ad, "amend_price_order",
                          return_value={"id": "55"}) as am, \
             patch.object(self.ad, "attach_protective_orders",
                          side_effect=lambda *a, **k: attach_calls.append(a) or {"sl": "x"}), \
             patch.object(self.ad, "cancel_price_order",
                          side_effect=lambda oid: attach_calls.append(("cancel", oid))):
            new_id = self.ad.amend_stop_loss("BTC", "long", "55", 78000.0)
        self.assertEqual(new_id, "55")             # 同单改价，无缝隙
        self.assertEqual(attach_calls, [])         # 未走挂新撤旧

    def test_ratchet_falls_back_when_amend_unsupported(self):
        seq = []
        def amend_fail(*a, **k):
            raise GateAPIError("404", "price_orders/amend not found", status=404)
        def attach(symbol, side, tp_px=None, sl_px=None, expiration=604800, price_type=0):
            seq.append("attach_new")
            return {"sl": "99"}
        def cancel(oid):
            seq.append(f"cancel_old:{oid}")
        with patch.object(self.ad, "amend_price_order", amend_fail), \
             patch.object(self.ad, "attach_protective_orders", attach), \
             patch.object(self.ad, "cancel_price_order", cancel):
            new_id = self.ad.amend_stop_loss("BTC", "long", "55", 77999.5)
        self.assertEqual(new_id, "99")
        self.assertEqual(seq, ["attach_new", "cancel_old:55"])  # 先挂新再撤旧，无裸窗


# ----------------------------------------------------------------------
# 4) OKX attachAlgo failCode 核验三态（纯函数零网络）
# ----------------------------------------------------------------------
class TestOkxAttachVerification(unittest.TestCase):
    LEG = {"kind": "sl", "side": "buy", "sz": "2", "x_price": "78000"}
    PEND = [{"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "2.000",
             "xPrice": "78000.0", "algoId": "a1"}]

    def test_http_200_with_failcode_is_unprotected(self):
        r = ots.verify_attached_protection(
            inst_id="BTC-USDT-SWAP", expected_legs=[self.LEG],
            attach_rows=[{"sz": "2", "xPrice": "78000", "side": "buy",
                          "failCode": "51177", "failReason": "trigger price invalid"}],
            pending_rows=self.PEND, main_order_state="filled")
        self.assertEqual(r["status"], "UNPROTECTED")     # 回执有 failCode → 该腿未受理
        self.assertEqual(r["legs"][0]["state"], "failed")
        self.assertEqual(r["legs"][0]["failCode"], "51177")

    def test_pending_full_coverage_is_protected(self):
        r = ots.verify_attached_protection(
            inst_id="BTC-USDT-SWAP", expected_legs=[self.LEG],
            pending_rows=self.PEND, main_order_state="filled")
        self.assertEqual(r["status"], "PROTECTED")       # 十进制字符串比较 2==2.000

    def test_filled_but_missing_readback_is_pending_not_claimed(self):
        r = ots.verify_attached_protection(
            inst_id="BTC-USDT-SWAP", expected_legs=[self.LEG],
            pending_rows=[], main_order_state="new")
        self.assertEqual(r["status"], "PROTECTION_PENDING")  # 未回读绝不宣称受保护

    def test_post_only_direct_terminal_canceled_no_wait(self):
        r = ots.verify_attached_protection(
            inst_id="BTC-USDT-SWAP", expected_legs=[self.LEG],
            pending_rows=[], main_order_state="canceled")  # 2026-08-20 直达终态
        self.assertEqual(r["status"], "UNPROTECTED")
        self.assertIn("终态", r["detail"])                  # 不无限等待 live 的语义

    def test_other_instrument_row_cannot_satisfy(self):
        rows = [{"instId": "ETH-USDT-SWAP", "side": "buy", "sz": "2",
                 "xPrice": "78000"}]
        r = ots.verify_attached_protection(
            inst_id="BTC-USDT-SWAP", expected_legs=[self.LEG],
            pending_rows=rows, main_order_state="filled")
        self.assertNotEqual(r["status"], "PROTECTED")      # 账户/合约归属必须一致

    # ---- US-004 观察项②收口：覆盖数量结构性短缺显式 UNPROTECTED ----
    def _chk(self, sz_exp, rows, state="filled"):
        return ots.verify_attached_protection(
            inst_id="BTC-USDT-SWAP",
            expected_legs=[{"kind": "sl", "side": "buy", "sz": sz_exp,
                            "x_price": "78000"}],
            pending_rows=rows, main_order_state=state)

    def test_shortage_row_is_unprotected_with_gap_reason(self):
        r = self._chk("5", [{"instId": "BTC-USDT-SWAP", "side": "buy",
                             "sz": "4.000", "xPrice": "78000.0", "algoId": "a9"}])
        self.assertEqual(r["status"], "UNPROTECTED")          # 短缺≠尚未回读
        self.assertEqual(r["legs"][0]["state"], "coverage_shortfall")
        self.assertEqual(r["legs"][0]["reason"], "covered 4 < filled 5")
        self.assertEqual(r["legs"][0]["algo_ids"], ["a9"])

    def test_empty_readback_on_filled_stays_pending(self):
        r = self._chk("5", [])
        self.assertEqual(r["status"], "PROTECTION_PENDING")   # 行缺失=等待语义，非短缺

    def test_full_match_still_protected_after_shortage_split(self):
        r = self._chk("5", [{"instId": "BTC-USDT-SWAP", "side": "buy",
                             "sz": "5", "xPrice": "78000", "algoId": "a5"}])
        self.assertEqual(r["status"], "PROTECTED")
        self.assertEqual(r["legs"][0]["state"], "protected")

    def test_exact_row_wins_over_coexisting_shortage_rows(self):
        rows = [{"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "1",
                 "xPrice": "78000", "algoId": "x"},
                {"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "5",
                 "xPrice": "78000", "algoId": "y"}]
        r = self._chk("5", rows)
        self.assertEqual(r["status"], "PROTECTED")            # 精确行救场，不误杀

    def test_multi_short_rows_summing_below_expected_is_unprotected(self):
        rows = [{"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "2",
                 "xPrice": "78000", "algoId": "m1"},
                {"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "2",
                 "xPrice": "78000", "algoId": "m2"}]
        r = self._chk("5", rows)
        self.assertEqual(r["status"], "UNPROTECTED")
        self.assertEqual(r["legs"][0]["reason"], "covered 4 < filled 5")

    def test_multi_rows_summing_to_expected_stay_pending_not_promoted(self):
        # 多腿合计达期望但无单腿精确匹配：保守留在 PENDING（不冒充精确核验）
        rows = [{"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "2",
                 "xPrice": "78000", "algoId": "p1"},
                {"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "3",
                 "xPrice": "78000", "algoId": "p2"}]
        r = self._chk("5", rows)
        self.assertEqual(r["status"], "PROTECTION_PENDING")

    def test_oversized_or_incomparable_rows_never_claim_shortage(self):
        big = [{"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "9",
                "xPrice": "78000", "algoId": "b"}]
        r = self._chk("5", big)
        self.assertEqual(r["status"], "PROTECTION_PENDING")    # 超量：中间态保守
        mixed = [{"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "1",
                  "xPrice": "78000", "algoId": "s"},
                 {"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "x?",
                  "xPrice": "78000", "algoId": "g"}]
        r2 = self._chk("5", mixed)
        self.assertEqual(r2["status"], "PROTECTION_PENDING")   # 不可量化行挡短缺武断

    def test_differside_or_trigger_rows_not_counted_as_shortage(self):
        # side/触发值不匹配的行（如旧棘轮残留腿）不得拼成"短缺"错判
        rows = [{"instId": "BTC-USDT-SWAP", "side": "sell", "sz": "5",
                 "xPrice": "78000", "algoId": "sd"},
                {"instId": "BTC-USDT-SWAP", "side": "buy", "sz": "5",
                 "xPrice": "66000", "algoId": "tg"}]
        r = self._chk("5", rows)
        self.assertEqual(r["status"], "PROTECTION_PENDING")
        self.assertEqual(r["legs"][0]["state"], "pending_readback")

    def test_three_state_enum_declared(self):
        self.assertEqual(set(ots.PROTECTION_STATES),
                         {"PROTECTION_PENDING", "PROTECTED", "UNPROTECTED"})


if __name__ == "__main__":
    unittest.main()

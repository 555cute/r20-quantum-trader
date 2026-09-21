"""Gate 下单：十进制 amount 的**双许可**与只发 amount 的 fail-closed（第二百七十五刀）。

审计 §2/§3 的要点：真实账户支持未证之前**绝不盲发 amount**。

| 语义 | 纪律 |
|---|---|
| 双许可 | `capability.decimal_amount=True` **且** 合约规格 `order_size_min < 1`；任一不满足或规格读不到 ⇒ `False`（回退 int 张数保守路径）|
| amount 只发 amount | 不并传 `size` —— 服务端不识别时会**显式报错**，而不是按截断的 `size` **静默错量** |
| 价格/有效期 | `price=None` ⇒ `price="0"` + `tif="ioc"`（市价）；给价则 `tif` 透传 |
| 拒单 | 张数/金额非正 ⇒ `ExchangeCapabilityError`；回执缺 `id`/`text` ⇒ `bad_response` |
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from r20_backend.exchanges.gate import GateAdapter, GateAPIError
from r20_backend.exchanges.base import ExchangeCapabilityError


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = GateAdapter.__new__(GateAdapter)
        self.ad.base_url = "https://api.gateio.ws"
        self.ad.native_symbol = lambda s: f"{s}_USDT" if s else ""
        self.sent = []

    def _cap(self, decimal_amount=True):
        self.ad.capabilities = SimpleNamespace(decimal_amount=decimal_amount,
                                               max_candle_limit=2000)
        return self.ad

    def _spec(self, raw_min):
        raw = {} if raw_min is None else {"order_size_min": raw_min}
        self.ad.fetch_instrument_spec = lambda s: SimpleNamespace(raw=raw)

    def _send(self, payload=None):
        def _req(method, path, params=None, body=None, **kw):
            self.sent.append((method, path, dict(params or {}), dict(body or {})))
            return {"id": 1} if payload is None else payload
        self.ad.signed_request = _req
        return self.ad


class DecimalAmountAllowedTest(_Base):
    def test_requires_both_capability_and_spec(self):
        self._cap(decimal_amount=True)
        self._spec(0.5)
        self.assertTrue(self.ad._decimal_amount_allowed("BTC"), "双许可齐备才为真")
        self._cap(decimal_amount=False)
        self.assertFalse(self.ad._decimal_amount_allowed("BTC"), "capability 否 ⇒ 一律否")

    def test_spec_side_failures_are_all_false(self):
        self._cap()
        self.ad.fetch_instrument_spec = lambda s: None
        self.assertFalse(self.ad._decimal_amount_allowed("BTC"), "规格读不到 ⇒ 否（未证不发）")
        self._spec(None)
        self.assertFalse(self.ad._decimal_amount_allowed("BTC"), "缺 order_size_min ⇒ 否")
        self._spec(1)
        self.assertFalse(self.ad._decimal_amount_allowed("BTC"), "最小 1 张 ⇒ 不支持小数")
        self._spec("0.001")
        self.assertTrue(self.ad._decimal_amount_allowed("BTC"), "字符串规格同样识别")

    def test_spec_exception_is_fail_closed(self):
        self._cap()

        def _boom(s):
            raise RuntimeError("规格端点挂了")

        self.ad.fetch_instrument_spec = _boom
        self.assertFalse(self.ad._decimal_amount_allowed("BTC"),
                         "读规格炸了 ⇒ 否（保守回退 int 路径，绝不盲发）")


class PlaceOrderTest(_Base):
    def test_amount_without_dual_permission_is_refused(self):
        """★ 未获双许可 ⇒ **拒发**（把「服务端不识别 ⇒ 显式报错」提前到本地）。"""
        self._cap(decimal_amount=False)
        self._spec(0.5)
        self._send()
        with self.assertRaises(ExchangeCapabilityError) as ctx:
            self.ad.place_order("BTC", "long", 1, amount="0.5")
        self.assertIn("未获双许可", str(ctx.exception))
        self.assertEqual(self.sent, [], "拒单必须发生在发请求之前")

    def test_non_positive_amount_is_refused(self):
        self._cap()
        self._spec(0.5)
        self._send()
        for bad in ("0", "-0.5"):
            with self.subTest(amount=bad):
                with self.assertRaises(ExchangeCapabilityError):
                    self.ad.place_order("BTC", "long", 1, amount=bad)

    def test_amount_path_sends_amount_only_and_signs_by_side(self):
        """★ **只发 amount 不发 size**：不识别时服务端显式报错，而不是按截断 size 静默错量。"""
        self._cap()
        self._spec(0.5)
        self._send()
        self.ad.place_order("BTC", "long", 1, amount="1.5")
        body = self.sent[-1][3]
        self.assertEqual(body["amount"], "1.5")
        self.assertNotIn("size", body, "绝不并传 size")
        self.ad.place_order("BTC", "short", 1, amount="1.5")
        self.assertEqual(self.sent[-1][3]["amount"], "-1.5", "方向由符号表达")

    def test_int_path_uses_signed_size(self):
        self._cap()
        self._spec(1)
        self._send()
        self.ad.place_order("BTC", "long", 2.4)
        self.assertEqual(self.sent[-1][3]["size"], 2, "四舍五入取整")
        self.ad.place_order("BTC", "short", 3)
        self.assertEqual(self.sent[-1][3]["size"], -3, "空头为负张数")
        with self.assertRaises(ExchangeCapabilityError):
            self.ad.place_order("BTC", "long", 0.4)
        self._spec(1)
        with self.assertRaises(ExchangeCapabilityError):
            self.ad.place_order("BTC", "long", 0)

    def test_price_defaults_to_ioc_market_and_passthrough_when_given(self):
        self._cap()
        self._spec(1)
        self._send()
        self.ad.place_order("BTC", "long", 1)
        body = self.sent[-1][3]
        self.assertEqual(body["price"], "0", "无价 ⇒ 价 0")
        self.assertEqual(body["tif"], "ioc", "无价 ⇒ ioc（市价语义）")
        self.ad.place_order("BTC", "long", 1, price=100.5, tif="gtc")
        body2 = self.sent[-1][3]
        self.assertEqual(body2["price"], "100.5")
        self.assertEqual(body2["tif"], "gtc", "给价则 tif 透传")

    def test_text_default_reduce_only_and_bad_receipt(self):
        self._cap()
        self._spec(1)
        self._send()
        self.ad.place_order("BTC", "long", 1, reduce_only=True)
        body = self.sent[-1][3]
        self.assertTrue(body["reduce_only"])
        self.assertTrue(body["text"].startswith("t-r20"), body["text"])
        self.ad.place_order("BTC", "long", 1, text="my-tag")
        self.assertEqual(self.sent[-1][3]["text"], "my-tag", "调用方给的 text 优先")
        self._send(payload={"msg": "no id"})
        with self.assertRaises(GateAPIError) as ctx:
            self.ad.place_order("BTC", "long", 1)
        self.assertEqual(ctx.exception.label, "bad_response")


if __name__ == "__main__":
    unittest.main()

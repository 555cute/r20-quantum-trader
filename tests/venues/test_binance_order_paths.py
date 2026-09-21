"""Binance 下单与撤单路径（第二百六十二刀）。

| 语义 | 纪律 |
|---|---|
| 数量 | `contracts <= 0` ⇒ **`ValueError`**（不下 0 张/负数单）|
| 响应结构 | 非 dict ⇒ `BinanceAPIError(bad_response)` —— **不能确认受理就不许假装成功** |
| 撤单目标 | **非数字 `order_id` 一律改走 `origClientOrderId`**（审计 D6：上游回退链会把 client text `t-r20e*` 当 order_id 传来，混族直传会被 `-2011` 拒撤 ⇒ **回滚漏网孤儿入场单裸挂**）|
| 撤单状态 | **status 必须回显交易所真实值**（审计 D3：旧实现硬编码 `CANCELED`，抢撤竞态下响应实为 `FILLED` 也会被伪报撤成功）——「**受理 ≠ 撤掉**」|
| 缺失状态 | ⇒ `UNKNOWN`（不是 `CANCELED`）|
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from r20_backend.exchanges.binance import BinanceAdapter, BinanceAPIError


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = BinanceAdapter.__new__(BinanceAdapter)
        self.ad.base_url = "https://fapi.binance.com"
        self.ad.environment = "demo"
        self.ad.native_symbol = lambda s: f"{s}USDT"
        self.ad.fetch_instrument_spec = lambda s: SimpleNamespace(
            tick_size=0.1, step_size=0.001, min_size=0.001, ct_val=1.0)
        self.sent = []

    def _signed(self, result):
        def _req(method, path, params=None, **kwargs):
            self.sent.append((method, path, dict(params or {})))
            return result
        self.ad.signed_request = _req
        return self.ad


class PlaceOrderTest(_Base):
    def test_non_positive_quantity_is_refused(self):
        """0 张 / 负数张 ⇒ `ValueError`（**不静默改成 1** —— 那是凭空放大仓位）。"""
        for bad in (0, -1, -0.5):
            with self.subTest(contracts=bad):
                with self.assertRaises(ValueError) as ctx:
                    self.ad.place_order("BTC", "long", bad)
                self.assertIn("必须为正数", str(ctx.exception))

    def test_non_dict_response_is_a_bad_response_not_a_success(self):
        """★ 响应不是 dict ⇒ 抛 `bad_response`：**不能确认受理就不许假装成功**。"""
        self._signed(["unexpected", "list"])
        with self.assertRaises(BinanceAPIError) as ctx:
            self.ad.place_order("BTC", "long", 1.0)
        self.assertEqual(ctx.exception.code, "bad_response")
        self.assertEqual(len(self.sent), 1, "护栏在**发出请求之后**（受理与否只能看回包）")

    def test_happy_path_normalises_the_order(self):
        self._signed({"orderId": 123, "clientOrderId": "t-r20e1", "status": "NEW",
                      "price": "0", "origQty": "1", "executedQty": "0"})
        out = self.ad.place_order("BTC", "long", 1.0)
        self.assertEqual(out["order_id"], "123")
        self.assertEqual(out["client_order_id"], "t-r20e1")
        self.assertEqual(out["status"], "NEW")
        self.assertEqual(out["side"], "buy", "side 归一化为小写 buy/sell")
        self.assertEqual(out["executedQty"], 0.0)
        self.assertEqual(self.sent[-1][0], "POST")

    def test_short_side_maps_to_sell(self):
        self._signed({"orderId": 1})
        self.ad.place_order("BTC", "short", 1.0)
        self.assertEqual(self.sent[-1][2]["side"], "SELL")


class CancelOrderTest(_Base):
    def test_numeric_order_id_goes_to_order_id(self):
        self._signed({"orderId": 999, "status": "CANCELED"})
        self.ad.cancel_order("BTC", order_id="999")
        params = self.sent[-1][2]
        self.assertEqual(params["orderId"], "999")
        self.assertNotIn("origClientOrderId", params)

    def test_non_numeric_order_id_becomes_orig_client_order_id(self):
        """★ 审计 D6：上游回退链会把 client text（`t-r20e*`，非数字）当 order_id 传来；
        Binance 只认纯数字 `orderId`，混族直传会被 `-2011` 拒撤 ⇒ **孤儿入场单裸挂**。"""
        self._signed({"orderId": 1, "status": "CANCELED"})
        self.ad.cancel_order("BTC", order_id="t-r20e12345")
        params = self.sent[-1][2]
        self.assertEqual(params["origClientOrderId"], "t-r20e12345")
        self.assertNotIn("orderId", params)

    def test_explicit_client_order_id_is_honoured(self):
        self._signed({"orderId": 1, "status": "CANCELED"})
        self.ad.cancel_order("BTC", client_order_id="t-r20sl9")
        self.assertEqual(self.sent[-1][2]["origClientOrderId"], "t-r20sl9")

    def test_no_target_is_refused(self):
        """既没 order_id 也没 client_order_id ⇒ `ValueError`（**不猜撤哪张**）。"""
        with self.assertRaises(ValueError) as ctx:
            self.ad.cancel_order("BTC")
        self.assertIn("需 order_id 或 client_order_id", str(ctx.exception))
        self.assertEqual(self.sent, [], "拒撤必须发生在**发请求之前**")

    def test_status_is_echoed_so_a_fill_is_not_reported_as_cancelled(self):
        """★ 审计 D3：抢撤竞态下交易所回 `FILLED` ⇒ 必须回显 `FILLED`。

        旧实现硬编码 `CANCELED`，会把「**没撤掉、其实已成交**」伪报成撤成功
        —— 上游据此以为仓位/挂单已清，正是「受理 ≠ 撤掉」的假成功。
        """
        self._signed({"orderId": 7, "status": "FILLED"})
        out = self.ad.cancel_order("BTC", order_id="7")
        self.assertEqual(out["status"], "FILLED", "撤销响应必须回显交易所真实状态")

    def test_missing_status_is_unknown_not_cancelled(self):
        self._signed({"orderId": 7})
        out = self.ad.cancel_order("BTC", order_id="7")
        self.assertEqual(out["status"], "UNKNOWN", "读不到状态 ⇒ UNKNOWN，绝不默认成撤成功")

    def test_cancel_all_orders_posts_symbol(self):
        self._signed({"code": "200"})
        out = self.ad.cancel_all_orders("BTC")
        self.assertEqual(self.sent[-1][2], {"symbol": "BTCUSDT"})
        self.assertEqual(out["symbol"], "BTCUSDT")


if __name__ == "__main__":
    unittest.main()

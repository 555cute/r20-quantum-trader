"""Binance 账户/持仓/挂单读取的边界（第二百六十八刀）。

取数层的第一纪律在**行级**也要成立：回包整体结构不对 ⇒ **不假装有数据**
（`account_snapshot` 直接上抛 `bad_response`），而**单行坏掉只跳过该行**
（持仓/挂单列表里混进非 dict 不能毁掉整次读取）。
"""

import unittest

from r20_backend.exchanges.binance import BinanceAdapter, BinanceAPIError


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = BinanceAdapter.__new__(BinanceAdapter)
        self.ad.base_url = "https://fapi.binance.com"
        self.ad.native_symbol = lambda s: f"{s}USDT" if s else ""
        self.ad.canonical = lambda s: str(s).replace("USDT", "")
        self.sent = []

    def _req(self, payload):
        def _r(method, path, params=None, **kw):
            self.sent.append((method, path, dict(params or {})))
            return payload
        self.ad.signed_request = _r
        return self.ad


class AccountSnapshotTest(_Base):
    def test_non_dict_payload_is_a_bad_response(self):
        """整个结构不对 ⇒ **上抛**（不返回全是 0 的快照 —— 那等于凭 0 权益做决策）。"""
        for payload in (None, [], "html"):
            with self.subTest(payload=payload):
                self._req(payload)
                with self.assertRaises(BinanceAPIError) as ctx:
                    self.ad.account_snapshot()
                self.assertEqual(ctx.exception.code, "bad_response")


class PositionsTest(_Base):
    def test_bad_rows_are_skipped_and_sides_mapped(self):
        self._req([
            "not-a-dict",
            {"symbol": "BTCUSDT", "positionAmt": "0", "entryPrice": "1"},   # 零仓 ⇒ 不算持仓
            {"symbol": "BTCUSDT", "positionAmt": "2.5", "entryPrice": "100",
             "markPrice": "101", "leverage": "3", "unRealizedProfit": "5",
             "liquidationPrice": "0", "marginType": "CROSS"},
            {"symbol": "ETHUSDT", "positionAmt": "-1.5", "entryPrice": "10"},
        ])
        out = self.ad.positions()
        self.assertEqual([p["inst_id"] for p in out], ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(out[0]["side"], "long")
        self.assertEqual(out[1]["side"], "short", "负数量 ⇒ short（数量符号就是方向）")
        self.assertEqual(out[1]["size_signed"], -1.5)
        self.assertIsNone(out[0]["liq_price"], "强平价 0 ⇒ None（不是 0.0 冒充价格）")
        self.assertEqual(out[0]["margin_mode"], "cross")

    def test_non_list_response_is_empty(self):
        self._req({"code": "-1"})
        self.assertEqual(self.ad.positions(), [])


class OpenOrdersTest(_Base):
    def test_symbol_filter_is_optional_and_normalised(self):
        self._req([])
        self.ad.open_orders()
        self.assertEqual(self.sent[-1][2], {}, "不传 symbol ⇒ 不带过滤（读全部在途）")
        self.ad.open_orders("BTC")
        self.assertEqual(self.sent[-1][2], {"symbol": "BTCUSDT"},
                         "传 symbol ⇒ 换成交易所原生符号再过滤")

    def test_bad_rows_are_skipped(self):
        self._req([
            "not-a-dict",
            {"orderId": 7, "clientOrderId": "t-r20e1", "symbol": "BTCUSDT", "side": "BUY",
             "type": "LIMIT", "price": "100", "origQty": "1", "executedQty": "0",
             "status": "NEW", "time": 1},
        ])
        out = self.ad.open_orders()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["order_id"], "7", "ID 一律转 str（int64 精度风险）")
        self.assertEqual(out[0]["client_order_id"], "t-r20e1")

    def test_non_list_response_is_empty(self):
        self._req("html")
        self.assertEqual(self.ad.open_orders(), [])


class CreateOrderAliasTest(_Base):
    def test_create_order_delegates_to_place_order(self):
        """别名必须**如实委托**，不得另搞一套语义（否则两处行为会漂移）。"""
        calls = []
        self.ad.place_order = lambda *a, **kw: calls.append((a, kw)) or {"order_id": "1"}
        out = self.ad.create_order("BTC", "long", 2.0, price=100.0)
        self.assertEqual(out, {"order_id": "1"})
        self.assertEqual(calls, [(("BTC", "long", 2.0), {"price": 100.0})])


if __name__ == "__main__":
    unittest.main()

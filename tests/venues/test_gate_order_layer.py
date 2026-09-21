"""Gate 撤单与挂单归一（第二百七十一刀）。

| 语义 | 纪律 |
|---|---|
| `cancel_order` | `order_id` 为 `all`/`*`/空 ⇒ **全撤**（DELETE 不带 id、只带 contract）；否则撤指定单 |
| `_normalize_order_item` | 方向由**数量符号**定（0 ⇒ **空字符串**，不猜）；`size` 优先、缺失才用 `amount`；`size` 取绝对值、`size_signed` 保有符号；`reduce_only` 来自 `is_reduce_only`/`is_close`；`amount` 用 `Decimal` 规范（**不走 float**）|
| `list_open_orders` | ★ 合约不存在（`CONTRACT_NOT_FOUND`）⇒ **`[]`**（「该合约没有挂单」）；**其他错误一律上抛**（读失败不得伪装成没有）|
| `open_orders` | 非 list ⇒ `[]`；非 dict 行跳过 |
"""

import unittest

from r20_backend.exchanges.gate import GateAdapter, GateAPIError


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = GateAdapter.__new__(GateAdapter)
        self.ad.base_url = "https://api.gateio.ws"
        self.ad.native_symbol = lambda s: f"{s}_USDT" if s else ""
        self.ad.canonical = lambda s: str(s).split("_")[0]
        self.sent = []

    def _req(self, payload):
        def _r(method, path, params=None, **kw):
            self.sent.append((method, path, dict(params or {})))
            if isinstance(payload, Exception):
                raise payload
            return payload
        self.ad.signed_request = _r
        return self.ad


class CancelOrderTest(_Base):
    def test_all_or_empty_means_cancel_everything(self):
        """`all`/`*`/空 ⇒ **全撤**（DELETE 集合端点，不带 id）。"""
        for oid in ("all", "ALL", "*", "", None):
            with self.subTest(order_id=oid):
                self.sent.clear()
                self._req({"ok": True})
                self.ad.cancel_order("BTC", oid)
                method, path, params = self.sent[-1]
                self.assertEqual(method, "DELETE")
                self.assertNotIn("orders/", path, "全撤不走单笔端点")
                self.assertEqual(params, {"contract": "BTC_USDT"})

    def test_specific_order_id_goes_into_the_path(self):
        self._req({"ok": True})
        self.ad.cancel_order("BTC", "12345")
        _, path, params = self.sent[-1]
        self.assertTrue(path.endswith("/orders/12345"), path)
        self.assertEqual(params, {})


class NormalizeOrderItemTest(_Base):
    def _norm(self, o, default_symbol=""):
        return self.ad._normalize_order_item(o, default_symbol=default_symbol)

    def test_side_comes_from_the_sign_and_zero_means_no_side(self):
        self.assertEqual(self._norm({"size": 3})["side"], "buy")
        self.assertEqual(self._norm({"size": -3})["side"], "sell")
        self.assertEqual(self._norm({"size": 0})["side"], "",
                         "数量为 0 ⇒ **空字符串**（不猜方向）")

    def test_size_wins_but_amount_is_the_fallback(self):
        r = self._norm({"size": 2, "amount": "-9"})
        self.assertEqual(r["size_signed"], 2.0, "size 优先")
        r2 = self._norm({"amount": "-9"})
        self.assertEqual(r2["size_signed"], -9.0, "size 缺失才用 amount")
        r3 = self._norm({"size": 0, "amount": "-9"})
        self.assertEqual(r3["size_signed"], -9.0, "size 为 0 等价于缺失 ⇒ 继续找 amount")

    def test_absolute_and_signed_sizes_coexist(self):
        r = self._norm({"size": -2.5})
        self.assertEqual(r["size_signed"], -2.5)
        self.assertEqual(r["size"], 2.5)

    def test_reduce_only_and_identity_defaults(self):
        r = self._norm({"size": 1, "id": 777, "contract": "ETH_USDT", "is_close": True})
        self.assertEqual(r["order_id"], "777", "ID 一律 str（int64 精度）")
        self.assertEqual(r["base"], "ETH")
        self.assertTrue(r["reduce_only"])
        self.assertEqual(r["venue"], "gate")
        r2 = self._norm({"size": 1, "is_reduce_only": True})
        self.assertTrue(r2["reduce_only"])

    def test_amount_is_normalised_with_decimal_not_float(self):
        r = self._norm({"size": 1, "amount": "-0.30000000000000004"})
        self.assertEqual(r["amount"], "0.30000000000000004",
                         "用 Decimal 规范成 abs 字符串，不经 float 二次污染")
        bad = self._norm({"size": 1, "amount": "abc"})
        self.assertEqual(bad["amount"], "abc", "坏 amount ⇒ 原样保留（不炸）")

    def test_unparseable_size_is_skipped_not_fatal(self):
        """`size` 不是数字 ⇒ **跳过该来源**继续找（不炸、也不把方向猜成 sell）。"""
        r = self._norm({"size": "abc", "amount": "-4"})
        self.assertEqual(r["size_signed"], -4.0, "坏 size ⇒ 退到可解析的 amount")
        r2 = self._norm({"size": "abc"})
        self.assertEqual(r2["size_signed"], 0.0)
        self.assertEqual(r2["side"], "", "两个来源都坏 ⇒ 没方向（空串），不猜")


class OpenOrdersTest(_Base):
    def test_bad_rows_and_non_list_are_empty(self):
        self._req("html")
        self.assertEqual(self.ad.open_orders(), [])
        self._req([{"size": 1, "id": 1}, "not-a-dict", None])
        out = self.ad.open_orders()
        self.assertEqual([r["order_id"] for r in out], ["1"])

    def test_open_orders_asks_for_open_status_only(self):
        self._req([])
        self.ad.open_orders()
        _, path, params = self.sent[-1]
        self.assertEqual(path, "/api/v4/futures/usdt/orders")
        self.assertEqual(params["status"], "open")
        self.assertNotIn("contract", params, "全合约挂单：不带 contract")

    def test_list_open_orders_filters_by_contract(self):
        self._req([{"size": 1, "id": 5}])
        out = self.ad.list_open_orders("BTC")
        self.assertEqual(self.sent[-1][2]["contract"], "BTC_USDT")
        self.assertEqual(out[0]["base"], "BTC", "缺 contract 时用传入的标的兜底")

    def test_missing_contract_means_no_orders(self):
        """★ 合约不存在 ⇒ `[]`（**「该合约没有挂单」**，不是读失败）。"""
        for message in ("CONTRACT_NOT_FOUND", "contract not found"):
            with self.subTest(message=message):
                self._req(GateAPIError("CONTRACT_NOT_FOUND", message))
                self.assertEqual(self.ad.list_open_orders("NOPE"), [])

    def test_other_errors_are_re_raised(self):
        """★★ 其他错误**必须上抛**：读失败不得伪装成「没有挂单」（否则会重复挂单）。"""
        self._req(GateAPIError("INVALID_KEY", "bad key"))
        with self.assertRaises(GateAPIError):
            self.ad.list_open_orders("BTC")

    def test_non_list_payload_for_a_contract_is_empty(self):
        """带 `contract` 查询返回非 list（网关 HTML 等）⇒ `[]`（结构不对不是「有挂单」）。"""
        self._req({"detail": "upstream"})
        self.assertEqual(self.ad.list_open_orders("BTC"), [])


if __name__ == "__main__":
    unittest.main()

"""Binance 保护腿列表与市价全平（第二百六十四刀）。

| 语义 | 纪律 |
|---|---|
| `list_protective_orders` | 回包非列表 ⇒ `[]`；非 dict 行跳过；`algoId` 缺失退回 `orderId`；`side` 小写、`triggerPrice` 转 float |
| `fast_close_position`（审计 B1）| **净模式带 `reduceOnly=true`**（与云端 TP 竞态时**只减不增**，绝不反向开新仓）；**对冲模式按所方契约禁传 `reduceOnly`**、显式钉 `positionSide`；★ **双向同存且未指明方向 ⇒ 拒绝盲平**（`closed:False`，由上层 fail-closed）|
| 无仓/零仓 | `closed:False` + 明确 reason（**受理 ≠ 平掉**的同族：没平就别说平）|
"""

import unittest
from unittest.mock import MagicMock

from r20_backend.exchanges.binance import BinanceAdapter


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = BinanceAdapter.__new__(BinanceAdapter)
        self.ad.base_url = "https://fapi.binance.com"
        self.ad.environment = "demo"
        self.ad.native_symbol = lambda s: f"{s}USDT"
        self.orders = []
        self.ad.place_order = lambda *a, **kw: (self.orders.append((a, kw)) or {"order_id": "1"})

    def _positions(self, rows):
        self.ad.positions = lambda: rows

    @staticmethod
    def _pos(side, size, *, raw_side=None, size_signed=None):
        raw = {"positionSide": raw_side} if raw_side else {}
        return {"inst_id": "BTCUSDT", "side": side, "raw": raw,
                "size_signed": size if size_signed is None else size_signed}


class ListProtectiveOrdersTest(_Base):
    def _list(self, payload):
        self.ad.build_algo_open_orders_request = lambda **kw: {"path": "/fapi/v1/algoOrder"}
        self.ad._private_algo_send = lambda req: payload
        return self.ad.list_protective_orders()

    def test_non_list_response_becomes_an_empty_list(self):
        """⚠️ 如实钉住：**回包结构不对 ⇒ 空列表**（而不是抛错）。

        这条与「读不到 ≠ 没有」存在张力：空列表在下游与「**确实没有保护腿**」不可区分
        ⇒ 有可能被当成「无需补挂」。此处只钉现状，列为待议（改它属钱路语义变更）。
        """
        for payload in (None, {}, "html", {"code": "-1"}):
            with self.subTest(payload=payload):
                self.assertEqual(self._list(payload), [])

    def test_rows_are_normalised_and_bad_rows_skipped(self):
        out = self._list([
            {"algoId": 11, "symbol": "BTCUSDT", "side": "SELL", "triggerPrice": "100.5",
             "algoType": "CONDITIONAL"},
            "not-a-dict",
            {"orderId": 12, "symbol": "BTCUSDT", "side": "BUY", "triggerPrice": 99,
             "type": "STOP"},
        ])
        self.assertEqual([r["algo_id"] for r in out], ["11", "12"],
                         "algoId 缺失时退回 orderId")
        self.assertEqual(out[0]["side"], "sell")
        self.assertEqual(out[0]["trigger_price"], 100.5)
        self.assertEqual(out[1]["trigger_price"], 99.0)
        self.assertEqual(out[1]["type"], "STOP")


class FastClosePositionTest(_Base):
    def test_no_position_reports_not_closed(self):
        self._positions([])
        res = self.ad.fast_close_position("BTC")
        self.assertFalse(res["closed"])
        self.assertEqual(res["reason"], "无持仓")
        self.assertEqual(self.orders, [], "没仓就不得下单")

    def test_zero_size_reports_not_closed(self):
        self._positions([self._pos("long", 0.0)])
        res = self.ad.fast_close_position("BTC")
        self.assertFalse(res["closed"])
        self.assertEqual(res["reason"], "持仓为0")
        self.assertEqual(self.orders, [])

    def test_both_directions_without_pos_side_refuses_to_blind_close(self):
        """★ 对冲模式双向同存、调用方又没指明方向 ⇒ **拒绝盲平**（不猜平哪条腿）。"""
        self._positions([self._pos("long", 1.0), self._pos("short", 2.0)])
        res = self.ad.fast_close_position("BTC")
        self.assertFalse(res["closed"])
        self.assertIn("拒绝盲平", res["reason"])
        self.assertEqual(self.orders, [], "盲平等于随机砍一条腿 ⇒ 必须一条都不发")

    def test_pos_side_filters_to_that_leg(self):
        self._positions([self._pos("long", 1.0, raw_side="LONG"),
                         self._pos("short", 2.0, raw_side="SHORT")])
        self.ad.fast_close_position("BTC", pos_side="short")
        (args, kwargs) = self.orders[-1]
        self.assertEqual(args[1], "BUY", "平空 = BUY")
        self.assertEqual(kwargs["position_side"], "SHORT")

    def test_net_mode_uses_reduce_only(self):
        """★ 净模式必须带 `reduceOnly`：与云端 TP 竞态时**只减不增**，绝不反向开新仓。"""
        self._positions([self._pos("long", 3.0)])          # 无 positionSide ⇒ 净模式
        self.ad.fast_close_position("BTC")
        (args, kwargs) = self.orders[-1]
        self.assertEqual(args[1], "SELL", "平多 = SELL")
        self.assertEqual(args[2], 3.0, "数量取绝对持仓")
        self.assertIs(kwargs.get("reduce_only"), True)

    def test_hedge_mode_pins_the_leg_and_never_sends_reduce_only(self):
        """★ 对冲模式按所方契约**禁传 `reduceOnly`**（传了会被拒），改为显式钉腿。"""
        self._positions([self._pos("long", 3.0, raw_side="LONG")])
        self.ad.fast_close_position("BTC")
        (_, kwargs) = self.orders[-1]
        self.assertEqual(kwargs["position_side"], "LONG")
        self.assertNotIn("reduce_only", kwargs)
        self.assertIsNone(kwargs["price"], "市价全平不挂限价")

    def test_signed_size_is_used_so_short_legs_close_by_absolute_size(self):
        self._positions([self._pos("short", 0.0, size_signed=-2.5)])
        self.ad.fast_close_position("BTC")
        (args, _) = self.orders[-1]
        self.assertEqual(args[2], 2.5, "空头 size_signed 为负 ⇒ 取下单量必须取绝对值")

    def test_other_symbols_are_ignored(self):
        self._positions([{"inst_id": "ETHUSDT", "side": "long", "size_signed": 1.0, "raw": {}}])
        res = self.ad.fast_close_position("BTC")
        self.assertEqual(res["reason"], "无持仓")


if __name__ == "__main__":
    unittest.main()

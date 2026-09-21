"""Binance 保护腿数量量化与双源合并视图（第二百六十九刀）。

| 语义 | 纪律 |
|---|---|
| 数量量化 | 下单量必须按交易所 `step_size` **向下取整**（`ROUND_DOWN`）并去掉尾随零 —— 量化错方向（向上）会被交易所拒单或**超出意图** |
| 合并视图 | 普通挂单与 Algo 条件单**分列**（`entries` / `conditional`）；`protection_total` **只数条件单**，并显式声明 `open_orders_only: False`（普通挂单 ≠ 保护单全集）；ID 一律 `str`（int64 精度风险）；非 dict 行跳过 |
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from r20_backend.exchanges.binance import BinanceAdapter


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = BinanceAdapter.__new__(BinanceAdapter)
        self.ad.base_url = "https://fapi.binance.com"
        self.ad.native_symbol = lambda s: f"{s}USDT" if s else ""
        self.sent = []

    def _capture(self, *, step="0.01"):
        self.ad.fetch_instrument_spec = lambda s: SimpleNamespace(step_size=float(step))
        self.ad._require_working_type = lambda wt: "CONTRACT_PRICE"

        def _send(**kw):
            self.sent.append(kw)
            return {"algoId": "1"}

        self._patcher = patch("r20_backend.exchanges.binance.send_protective_order",
                              side_effect=_send)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        return self.ad


class QuantityQuantisationTest(_Base):
    def test_quantity_is_rounded_down_to_step(self):
        """★ **向下**取整：1.239 / step 0.01 ⇒ 1.23（向上会被拒，或超出意图数量）。"""
        self._capture(step="0.01")
        self.ad.attach_protective_orders("BTC", "long", tp_px=110.0, sl_px=90.0,
                                         contracts=1.239)
        self.assertEqual([c["qty_str"] for c in self.sent], ["1.23", "1.23"],
                         "TP 与 SL 必须用同一个已量化数量")

    def test_trailing_zeros_are_stripped(self):
        self._capture(step="0.01")
        self.ad.attach_protective_orders("BTC", "long", tp_px=110.0, contracts=5.0)
        self.assertEqual(self.sent[0]["qty_str"], "5", "不要下发 5.00 这类尾随零")

    def test_zero_quantity_sends_no_quantity(self):
        """数量为 0 ⇒ **不传数量**（由所方语义决定是 closePosition 还是拒绝），不伪造一个数。"""
        self._capture(step="0.01")
        self.ad.attach_protective_orders("BTC", "long", tp_px=110.0)
        self.assertIsNone(self.sent[0]["qty_str"])

    def test_sides_and_types_are_pinned(self):
        """多头平仓腿 = 反向 SELL；两条腿类型分别是 TAKE_PROFIT_MARKET / STOP_MARKET。"""
        self._capture(step="0.01")
        self.ad.attach_protective_orders("BTC", "long", tp_px=110.0, sl_px=90.0,
                                         contracts=1.0)
        self.assertEqual([c["opp_side"] for c in self.sent], ["SELL", "SELL"])
        self.assertEqual([c["type_"] for c in self.sent],
                         ["TAKE_PROFIT_MARKET", "STOP_MARKET"])
        self.assertEqual([c["trigger_price"] for c in self.sent], [110.0, 90.0])


class MergedProtectionViewTest(_Base):
    def test_entries_and_conditional_are_kept_apart(self):
        """★ 普通挂单 ≠ 保护单全集：两源**分列**，并显式声明这一点。"""
        out = self.ad.merged_protection_view(
            normal_open=[{"orderId": 1}],
            algo_open=[{"algoId": 2}, {"algoId": 3}],
        )
        self.assertEqual(len(out["entries"]), 1)
        self.assertEqual(len(out["conditional"]), 2)
        self.assertEqual(out["protection_total"], 2, "只数条件单（真正的保护腿）")
        self.assertIs(out["open_orders_only"], False,
                      "必须显式否认「普通挂单即保护单全集」")

    def test_non_dict_rows_are_skipped(self):
        out = self.ad.merged_protection_view(
            normal_open=["html", None, {"orderId": 1}],
            algo_open=[{}, "x"],
        )
        self.assertEqual(len(out["entries"]), 1)
        self.assertEqual(len(out["conditional"]), 1)

    def test_ids_are_normalised_to_strings(self):
        """★ ID 一律 `str`：JSON Number 链路不保 int64 精度。"""
        out = self.ad.merged_protection_view(
            normal_open=[{"orderId": 9007199254740993,
                          "origClientOrderId": "t-r20e1"}],
            algo_open=[{"algoId": 9007199254740995, "clientAlgoId": 7}],
        )
        self.assertEqual(out["entries"][0]["orderId"], "9007199254740993")
        self.assertEqual(out["entries"][0]["origClientOrderId"], "t-r20e1")
        self.assertEqual(out["conditional"][0]["algoId"], "9007199254740995")
        self.assertEqual(out["conditional"][0]["clientAlgoId"], "7")

    def test_none_values_are_left_alone(self):
        out = self.ad.merged_protection_view(normal_open=[{"orderId": None}],
                                             algo_open=[])
        self.assertIsNone(out["entries"][0]["orderId"], "真缺 ID 就保持 None，不写成 'None'")


if __name__ == "__main__":
    unittest.main()

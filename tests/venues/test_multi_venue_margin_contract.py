from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from r20_backend.dashboard_payload.multi_venue import collect_cross_venue_positions


class MultiVenueMarginContractTests(unittest.TestCase):
    """外所（Gate / Binance）持仓名义价值与保证金口径契约测试。

    防止 Gate 等交易所因合约张数单位（如 BTC 1 张 = 0.0001 BTC）
    直接乘单价导致名义价值被放大万倍、保证金失真爆表的回归。
    """

    def test_gate_contract_multiplier_notional_and_margin_precedence(self):
        """Gate 返回 170 张合约时，必须优先使用官方 value/margin 而非张数乘市价。"""
        gate_pos = [{
            "venue": "gate",
            "inst_id": "BTC_USDT",
            "base": "BTC",
            "side": "long",
            "size_signed": 170.0,
            "entry_price": 81020.0,
            "mark_price": 81100.0,
            "leverage": 6.0,
            "margin": 230.59,
            "notional": 1378.69,
            "unrealized_pnl": 1.35,
            "raw": {
                "size": 170,
                "value": "1378.69",
                "margin": "230.59",
                "initial_margin": "230.82",
            }
        }]

        mock_gate = MagicMock()
        mock_gate.positions.return_value = gate_pos
        mock_gate.open_orders.return_value = []
        mock_gate.list_protective_orders.return_value = []

        mock_binance = MagicMock()
        mock_binance.positions.return_value = []
        mock_binance.open_orders.return_value = []
        mock_binance.list_protective_orders.return_value = []

        def get_ad(v, **kw):
            if v == "gate":
                return mock_gate
            return mock_binance

        positions = []
        pending_orders = []
        with patch("r20_backend.exchanges.get_adapter", side_effect=get_ad):
            long_c, short_c, upl = collect_cross_venue_positions(
                positions, pending_orders, 0, 0, 0.0
            )

        self.assertEqual(len(positions), 1)
        p = positions[0]
        self.assertEqual(p["instId"], "BTC-USDT-SWAP")
        self.assertEqual(p["notional_usdt"], 1378.69, "名义价值必须使用 Gate 官方 1378.69U，严禁放大万倍")
        self.assertEqual(p["margin_usdt"], 230.59, "保证金必须使用 Gate 官方 230.59U，严禁放大万倍")
        self.assertLess(p["margin_usdt"], 1000.0)

    def test_binance_official_notional_precedence(self):
        """Binance 持仓必须优先读取官方 notional 字段。"""
        binance_pos = [{
            "venue": "binance",
            "inst_id": "ETHUSDT",
            "base": "ETH",
            "side": "long",
            "size_signed": 0.527,
            "entry_price": 2618.5,
            "mark_price": 2627.82,
            "leverage": 6.0,
            "margin": 0.0,
            "notional": 1384.86,
            "unrealized_pnl": 4.91,
            "raw": {
                "notional": "1384.86",
                "isolatedMargin": "0",
            }
        }]

        mock_gate = MagicMock()
        mock_gate.positions.return_value = []
        mock_gate.open_orders.return_value = []
        mock_gate.list_protective_orders.return_value = []

        mock_binance = MagicMock()
        mock_binance.positions.return_value = binance_pos
        mock_binance.open_orders.return_value = []
        mock_binance.list_protective_orders.return_value = []

        def get_ad(v, **kw):
            if v == "binance":
                return mock_binance
            return mock_gate

        positions = []
        pending_orders = []
        with patch("r20_backend.exchanges.get_adapter", side_effect=get_ad):
            collect_cross_venue_positions(positions, pending_orders, 0, 0, 0.0)

        self.assertEqual(len(positions), 1)
        p = positions[0]
        self.assertEqual(p["notional_usdt"], 1384.86)
        self.assertEqual(p["margin_usdt"], round(1384.86 / 6.0, 2))


class CrossVenuePendingOrderMarginTests(unittest.TestCase):
    """外所**挂单**保证金口径（aa6d4e0 起：唯一权威是后端，且绝不猜面值）。

    挂单表的「保证金」列此前由前端自维护面值表推算；面值表与池子一旦漂移，
    屏幕上就是一个凭空捏造的仓位金额。现在改由后端按**适配器实况面值**折算，
    面值不可得时给 None（前端回落原生张数）。
    """

    def _run(self, venue, order, spec):
        pos = [{
            "venue": venue, "inst_id": "BTC_USDT", "base": "BTC", "side": "long",
            "size_signed": 1.0, "entry_price": 80000.0, "mark_price": 80000.0,
            "leverage": 5.0, "margin": 0.0, "notional": 0.0,
            "unrealized_pnl": 0.0, "raw": {},
        }]
        ad = MagicMock()
        ad.positions.return_value = pos
        ad.open_orders.return_value = [order]
        ad.list_protective_orders.return_value = []
        ad.fetch_instrument_spec.return_value = spec
        other = MagicMock()
        other.positions.return_value = []
        other.open_orders.return_value = []
        other.list_protective_orders.return_value = []

        def get_ad(v, **kw):
            return ad if v == venue else other

        positions, pending = [], []
        with patch("r20_backend.exchanges.get_adapter", side_effect=get_ad):
            collect_cross_venue_positions(positions, pending, 0, 0, 0.0)
        return pending

    def _order(self):
        return {"order_id": "o1", "price": "80000", "size": 2, "side": "buy",
                "leverage": "5", "contract": "BTC_USDT", "id": "o1"}

    def test_contract_venue_uses_adapter_spec_ct_val(self):
        """合约语义（Gate）：名义 = 张数 × **适配器实况面值** × 价，保证金 = /杠杆。"""
        spec = MagicMock()
        spec.ct_val = 0.0001
        pending = self._run("gate", self._order(), spec)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["margin_usdt"], round(2 * 0.0001 * 80000 / 5.0, 2))
        self.assertEqual(pending[0]["lever"], "5x", "展示杠杆必须用实况杠杆，不能写死 3x")

    def test_missing_spec_gives_no_number_not_a_guess(self):
        """面值不可得 ⇒ None（前端回落原生张数）。**严禁**按币名猜 0.0001/0.01/1.0。"""
        spec = MagicMock()
        spec.ct_val = 0.0
        pending = self._run("gate", self._order(), spec)
        self.assertEqual(len(pending), 1)
        self.assertIsNone(pending[0]["margin_usdt"],
                          "面值不可得时给了数字 ⇒ 前端会显示捏造的保证金")

    def test_adapter_without_spec_api_also_gives_none(self):
        """适配器根本没有 `fetch_instrument_spec` 时同样给 None（不回落猜测）。"""
        ad = MagicMock(spec=["positions", "open_orders", "list_protective_orders"])
        ad.positions.return_value = []
        ad.open_orders.return_value = [self._order()]
        ad.list_protective_orders.return_value = []
        other = MagicMock()
        other.positions.return_value = []
        other.open_orders.return_value = []
        other.list_protective_orders.return_value = []

        def get_ad(v, **kw):
            return ad if v == "gate" else other

        positions, pending = [], []
        with patch("r20_backend.exchanges.get_adapter", side_effect=get_ad):
            collect_cross_venue_positions(positions, pending, 0, 0, 0.0)
        self.assertTrue(pending, "挂单行没被装配出来（用例前提不成立）")
        self.assertIsNone(pending[0]["margin_usdt"])

    def test_binance_base_asset_semantics(self):
        """币本位语义（Binance）：名义 = 币数 × 价（面值恒 1），不需要 ct_val。"""
        spec = MagicMock()
        spec.ct_val = 0.0
        pending = self._run("binance", self._order(), spec)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["margin_usdt"], round(2 * 80000 / 5.0, 2))

    def test_no_coin_name_guess_chain_in_source(self):
        """源码钉：禁止再出现「按币名猜面值」的兜底链（那是捏造数字的入口）。"""
        src = (Path(__file__).resolve().parents[2]
               / "r20_backend" / "dashboard_payload" / "multi_venue.py").read_text(encoding="utf-8")
        for guess in ("0.0001 if base_sym", "0.01 if base_sym", 'ct_val or 0.0001'):
            self.assertNotIn(guess, src, f"按币名猜面值的兜底链回流：{guess}")


if __name__ == "__main__":
    unittest.main()

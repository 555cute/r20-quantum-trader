"""多所看板聚合装配（`r20_backend/dashboard_payload/multi_venue.py`）残余分支收口测试 —— 第 343 刀。

本模块 471 行，是操盘控制台跨所视野核心装配引擎：
- 云端保护触发价提取（`_protection_triggers`）：非字典跳过、非正价格过滤、Gate auto_size 方向严格校验；
- 孤儿保护腿聚合（`_venue_orphan_summary`）：归属计算异常兜底报告与结构对齐；
- 保护判据合成（`_protection_verdict`）：不可读早退、异常容错、到期态（expiring 与 never）研判；
- 跨所持仓与挂单收集（`collect_cross_venue_positions`）：零持仓过滤、异常杠杆修正、挂单方向与时间推导、合约面值异常回退。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from r20_backend.dashboard_payload.multi_venue import (
    _protection_triggers,
    _protection_verdict,
    _venue_orphan_summary,
    collect_cross_venue_positions,
)


class DashboardMultiVenueTailsTests(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. 保护触发价提取 (_protection_triggers)
    # -------------------------------------------------------------------------
    def test_protection_triggers_filters_invalid_items_and_negative_prices(self):
        algos = [
            "not-a-dict",  # line 32
            {"symbol": "BTC", "triggerPrice": "-500"},  # line 44
            {"symbol": "BTC", "triggerPrice": "0"},     # line 44
            {"symbol": "BTC", "triggerPrice": "50000", "type": "STOP", "side": "sell"},
            {"symbol": "BTC", "triggerPrice": "60000", "type": "TAKE_PROFIT", "side": "sell"},
        ]
        sl, tp = _protection_triggers(algos, "BTC", "sell")
        self.assertEqual(sl, 50000.0)
        self.assertEqual(tp, 60000.0)

    def test_protection_triggers_gate_auto_size_direction_filtering(self):
        # 针对 sell / short 方向过滤不包含 short 或 close_long 的条目
        algos_sell = [
            {"contract": "BTC_USDT", "trigger": {"price": "45000"}, "type": "STOP", "initial": {"auto_size": "wrong_dir"}},  # line 63
            {"contract": "BTC_USDT", "trigger": {"price": "46000"}, "type": "STOP", "initial": {"auto_size": "close_long"}},
        ]
        sl_sell, _ = _protection_triggers(algos_sell, "BTC", "sell")
        self.assertEqual(sl_sell, 46000.0)

        # 针对 buy / long 方向过滤不包含 long 或 close_short 的条目 (lines 64-66)
        algos_buy = [
            {"contract": "BTC_USDT", "trigger": {"price": "55000"}, "type": "STOP", "initial": {"auto_size": "other_dir"}},  # line 66
            {"contract": "BTC_USDT", "trigger": {"price": "54000"}, "type": "STOP", "initial": {"auto_size": "close_short"}},
        ]
        sl_buy, _ = _protection_triggers(algos_buy, "BTC", "buy")
        self.assertEqual(sl_buy, 54000.0)

    # -------------------------------------------------------------------------
    # 2. 孤儿保护腿聚合 (_venue_orphan_summary)
    # -------------------------------------------------------------------------
    def test_venue_orphan_summary_attribute_exception_handled(self):
        # 归属函数抛出异常时回退结构并披露 error (lines 107-109)
        with patch("scripts.trader.venue_protection.attribute_protective_orders", side_effect=RuntimeError("ledger crash")):
            res = _venue_orphan_summary([], [], [], readable=True)
            self.assertFalse(res["readable"])
            self.assertIn("RuntimeError: ledger crash", res.get("error", ""))
            self.assertEqual(res["ledgerRows"], "unknown")

    # -------------------------------------------------------------------------
    # 3. 保护判据合成 (_protection_verdict)
    # -------------------------------------------------------------------------
    def test_protection_verdict_scan_exception_handled(self):
        # 扫描保护单抛异常时安全返回默认 out (line 170)
        with patch("scripts.trader.venue_protection.scan_protective_orders", side_effect=RuntimeError("scan fail")):
            res = _protection_verdict([], "BTC", "long", 1.0, readable=True)
            self.assertEqual(res["protectionStatus"], "unknown")
            self.assertIsNone(res["protectionCoveragePct"])

    def test_protection_verdict_expiring_and_never_branches(self):
        # 1) expiring 分支 (line 194)
        mock_expiring = {"expiring": True, "ours": [{"kind": "sl", "remaining_s": 300}]}
        with patch("scripts.trader.venue_protection.scan_protective_orders", return_value=mock_expiring):
            res1 = _protection_verdict([], "BTC", "long", 1.0, readable=True)
            self.assertEqual(res1["protectionExpiry"], "expiring")

        # 2) never 分支 (line 196)
        mock_never = {"ours": [{"kind": "sl", "expiry_state": "never", "remaining_s": 999999}]}
        with patch("scripts.trader.venue_protection.scan_protective_orders", return_value=mock_never):
            res2 = _protection_verdict([], "BTC", "long", 1.0, readable=True)
            self.assertEqual(res2["protectionExpiry"], "never")

    # -------------------------------------------------------------------------
    # 4. 跨所持仓与挂单收集 (collect_cross_venue_positions)
    # -------------------------------------------------------------------------
    def test_collect_cross_venue_positions_positions_filtering_and_leverage(self):
        mock_ad = MagicMock()
        mock_ad.positions.return_value = [
            {"size_signed": 0},  # line 271: 零持仓忽略
            {"size_signed": 1.5, "base": "BTC", "leverage": -1, "entry_price": 50000},  # line 286: 非法杠杆回退 3.0
        ]
        mock_ad.open_orders.return_value = []
        mock_ad.list_protective_orders.return_value = []

        with patch("r20_backend.exchanges.get_adapter", return_value=mock_ad):
            positions, orders = [], []
            l, s, upl = collect_cross_venue_positions(positions, orders, 0, 0, 0.0)
            self.assertEqual(len(positions), 2)  # binance 与 gate 各 1 笔
            self.assertEqual(positions[0]["lever"], "3")
            self.assertEqual(l, 2)

    def test_collect_cross_venue_positions_orders_side_and_time_fallbacks(self):
        mock_ad = MagicMock()
        mock_ad.positions.return_value = []
        mock_ad.open_orders.return_value = [
            # 1) 无 side，从正数 size 推导 buy；创建时间无效字符串回退 0 (line 408)；负杠杆回退 3.0x (line 414)
            {"base": "ETH", "size": "2.0", "price": "3000", "create_time": "invalid_date", "leverage": -1},
            # 2) 无 side，从负数 size 推导 sell (line 379)
            {"base": "SOL", "size": "-5.0", "price": "150"},
            # 3) 无 side，非数值 size (line 381 & line 389)
            {"base": "DOGE", "size": "not-numeric", "price": "0.1"},
        ]
        mock_ad.list_protective_orders.return_value = []

        with patch("r20_backend.exchanges.get_adapter", return_value=mock_ad):
            positions, orders = [], []
            collect_cross_venue_positions(positions, orders, 0, 0, 0.0)
            # binance 3 单 + gate 3 单
            self.assertEqual(len(orders), 6)
            # 第一单：buy, cTime='', lever=3x
            self.assertEqual(orders[0]["side"], "buy")
            self.assertEqual(orders[0]["cTime"], "")
            self.assertEqual(orders[0]["lever"], "3x")
            # 第二单：sell
            self.assertEqual(orders[1]["side"], "sell")
            # 第三单：sz 保留原始非数值串
            self.assertEqual(orders[2]["sz"], "not-numeric")

    def test_collect_cross_venue_positions_gate_margin_exception_handled(self):
        mock_ad = MagicMock()
        mock_ad.positions.return_value = []
        mock_ad.open_orders.return_value = [{"base": "BTC", "size": 1.0, "price": 50000, "side": "buy"}]
        mock_ad.capabilities.quantity_unit = "contracts"
        mock_ad.fetch_instrument_spec.side_effect = RuntimeError("spec contract error")

        with patch("r20_backend.exchanges.get_adapter", return_value=mock_ad):
            positions, orders = [], []
            collect_cross_venue_positions(positions, orders, 0, 0, 0.0)
            # Gate 订单（索引 1）在面值抛异常时保证金安全为 None (line 432)
            self.assertIsNone(orders[1]["margin_usdt"])

    def test_collect_cross_venue_positions_outer_exception_suppressed(self):
        # 最外层环境轴或导入异常时静默 pass 并保留原有计数 (line 470)
        with patch("r20_backend.dashboard_payload.multi_venue._global_env_axis", side_effect=RuntimeError("axis crash")):
            l, s, upl = collect_cross_venue_positions([], [], 5, 3, 100.5)
            self.assertEqual(l, 5)
            self.assertEqual(s, 3)
            self.assertEqual(upl, 100.5)


if __name__ == "__main__":
    unittest.main()

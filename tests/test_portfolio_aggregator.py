"""US-006：多所 6 账户权益聚合与组合风险隔离测试（纯函数、零网络）。

测试覆盖：
1. demo 与 live 严格分区隔离
2. 3 账户全就绪时的总权益、可用、资产分布与利用率计算
3. 部分账户非就绪（degraded/unavailable）时的容错与单所计入
4. 零权益/空账户边缘防御（零除防护）
5. 风险等级（LOW / MEDIUM / HIGH）阶梯断言
"""
from __future__ import annotations

import unittest

from r20_backend.portfolio_aggregator import aggregate_venue_accounts


class PortfolioAggregatorTests(unittest.TestCase):
    def test_environment_validation(self):
        valid_venues = {
            "okx": {"status": "ready", "equity": 100.0, "available": 80.0},
            "binance": {"status": "unavailable", "equity": None},
            "gate": {"status": "unavailable", "equity": None},
        }
        res_demo = aggregate_venue_accounts(valid_venues, "demo")
        self.assertEqual(res_demo["environment"], "demo")

        res_live = aggregate_venue_accounts(valid_venues, "live")
        self.assertEqual(res_live["environment"], "live")

        with self.assertRaises(ValueError):
            aggregate_venue_accounts(valid_venues, "sandbox")
        with self.assertRaises(ValueError):
            aggregate_venue_accounts(valid_venues, "invalid")

    def test_full_ready_aggregation_and_distribution(self):
        venues = {
            "okx": {
                "status": "ready",
                "equity": 1000.0,
                "available": 800.0,
                "positions_count": 2,
                "open_orders_count": 1,
            },
            "binance": {
                "status": "ready",
                "equity": 2000.0,
                "available": 1400.0,
                "positions_count": 3,
                "open_orders_count": 2,
            },
            "gate": {
                "status": "ready",
                "equity": 1000.0,
                "available": 600.0,
                "positions_count": 1,
                "open_orders_count": 1,
            },
        }

        res = aggregate_venue_accounts(venues, "demo")
        # 1000 + 2000 + 1000 = 4000.0
        self.assertEqual(res["total_equity"], 4000.0)
        # 800 + 1400 + 600 = 2800.0
        self.assertEqual(res["total_available"], 2800.0)
        self.assertEqual(res["positions_count"], 6)
        self.assertEqual(res["open_orders_count"], 4)
        self.assertEqual(res["active_venues_count"], 3)
        self.assertEqual(set(res["reporting_venues"]), {"okx", "binance", "gate"})

        # 保证金占用: 4000 - 2800 = 1200.0
        self.assertEqual(res["margin_used"], 1200.0)
        # 利用率: 1200 / 4000 = 30.0% -> LOW
        self.assertEqual(res["utilization_pct"], 30.0)
        self.assertEqual(res["risk_level"], "LOW")

        # 资产分布:
        dist = res["asset_distribution"]
        self.assertAlmostEqual(dist["okx"]["share_pct"], 25.0)
        self.assertAlmostEqual(dist["binance"]["share_pct"], 50.0)
        self.assertAlmostEqual(dist["gate"]["share_pct"], 25.0)

    def test_partial_ready_aggregation(self):
        venues = {
            "okx": {
                "status": "ready",
                "equity": 1500.0,
                "available": 1000.0,
                "positions_count": 1,
                "open_orders_count": 0,
            },
            "binance": {
                "status": "degraded",
                "equity": None,
                "available": None,
            },
            "gate": {
                "status": "unavailable",
                "equity": None,
            },
        }

        res = aggregate_venue_accounts(venues, "live")
        self.assertEqual(res["total_equity"], 1500.0)
        self.assertEqual(res["total_available"], 1000.0)
        self.assertEqual(res["active_venues_count"], 1)
        self.assertEqual(res["reporting_venues"], ["okx"])

        dist = res["asset_distribution"]
        self.assertEqual(dist["okx"]["share_pct"], 100.0)
        self.assertEqual(dist["binance"]["share_pct"], 0.0)
        self.assertEqual(dist["gate"]["share_pct"], 0.0)

    def test_zero_division_guard(self):
        venues = {
            "okx": {"status": "ready", "equity": 0.0, "available": 0.0},
            "binance": {"status": "unavailable", "equity": None},
            "gate": {"status": "unavailable", "equity": None},
        }
        res = aggregate_venue_accounts(venues, "demo")
        self.assertEqual(res["total_equity"], 0.0)
        self.assertEqual(res["utilization_pct"], 0.0)
        self.assertEqual(res["risk_level"], "LOW")
        self.assertEqual(res["asset_distribution"]["okx"]["share_pct"], 0.0)

    def test_risk_level_thresholds(self):
        # Medium: >30% and <= 70%
        venues_med = {
            "okx": {"status": "ready", "equity": 1000.0, "available": 500.0}  # 50%
        }
        res_med = aggregate_venue_accounts(venues_med, "live")
        self.assertEqual(res_med["utilization_pct"], 50.0)
        self.assertEqual(res_med["risk_level"], "MEDIUM")

        # High: >70%
        venues_high = {
            "okx": {"status": "ready", "equity": 1000.0, "available": 100.0}  # 90%
        }
        res_high = aggregate_venue_accounts(venues_high, "live")
        self.assertEqual(res_high["utilization_pct"], 90.0)
        self.assertEqual(res_high["risk_level"], "HIGH")


if __name__ == "__main__":
    unittest.main()

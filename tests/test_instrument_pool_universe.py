"""Offline isolated test suite for Dynamic Instrument Pool & Universe Tiering.
Validates:
1. Tier classification (Tier-1 Bluechip vs Tier-2 Momentum).
2. Universe candidate scoring across liquidity, volatility, and funding rate.
3. Default universe integrity and automatic parameter backfill.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.instrument_pool as ip
from tests.risk_test_env import pin_baseline_risk_env


def setUpModule():
    # PoolCapacity 断言同向上限=3（基线）；隔离生产 .env 当前套件值
    pin_baseline_risk_env()


class InstrumentPoolUniverseTests(unittest.TestCase):
    def test_evaluate_instrument_tier(self):
        self.assertEqual(ip.evaluate_instrument_tier("BTC-USDT-SWAP", "BTC"), "tier_1_bluechip")
        self.assertEqual(ip.evaluate_instrument_tier("ETH-USDT-SWAP", "ETH"), "tier_1_bluechip")
        self.assertEqual(ip.evaluate_instrument_tier("SOL-USDT-SWAP", "SOL"), "tier_2_momentum")
        self.assertEqual(ip.evaluate_instrument_tier("DOGE-USDT-SWAP", "DOGE"), "tier_2_momentum")
        self.assertEqual(ip.evaluate_instrument_tier("SUI-USDT-SWAP", "SUI"), "tier_2_momentum")

    def test_score_universe_candidate(self):
        cand = {"instId": "SOL-USDT-SWAP", "name": "SOL"}
        # High liquidity, optimal volatility (3.5%), neutral funding
        scored = ip.score_universe_candidate(cand, vol_24h_usd=80_000_000, atr_pct=3.5, funding_rate=0.0001)
        self.assertIs(scored, cand)
        self.assertEqual(scored["tier"], "tier_2_momentum")
        self.assertEqual(scored["max_leverage"], 3)
        self.assertEqual(scored["sl_atr_mult"], 2.2)
        self.assertGreaterEqual(scored["universe_score"], 80.0)

        # Extreme high funding rate penalty
        cand_crowded = {"instId": "DOGE-USDT-SWAP", "name": "DOGE"}
        scored_crowded = ip.score_universe_candidate(cand_crowded, vol_24h_usd=50_000_000, atr_pct=3.0, funding_rate=0.0009)
        self.assertIs(scored_crowded, cand_crowded)
        self.assertLess(scored_crowded["universe_score"], scored["universe_score"])

    def test_load_instruments_ensures_tiers_and_risk_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "instrument_pool.json"
            with patch.object(ip, "POOL_FILE", missing):
                with patch("r20_exchange.runtime.get_exchange", side_effect=AssertionError("network")):
                    insts = ip.load_instruments()
        self.assertGreaterEqual(len(insts), 6)
        for item in insts:
            self.assertIn("tier", item)
            self.assertIn(item["tier"], ["tier_1_bluechip", "tier_2_momentum"])
            self.assertIn("max_leverage", item)
            self.assertIn("sl_atr_mult", item)
            self.assertEqual(item["ctVal"], 1)
            self.assertEqual(item["quantity_unit"], "base")
            if item["name"] in ("BTC", "ETH"):
                self.assertEqual(item["tier"], "tier_1_bluechip")
                self.assertEqual(item["max_leverage"], 5)
                self.assertEqual(item["sl_atr_mult"], 1.8)
            else:
                self.assertEqual(item["tier"], "tier_2_momentum")
                self.assertEqual(item["max_leverage"], 3)
                self.assertEqual(item["sl_atr_mult"], 2.2)

    def test_old_pool_file_backfills_tiers_without_rescaling(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool_path = Path(tmp) / "instrument_pool.json"
            pool_path.write_text(json.dumps({
                "version": 1,
                "instruments": [{
                    "instId": "ETH-USDT-SWAP",
                    "name": "ETH",
                    "base_sz": 3,
                    "ctVal": 0.1,
                    "tickSz": "0.01",
                    "minSz": "0.01",
                }],
            }), encoding="utf-8")
            with patch.object(ip, "POOL_FILE", pool_path):
                insts = ip.load_instruments()
            self.assertEqual(len(insts), 1)
            eth = insts[0]
            self.assertEqual(eth["tier"], "tier_1_bluechip")
            self.assertEqual(eth["max_leverage"], 5)
            self.assertEqual(eth["sl_atr_mult"], 1.8)
            self.assertEqual(eth["base_qty"], 0.3)
            self.assertEqual(eth["ctVal"], 1)
            pool_path.write_text(json.dumps({"version": 1, "instruments": insts}), encoding="utf-8")
            with patch.object(ip, "POOL_FILE", pool_path):
                again = ip.load_instruments()
            self.assertEqual(again[0]["base_qty"], 0.3)


class PoolCapacityNotHardcodedTests(unittest.TestCase):
    """后台曾把标的池上限硬编码为 6，加第 7 个币直接被 409 拒绝 —— 部署者反馈「扩容跑不起来」的真凶。"""

    APP = Path(__file__).resolve().parent.parent / "r20_backend" / "app.py"
    SEC = Path(__file__).resolve().parent.parent / "frontend" / "src" / "views" / "admin" / "SecurityPage.vue"

    def test_backend_uses_configurable_pool_cap(self):
        src = self.APP.read_text(encoding="utf-8")
        self.assertIn('os.getenv("R20_MAX_POOL_SIZE"', src, "池容量上限必须可由环境变量配置")
        self.assertIn("len(current) >= MAX_POOL_SIZE", src, "添加校验必须使用常量而非字面量 6")
        self.assertNotIn("len(current) >= 6", src, "检测到硬编码的 6 个币种上限回归")
        self.assertNotIn("最多允许 6 个币种", src, "检测到硬编码错误文案回归")

    def test_frontend_default_cap_not_stuck_at_six(self):
        src = self.SEC.read_text(encoding="utf-8")
        self.assertNotIn("maximum: 6", src, "前端默认池容量占位仍写死 6")

    def test_concurrent_positions_follow_pool_size(self):
        import ai_factor_trader as aft
        self.assertEqual(aft.MAX_CONCURRENT_POSITIONS, len(ip.load_instruments()),
                         "并发持仓上限应随标的池自动伸缩")
        self.assertEqual(aft.MAX_SAME_DIRECTION_POSITIONS, 3,
                         "同向持仓上限应固定为 3(防 Beta 踩踏)，不随池扩容放大")


if __name__ == "__main__":
    unittest.main()

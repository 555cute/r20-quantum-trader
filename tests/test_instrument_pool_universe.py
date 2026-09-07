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


if __name__ == "__main__":
    unittest.main()

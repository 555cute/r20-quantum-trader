import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.ai_brain_trader import construct_full_market_prompt, fetch_single_instrument_package
from scripts.risk_constants import (
    DAILY_LOSS_EQUITY_RATIO,
    MAX_DAILY_LOSS_USDT,
    MAX_SINGLE_ASSET_MARGIN,
    SINGLE_ASSET_EQUITY_RATIO,
)


class FakeExchange:
    def __init__(self):
        self.env = SimpleNamespace(exchange="binance", mode="demo")
        self.canceled = []

    def cancel_order(self, inst_id, order_id):
        self.canceled.append((inst_id, order_id))
        return {"ordId": order_id}


def _item():
    return {"instId": "ETH-USDT-SWAP", "name": "ETH", "type": "crypto", "precision": 2}


def _missing_files():
    return patch.multiple(
        "scripts.ai_brain_trader",
        NEWS_SENTIMENT_FILE="/tmp/r20-test-file-does-not-exist",
        AI_MEMORY_MD_FILE="/tmp/r20-test-file-does-not-exist",
        AI_MEMORY_FILE="/tmp/r20-test-file-does-not-exist",
    )


class TestAiBrainMarketIntegration(unittest.TestCase):
    def test_missing_adx_is_invalid_not_zero(self):
        with patch("scripts.ai_brain_trader.fetch_ticker", return_value={"last": "65000", "bidPx": "64990", "askPx": "65010", "open24h": "64000"}), \
             patch("scripts.ai_brain_trader.fetch_candles", return_value=[]), \
             patch("scripts.ai_brain_trader.fetch_funding_rate", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_open_interest", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_long_short_ratio", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_taker_volume", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_single_indicator", return_value={}):
            pkg = fetch_single_instrument_package(_item())
        self.assertIsNone(pkg["adx_1h"])
        self.assertEqual(pkg["data_quality"], "invalid")
        self.assertFalse(pkg["smart_money"]["available"])
        self.assertIsNone(pkg["fundingRate"])
        self.assertEqual(pkg["oiUsd"], "UNAVAILABLE")
        self.assertEqual(pkg["lsRatio"], "UNAVAILABLE")
        self.assertEqual(pkg["takerNetUsd"], "UNAVAILABLE")
        self.assertNotEqual(pkg["oiUsd"], 0)
        self.assertNotEqual(pkg["lsRatio"], 0)
        self.assertNotEqual(pkg["takerNetUsd"], 0)
        self.assertNotEqual(pkg["fundingRate"], 0)

    def test_binance_four_stats_reach_rendered_prompt(self):
        with patch("scripts.ai_brain_trader.fetch_ticker", return_value={"last": "2500", "bidPx": "2499", "askPx": "2501", "open24h": "2400"}), \
             patch("scripts.ai_brain_trader.fetch_candles", return_value=[]), \
             patch("scripts.ai_brain_trader.fetch_funding_rate", return_value=0.0371), \
             patch("scripts.ai_brain_trader.fetch_open_interest", return_value={"oi": "10", "oiUsd": "12340000"}), \
             patch("scripts.ai_brain_trader.fetch_long_short_ratio", return_value=2.31), \
             patch("scripts.ai_brain_trader.fetch_taker_volume", return_value={"buyVol": 888000.0, "sellVol": 111000.0}), \
             patch("scripts.ai_brain_trader.fetch_single_indicator", return_value={}):
            pkg = fetch_single_instrument_package(_item())
        self.assertEqual(pkg["fundingRate"], 0.0371)
        self.assertEqual(pkg["lsRatio"], 2.31)
        self.assertIn("万", pkg["oiUsd"])
        self.assertIn("万", pkg["takerNetUsd"])
        with _missing_files():
            prompt = construct_full_market_prompt([pkg], pos_summary="0多0空", active_positions_detail=[], current_time_str="2026-09-08 12:00:00")
        self.assertIn("0.0371", prompt)
        self.assertIn("2.31", prompt)
        self.assertIn(pkg["oiUsd"], prompt)
        self.assertIn(pkg["takerNetUsd"], prompt)

    def test_missing_derivatives_render_unavailable_not_zero(self):
        with patch("scripts.ai_brain_trader.fetch_ticker", return_value={"last": "2500", "bidPx": "2499", "askPx": "2501", "open24h": "2400"}), \
             patch("scripts.ai_brain_trader.fetch_candles", return_value=[]), \
             patch("scripts.ai_brain_trader.fetch_funding_rate", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_open_interest", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_long_short_ratio", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_taker_volume", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_single_indicator", return_value={}):
            pkg = fetch_single_instrument_package(_item())
        with _missing_files():
            prompt = construct_full_market_prompt([pkg], pos_summary="0多0空", active_positions_detail=[], current_time_str="2026-09-08 12:00:00")
        self.assertIn("UNAVAILABLE", prompt)
        self.assertNotIn("0.0371", prompt)
        self.assertIsNone(pkg["fundingRate"])
        self.assertEqual(pkg["oiUsd"], "UNAVAILABLE")
        self.assertEqual(pkg["lsRatio"], "UNAVAILABLE")
        self.assertEqual(pkg["takerNetUsd"], "UNAVAILABLE")

    def test_zero_funding_is_kept_as_zero(self):
        with patch("scripts.ai_brain_trader.fetch_ticker", return_value={"last": "2500", "bidPx": "2499", "askPx": "2501", "open24h": "2400"}), \
             patch("scripts.ai_brain_trader.fetch_candles", return_value=[]), \
             patch("scripts.ai_brain_trader.fetch_funding_rate", return_value=0.0), \
             patch("scripts.ai_brain_trader.fetch_open_interest", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_long_short_ratio", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_taker_volume", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_single_indicator", return_value={}):
            pkg = fetch_single_instrument_package(_item())
        self.assertEqual(pkg["fundingRate"], 0.0)
        with _missing_files():
            prompt = construct_full_market_prompt([pkg], pos_summary="0多0空", active_positions_detail=[], current_time_str="2026-09-08 12:00:00")
        self.assertIn("0.0%", prompt)

    def test_prompt_risk_budget_uses_min_absolute_and_ratio_caps(self):
        eq = 4000.0
        asset = min(MAX_SINGLE_ASSET_MARGIN, max(round(eq * SINGLE_ASSET_EQUITY_RATIO, 2), 1.0))
        daily = min(MAX_DAILY_LOSS_USDT, max(round(eq * DAILY_LOSS_EQUITY_RATIO, 2), 1.0))
        uncapped_asset = round(eq * SINGLE_ASSET_EQUITY_RATIO, 2)
        ctx = {}
        with _missing_files():
            prompt = construct_full_market_prompt([], pos_summary="无", active_positions_detail=[], current_time_str="2026-09-08 12:00:00", usdt_available=eq, runtime_context_out=ctx)
        self.assertIn(ctx["risk_budget"], prompt)
        self.assertEqual(prompt.count(ctx["risk_budget"]), 1)
        self.assertIn(f"{asset} USDT", ctx["risk_budget"])
        self.assertIn(f"-{daily} USDT", ctx["risk_budget"])
        if uncapped_asset != asset:
            self.assertNotIn(f"{uncapped_asset} USDT", ctx["risk_budget"])



if __name__ == "__main__":
    unittest.main()

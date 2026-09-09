import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.instrument_pool import (
    DEFAULT_INSTRUMENTS,
    canonical_inst_id,
    from_okx_instrument,
    load_instruments,
    normalize_pool_item,
    resolve_instrument_rules,
    venue_symbol,
)



class TestInstrumentPoolBaseUnits(unittest.TestCase):
    def test_canonical_inst_id_accepts_binance_symbol(self):
        self.assertEqual(canonical_inst_id("btcusdt"), "BTC-USDT-SWAP")
        self.assertEqual(canonical_inst_id("BTC-USDT"), "BTC-USDT-SWAP")
        self.assertEqual(canonical_inst_id("BTC-USDT-SWAP"), "BTC-USDT-SWAP")
        self.assertEqual(venue_symbol("BTC-USDT-SWAP", "binance"), "BTCUSDT")
        self.assertEqual(venue_symbol("SOLUSDT", "okx"), "SOL-USDT-SWAP")
        with self.assertRaises(ValueError):
            canonical_inst_id("BTC-USD-SWAP")

    def test_legacy_okx_contracts_migrate_once_to_base_qty(self):
        item = normalize_pool_item({
            "instId": "BTC-USDT-SWAP",
            "name": "BTC",
            "base_sz": 1,
            "ctVal": 0.01,
            "tickSz": "0.1",
            "minSz": "1",
            "lotSz": "0.1",
            "risk_per_trade_usd": 15.0,
        })
        self.assertEqual(item["ctVal"], 1)
        self.assertEqual(item["base_qty"], 0.01)
        self.assertEqual(item["base_sz"], 0.01)
        self.assertEqual(item["nativeCtVal"], 0.01)
        self.assertEqual(item["quantity_unit"], "base")
        self.assertEqual(item["tier"], "tier_1_bluechip")
        self.assertEqual(item["max_leverage"], 5)
        self.assertEqual(item["sl_atr_mult"], 1.8)
        self.assertEqual(item["minSz"], "0.01")
        self.assertEqual(item["lotSz"], "0.001")
        again = normalize_pool_item(item)
        self.assertEqual(again["base_qty"], 0.01)
        self.assertEqual(again["nativeCtVal"], 0.01)
        self.assertEqual(again["minSz"], item["minSz"])
        self.assertEqual(again["lotSz"], item["lotSz"])

    def test_raw_exchange_quantity_rules_are_converted_with_contract_value(self):
        item = from_okx_instrument({
            "instId": "BTC-USDT-SWAP", "baseCcy": "BTC", "ctVal": "0.01",
            "minSz": "0.1", "lotSz": "0.1", "tickSz": "0.1",
        })
        self.assertEqual(item["base_qty"], 0.001)
        self.assertEqual(item["minSz"], "0.001")
        self.assertEqual(item["lotSz"], "0.001")

    def test_adapter_normalized_metadata_is_not_multiplied_again(self):
        item = from_okx_instrument({
            "instId": "BTC-USDT-SWAP",
            "baseCcy": "BTC",
            "ctVal": "1",
            "nativeCtVal": "0.01",
            "lotSz": "0.001",
            "minSz": "0.001",
            "tickSz": "0.1",
            "minNotional": "5",
            "state": "live",
        })
        self.assertEqual(item["ctVal"], 1)
        self.assertEqual(item["base_qty"], 0.001)
        self.assertEqual(item["minSz"], "0.001")
        self.assertEqual(item["lotSz"], "0.001")
        self.assertEqual(item["nativeCtVal"], 0.01)
        self.assertEqual(item["tier"], "tier_1_bluechip")
        self.assertEqual(item["max_leverage"], 5)
        self.assertEqual(item["sl_atr_mult"], 1.8)
        self.assertIsNotNone(item["minNotional"])

    def test_from_okx_instrument_legacy_contracts_and_momentum_tier(self):
        item = from_okx_instrument({
            "instId": "SOL-USDT-SWAP",
            "baseCcy": "SOL",
            "ctVal": "1.0",
            "minSz": "7",
            "tickSz": "0.01",
            "state": "live",
        })
        self.assertEqual(item["tier"], "tier_2_momentum")
        self.assertEqual(item["max_leverage"], 3)
        self.assertEqual(item["sl_atr_mult"], 2.2)
        self.assertEqual(item["ctVal"], 1)
        self.assertEqual(item["quantity_unit"], "base")

    def test_load_instruments_has_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "instrument_pool.json"
            with patch("scripts.instrument_pool.POOL_FILE", missing):
                with patch("r20_exchange.runtime.get_exchange", side_effect=AssertionError("network")):
                    pool = load_instruments()
        self.assertTrue(pool)
        self.assertEqual(pool[0]["ctVal"], 1)
        self.assertEqual(pool[0]["quantity_unit"], "base")

    def test_missing_pool_uses_upstream_defaults_normalized_once(self):
        raw_btc = next(item for item in DEFAULT_INSTRUMENTS if item["name"] == "BTC")
        self.assertEqual(raw_btc["base_sz"], 1)
        self.assertEqual(raw_btc["ctVal"], 0.01)
        self.assertEqual(raw_btc["tier"], "tier_1_bluechip")
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "instrument_pool.json"
            with patch("scripts.instrument_pool.POOL_FILE", missing):
                with patch("r20_exchange.runtime.get_exchange", side_effect=AssertionError("network")):
                    pool = load_instruments()
        self.assertGreaterEqual(len(pool), 6)
        btc = next(item for item in pool if item["name"] == "BTC")
        self.assertEqual(btc["base_qty"], 0.01)
        self.assertEqual(btc["base_sz"], 0.01)
        self.assertEqual(btc["ctVal"], 1)
        self.assertEqual(btc["nativeCtVal"], 0.01)
        self.assertEqual(btc["quantity_unit"], "base")
        self.assertEqual(btc["tier"], "tier_1_bluechip")
        self.assertEqual(btc["max_leverage"], 5)
        self.assertEqual(btc["sl_atr_mult"], 1.8)
        eth = next(item for item in pool if item["name"] == "ETH")
        self.assertEqual(eth["base_qty"], 0.3)
        sol = next(item for item in pool if item["name"] == "SOL")
        self.assertEqual(sol["tier"], "tier_2_momentum")
        self.assertEqual(sol["max_leverage"], 3)
        self.assertEqual(sol["sl_atr_mult"], 2.2)

    def test_old_and_new_pool_files_readable_without_double_scale(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool_path = Path(tmp) / "instrument_pool.json"
            pool_path.write_text(json.dumps({
                "version": 1,
                "instruments": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "name": "BTC",
                        "base_sz": 1,
                        "ctVal": 0.01,
                        "tickSz": "0.1",
                        "minSz": "1",
                    },
                    {
                        "instId": "SOL-USDT-SWAP",
                        "name": "SOL",
                        "base_sz": 7,
                        "ctVal": 1.0,
                        "tickSz": "0.01",
                        "minSz": "0.01",
                    },
                ],
            }), encoding="utf-8")
            with patch("scripts.instrument_pool.POOL_FILE", pool_path):
                with patch("r20_exchange.runtime.get_exchange", side_effect=AssertionError("network")):
                    loaded = load_instruments()
            btc = next(item for item in loaded if item["name"] == "BTC")
            sol = next(item for item in loaded if item["name"] == "SOL")
            self.assertEqual(btc["base_qty"], 0.01)
            self.assertEqual(btc["ctVal"], 1)
            self.assertEqual(btc["nativeCtVal"], 0.01)
            self.assertEqual(btc["tier"], "tier_1_bluechip")
            self.assertEqual(btc["max_leverage"], 5)
            self.assertEqual(btc["sl_atr_mult"], 1.8)
            self.assertEqual(sol["base_qty"], 7.0)
            self.assertEqual(sol["tier"], "tier_2_momentum")
            self.assertEqual(sol["max_leverage"], 3)
            self.assertEqual(sol["sl_atr_mult"], 2.2)

            pool_path.write_text(json.dumps({"version": 1, "instruments": loaded}), encoding="utf-8")
            with patch("scripts.instrument_pool.POOL_FILE", pool_path):
                again = load_instruments()
            btc_again = next(item for item in again if item["name"] == "BTC")
            self.assertEqual(btc_again["base_qty"], 0.01)
            self.assertEqual(btc_again["nativeCtVal"], 0.01)

            pool_path.write_text(json.dumps({
                "version": 1,
                "instruments": [{
                    "instId": "BTC-USDT-SWAP",
                    "name": "BTC",
                    "tier": "tier_1_bluechip",
                    "max_leverage": 5,
                    "sl_atr_mult": 1.8,
                    "base_qty": 0.01,
                    "base_sz": 0.01,
                    "ctVal": 1,
                    "nativeCtVal": 0.01,
                    "tickSz": "0.1",
                    "minSz": "0.01",
                    "quantity_unit": "base",
                }],
            }), encoding="utf-8")
            with patch("scripts.instrument_pool.POOL_FILE", pool_path):
                fresh = load_instruments()
            self.assertEqual(len(fresh), 1)
            self.assertEqual(fresh[0]["base_qty"], 0.01)
            self.assertEqual(fresh[0]["ctVal"], 1)
            self.assertEqual(fresh[0]["tier"], "tier_1_bluechip")
            self.assertEqual(fresh[0]["max_leverage"], 5)

    def test_overlay_copies_live_rules_without_re_multiplying_lotsz(self):
        fake = SimpleNamespace(instruments=lambda: [{
            "instId": "BTC-USDT-SWAP",
            "baseCcy": "BTC",
            "state": "live",
            "ctVal": "1",
            "nativeCtVal": "0.01",
            "lotSz": "0.002",
            "minSz": "0.002",
            "tickSz": "0.1",
            "minNotional": "100",
            "settleCcy": "USDT",
        }])
        resolved = resolve_instrument_rules(
            [{"instId": "BTC-USDT-SWAP", "name": "BTC", "base_qty": 0.01, "ctVal": 1, "tickSz": "0.1"}],
            exchange=fake,
        )
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["lotSz"], "0.002")
        self.assertEqual(resolved[0]["minSz"], "0.002")
        self.assertEqual(resolved[0]["ctVal"], 1)
        self.assertEqual(resolved[0]["base_qty"], 0.01)
        self.assertEqual(resolved[0]["tier"], "tier_1_bluechip")
        self.assertEqual(resolved[0]["max_leverage"], 5)
        self.assertEqual(resolved[0]["sl_atr_mult"], 1.8)


if __name__ == "__main__":
    unittest.main()

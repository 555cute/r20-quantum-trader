import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.instrument_pool import (
    from_okx_instrument,
    load_instruments,
    normalize_pool_item,
    resolve_instrument_rules,
)


class TestInstrumentPoolBaseUnits(unittest.TestCase):
    def test_legacy_okx_contracts_migrate_once_to_base_qty(self):
        item = normalize_pool_item({
            "instId": "BTC-USDT-SWAP",
            "name": "BTC",
            "base_sz": 1,
            "ctVal": 0.01,
            "tickSz": "0.1",
            "minSz": "1",
            "risk_per_trade_usd": 15.0,
        })
        self.assertEqual(item["ctVal"], 1)
        self.assertEqual(item["base_qty"], 0.01)
        self.assertEqual(item["base_sz"], 0.01)
        self.assertEqual(item["nativeCtVal"], 0.01)
        self.assertEqual(item["quantity_unit"], "base")
        again = normalize_pool_item(item)
        self.assertEqual(again["base_qty"], 0.01)

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

    def test_load_instruments_has_no_network(self):
        with patch("r20_exchange.runtime.get_exchange", side_effect=AssertionError("network")):
            pool = load_instruments()
        self.assertTrue(pool)
        self.assertEqual(pool[0]["ctVal"], 1)
        self.assertEqual(pool[0]["quantity_unit"], "base")

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


if __name__ == "__main__":
    unittest.main()

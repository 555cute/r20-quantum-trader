"""Offline exchange identity and base-quantity contracts."""
from decimal import Decimal
import unittest
from unittest.mock import Mock, patch

from r20_exchange import runtime
from r20_exchange.okx import OKXExchange


class ExchangeRuntimeTests(unittest.TestCase):
    def tearDown(self):
        runtime.unfreeze_environment()

    def test_explicit_empty_configuration_never_loads_local_secrets(self):
        with patch.object(runtime.okx_runtime, "_load_dotenv", side_effect=AssertionError("must not read credentials")):
            environment = runtime.selected_environment({})
        self.assertEqual(environment.exchange, "okx")
        self.assertFalse(environment.configured)

    def test_binance_demo_never_falls_back_to_live_credentials(self):
        environment = runtime.selected_environment({"R20_EXCHANGE": "binance", "BINANCE_LIVE_API_KEY": "live", "BINANCE_LIVE_SECRET_KEY": "secret"})
        self.assertEqual(environment.base_url, "https://demo-fapi.binance.com")
        self.assertFalse(environment.configured)
        self.assertEqual(environment.api_key, "")

    def test_cycle_freezes_exchange_credentials_and_state_scope(self):
        frozen = runtime.freeze_environment({"R20_EXCHANGE": "binance", "BINANCE_DEMO_API_KEY": "first", "BINANCE_DEMO_SECRET_KEY": "one"})
        with patch.object(runtime.okx_runtime, "_load_dotenv", return_value={"R20_EXCHANGE": "okx", "R20_OKX_ENV": "live"}):
            self.assertEqual(runtime.selected_environment(), frozen)
            self.assertEqual(runtime.state_path("ledger.json"), runtime.state_path("ledger.json", frozen))
            runtime.unfreeze_environment()
            self.assertEqual(runtime.selected_environment().exchange, "okx")

    def test_account_and_environment_changes_do_not_share_runtime_files(self):
        environments = [runtime.ExchangeEnvironment("binance", "demo", "same-key", "first"), runtime.ExchangeEnvironment("binance", "live", "same-key", "first"), runtime.ExchangeEnvironment("binance", "demo", "same-key", "rotated"), runtime.ExchangeEnvironment("okx", "demo", "same-key", "first", "pass")]
        paths = {runtime.state_path("trading_ledger.json", environment) for environment in environments}
        self.assertEqual(len(paths), 4)
        with self.assertRaises(ValueError):
            runtime.state_path("../secrets", environments[0])

    def test_credentials_are_not_exposed_in_environment_repr(self):
        environment = runtime.ExchangeEnvironment("binance", "live", "PRIVATEKEY", "PRIVATESECRET")
        self.assertNotIn("PRIVATEKEY", repr(environment))
        self.assertNotIn("PRIVATESECRET", repr(environment))
        with self.assertRaises(ValueError):
            runtime.ExchangeEnvironment("binance", "live", base_url="https://example.com")


class OKXBaseQuantityTests(unittest.TestCase):
    def setUp(self):
        self.environment = runtime.ExchangeEnvironment("okx", "demo", "key", "secret", "pass")
        self.session = Mock()
        self.exchange = OKXExchange(self.environment, self.session)
        self.exchange._metadata["BTC-USDT-SWAP"] = {"instId": "BTC-USDT-SWAP", "ctVal": "0.01", "lotSz": "0.01", "minSz": "0.01", "tickSz": "0.1", "state": "live", "settleCcy": "USDT"}

    def test_small_bitcoin_notional_is_converted_to_contracts_once(self):
        with patch("r20_exchange.okx._request", return_value=[{"ordId": "entry"}]) as request:
            self.exchange.place_protected_limit_order("BTC-USDT-SWAP", "buy", "long", Decimal("0.001234"), Decimal("100000"), Decimal("102000"), Decimal("99000"))
        sent = request.call_args.args[2]
        base_quantity = Decimal(sent["sz"]) * Decimal("0.01")
        self.assertEqual(base_quantity, Decimal("0.0012"))
        self.assertLessEqual(base_quantity * Decimal(sent["px"]), Decimal("123.4"))
        self.assertEqual(len(sent["attachAlgoOrds"]), 1)
        self.session.get.assert_not_called()

    def test_existing_position_contracts_become_base_quantity(self):
        with patch("r20_exchange.okx._request", return_value=[{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "0.1", "avgPx": "100000"}]):
            positions = self.exchange.positions()
        self.assertEqual(Decimal(positions[0]["pos"]), Decimal("0.001"))
        metadata = self.exchange.instruments("BTC-USDT-SWAP")[0]
        self.assertEqual(Decimal(metadata["ctVal"]), Decimal(1))
        self.assertEqual(Decimal(metadata["lotSz"]), Decimal("0.0001"))

    def test_open_interest_contracts_are_converted_to_base_once(self):
        with patch.object(self.exchange, "_public", return_value=[{"oi": "100", "oiCcy": "1"}]):
            interest = self.exchange.open_interest("BTC-USDT-SWAP")
        self.assertEqual(Decimal(interest["oi"]) * Decimal("100000"), Decimal("100000"))

    def test_below_minimum_and_invalid_quotes_never_submit(self):
        with patch("r20_exchange.okx._request") as request:
            with self.assertRaises(ValueError):
                self.exchange.place_protected_limit_order("BTC-USDT-SWAP", "buy", "long", "0.00001", "100000", "102000", "99000")
            with self.assertRaises(ValueError):
                self.exchange.place_protected_limit_order("BTC-USDT-SWAP", "buy", "long", "0.001", "100000", "100500", "99000")
        request.assert_not_called()

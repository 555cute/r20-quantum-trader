"""多交易所适配层单元测试（全 mock、零网络、零真实交易所触碰）。

覆盖：符号转译矩阵、数量语义换算（币本位截断 vs 张数取整）、规格解析、
能力表声明、私有面 fail-closed 显式拒绝、只读行情归一与 fail-soft。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from r20_backend.exchanges import (
    BinanceAdapter,
    ExchangeCapabilityError,
    GateAdapter,
    InstrumentSpec,
    canonical_base,
    get_adapter,
    registry,
)


class TestSymbolMapping(unittest.TestCase):
    def setUp(self):
        self.bn = BinanceAdapter()
        self.gt = GateAdapter()

    def test_canonical_base_matrix(self):
        cases = {"BTC": "BTC", "btc": "BTC", "BTC-USDT-SWAP": "BTC",
                 "BTCUSDT": "BTC", "BTC_USDT": "BTC", "PEPEUSDT": "PEPE",
                 "DOGE_USDT": "DOGE", " eth-usdt-swap ": "ETH"}
        for raw, want in cases.items():
            self.assertEqual(canonical_base(raw), want, raw)

    def test_native_symbol_per_venue(self):
        self.assertEqual(self.bn.native_symbol("BTC"), "BTCUSDT")
        self.assertEqual(self.gt.native_symbol("BTC-USDT-SWAP"), "BTC_USDT")
        self.assertEqual(self.bn.native_symbol("btc_usdt"), "BTCUSDT")
        self.assertEqual(self.gt.canonical("BTC_USDT"), "BTC")

    def test_bar_mapping(self):
        self.assertEqual(self.bn.to_bar("1H"), "1h")
        self.assertEqual(self.bn._interval("4H"), "4h")
        self.assertEqual(self.gt._interval("15m"), "15m")
        self.assertEqual(self.gt._interval("1D"), "1d")


class TestQuantitySemantics(unittest.TestCase):
    def test_binance_base_asset_floor_to_step(self):
        bn = BinanceAdapter()
        spec = InstrumentSpec(venue="binance", inst_id="BTCUSDT", base="BTC",
                              tick_size=0.1, step_size=0.001, ct_val=1.0,
                              min_size=0.001)
        # 150U * 3x = 450U 名义 @79650 → 0.005649... BTC → 截断 step 0.001 → 0.005
        qty = bn.quote_qty_to_native(450.0, 79650.0, spec)
        self.assertAlmostEqual(qty, 0.005, places=10)

    def test_gate_contracts_round(self):
        gt = GateAdapter()
        spec = InstrumentSpec(venue="gate", inst_id="BTC_USDT", base="BTC",
                              tick_size=0.1, step_size=0.0001, ct_val=0.0001,
                              min_size=1)
        # 450U @79650，每张名义 7.965U → 56.5 张 → round → 56 or 57
        qty = gt.quote_qty_to_native(450.0, 79650.0, spec)
        self.assertIn(qty, (56.0, 57.0))
        self.assertEqual(qty % 1, 0)

    def test_below_minimum_rejected(self):
        gt = GateAdapter()
        spec = InstrumentSpec(venue="gate", inst_id="BTC_USDT", base="BTC",
                              tick_size=0.1, step_size=0.0001, ct_val=0.0001,
                              min_size=1)
        self.assertEqual(gt.quote_qty_to_native(1.0, 79650.0, spec), 0.0)  # <1 张
        bn = BinanceAdapter()
        bspec = InstrumentSpec(venue="binance", inst_id="BTCUSDT", base="BTC",
                               tick_size=0.1, step_size=0.001, ct_val=1.0,
                               min_size=0.001)
        self.assertEqual(bn.quote_qty_to_native(50.0, 79650.0, bspec), 0.0)  # 截断后 <minQty
        self.assertEqual(bn.quote_qty_to_native(0, 79650.0, bspec), 0.0)
        self.assertEqual(bn.quote_qty_to_native(450.0, 0, bspec), 0.0)


class TestCapabilityTable(unittest.TestCase):
    def test_binance_pitfalls_declared(self):
        cap = BinanceAdapter.capabilities
        # US-004 律③修正：旧断言钉「默认 MARK_PRICE」是被时效审计推翻的假事实——
        # Algo workingType 官方默认 CONTRACT_PRICE，本系统要求显式传参不吃任何默认。
        self.assertEqual(cap.trigger_price_default, "explicit_only")
        self.assertEqual(cap.conditional_family, "algo_service")   # 独立 /fapi/v1/algoOrder 族
        self.assertFalse(cap.supports_attached_tp_sl)             # 无附属 TP/SL
        self.assertTrue(cap.mainland_ip_restricted)               # 大陆 IP 明文封锁
        self.assertEqual(cap.quantity_unit, "base_asset")

    def test_gate_semantics_declared(self):
        cap = GateAdapter.capabilities
        self.assertTrue(cap.signed_size)                          # 带符号张数
        self.assertEqual(cap.quantity_unit, "contracts")
        self.assertFalse(cap.mainland_ip_restricted)
        self.assertTrue(cap.has_top_trader_ratio)


class TestFailClosedPrivateFacets(unittest.TestCase):
    def test_orders_rejected_on_readonly_adapters(self):
        for adapter in (BinanceAdapter(),):
            with self.assertRaises(ExchangeCapabilityError):
                adapter.place_order()
            with self.assertRaises(ExchangeCapabilityError):
                adapter.attach_protective_orders()
            with self.assertRaises(ExchangeCapabilityError):
                adapter.account_snapshot()
            with self.assertRaises(ExchangeCapabilityError):
                adapter.positions()

    def test_gate_private_requires_credentials_fail_closed(self):
        # Gate 私有面已实装但仍 fail-closed：无凭证 → 显式拒绝，绝不静默
        import r20_gateway.secrets as gw_secrets
        with patch.object(gw_secrets, "load_secrets", lambda: {}):
            gt = GateAdapter()
            with self.assertRaises(ExchangeCapabilityError):
                gt.account_snapshot()
            with self.assertRaises(ExchangeCapabilityError):
                gt.place_order("BTC", "long", 5, price=78000)

    def test_registry_execution_gate(self):
        with self.assertRaises(ExchangeCapabilityError):
            registry.require_execution("binance")
        with self.assertRaises(ExchangeCapabilityError):
            registry.require_execution("gate")
        with self.assertRaises(ExchangeCapabilityError):
            registry.require_execution("okx")   # OKX 执行在遗留链路，适配器路由未开闸
        with self.assertRaises(ExchangeCapabilityError):
            registry.get_adapter("hyperliquid")
        self.assertEqual(registry.registered_venues(), ["binance", "gate", "okx"])

    def test_instance_singleton(self):
        self.assertIs(get_adapter("binance"), get_adapter("Binance"))


class _FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


class _FakeSession:
    def __init__(self, routes):
        self.routes = routes  # path substring -> payload
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        for frag, payload in self.routes.items():
            if frag in url:
                return _FakeResp(payload)
        return _FakeResp(None, status=404)


class TestReadOnlyMarketData(unittest.TestCase):
    def test_binance_ticker_normalized(self):
        bn = BinanceAdapter(session=_FakeSession({
            "ticker/24hr": {"symbol": "BTCUSDT", "lastPrice": "79650.10",
                            "openPrice": "78700", "highPrice": "80000",
                            "lowPrice": "78500", "volume": "25000",
                            "quoteVolume": "2e9", "priceChangePercent": "1.21",
                            "closeTime": 1788000000000},
            "bookTicker": {"bidPrice": "79650.0", "askPrice": "79650.2"},
        }))
        t = bn.fetch_ticker("BTC")
        self.assertEqual(t["venue"], "binance")
        self.assertEqual(t["last"], 79650.10)
        self.assertEqual(t["bid"], 79650.0)
        self.assertEqual(t["ts_ms"], 1788000000000)
        self.assertEqual(t["chg_24h_pct"], 1.21)

    def test_binance_ticker_bbo_depth_fallback(self):
        # bookTicker 被 WAF 拦（无路由命中→404）时回退 depth 档一
        bn = BinanceAdapter(session=_FakeSession({
            "ticker/24hr": {"lastPrice": "100", "closeTime": 1},
            "depth": {"bids": [["99.9", "5"]], "asks": [["100.1", "3"]]},
        }))
        t = bn.fetch_ticker("BTC")
        self.assertEqual(t["bid"], 99.9)
        self.assertEqual(t["ask"], 100.1)

    def test_gate_ticker_normalized(self):
        gt = GateAdapter(session=_FakeSession({
            "tickers": [{"contract": "BTC_USDT", "last": "78977.4",
                         "highest_bid": "78983.6", "lowest_ask": "78983.7",
                         "mark_price": "78989.78", "change_percentage": "1.17",
                         "volume_24h_base": "48460", "volume_24h_quote": "3.8e9",
                         "funding_rate": "0.000032"}],
        }))
        t = gt.fetch_ticker("BTC")
        self.assertEqual(t["bid"], 78983.6)
        self.assertEqual(t["ask"], 78983.7)
        self.assertEqual(t["chg_24h_pct"], 1.17)
        self.assertAlmostEqual(t["open_24h"], 78977.4 / 1.0117, places=4)

    def test_binance_candles_shape_ascending(self):
        payload = [
            [1600000, "1", "2", "0.5", "1.5", "100"],
            [1600060000, "1.5", "3", "1.4", "2", "200"],
        ]
        bn = BinanceAdapter(session=_FakeSession({"klines": payload}))
        kl = bn.fetch_candles("BTC", "15m", 100)
        self.assertEqual(len(kl), 2)
        self.assertLess(kl[0][0], kl[1][0])  # 升序契约

    def test_binance_spec_parse(self):
        payload = {"symbols": [{
            "symbol": "BTCUSDT", "status": "TRADING",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            ],
        }]}
        bn = BinanceAdapter(session=_FakeSession({"exchangeInfo": payload}))
        spec = bn.fetch_instrument_spec("BTC-USDT-SWAP")
        self.assertEqual(spec.tick_size, 0.1)
        self.assertEqual(spec.step_size, 0.001)
        self.assertEqual(spec.ct_val, 1.0)
        self.assertEqual(spec.base, "BTC")

    def test_gate_spec_and_ratio(self):
        sess = _FakeSession({
            "contracts": [{"name": "BTC_USDT", "quanto_multiplier": "0.0001",
                           "order_price_round": "0.1", "order_size_min": "1",
                           "in_delisting": False}],
            "contract_stats": [{"top_lsr_size": "1.87"}],
        })
        gt = GateAdapter(session=sess)
        spec = gt.fetch_instrument_spec("BTC")
        self.assertEqual(spec.ct_val, 0.0001)
        self.assertEqual(spec.tick_size, 0.1)
        self.assertEqual(gt.fetch_top_trader_ratio("BTC"), 1.87)

    def test_fail_soft_on_error(self):
        bn = BinanceAdapter(session=_FakeSession({}))  # 一切 404
        self.assertIsNone(bn.fetch_ticker("BTC"))
        self.assertIsNone(bn.fetch_candles("BTC"))
        self.assertIsNone(bn.fetch_funding_rate("BTC"))
        self.assertIsNone(bn.fetch_instrument_spec("BTC"))

    def test_top_trader_ratio_declared(self):
        # 能力表为类级冻结声明；缺能力时 fetch_top_trader_ratio 返回 None 不伪造
        self.assertTrue(GateAdapter.capabilities.has_top_trader_ratio)


if __name__ == "__main__":
    unittest.main()

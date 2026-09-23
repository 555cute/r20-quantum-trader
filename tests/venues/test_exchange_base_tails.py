"""交易所适配器基类（`r20_backend/exchanges/base.py`）残余分支收口测试 —— 第 344 刀。

本模块 328 行，是 OKX / Binance / Gate 及未来扩展交易所的抽象基类与接口契约：
- 未配 Profile 场所的旧 Base URL 兜底（`test_url` 与 `live_url`）；
- 会话懒加载（`get_session`）与公共请求异常保护（`_public_get`）；
- 行情与合约规格接口契约（`fetch_ticker`、`fetch_candles`、`fetch_funding_rate`、`fetch_orderbook`、`fetch_top_trader_ratio`、`_load_spec`）；
- 规格缓存穿透与命中（`fetch_instrument_spec`）；
- 数量换算零面值保护（`quote_qty_to_native`）；
- 私有执行接口阶段显式拒绝（`account_snapshot`、`positions`、`attach_protective_orders`、`cancel_protective_orders`、`cancel_order`）。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import requests

from r20_backend.exchanges.base import (
    BaseExchangeAdapter,
    ExchangeCapabilities,
    ExchangeCapabilityError,
    InstrumentSpec,
)


class DummyUnprofiledVenue(BaseExchangeAdapter):
    capabilities = ExchangeCapabilities(
        venue="unprofiled_venue",
        display_name="Unprofiled",
        symbol_template="{base}_USDT",
        has_top_trader_ratio=False,
    )
    live_url = "https://unprofiled.live.com"
    test_url = "https://unprofiled.test.com"


class DummyWithTopTrader(BaseExchangeAdapter):
    capabilities = ExchangeCapabilities(
        venue="top_trader_venue",
        display_name="TopTraderVenue",
        symbol_template="{base}_USDT",
        has_top_trader_ratio=True,
    )


class ExchangeBaseTailsTests(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. 未配置 Profile 时的环境 URL 兜底 (__init__)
    # -------------------------------------------------------------------------
    def test_unprofiled_venue_base_url_fallbacks(self):
        # 1) sandbox / testnet 模式回退 test_url (line 169)
        adapter_test = DummyUnprofiledVenue(environment="sandbox")
        self.assertEqual(adapter_test.base_url, "https://unprofiled.test.com")

        # 2) live 模式回退 live_url (line 171)
        adapter_live = DummyUnprofiledVenue(environment="live")
        self.assertEqual(adapter_live.base_url, "https://unprofiled.live.com")

    # -------------------------------------------------------------------------
    # 2. 会话懒加载与公共请求容错 (get_session & _public_get)
    # -------------------------------------------------------------------------
    def test_get_session_lazy_initialization(self):
        # 未传入 session 时首次调用自动创建 requests.Session (lines 181-183)
        adapter = DummyUnprofiledVenue(session=None)
        self.assertIsNone(adapter._session)
        s = adapter.get_session()
        self.assertIsInstance(s, requests.Session)
        self.assertIs(adapter.get_session(), s)

    def test_public_get_exception_returns_none(self):
        # 网络请求抛出异常（如连接失败/超时）安全返回 None (line 195)
        adapter = DummyUnprofiledVenue()
        with patch.object(adapter.get_session(), "get", side_effect=requests.exceptions.ConnectionError("offline")):
            res = adapter._public_get("/api/v1/ping")
            self.assertIsNone(res)

    # -------------------------------------------------------------------------
    # 3. 抽象行情与规格契约 (NotImplementedError & Top Trader Ratio)
    # -------------------------------------------------------------------------
    def test_abstract_market_methods_raise_not_implemented(self):
        adapter = DummyUnprofiledVenue()
        with self.assertRaises(NotImplementedError):
            adapter.fetch_ticker("BTC")
        with self.assertRaises(NotImplementedError):
            adapter.fetch_candles("BTC")
        with self.assertRaises(NotImplementedError):
            adapter.fetch_funding_rate("BTC")
        with self.assertRaises(NotImplementedError):
            adapter.fetch_orderbook("BTC")
        with self.assertRaises(NotImplementedError):
            adapter._load_spec("BTC_USDT")

    def test_fetch_top_trader_ratio_semantics(self):
        # 1) has_top_trader_ratio=False 时显式返回 None (lines 233-234)
        adapter_no = DummyUnprofiledVenue()
        self.assertIsNone(adapter_no.fetch_top_trader_ratio("BTC"))

        # 2) has_top_trader_ratio=True 时子类未实现抛 NotImplementedError (line 235)
        adapter_yes = DummyWithTopTrader()
        with self.assertRaises(NotImplementedError):
            adapter_yes.fetch_top_trader_ratio("BTC")

    def test_fetch_instrument_spec_cache_hit(self):
        # 缓存命中时直接返回缓存对象，不穿透 _load_spec (line 243)
        adapter = DummyUnprofiledVenue()
        inst = adapter.native_symbol("BTC")
        mock_spec = InstrumentSpec(
            venue="unprofiled_venue",
            base="BTC",
            inst_id=inst,
            tick_size=0.1,
            step_size=1.0,
            min_size=1.0,
            ct_val=0.01,
        )
        adapter._specs_cache[inst] = mock_spec
        # 非 refresh 请求命中缓存
        res = adapter.fetch_instrument_spec("BTC", refresh=False)
        self.assertIs(res, mock_spec)

    # -------------------------------------------------------------------------
    # 4. 换算边界与未实装私有接口 (_unsupported)
    # -------------------------------------------------------------------------
    def test_quote_qty_to_native_zero_per_contract(self):
        # ct_val 为负导致每张面值非正数时安全返回 0.0 (line 292)
        adapter = DummyUnprofiledVenue()
        adapter.capabilities = ExchangeCapabilities(
            venue="contracts_venue",
            display_name="Contracts",
            symbol_template="{base}_USDT",
            quantity_unit="contracts",
        )
        spec = InstrumentSpec(
            venue="contracts_venue",
            base="BTC",
            inst_id="BTC_USDT",
            tick_size=0.1,
            step_size=1.0,
            min_size=1.0,
            ct_val=-0.5,
        )
        res = adapter.quote_qty_to_native(100.0, 50000.0, spec)
        self.assertEqual(res, 0.0)

    def test_unsupported_private_methods_raise_capability_error(self):
        adapter = DummyUnprofiledVenue()
        unsupported_calls = [
            ("account_snapshot", []),
            ("positions", []),
            ("attach_protective_orders", []),
            ("cancel_protective_orders", []),
            ("cancel_order", []),
        ]
        for name, args in unsupported_calls:
            with self.subTest(method=name):
                fn = getattr(adapter, name)
                with self.assertRaises(ExchangeCapabilityError) as ctx:
                    fn(*args)
                self.assertIn("未实装", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

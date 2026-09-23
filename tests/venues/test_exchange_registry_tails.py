"""交易所适配器注册表（`r20_backend/exchanges/registry.py`）残余分支收口测试 —— 第 353 刀。

本模块 264 行，是交易所适配器统一注册、执行开闸校验、凭据原子档位解析与合约符号纯映射核心：
- 凭证解析容错（`venue_credentials`）：密钥库读取异常自愈、半配档位（仅有 Key 或仅有 Secret）跳过并降级至下一档位；
- 通行密钥读取（`venue_passphrase`）：非 OKX 场所直接返回空串、密钥库异常自愈、未指定环境时直读主通行密码；
- 执行开闸保护（`require_execution`）：开闸后适配器 `supports_orders=False` 时的防御性拦截报错；
- 符号纯解析（`native_symbol_pure`）：未知场所无模板时平滑回退标准资产基准码（canonical_base）。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from r20_backend.exchanges.registry import (
    ExchangeCapabilityError,
    native_symbol_pure,
    require_execution,
    venue_credentials,
    venue_passphrase,
)


class ExchangeRegistryTailsTests(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. 凭据原子档位解析 (venue_credentials)
    # -------------------------------------------------------------------------
    def test_venue_credentials_secrets_load_exception_returns_empty(self):
        # 密钥加载异常时捕获并返回 ("", "") (lines 158-159)
        with patch("r20_gateway.secrets.load_secrets", side_effect=RuntimeError("secrets vault locked")):
            self.assertEqual(venue_credentials("gate", "sandbox"), ("", ""))

    def test_venue_credentials_half_configured_tier_skipped(self):
        # 某档位半配（只有 key 或只有 secret）时不能跨档拼配，跳过该档继续向下回退 (lines 176-178)
        secrets = {
            "GATE_DEMO_API_KEY": "demo_key_only",  # 缺少 GATE_DEMO_SECRET_KEY
            "GATE_API_KEY": "fallback_key",
            "GATE_SECRET_KEY": "fallback_secret",
        }
        with patch("r20_gateway.secrets.load_secrets", return_value=secrets):
            creds = venue_credentials("gate", "sandbox")
            self.assertEqual(creds, ("fallback_key", "fallback_secret"))

    # -------------------------------------------------------------------------
    # 2. 通行密钥读取 (venue_passphrase)
    # -------------------------------------------------------------------------
    def test_venue_passphrase_non_okx_returns_empty(self):
        # 非 OKX 交易所无 passphrase 概念，直接返回空串 (lines 187-188)
        self.assertEqual(venue_passphrase("gate"), "")
        self.assertEqual(venue_passphrase("binance"), "")

    def test_venue_passphrase_secrets_load_exception_returns_empty(self):
        # 密钥库异常时捕获并安全返回空串 (lines 192-193)
        with patch("r20_gateway.secrets.load_secrets", side_effect=RuntimeError("vault error")):
            self.assertEqual(venue_passphrase("okx", "sandbox"), "")

    def test_venue_passphrase_no_environment_reads_default_key(self):
        # 未指定环境档位时直接读取默认 OKX_PASSPHRASE (line 206)
        with patch("r20_gateway.secrets.load_secrets", return_value={"OKX_PASSPHRASE": "test_passphrase_123"}):
            self.assertEqual(venue_passphrase("okx", None), "test_passphrase_123")

    # -------------------------------------------------------------------------
    # 3. 执行能力保护 (require_execution)
    # -------------------------------------------------------------------------
    def test_require_execution_orders_not_supported_raises_capability_error(self):
        # 当执行开关已开但适配器未实装下单功能 (supports_orders=False) 时抛出 ExchangeCapabilityError (line 245)
        mock_adapter = MagicMock()
        mock_adapter.capabilities.venue = "okx"
        mock_adapter.capabilities.display_name = "MockOKX"
        mock_adapter.capabilities.supports_orders = False
        mock_adapter.environment = "live"

        with patch("r20_backend.exchanges.registry.get_adapter", return_value=mock_adapter):
            with patch("r20_backend.exchanges.registry.execution_open", return_value=True):
                with self.assertRaises(ExchangeCapabilityError) as ctx:
                    require_execution("okx", "live")
                self.assertIn("supports_orders=False，适配器未实装下单", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 4. 符号纯解析回退 (native_symbol_pure)
    # -------------------------------------------------------------------------
    def test_native_symbol_pure_unknown_venue_falls_back_to_canonical_base(self):
        # 未知场所适配器类无模板，平滑回退 canonical_base(symbol) (lines 262-263)
        self.assertEqual(native_symbol_pure("BTC-USDT-SWAP", "unregistered_exchange"), "BTC")


if __name__ == "__main__":
    unittest.main()

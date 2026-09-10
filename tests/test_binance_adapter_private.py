"""US-004：Binance USDT-M 合约私有账户与持仓挂单只读适配测试（全 mock、零出网）。

验收标准覆盖：
1. 签名与鉴权头：HMAC-SHA256 签名与 X-MBX-APIKEY 头
2. /fapi/v1/account 账户快照：总权益、可用保证金、仓位/挂单保证金、未实现盈亏
3. /fapi/v1/positionRisk 与 /fapi/v1/openOrders：持仓过滤与普通在途挂单解析
4. capabilities.supports_account=True 且接入 /api/v1/venue_accounts
5. 错误与异常处理：BinanceAPIError 错误码捕获
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from r20_backend.exchanges import (
    BinanceAPIError,
    BinanceAdapter,
    ExchangeCapabilityError,
    get_adapter,
)


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, *a, **k):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class BinancePrivateAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = BinanceAdapter(environment="demo")

    def test_capabilities_declaration(self):
        cap = self.adapter.capabilities
        self.assertTrue(cap.supports_account, "US-004: supports_account 必须声明为 True")
        self.assertFalse(cap.supports_orders, "US-004: supports_orders 在 US-005 执行故事前保持 False")

    def test_unconfigured_credentials_raises_capability_error(self):
        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=("", "")):
            with self.assertRaises(ExchangeCapabilityError) as ctx:
                self.adapter.account_snapshot()
            self.assertIn("未配置", str(ctx.exception))

    def test_signed_request_headers_and_hmac_sha256(self):
        api_key = "test_binance_api_key_123"
        secret_key = "test_binance_secret_key_456"

        captured_req = []

        def mock_urlopen(req, timeout=15.0):
            captured_req.append(req)
            return _FakeResp(json.dumps({"test": "ok"}).encode("utf-8"))

        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=(api_key, secret_key)), \
             patch("r20_backend.exchanges.binance.urlopen", mock_urlopen):
            res = self.adapter.signed_request("GET", "/fapi/v1/test", params={"symbol": "BTCUSDT"})
            self.assertEqual(res, {"test": "ok"})

        self.assertEqual(len(captured_req), 1)
        req = captured_req[0]

        # 1. 验证 Header
        self.assertEqual(req.headers.get("X-mbx-apikey"), api_key)
        self.assertEqual(req.headers.get("Accept"), "application/json")

        # 2. 验证 Query 与 HMAC-SHA256 签名
        parsed = urlparse(req.full_url)
        self.assertEqual(parsed.path, "/fapi/v1/test")
        qs = parse_qs(parsed.query)
        self.assertEqual(qs.get("symbol"), ["BTCUSDT"])
        self.assertIn("timestamp", qs)
        self.assertEqual(qs.get("recvWindow"), ["5000"])
        self.assertIn("signature", qs)

        # 验证签名一致性
        sig = qs["signature"][0]
        # 移除 &signature=... 后的原始 query 串
        query_without_sig = parsed.query.split("&signature=")[0]
        expected_sig = hmac.new(
            secret_key.encode("utf-8"), query_without_sig.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        self.assertEqual(sig, expected_sig)

    def test_account_snapshot_parsing(self):
        api_key = "ak"
        secret_key = "sk"

        mock_account_data = {
            "feeTier": 0,
            "canTrade": True,
            "totalInitialMargin": "120.50",
            "totalMaintMargin": "10.00",
            "totalWalletBalance": "5000.00",
            "totalUnrealizedProfit": "250.75",
            "totalMarginBalance": "5250.75",
            "totalPositionInitialMargin": "120.50",
            "totalOpenOrderInitialMargin": "45.00",
            "availableBalance": "5085.25",
            "maxWithdrawAmount": "5085.25",
            "assets": [{"asset": "USDT", "walletBalance": "5000.00"}],
        }

        def mock_urlopen(req, timeout=15.0):
            return _FakeResp(json.dumps(mock_account_data).encode("utf-8"))

        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=(api_key, secret_key)), \
             patch("r20_backend.exchanges.binance.urlopen", mock_urlopen):
            snap = self.adapter.account_snapshot()

        self.assertEqual(snap["venue"], "binance")
        self.assertEqual(snap["currency"], "USDT")
        self.assertAlmostEqual(snap["equity_usdt"], 5250.75)
        self.assertAlmostEqual(snap["available_usdt"], 5085.25)
        self.assertAlmostEqual(snap["position_margin"], 120.50)
        self.assertAlmostEqual(snap["order_margin"], 45.00)
        self.assertAlmostEqual(snap["unrealized_pnl"], 250.75)
        self.assertTrue(snap["can_trade"])

    def test_positions_filters_zeros_and_formats_properly(self):
        api_key = "ak"
        secret_key = "sk"

        mock_positions_data = [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.25",
                "entryPrice": "62000.0",
                "markPrice": "63500.0",
                "unRealizedProfit": "375.0",
                "liquidationPrice": "48000.0",
                "leverage": "10",
                "marginType": "cross",
                "isolatedMargin": "0.0",
                "positionInitialMargin": "155.0",
            },
            {
                "symbol": "ETHUSDT",
                "positionAmt": "-2.0",
                "entryPrice": "3100.0",
                "markPrice": "3050.0",
                "unRealizedProfit": "100.0",
                "liquidationPrice": "3800.0",
                "leverage": "5",
                "marginType": "cross",
                "isolatedMargin": "0.0",
                "positionInitialMargin": "122.0",
            },
            {
                "symbol": "SOLUSDT",
                "positionAmt": "0.00000000",
                "entryPrice": "0.0",
                "markPrice": "150.0",
                "unRealizedProfit": "0.0",
                "liquidationPrice": "0.0",
                "leverage": "5",
                "marginType": "cross",
            },
        ]

        def mock_urlopen(req, timeout=15.0):
            return _FakeResp(json.dumps(mock_positions_data).encode("utf-8"))

        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=(api_key, secret_key)), \
             patch("r20_backend.exchanges.binance.urlopen", mock_urlopen):
            pos_list = self.adapter.positions()

        # 0 仓位被剔除，仅保留真实仓位
        self.assertEqual(len(pos_list), 2)
        btc = pos_list[0]
        self.assertEqual(btc["venue"], "binance")
        self.assertEqual(btc["inst_id"], "BTCUSDT")
        self.assertEqual(btc["base"], "BTC")
        self.assertEqual(btc["side"], "long")
        self.assertAlmostEqual(btc["size_signed"], 0.25)
        self.assertAlmostEqual(btc["entry_price"], 62000.0)
        self.assertAlmostEqual(btc["mark_price"], 63500.0)
        self.assertAlmostEqual(btc["unrealized_pnl"], 375.0)

        eth = pos_list[1]
        self.assertEqual(eth["inst_id"], "ETHUSDT")
        self.assertEqual(eth["side"], "short")
        self.assertAlmostEqual(eth["size_signed"], -2.0)

    def test_open_orders_parsing(self):
        api_key = "ak"
        secret_key = "sk"

        mock_orders_data = [
            {
                "orderId": 883921049281,
                "symbol": "BTCUSDT",
                "clientOrderId": "r20_limit_01",
                "price": "60000.00",
                "origQty": "0.100",
                "executedQty": "0.000",
                "side": "BUY",
                "status": "NEW",
                "type": "LIMIT",
            }
        ]

        def mock_urlopen(req, timeout=15.0):
            return _FakeResp(json.dumps(mock_orders_data).encode("utf-8"))

        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=(api_key, secret_key)), \
             patch("r20_backend.exchanges.binance.urlopen", mock_urlopen):
            orders = self.adapter.open_orders()

        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual(o["venue"], "binance")
        self.assertEqual(o["order_id"], "883921049281")  # str 归一化
        self.assertEqual(o["side"], "buy")
        self.assertAlmostEqual(o["price"], 60000.0)
        self.assertAlmostEqual(o["size"], 0.1)

    def test_binance_api_error_wrapped(self):
        api_key = "ak"
        secret_key = "sk"

        error_body = json.dumps({"code": -2014, "msg": "API-key format invalid."}).encode("utf-8")

        def mock_urlopen(req, timeout=15.0):
            fp = io.BytesIO(error_body)
            raise HTTPError(req.full_url, 400, "Bad Request", {"Content-Type": "application/json"}, fp)

        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=(api_key, secret_key)), \
             patch("r20_backend.exchanges.binance.urlopen", mock_urlopen):
            with self.assertRaises(BinanceAPIError) as ctx:
                self.adapter.account_snapshot()

            self.assertEqual(ctx.exception.code, -2014)
            self.assertIn("API-key format invalid", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()

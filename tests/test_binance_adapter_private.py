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
        self.assertTrue(cap.supports_orders, "US-005: supports_orders 声明为 True")

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


class BinanceExecutionAndProtectionTests(unittest.TestCase):
    """US-005：Binance 交易执行闭环与条件单止盈止损单测（全 mock、零出网）。"""

    def setUp(self):
        self.adapter = BinanceAdapter(environment="live")

    def test_place_order_limit_and_market(self):
        api_key = "ak"
        secret_key = "sk"

        captured = []

        def mock_urlopen(req, timeout=15.0):
            captured.append(req)
            parsed = urlparse(req.full_url)
            qs = parse_qs(parsed.query)
            t = qs.get("type", [""])[0]
            resp_data = {
                "orderId": 99881122,
                "clientOrderId": qs.get("newClientOrderId", ["r20_order"])[0],
                "symbol": qs.get("symbol", ["BTCUSDT"])[0],
                "status": "NEW",
                "price": qs.get("price", ["0.0"])[0],
                "origQty": qs.get("quantity", ["0.1"])[0],
                "executedQty": "0.0",
            }
            return _FakeResp(json.dumps(resp_data).encode("utf-8"))

        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=(api_key, secret_key)), \
             patch("r20_backend.exchanges.binance.urlopen", mock_urlopen):
            # 1. 限价单
            res_limit = self.adapter.place_order(
                symbol="BTC", side="buy", contracts=0.25, price=60123.45, text="r20_cid_01"
            )
            self.assertEqual(res_limit["venue"], "binance")
            self.assertEqual(res_limit["order_id"], "99881122")
            self.assertEqual(res_limit["status"], "NEW")
            self.assertEqual(res_limit["side"], "buy")

            qs_l = parse_qs(urlparse(captured[-1].full_url).query)
            self.assertEqual(qs_l["type"], ["LIMIT"])
            self.assertEqual(qs_l["timeInForce"], ["GTC"])
            self.assertEqual(qs_l["symbol"], ["BTCUSDT"])
            self.assertEqual(qs_l["newClientOrderId"], ["r20_cid_01"])

            # 2. 市价单
            res_market = self.adapter.place_order(symbol="BTC", side="sell", contracts=0.1)
            qs_m = parse_qs(urlparse(captured[-1].full_url).query)
            self.assertEqual(qs_m["type"], ["MARKET"])
            self.assertNotIn("price", qs_m)

    def test_cancel_order_and_cancel_all(self):
        api_key = "ak"
        secret_key = "sk"

        captured = []

        def mock_urlopen(req, timeout=15.0):
            captured.append(req)
            return _FakeResp(json.dumps({"orderId": 12345, "status": "CANCELED"}).encode("utf-8"))

        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=(api_key, secret_key)), \
             patch("r20_backend.exchanges.binance.urlopen", mock_urlopen):
            # 1. 单撤
            c1 = self.adapter.cancel_order("BTC", order_id="12345")
            self.assertEqual(c1["order_id"], "12345")
            self.assertEqual(c1["status"], "CANCELED")
            qs1 = parse_qs(urlparse(captured[-1].full_url).query)
            self.assertEqual(qs1["orderId"], ["12345"])

            # 2. 全撤
            c2 = self.adapter.cancel_all_orders("BTC")
            self.assertEqual(c2["symbol"], "BTCUSDT")
            self.assertIn("/fapi/v1/allOpenOrders", captured[-1].full_url)

    def test_attach_protective_orders_algo_service(self):
        api_key = "ak"
        secret_key = "sk"

        captured = []

        def mock_urlopen(req, timeout=15.0):
            captured.append(req)
            parsed = urlparse(req.full_url)
            qs = parse_qs(parsed.query)
            t = qs.get("type", [""])[0]
            algo_id = 701 if "TAKE_PROFIT" in t else 702
            return _FakeResp(json.dumps({"algoId": algo_id, "code": "200"}).encode("utf-8"))

        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=(api_key, secret_key)), \
             patch("r20_backend.exchanges.binance.urlopen", mock_urlopen):
            # 多头持仓 -> 平仓方向为反向 SELL，closePosition=true 全平
            legs = self.adapter.attach_protective_orders(
                symbol="BTC", side="long", tp_px=65000.0, sl_px=58000.0, working_type="CONTRACT_PRICE"
            )
            self.assertEqual(legs["tp"], "701")
            self.assertEqual(legs["sl"], "702")

            self.assertEqual(len(captured), 2)
            # TP 请求检查
            tp_qs = parse_qs(urlparse(captured[0].full_url).query)
            self.assertEqual(tp_qs["symbol"], ["BTCUSDT"])
            self.assertEqual(tp_qs["side"], ["SELL"])
            self.assertEqual(tp_qs["type"], ["TAKE_PROFIT_MARKET"])
            self.assertEqual(tp_qs["closePosition"], ["true"])
            self.assertEqual(tp_qs["workingType"], ["CONTRACT_PRICE"])

            # SL 请求检查
            sl_qs = parse_qs(urlparse(captured[1].full_url).query)
            self.assertEqual(sl_qs["symbol"], ["BTCUSDT"])
            self.assertEqual(sl_qs["side"], ["SELL"])
            self.assertEqual(sl_qs["type"], ["STOP_MARKET"])
            self.assertEqual(sl_qs["closePosition"], ["true"])
            self.assertEqual(sl_qs["workingType"], ["CONTRACT_PRICE"])

    def test_execution_switch_gatekeeping(self):
        from r20_backend.exchanges import require_execution, execution_open
        import os

        # 默认关：require_execution 必须拒绝
        with patch.dict(os.environ, {"R20_BINANCE_EXECUTION": "0", "R20_BINANCE_DEMO_EXECUTION": "0"}):
            self.assertFalse(execution_open("binance", "live"))
            self.assertFalse(execution_open("binance", "demo"))
            with self.assertRaises(ExchangeCapabilityError) as ctx_live:
                require_execution("binance", "live")
            self.assertIn("R20_BINANCE_EXECUTION=1", str(ctx_live.exception))

            with self.assertRaises(ExchangeCapabilityError) as ctx_demo:
                require_execution("binance", "demo")
            self.assertIn("R20_BINANCE_DEMO_EXECUTION=1", str(ctx_demo.exception))

        # 开闸放行
        with patch.dict(os.environ, {"R20_BINANCE_EXECUTION": "1"}):
            self.assertTrue(execution_open("binance", "live"))
            # require_execution 不报错
            require_execution("binance", "live")

    def test_execution_router_integration_with_binance(self):
        import os
        from r20_backend import execution_router as er

        ad = self.adapter
        # Mock 适配器关键动作
        ad._keys = lambda: ("ak", "sk")
        ad.fetch_ticker = lambda s: {"last": 60000.0, "mark_price": 60000.0}
        ad.set_leverage = Mock(return_value={"leverage": 5})
        ad.place_order = Mock(return_value={"order_id": "112233", "id": "112233", "status": "NEW"})
        ad.attach_protective_orders = Mock(return_value={"tp": "801", "sl": "802"})
        ad.list_protective_orders = Mock(return_value=[{"algo_id": "801"}, {"algo_id": "802"}])
        ad.positions = Mock(return_value=[])

        decision = {
            "venue": "binance",
            "asset": "BTC",
            "action": "BUY_LONG",
            "margin_usdt": 120.0,
            "leverage": 5,
            "entry_price": 60000.0,
            "take_profit_price": 63000.0,
            "stop_loss_price": 58500.0,
        }

        with patch.dict(os.environ, {"R20_BINANCE_EXECUTION": "1", "R20_BINANCE_DEMO_EXECUTION": "1"}):
            res = er.open_protected_position(decision, adapter=ad)

        self.assertTrue(res["ok"])
        self.assertEqual(res["venue"], "binance")
        self.assertEqual(res["stage"], "done")
        self.assertEqual(res["order_id"], "112233")
        self.assertEqual(res["tp_id"], "801")
        self.assertEqual(res["sl_id"], "802")
        ad.place_order.assert_called_once()
        ad.attach_protective_orders.assert_called_once()

    def test_execution_router_rollback_on_protective_gap(self):
        import os
        from r20_backend import execution_router as er

        ad = self.adapter
        ad._keys = lambda: ("ak", "sk")
        ad.fetch_ticker = lambda s: {"last": 60000.0, "mark_price": 60000.0}
        ad.set_leverage = Mock(return_value={})
        ad.place_order = Mock(return_value={"order_id": "9999", "id": "9999"})
        ad.cancel_order = Mock()
        ad.attach_protective_orders = Mock(return_value={"tp": "801", "sl": "802"})
        # 回读遗漏 SL 腿，模拟触发保护缺口
        ad.list_protective_orders = Mock(return_value=[{"algo_id": "801"}])
        ad.positions = Mock(return_value=[])

        decision = {
            "venue": "binance",
            "asset": "BTC",
            "action": "BUY_LONG",
            "margin_usdt": 100.0,
            "leverage": 3,
            "entry_price": 60000.0,
            "take_profit_price": 62000.0,
            "stop_loss_price": 59000.0,
        }

        with patch.dict(os.environ, {"R20_BINANCE_EXECUTION": "1", "R20_BINANCE_DEMO_EXECUTION": "1"}):
            res = er.open_protected_position(decision, adapter=ad)

        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "protective")
        self.assertIn("入场单已撤销", res["detail"])
        ad.cancel_order.assert_called_once_with("BTC", "9999")


if __name__ == "__main__":
    unittest.main()

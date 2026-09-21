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
import warnings
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from r20_backend.exchanges import (
    BinanceAPIError,
    BinanceAdapter,
    ExchangeCapabilityError,
    get_adapter,
)

_AMBIENT: dict = {}


_READ_SCOPE = None


def setUpModule():
    """隔离宿主 .env 的执行/档位旗标（r20_backend.config 导入期会把它们载入
    os.environ）——执行路由用例的开闸语义只由本模块夹具决定。"""
    import os
    global _AMBIENT
    _AMBIENT = {k: os.environ.pop(k, None) for k in list(os.environ)
                if k.startswith("R20_") and ("EXECUTION" in k or "TESTNET" in k)}
    # ⚠️ 第二百三十三刀登记，第二百三十四刀**试改夹具池失败**（证据在此，免得后人重走）：
    # 读线上 `data/instrument_pool.json`（读取点 `scripts/instrument_pool.py:239`，由生产读守卫指出）。
    # 试过把 `ip.POOL_FILE` patch 成同形夹具池（含 tier/max_leverage/risk_per_trade_usd 全字段），
    # 断言反而从 `protective` 变成 `venue_pool` ⇒ 说明这两条用例的分支**还取决于线上场所路由**
    # （`data/venue_routing.json` 的 per-venue 准入），不止标的池 ⇒ 依赖比"换池"更深，
    # 需单独一刀连场所路由一起沙箱化。故本刀仍显式声明：**已知的生产数据依赖**，不是"已经安全"。
    from tests import allow_real_data_reads
    global _READ_SCOPE
    _READ_SCOPE = allow_real_data_reads()
    _READ_SCOPE.__enter__()


def tearDownModule():
    global _READ_SCOPE
    if _READ_SCOPE is not None:
        _READ_SCOPE.__exit__(None, None, None)
        _READ_SCOPE = None
    import os
    for k, v in _AMBIENT.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


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
             patch.object(self.adapter, "server_time_offset_ms", lambda **k: 0.0), \
             patch("r20_backend.exchanges.binance.urlopen", mock_urlopen):
            res = self.adapter.signed_request("GET", "/fapi/v1/test", params={"symbol": "BTCUSDT"})
            self.assertEqual(res, {"test": "ok"})

        # 本用例只钉 Header/签名/参数，故把 2026-09-16 新增的**服务器校时**网络面钉掉
        # （校时行为由 ServerClockAlignmentTest 专测）；否则计数会多出一次 time 请求。
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
             patch("r20_backend.exchanges.binance.urlopen", mock_urlopen), \
             patch.object(self.adapter, "server_time_offset_ms", lambda **k: 0.0), \
             patch.object(type(self.adapter), "_public_get", lambda *a, **k: None):
             # 第七十九刀：spec 路径走 requests.Session 不是 urlopen，
             # 探针实测非离线时**真打 fapi.binance.com**；离线时它被拦后
             # fail-soft 返 None 也绿 ⇒ patch 成 None = 与离线行为等价。
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
             patch("r20_backend.exchanges.binance.urlopen", mock_urlopen), \
             patch.object(self.adapter, "server_time_offset_ms", lambda **k: 0.0), \
             patch.object(type(self.adapter), "_public_get", lambda *a, **k: None):
             # 第七十九刀：spec 路径走 requests.Session 不是 urlopen，
             # 探针实测非离线时**真打 fapi.binance.com**；离线时它被拦后
             # fail-soft 返 None 也绿 ⇒ patch 成 None = 与离线行为等价。
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
        from r20_backend.exchanges import InstrumentSpec

        ad = self.adapter
        # Mock 适配器关键动作（封闭三律：fetch_instrument_spec 必须钉死——
        # 不 mock 会真连 urlopen，离线套件下被 socket 守卫拦成 stage=specs 红）
        ad._keys = lambda: ("ak", "sk")
        # 持仓模式只读探测（router 新增体检）：真适配器会去打
        # GET /fapi/v1/positionSide/dual，本用例只打桩了规格/行情，必须一并钉死，
        # 否则探测走真网络（离线套件下被守卫拦成 unknown ⇒ 开仓被拒）。
        # 取值 "net" 与真实 DEMO 账户一致（实测 dualSidePosition=False）。
        ad.detect_position_mode = lambda: "net"
        ad.fetch_instrument_spec = Mock(return_value=InstrumentSpec(
            venue="binance", inst_id="BTCUSDT", base="BTC", tick_size=0.1,
            step_size=0.001, ct_val=1.0, min_size=0.001, max_leverage=20))
        ad.quote_qty_to_native = Mock(return_value=0.01)
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

        from r20_backend.exchanges import listing as listing_mod
        _okl = listing_mod.ListingCheck(ok=True, reason=None,
                                        checked_at="2026-09-11T00:00:00Z", source="cache")
        with patch.dict(os.environ, {"R20_BINANCE_EXECUTION": "1", "R20_BINANCE_DEMO_EXECUTION": "1"}), \
                patch.object(listing_mod, "ensure_contract_listed", lambda v, e, c: _okl):
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
        # 持仓模式只读探测（router 新增体检）：真适配器会去打
        # GET /fapi/v1/positionSide/dual，本用例只打桩了规格/行情，必须一并钉死，
        # 否则探测走真网络（离线套件下被守卫拦成 unknown ⇒ 开仓被拒）。
        # 取值 "net" 与真实 DEMO 账户一致（实测 dualSidePosition=False）。
        ad.detect_position_mode = lambda: "net"
        # 同族封闭钉：规格 + listing 对账两处分发前动作必须 mock（离线套件纪律）
        from r20_backend.exchanges import InstrumentSpec as _Spec
        ad.fetch_instrument_spec = Mock(return_value=_Spec(
            venue="binance", inst_id="BTCUSDT", base="BTC", tick_size=0.1,
            step_size=0.001, ct_val=1.0, min_size=0.001, max_leverage=20))
        ad.quote_qty_to_native = Mock(return_value=0.01)
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

        from r20_backend.exchanges import listing as listing_mod
        _okl = listing_mod.ListingCheck(ok=True, reason=None,
                                        checked_at="2026-09-11T00:00:00Z", source="cache")
        with patch.dict(os.environ, {"R20_BINANCE_EXECUTION": "1", "R20_BINANCE_DEMO_EXECUTION": "1"}), \
                patch.object(listing_mod, "ensure_contract_listed", lambda v, e, c: _okl):
            res = er.open_protected_position(decision, adapter=ad)

        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "protective")
        self.assertIn("入场单已撤销", res["detail"])
        # 审计④#9：回滚须 best-effort 撤掉已挂成功的腿（本例 tp=801 挂成、sl 缺口）
        # + 入场单，绝不留孤儿触发单。旧断言只认撤入场单一次，正是被修掉的缺陷。
        legs = [c.args[1] for c in ad.cancel_order.call_args_list]
        self.assertIn("801", legs, "已挂成功的 TP 腿必须回滚")
        self.assertIn("9999", legs, "入场单必须回滚")


class SetLeverageMarginTypeTest(unittest.TestCase):
    """2026-09-16 P1：Binance Demo 域 `/fapi/v1/marginType` 不可用，不得再阻塞下单。

    事故：旧实现无条件先调该端点，Demo 域下 参数放 query → -1102「margintype 缺失」、
    放 JSON body → -1022「签名无效」（下方 test_demo_domain_* 记录实测形状），
    于是**每一笔 Binance 下单**都死在「设置杠杆失败」，而账户档位本来就是 cross。
    修法：先读 positionRisk 的 marginType，已是目标档就不写；非写不可时失败后
    **必须再读回核对**，读回仍不一致才 fail-closed。
    """

    def setUp(self):
        self.adapter = BinanceAdapter(environment="demo")
        self.calls: list = []

    def _wire(self, *, margin_reads, write_error=None, path_prefix="demo-fapi"):
        """伪 signed_request：按 (method,path) 记录调用并按脚本回放。"""
        reads = list(margin_reads)

        def fake(method, path, params=None, body=None, timeout=15.0):
            self.calls.append((method, path))
            if path == "/fapi/v2/positionRisk":
                mt = reads.pop(0) if reads else None
                if mt == "RAISE":
                    raise BinanceAPIError("-1130", "positionRisk 挂了")
                return [{"symbol": "ADAUSDT", "marginType": mt, "leverage": "2"}]
            if path == "/fapi/v1/marginType":
                if write_error is not None:
                    raise BinanceAPIError(write_error, "probe")
                return {}
            if path == "/fapi/v1/leverage":
                return {"symbol": "ADAUSDT", "leverage": 3}
            raise AssertionError(f"未预期的路径 {path}")

        self.adapter.signed_request = fake

    def test_already_target_skips_the_broken_endpoint(self):
        """读回已是 cross ⇒ 压根不调 marginType（这就是线上 -1102 的根治点）。"""
        self._wire(margin_reads=["cross"])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.adapter.set_leverage("ADA", 3, margin_mode="cross")
        self.assertNotIn(("POST", "/fapi/v1/marginType"), self.calls,
                         "档位已一致还去写坏端点 = 老 bug 复发")
        self.assertIn(("POST", "/fapi/v1/leverage"), self.calls)
        self.assertEqual([w for w in caught if issubclass(w.category, RuntimeWarning)], [])

    def test_case_and_whitespace_tolerated_on_target(self):
        self._wire(margin_reads=["cross"])
        self.adapter.set_leverage("ADA", 3, margin_mode=" CROSS ")
        self.assertNotIn(("POST", "/fapi/v1/marginType"), self.calls)

    def test_mismatch_then_write_fails_but_readback_confirms(self):
        """不一致才写；写入失败但读回已是目标 ⇒ 告警放行（端点坏 ≠ 现状坏）。"""
        self._wire(margin_reads=["isolated", "cross"], write_error=-1102)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.adapter.set_leverage("ADA", 3, margin_mode="cross")
        self.assertIn(("POST", "/fapi/v1/marginType"), self.calls)
        self.assertIn(("POST", "/fapi/v1/leverage"), self.calls)
        self.assertTrue(any(issubclass(w.category, RuntimeWarning) for w in caught),
                        "端点不可用但现状正确必须留告警，不得静默")

    def test_write_fails_and_readback_still_wrong_fails_closed(self):
        """读回仍不是目标档 ⇒ 上抛（审计 C1：绝不在错误保证金模式下建仓）。"""
        self._wire(margin_reads=["isolated", "isolated"], write_error=-4059)
        with self.assertRaises(BinanceAPIError):
            self.adapter.set_leverage("ADA", 3, margin_mode="cross")
        self.assertNotIn(("POST", "/fapi/v1/leverage"), self.calls,
                         "保证金档未落地就不得强设杠杆")

    def test_4046_needs_no_change(self):
        self._wire(margin_reads=["isolated"], write_error=-4046)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.adapter.set_leverage("ADA", 3, margin_mode="cross")
        self.assertIn(("POST", "/fapi/v1/leverage"), self.calls)
        self.assertEqual([w for w in caught if issubclass(w.category, RuntimeWarning)], [])

    def test_readback_unavailable_warns_but_does_not_block(self):
        """连档位都读不回且端点不可用 ⇒ 告警放行（不假装核对通过，也不制造假阻塞）。"""
        self._wire(margin_reads=["RAISE", "RAISE"], write_error=-1102)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.adapter.set_leverage("ADA", 3, margin_mode="cross")
        self.assertIn(("POST", "/fapi/v1/leverage"), self.calls)
        self.assertGreaterEqual(len([w for w in caught if issubclass(w.category, RuntimeWarning)]), 2)

    def test_readback_unavailable_with_other_error_still_fails_closed(self):
        """读不回 + 非"端点不可用"错误码 ⇒ 仍 fail-closed（不放过未知错误）。"""
        self._wire(margin_reads=["RAISE", "RAISE"], write_error=-2015)
        with self.assertRaises(BinanceAPIError):
            self.adapter.set_leverage("ADA", 3, margin_mode="cross")


class ServerClockAlignmentTest(unittest.TestCase):
    """2026-09-16 P1：宿主机时钟比交易所慢 ~2.1s，recvWindow=5000ms 只剩 ~2.9s 余量。

    实测已撞到 `[-1021] Timestamp for this request is outside of the recvWindow`。
    修法=按域测 `GET /fapi/v1/time`（公共免签）算偏差，签名时用校时时间戳；
    仍被判 -1021 时重测偏差重试一次。本组全 mock、零出网。
    """

    def setUp(self):
        self.adapter = BinanceAdapter(environment="demo")
        BinanceAdapter._SERVER_TIME_CACHE.clear()

    def _fake_time_resp(self, server_ms, delay_s=0.0):
        import time as _time
        def fake(req, timeout=None):
            if delay_s:
                _time.sleep(delay_s)
            return _FakeResp(json.dumps({"serverTime": server_ms}).encode())
        return fake

    def test_offset_applied_and_cached(self):
        import time as _time
        calls = {"n": 0}

        def fake(req, timeout=None):
            calls["n"] += 1
            return _FakeResp(json.dumps({"serverTime": int(_time.time() * 1000) + 2000}).encode())

        with patch("r20_backend.exchanges.binance.urlopen", fake):
            first = self.adapter.server_time_offset_ms()
            second = self.adapter.server_time_offset_ms()
            forced = self.adapter.server_time_offset_ms(force=True)
        self.assertAlmostEqual(first, 2000, delta=60, msg="偏差≈服务器-本机（含半程 RTT 修正）")
        self.assertEqual(first, second, "TTL 内必须命中缓存")
        self.assertEqual(calls["n"], 2, "仅 force=True 才重新出网")
        self.assertAlmostEqual(forced, 2000, delta=60)

    def test_aligned_ms_carries_the_offset(self):
        import time as _time
        with patch.object(self.adapter, "server_time_offset_ms", lambda **k: 1500.0):
            got = self.adapter._server_aligned_ms()
        self.assertAlmostEqual(got - int(_time.time() * 1000), 1500, delta=50)

    def test_signed_request_timestamp_is_aligned(self):
        import time as _time
        seen: dict = {}

        def fake(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "/fapi/v1/time" in url:
                return _FakeResp(json.dumps({"serverTime": int(_time.time() * 1000) + 2000}).encode())
            seen["url"] = url
            return _FakeResp(b'{"ok": true}')

        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=("ak", "sk")), \
             patch("r20_backend.exchanges.binance.urlopen", fake):
            self.adapter.signed_request("GET", "/fapi/v2/account")
        ts = int(parse_qs(urlparse(seen["url"]).query)["timestamp"][0])
        self.assertAlmostEqual(ts - int(_time.time() * 1000), 2000, delta=120,
                               msg="签名时间戳必须带上校时偏差（否则 -1021 复现）")

    def test_1021_retries_once_after_resync(self):
        sends = {"n": 0}

        def fake(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "/fapi/v1/time" in url:
                import time as _t
                return _FakeResp(json.dumps({"serverTime": int(_t.time() * 1000)}).encode())
            sends["n"] += 1
            if sends["n"] == 1:
                raise HTTPError(url, 400, "bad", {}, io.BytesIO(
                    b'{"code":-1021,"msg":"Timestamp for this request is outside of the recvWindow."}'))
            return _FakeResp(b'{"ok": true}')

        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=("ak", "sk")), \
             patch("r20_backend.exchanges.binance.urlopen", fake), \
             patch.object(self.adapter, "server_time_offset_ms",
                          side_effect=lambda **k: 0.0) as resync:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                out = self.adapter.signed_request("GET", "/fapi/v2/account")
        self.assertEqual(out, {"ok": True})
        self.assertEqual(sends["n"], 2, "必须重试且只重试一次")
        self.assertTrue(any(c.kwargs.get("force") for c in resync.call_args_list),
                        "重试前必须 force 重测偏差")
        self.assertTrue(any("-1021" in str(w.message) for w in caught), "重试必须留告警")

    def test_1021_persisting_raises_after_one_retry(self):
        sends = {"n": 0}

        def fake(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "/fapi/v1/time" in url:
                import time as _t
                return _FakeResp(json.dumps({"serverTime": int(_t.time() * 1000)}).encode())
            sends["n"] += 1
            raise HTTPError(url, 400, "bad", {}, io.BytesIO(
                b'{"code":-1021,"msg":"Timestamp for this request is outside of the recvWindow."}'))

        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=("ak", "sk")), \
             patch("r20_backend.exchanges.binance.urlopen", fake), \
             patch.object(self.adapter, "server_time_offset_ms", side_effect=lambda **k: 0.0):
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                with self.assertRaises(BinanceAPIError):
                    self.adapter.signed_request("GET", "/fapi/v2/account")
        self.assertEqual(sends["n"], 2, "不得无限重试（恰好 2 次后上抛）")

    def test_offset_measure_failure_is_fail_open_with_warning(self):
        def fake(req, timeout=None):
            raise OSError("no route")

        with patch("r20_backend.exchanges.binance.urlopen", fake):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                off = self.adapter.server_time_offset_ms()
        self.assertEqual(off, 0.0, "校时不可用必须 fail-open（绝不因校时阻断交易）")
        self.assertTrue(any(issubclass(w.category, RuntimeWarning) for w in caught))

    def test_large_offset_warns_with_remediation(self):
        import time as _time

        def fake(req, timeout=None):
            return _FakeResp(json.dumps({"serverTime": int(_time.time() * 1000) + 3600}).encode())

        with patch("r20_backend.exchanges.binance.urlopen", fake):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                self.adapter.server_time_offset_ms()
        msgs = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
        self.assertTrue(any("NTP" in m and "recvWindow" in m for m in msgs),
                        f"偏差过大必须点名 NTP 与 recvWindow：{msgs}")


if __name__ == "__main__":
    unittest.main()

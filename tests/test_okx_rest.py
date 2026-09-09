"""US-001 封闭测试：scripts/okx_rest.py 统一 V5 直签客户端。

封闭三律对齐：
- 律①：一切交易所调用停在 HTTP 边界——patch `scripts.okx_rest.urlopen`（import 时
  绑定的别名，patch 位置即生效位置），零 subprocess、零真实网络。
- 律②：okx_rest 在 import 时绑定 `urlopen` 与 `current_environment`，测试 patch 的
  是 okx_rest 命名空间内的名字，而非 urllib.request 源头的同名函数。
- 律③：只钉住跨故事契约（方法名、异常、签名口径、头、端点路径、数组体），不钉历史
  CLI 实现。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json
import unittest
from unittest.mock import MagicMock, patch

import scripts.okx_rest as okx_rest
from scripts.okx_runtime import freeze_environment, unfreeze_environment

DEMO_ENV = {
    "R20_OKX_ENV": "demo",
    "OKX_DEMO_API_KEY": "DEMO_AK", "OKX_DEMO_SECRET_KEY": "DEMO_SK", "OKX_DEMO_PASSPHRASE": "DEMO_PP",
}
LIVE_ENV = {
    "R20_OKX_ENV": "live",
    "OKX_LIVE_API_KEY": "LIVE_AK", "OKX_LIVE_SECRET_KEY": "LIVE_SK", "OKX_LIVE_PASSPHRASE": "LIVE_PP",
}
UNCONFIGURED_ENV = {"R20_OKX_ENV": "demo"}  # 无任何键 → configured False


def _response(code="0", msg="", data=None):
    body = json.dumps({"code": code, "msg": msg, "data": data if data is not None else []}).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _captured(mock):
    """Return (method, url, headers_lower, data_bytes) of the single urlopen call."""
    mock.assert_called_once()
    request = mock.call_args.args[0]
    headers = {str(key).lower(): value for key, value in request.header_items()}
    return request.get_method(), request.full_url, headers, request.data


def _expected_sign(secret: str, timestamp: str, method: str, path_with_query: str, body_text: str) -> str:
    prehash = timestamp + method + path_with_query + body_text
    return base64.b64encode(hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()).decode()


class OKXRestHttpBoundaryTests(unittest.TestCase):
    """Each case freezes the env (the real cycle mechanism) and intercepts urlopen."""

    def setUp(self):
        unfreeze_environment()
        patcher = patch.object(okx_rest, "urlopen")
        self.urlopen = patcher.start()
        self.addCleanup(unfreeze_environment)
        self.addCleanup(patcher.stop)

    # -- 签名口径 / 头 ------------------------------------------------------

    def test_signed_get_recomputable_prehash_and_demo_header(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"ordId": "1", "instId": "BTC-USDT-SWAP"}])
        rows = okx_rest.pending_orders("BTC-USDT-SWAP")
        self.assertEqual(rows[0]["ordId"], "1")
        method, url, headers, data = _captured(self.urlopen)
        self.assertEqual(method, "GET")
        self.assertIsNone(data)
        self.assertEqual(url, "https://www.okx.com/api/v5/trade/orders-pending?instType=SWAP&instId=BTC-USDT-SWAP")
        self.assertEqual(headers["ok-access-key"], "DEMO_AK")
        self.assertEqual(headers["ok-access-passphrase"], "DEMO_PP")
        self.assertEqual(headers["x-simulated-trading"], "1")
        self.assertEqual(
            headers["ok-access-sign"],
            _expected_sign("DEMO_SK", headers["ok-access-timestamp"], "GET",
                           url.replace("https://www.okx.com", ""), ""),
        )

    def test_live_mode_has_no_simulated_header_and_signs_with_body(self):
        freeze_environment(LIVE_ENV)
        self.urlopen.return_value = _response(data=[{"ordId": "42"}])
        okx_rest.cancel_order("ETH-USDT-SWAP", "42")
        method, url, headers, data = _captured(self.urlopen)
        self.assertEqual(method, "POST")
        self.assertNotIn("x-simulated-trading", headers)
        self.assertEqual(headers["ok-access-key"], "LIVE_AK")
        body_text = data.decode("utf-8")
        self.assertEqual(json.loads(body_text), {"instId": "ETH-USDT-SWAP", "ordId": "42"})
        self.assertEqual(
            headers["ok-access-sign"],
            _expected_sign("LIVE_SK", headers["ok-access-timestamp"], "POST",
                           "/api/v5/trade/cancel-order", body_text),
        )

    # -- fail-closed ---------------------------------------------------------

    def test_unconfigured_raises_without_any_request(self):
        freeze_environment(UNCONFIGURED_ENV)
        with self.assertRaises(okx_rest.OKXNotConfigured):
            okx_rest.balances()
        self.urlopen.assert_not_called()

    def test_okx_not_configured_is_runtime_error_subclass(self):
        self.assertTrue(issubclass(okx_rest.OKXNotConfigured, RuntimeError))

    # -- 错误语义 ------------------------------------------------------------

    def test_envelope_code_failure_raises_with_code_and_msg(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(code="50011", msg="invalid signature")
        with self.assertRaises(RuntimeError) as ctx:
            okx_rest.positions()
        self.assertIn("50011", str(ctx.exception))
        self.assertIn("invalid signature", str(ctx.exception))

    def test_row_scodes_failure_raises(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"ordId": "7", "sCode": "51001", "sMsg": "Order does not exist"}])
        with self.assertRaises(RuntimeError) as ctx:
            okx_rest.cancel_order("BTC-USDT-SWAP", "7")
        self.assertIn("51001", str(ctx.exception))
        self.assertIn("Order does not exist", str(ctx.exception))

    def test_success_rows_only(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"sCode": "0", "ordId": "9"}, "junk"])
        rows = okx_rest.pending_orders()
        self.assertEqual(rows, [{"sCode": "0", "ordId": "9"}])

    # -- place_order 与 attach TP/SL ----------------------------------------

    def test_place_order_attach_tp_sl_leg_and_numeric_formatting(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"sCode": "0", "ordId": "ord-1"}])
        rows = okx_rest.place_order(
            "BTC-USDT-SWAP", "buy", 0.02, pos_side="long", td_mode="cross",
            ord_type="limit", px=27181.5, attach_tp=28000.0, attach_sl=26500.0,
        )
        self.assertEqual(rows[0]["ordId"], "ord-1")
        _, url, _, data = _captured(self.urlopen)
        self.assertEqual(url, "https://www.okx.com/api/v5/trade/order")
        body = json.loads(data.decode("utf-8"))
        self.assertEqual(body["sz"], "0.02")
        self.assertEqual(body["px"], "27181.5")
        self.assertEqual(body["posSide"], "long")
        self.assertEqual(body["attachAlgoOrds"], [{
            "tdMode": "cross", "tpTriggerPx": "28000", "tpOrdPx": "-1",
            "slTriggerPx": "26500", "slOrdPx": "-1",
        }])

    # -- algo 面 -------------------------------------------------------------

    def test_place_algo_oco_body_maps_cli_flags(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"algoId": "a-1"}])
        okx_rest.place_algo_oco("BTC-USDT-SWAP", "sell", 2, pos_side="long",
                                td_mode="cross", tp_trigger_px=29000, sl_trigger_px=26000)
        method, url, headers, data = _captured(self.urlopen)
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://www.okx.com/api/v5/trade/order-algo")
        body = json.loads(data.decode("utf-8"))
        self.assertEqual(body["ordType"], "oco")
        self.assertEqual(body["reduceOnly"], "true")
        self.assertEqual(body["cxlOnClosePos"], "true")
        self.assertEqual(body["slOrdPx"], "-1")
        self.assertEqual(body["tpOrdPx"], "-1")
        self.assertEqual(headers["content-type"], "application/json")

    def test_cancel_algo_orders_uses_array_body(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"algoId": "a-1", "sCode": "0"}])
        okx_rest.cancel_algo_orders(["a-1"])
        _, url, _, data = _captured(self.urlopen)
        self.assertEqual(url, "https://www.okx.com/api/v5/trade/cancel-algo-orders")
        self.assertEqual(json.loads(data.decode("utf-8")), [{"algoId": "a-1", "instType": "SWAP"}])
        self.urlopen.reset_mock()
        self.urlopen.return_value = _response(data=[])
        with self.assertRaises(ValueError):
            okx_rest.cancel_algo_orders([])
        self.urlopen.assert_not_called()

    def test_amend_algo_sl_defaults_market_px(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"algoId": "a-9", "sCode": "0"}])
        okx_rest.amend_algo_sl("a-9", 26750.5)
        _, url, _, data = _captured(self.urlopen)
        self.assertEqual(url, "https://www.okx.com/api/v5/trade/amend-algos")
        rows = json.loads(data.decode("utf-8"))
        self.assertIsInstance(rows, list)
        self.assertEqual(rows[0]["algoId"], "a-9")
        self.assertEqual(rows[0]["newSlOrdPx"], "-1")
        self.assertEqual(rows[0]["newSlTriggerPx"], "26750.5")

    def test_pending_algo_orders_filters_locally_by_inst(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[
            {"algoId": "a-1", "instId": "BTC-USDT-SWAP"},
            {"algoId": "a-2", "instId": "ETH-USDT-SWAP"},
        ])
        rows = okx_rest.pending_algo_orders("BTC-USDT-SWAP")
        self.assertEqual([row["algoId"] for row in rows], ["a-1"])
        _, url, _, _ = _captured(self.urlopen)
        self.assertTrue(url.startswith("https://www.okx.com/api/v5/trade/orders-algo-pending?"))

    # -- 端点映射（其余方法逐一钉住路径） ------------------------------------

    def test_endpoint_paths_and_query_defaults(self):
        freeze_environment(DEMO_ENV)
        cases = {
            "amend_order": (lambda: okx_rest.amend_order("BTC-USDT-SWAP", "5", new_px=100), "/api/v5/trade/amend-order"),
            "close_position": (lambda: okx_rest.close_position("BTC-USDT-SWAP", "long"), "/api/v5/trade/close-position"),
            "orders_history": (lambda: okx_rest.orders_history(limit=100), "/api/v5/trade/orders-history"),
            "fills": (lambda: okx_rest.fills(), "/api/v5/trade/fills"),
            "balances": (lambda: okx_rest.balances(), "/api/v5/account/balance"),
            "bills": (lambda: okx_rest.bills(limit=100), "/api/v5/account/bills"),
            "positions_history": (lambda: okx_rest.positions_history(limit=100), "/api/v5/account/positions-history"),
        }
        for name, (call, expected_url_part) in cases.items():
            with self.subTest(name=name):
                self.urlopen.reset_mock()
                self.urlopen.return_value = _response(data=[])
                call()
                _, url, _, data = _captured(self.urlopen)
                self.assertIn(expected_url_part, url.split("?")[0])
                if expected_url_part.endswith(("orders-history", "fills", "balance", "bills", "positions-history")):
                    self.assertIsNone(data, "queries must be GET")
                else:
                    self.assertIsNotNone(data, "mutations must POST a body")

    def test_positions_default_inst_type_swap_and_position_filters_zero(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"instId": "BTC-USDT-SWAP", "pos": "0"}, {"instId": "ETH-USDT-SWAP", "pos": "-2"}])
        row = okx_rest.position("ETH-USDT-SWAP")
        self.assertEqual(row["pos"], "-2")
        _, url, _, _ = _captured(self.urlopen)
        self.assertIn("instType=SWAP", url)
        self.assertIn("instId=ETH-USDT-SWAP", url)

    # -- 溯源 tripwire：CLI 永不复活 ----------------------------------------

    def test_module_never_touches_cli_or_subprocess(self):
        source = inspect.getsource(okx_rest)
        for banned in ("subprocess", "okx --", "replace_cli_prefix", "cli_prefix", "os.environ"):
            self.assertNotIn(banned, source, f"okx_rest must stay CLI-free: found {banned!r}")
        # 凭证唯一来源 = runtime 的 current_environment（含冻结优先）
        self.assertIn("current_environment", source)


class CurrentEnvironmentContractTests(unittest.TestCase):
    """okx_runtime.current_environment：冻结周期 env 优先，解冻回落实时选择。"""

    def tearDown(self):
        unfreeze_environment()

    def test_frozen_env_wins_then_live_selection(self):
        from scripts.okx_runtime import current_environment
        self.assertFalse(current_environment(UNCONFIGURED_ENV).configured)
        frozen = freeze_environment(DEMO_ENV)
        again = current_environment(UNCONFIGURED_ENV)  # 即使传入未配置 values，也必须命中冻结
        self.assertEqual(again.identity, frozen.identity)
        self.assertTrue(again.configured)
        self.assertTrue(again.simulated)
        unfreeze_environment()
        live = current_environment(LIVE_ENV)
        self.assertEqual(live.mode, "live")
        self.assertFalse(live.simulated)


if __name__ == "__main__":
    unittest.main()

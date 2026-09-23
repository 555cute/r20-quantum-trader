"""交易所连接诊断（`r20_backend/exchanges/diagnostics.py`）残余分支收口测试 —— 第 340 刀。

本模块 334 行，是 OKX / Binance / Gate 三所连通性与凭证鉴权诊断核心：
- `_default_http_call`：标准 HTTP 请求封装、JSON 与纯文本降级、HTTPError 解析；
- `diagnose_venue_connection`：无效交易所拦截、隐式凭证提取、公共端点连通探测与私有接口鉴权；
- `_diagnose_public_ping`：OKX/Gate/Binance 公共时间与合约接口探测及网络异常处理；
- 鉴权报错解析：Binance 错误码与错误信息提取、Gate 标签解析、OKX 业务状态码处理。
"""
from __future__ import annotations

import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from r20_backend.exchanges.diagnostics import (
    _default_http_call,
    _diagnose_binance,
    _diagnose_gate,
    _diagnose_okx,
    _diagnose_public_ping,
    _resolve_for_diagnostics,
    diagnose_venue_connection,
)


def _make_mock_response(status: int = 200, body: str | bytes = b"{}", headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    if isinstance(body, str):
        body_bytes = body.encode("utf-8")
    else:
        body_bytes = body
    resp.read.return_value = body_bytes
    resp.headers = headers or {"content-type": "application/json"}
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *a: False
    return resp


def _make_mock_http_error(code: int = 401, body: str | bytes = b"{}", headers: dict | None = None) -> urllib.error.HTTPError:
    err = urllib.error.HTTPError("http://test.api", code, "HTTP Error", headers or {}, None)
    if isinstance(body, str):
        body_bytes = body.encode("utf-8")
    else:
        body_bytes = body
    err.read = lambda: body_bytes
    err.headers = headers or {"content-type": "application/json"}
    return err


class ExchangeDiagnosticsTailsTests(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. 默认 HTTP 封装 (_default_http_call)
    # -------------------------------------------------------------------------
    @patch("r20_backend.exchanges.diagnostics.urlopen")
    def test_default_http_call_success_json(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_response(200, json.dumps({"server_time": 123456789}))
        status, data, headers = _default_http_call("https://api.test.com/time")
        self.assertEqual(status, 200)
        self.assertEqual(data.get("server_time"), 123456789)
        self.assertEqual(headers.get("content-type"), "application/json")

    @patch("r20_backend.exchanges.diagnostics.urlopen")
    def test_default_http_call_success_non_json(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_response(200, "plain text response")
        status, data, headers = _default_http_call("https://api.test.com/ping")
        self.assertEqual(status, 200)
        self.assertEqual(data.get("raw_text"), "plain text response")

    @patch("r20_backend.exchanges.diagnostics.urlopen")
    def test_default_http_call_http_error_json(self, mock_urlopen):
        mock_urlopen.side_effect = _make_mock_http_error(400, json.dumps({"code": -1001, "msg": "Invalid param"}))
        status, data, headers = _default_http_call("https://api.test.com/order")
        self.assertEqual(status, 400)
        self.assertEqual(data.get("code"), -1001)
        self.assertEqual(data.get("msg"), "Invalid param")

    @patch("r20_backend.exchanges.diagnostics.urlopen")
    def test_default_http_call_http_error_non_json(self, mock_urlopen):
        mock_urlopen.side_effect = _make_mock_http_error(502, "Bad Gateway HTML error page")
        status, data, headers = _default_http_call("https://api.test.com/down")
        self.assertEqual(status, 502)
        self.assertIn("Bad Gateway", data.get("raw_text", ""))

    # -------------------------------------------------------------------------
    # 2. 诊断主入口 (diagnose_venue_connection)
    # -------------------------------------------------------------------------
    def test_diagnose_venue_connection_unsupported_venue(self):
        res = diagnose_venue_connection(venue="coinbase", environment="live")
        self.assertFalse(res["ok"])
        self.assertEqual(res["mode"], "invalid_venue")
        self.assertIn("不支持的交易所", res["message"])

    def test_diagnose_venue_connection_implicit_credentials_loading_okx(self):
        # api_key 与 secret_key 均未显式传参时，从 venue_credentials 与 venue_passphrase 加载
        with patch("r20_backend.exchanges.diagnostics.venue_credentials", return_value=("ak_okx", "sk_okx")):
            with patch("r20_backend.exchanges.diagnostics.venue_passphrase", return_value="pp_okx"):
                caller = MagicMock(return_value=(200, {"code": "0", "data": [{"total": "100"}]}, {}))
                res = diagnose_venue_connection(
                    venue="okx",
                    environment="live",
                    http_client=caller,
                )
                self.assertTrue(res["ok"])
                self.assertEqual(res["mode"], "authenticated")
                self.assertIn("OKX LIVE 凭证鉴权成功", res["message"])

    def test_diagnose_venue_connection_implicit_credentials_loading_binance(self):
        with patch("r20_backend.exchanges.diagnostics.venue_credentials", return_value=("ak_bin", "sk_bin")):
            caller = MagicMock(return_value=(200, {"canTrade": True, "totalWalletBalance": "500"}, {}))
            res = diagnose_venue_connection(
                venue="binance",
                environment="live",
                http_client=caller,
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["mode"], "authenticated")
            self.assertIn("Binance LIVE 凭证鉴权成功", res["message"])

    def test_diagnose_venue_connection_private_call_exception_handled(self):
        # 鉴权过程中底层 caller 抛出非 HTTPError 异常（如网络断开/连接重置）
        caller = MagicMock(side_effect=ConnectionResetError("Peer reset"))
        res = diagnose_venue_connection(
            venue="binance",
            environment="live",
            api_key="ak",
            secret_key="sk",
            http_client=caller,
        )
        self.assertFalse(res["ok"])
        self.assertEqual(res["mode"], "network_error")
        self.assertIn("ConnectionResetError", res["message"])
        self.assertGreaterEqual(res["latency_ms"], 1)

    # -------------------------------------------------------------------------
    # 3. 公共连通性探测 (_diagnose_public_ping)
    # -------------------------------------------------------------------------
    def test_diagnose_public_ping_okx_success(self):
        caller = MagicMock(return_value=(200, {"code": "0", "data": [{"ts": "12345"}]}, {}))
        res = _diagnose_public_ping("okx", "live", caller, timeout=5.0)
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "public_fallback")
        self.assertIn("公共网络连通正常", res["message"])
        self.assertEqual(res["details"]["endpoint"], "/api/v5/public/time")

    def test_diagnose_public_ping_okx_failure(self):
        caller = MagicMock(return_value=(503, {}, {}))
        res = _diagnose_public_ping("okx", "live", caller, timeout=5.0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["mode"], "public_fallback")
        self.assertIn("公共网络连通失败", res["message"])

    def test_diagnose_public_ping_gate_success(self):
        caller = MagicMock(return_value=(200, [{"name": "BTC_USDT"}], {}))
        res = _diagnose_public_ping("gate", "live", caller, timeout=5.0)
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "public_fallback")
        self.assertEqual(res["details"]["endpoint"], "/api/v4/futures/usdt/contracts")

    def test_diagnose_public_ping_exception_handled(self):
        caller = MagicMock(side_effect=TimeoutError("DNS timeout"))
        res = _diagnose_public_ping("gate", "live", caller, timeout=5.0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["mode"], "network_error")
        self.assertIn("TimeoutError", res["message"])

    # -------------------------------------------------------------------------
    # 4. 私有鉴权分支报错解析 (_diagnose_binance & _diagnose_gate)
    # -------------------------------------------------------------------------
    def test_diagnose_binance_auth_failed_with_code_and_msg(self):
        caller = MagicMock(return_value=(401, {"code": -2015, "msg": "Invalid API-key, IP, or permissions for action"}, {}))
        res = _diagnose_binance("live", False, "bad_ak", "bad_sk", caller, 5.0, 0.0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["mode"], "auth_failed")
        self.assertIn("[-2015]", res["message"])
        self.assertIn("Invalid API-key", res["message"])

    def test_diagnose_binance_auth_failed_with_raw_text(self):
        caller = MagicMock(return_value=(500, {"raw_text": "Internal error"}, {}))
        res = _diagnose_binance("live", False, "bad_ak", "bad_sk", caller, 5.0, 0.0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["mode"], "auth_failed")
        self.assertIn("Internal error", res["message"])

    def test_diagnose_gate_auth_failed_with_label_and_message(self):
        caller = MagicMock(return_value=(401, {"label": "INVALID_KEY", "message": "Key not found"}, {}))
        res = _diagnose_gate("live", False, "bad_ak", "bad_sk", caller, 5.0, 0.0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["mode"], "auth_failed")
        self.assertIn("[INVALID_KEY]", res["message"])
        self.assertIn("Key not found", res["message"])

    def test_diagnose_okx_auth_failed_code_error(self):
        caller = MagicMock(return_value=(200, {"code": "50100", "msg": "API Key does not exist"}, {}))
        res = _diagnose_okx("live", False, "bad_ak", "bad_sk", "bad_pp", caller, 5.0, 0.0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["mode"], "auth_failed")
        self.assertIn("[50100]", res["message"])
        self.assertIn("API Key does not exist", res["message"])

    def test_resolve_for_diagnostics_probe_exception_handled(self):
        # 当沙盒未钉死且多候选探测全部抛异常时，触发 line 147 (return None)，最终 fail-closed 报错
        from r20_backend.exchanges.base import ExchangeCapabilityError
        caller = MagicMock(side_effect=ConnectionRefusedError("Connection refused"))
        with patch("r20_backend.exchanges.env_profiles.pinned_base_url", return_value=None):
            with self.assertRaises(ExchangeCapabilityError):
                _resolve_for_diagnostics("gate", "sandbox", caller)


if __name__ == "__main__":
    unittest.main()

"""US-003 封闭测试：ai_brain_trader 挂单查询/撤单执行走 scripts.okx_rest 唯一私有通道。

封闭三律对齐：
- 律①：一切交易所调用停在 HTTP 边界——patch `scripts.okx_rest.urlopen`（import 时
  绑定别名，patch 位置即生效位置），零 subprocess、零真实网络、零真实凭证。
- 律②：ai_brain 调用的是 okx_rest 模块函数，其网络出口绑定在 okx_rest 命名空间内，
  测试 patch 的就是该生效位置；凭证注入用真实 freeze_environment(假键) +
  tearDown unfreeze_environment()（US-001 Learnings 3 口径）。
- 律③：只钉跨故事契约（pending_orders/cancel_order 端点路径、签名头、demo 模拟头、
  失败继续循环、未配置零网络 fail-closed），不钉历史 CLI 实现细节。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ai_brain_trader
import scripts.okx_rest as okx_rest
from scripts.okx_runtime import freeze_environment, unfreeze_environment

DEMO_ENV = {
    "R20_OKX_ENV": "demo",
    "OKX_DEMO_API_KEY": "DEMO_AK", "OKX_DEMO_SECRET_KEY": "DEMO_SK", "OKX_DEMO_PASSPHRASE": "DEMO_PP",
}
UNCONFIGURED_ENV = {"R20_OKX_ENV": "demo"}  # 无任何键 → configured False


def _response(code="0", msg="", data=None):
    body = json.dumps({"code": code, "msg": msg, "data": data if data is not None else []}).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _captured(mock, index=0):
    request = mock.call_args_list[index].args[0]
    headers = {str(key).lower(): value for key, value in request.header_items()}
    return request.get_method(), request.full_url, headers, request.data


def _expected_sign(timestamp, method, path_with_query, body_text):
    prehash = timestamp + method + path_with_query + body_text
    return base64.b64encode(hmac.new(DEMO_ENV["OKX_DEMO_SECRET_KEY"].encode(), prehash.encode(), hashlib.sha256).digest()).decode()


class _EnvFreezeMixin:
    env = DEMO_ENV

    def setUp(self):
        freeze_environment(dict(self.env))
        self.addCleanup(unfreeze_environment)


class PendingOrdersFetchTests(_EnvFreezeMixin, unittest.TestCase):
    """fetch_pending_orders_list：GET orders-pending 直签 + fail-closed。"""

    def test_returns_list_via_signed_http_boundary(self):
        rows = [{"ordId": "1", "instId": "BTC-USDT-SWAP", "state": "live"}]
        with patch.object(okx_rest, "urlopen", return_value=_response(data=rows)) as net:
            got = ai_brain_trader.fetch_pending_orders_list()
        self.assertEqual(got, rows)
        method, url, headers, body = _captured(net)
        self.assertEqual(method, "GET")
        self.assertIn("/api/v5/trade/orders-pending", url)
        self.assertIn("instType=SWAP", url)
        self.assertIsNone(body)
        self.assertEqual(headers.get("ok-access-key"), "DEMO_AK")
        self.assertEqual(headers.get("x-simulated-trading"), "1")
        self.assertEqual(
            headers.get("ok-access-sign"),
            _expected_sign(headers["ok-access-timestamp"], "GET", url.split("okx.com", 1)[1], ""),
        )

    def test_unconfigured_is_none_with_zero_network(self):
        self.env = UNCONFIGURED_ENV
        freeze_environment(dict(UNCONFIGURED_ENV))
        with patch.object(okx_rest, "urlopen") as net:
            got = ai_brain_trader.fetch_pending_orders_list()
        self.assertIsNone(got)
        net.assert_not_called()

    def test_api_error_degrades_to_none_not_crash(self):
        with patch.object(okx_rest, "urlopen", return_value=_response(code="50011", msg="Signature error")):
            got = ai_brain_trader.fetch_pending_orders_list()
        self.assertIsNone(got)


class PendingCancelExecutionTests(_EnvFreezeMixin, unittest.TestCase):
    """execute_brain_pending_cancels：POST cancel-order、真实成功才报成功、失败继续。"""

    def test_only_cancel_actions_fire_and_sign_demo_headers(self):
        items = [
            {"action": "CANCEL", "ordId": "123", "instId": "BTC-USDT-SWAP", "reason": "过时限价"},
            {"action": "HOLD", "ordId": "999", "instId": "ETH-USDT-SWAP"},
            "not-a-dict",
            {"action": "CANCEL", "ordId": "", "instId": "SOL-USDT-SWAP"},
        ]
        with patch.object(okx_rest, "urlopen", return_value=_response(data=[{"ordId": "123", "sCode": "0"}])) as net:
            log = ai_brain_trader.execute_brain_pending_cancels(items)
        self.assertEqual(len(log), 1)
        self.assertTrue(log[0]["ok"])
        self.assertEqual(log[0]["ordId"], "123")
        net.assert_called_once()
        method, url, headers, body = _captured(net)
        self.assertEqual(method, "POST")
        self.assertIn("/api/v5/trade/cancel-order", url)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload, {"instId": "BTC-USDT-SWAP", "ordId": "123"})
        self.assertEqual(headers.get("ok-access-key"), "DEMO_AK")
        self.assertEqual(headers.get("x-simulated-trading"), "1")
        self.assertEqual(
            headers.get("ok-access-sign"),
            _expected_sign(headers["ok-access-timestamp"], "POST", "/api/v5/trade/cancel-order", body.decode("utf-8")),
        )

    def test_row_level_failure_no_fake_success_and_loop_continues(self):
        items = [
            {"action": "CANCEL", "ordId": "1", "instId": "BTC-USDT-SWAP"},
            {"action": "CANCEL", "ordId": "2", "instId": "ETH-USDT-SWAP"},
        ]
        responses = [
            _response(data=[{"ordId": "1", "sCode": "51420", "sMsg": "order not exist"}]),  # 行级失败
            _response(data=[{"ordId": "2", "sCode": "0"}]),
        ]
        with patch.object(okx_rest, "urlopen", side_effect=responses) as net:
            log = ai_brain_trader.execute_brain_pending_cancels(items)
        self.assertEqual([entry["ok"] for entry in log], [False, True])
        self.assertIn("51420", log[0]["error"])
        self.assertEqual(net.call_count, 2)

    def test_unconfigured_fail_closed_zero_network(self):
        self.env = UNCONFIGURED_ENV
        freeze_environment(dict(UNCONFIGURED_ENV))
        items = [{"action": "CANCEL", "ordId": "1", "instId": "BTC-USDT-SWAP"}]
        with patch.object(okx_rest, "urlopen") as net:
            log = ai_brain_trader.execute_brain_pending_cancels(items)
        self.assertEqual(len(log), 1)
        self.assertFalse(log[0]["ok"])
        net.assert_not_called()


class BrainFileHygieneTests(unittest.TestCase):
    """迁移边界 tripwire：ai_brain 私有交易面只准走 okx_rest（禁 CLI 字符串/前缀机制）。"""

    def test_ai_brain_has_no_legacy_cli_private_paths(self):
        src = Path(ai_brain_trader.__file__).read_text(encoding="utf-8")
        for banned in ("replace_cli_prefix", "okx_private_command", "okx swap", "okx account"):
            self.assertNotIn(banned, src, f"ai_brain_trader.py 残留已退役私有 CLI 路径: {banned}")


if __name__ == "__main__":
    unittest.main()

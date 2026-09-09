"""US-006 封闭测试：dashboard 账户/账单/保护单读路径走 V5 直签 REST。

一切断言发生在 HTTP 边界（patch scripts.okx_rest 内绑定的 urlopen），
凭证经 freeze_environment(values) 注入、tearDown 解冻（封闭三律①②）。
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import dashboard.app as dashboard
import scripts.okx_rest as okx_rest
from scripts.okx_runtime import freeze_environment, unfreeze_environment

DEMO_ENV = {
    "R20_OKX_ENV": "demo",
    "OKX_DEMO_API_KEY": "AKDEMO",
    "OKX_DEMO_SECRET_KEY": "SKDEMO",
    "OKX_DEMO_PASSPHRASE": "PPDEMO",
}
NO_KEY_ENV = {"R20_OKX_ENV": "demo"}


def _fake_urlopen(rows, record):
    """返回 (ok payload) 的假 urlopen；record 收集 (method, url, headers, body)。"""
    def _open(req, timeout=None):
        payload = {"code": "0", "data": rows}

        class _Resp:
            def read(self):
                return json.dumps(payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        record.append({
            "url": req.full_url,
            "method": req.get_method(),
            "key": req.get_header("Ok-access-key"),
            "sim": req.get_header("X-simulated-trading"),
        })
        return _Resp()

    return _open


class OKXReadPathTests(unittest.TestCase):
    def setUp(self):
        unfreeze_environment()
        self.addCleanup(unfreeze_environment)

    def test_success_keeps_tuple_contract_and_signed_demo_headers(self):
        freeze_environment(DEMO_ENV)
        record = []
        rows = [{"details": [{"ccy": "USDT", "eq": "42.5"}]}]
        with patch.object(okx_rest, "urlopen", _fake_urlopen(rows, record)):
            ok, data, err = dashboard._fetch_json(okx_rest.balances)
        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(data, rows)
        self.assertEqual(len(record), 1)
        self.assertIn("/api/v5/account/balance", record[0]["url"])
        self.assertEqual(record[0]["key"], "AKDEMO")
        self.assertEqual(record[0]["sim"], "1")

    def test_algo_orders_positional_instid_contract(self):
        """dashboard 以位置参数传 instId（对齐旧 CLI --instId），封装须本地过滤他币。"""
        freeze_environment(DEMO_ENV)
        record = []
        rows = [
            {"instId": "BTC-USDT-SWAP", "algoId": "1", "state": "effective"},
            {"instId": "ETH-USDT-SWAP", "algoId": "2", "state": "effective"},
        ]
        with patch.object(okx_rest, "urlopen", _fake_urlopen(rows, record)):
            ok, data, err = dashboard._fetch_json(okx_rest.pending_algo_orders, "BTC-USDT-SWAP")
        self.assertTrue(ok)
        self.assertEqual([r["algoId"] for r in data], ["1"])
        self.assertIn("orders-algo-pending", record[0]["url"])

    def test_not_ready_when_key_missing_and_zero_requests(self):
        freeze_environment(NO_KEY_ENV)
        record = []
        probes = [
            (okx_rest.balances, (), {}),
            (okx_rest.positions, (), {}),
            (okx_rest.pending_orders, (), {}),
            (okx_rest.pending_algo_orders, ("BTC-USDT-SWAP",), {}),
            (okx_rest.bills, (), {"limit": 100}),
        ]
        with patch.object(okx_rest, "urlopen", _fake_urlopen([], record)):
            for fn, args, kwargs in probes:
                ok, data, err = dashboard._fetch_json(fn, *args, **kwargs)
                self.assertFalse(ok, msg=fn.__name__)
                self.assertIsNone(data)
                self.assertIn("NOT READY", err)
                self.assertIn("API Key", err)
        self.assertEqual(record, [], "未配置 Key 时不得发出任何请求（fail-closed）")

    def test_exchange_error_maps_to_human_text(self):
        freeze_environment(DEMO_ENV)

        def _boom(req, timeout=None):
            class _Resp:
                def read(self):
                    return json.dumps({"code": "50011", "msg": "invalid signature"}).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

            return _Resp()

        with patch.object(okx_rest, "urlopen", _boom):
            ok, data, err = dashboard._fetch_json(okx_rest.bills, limit=100)
        self.assertFalse(ok)
        self.assertIn("50011", err)
        self.assertIn("invalid signature", err)

    def test_dashboard_source_has_no_cli_readers(self):
        """律③：dashboard 生产面不得再引用已删的 CLI 读路径符号。"""
        import inspect
        src = inspect.getsource(dashboard)
        for dead in ("okx_private_command", "replace_cli_prefix", "run_json_cmd_status",
                     '"okx account', '"okx swap', "f\"okx account", "f\"okx swap"):
            self.assertNotIn(dead, src)


if __name__ == "__main__":
    unittest.main()

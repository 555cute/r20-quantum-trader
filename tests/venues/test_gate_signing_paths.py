"""Gate 私有请求的签名与载荷（第二百七十四刀）。

Gate V4 的签名契约与 Binance 完全不同，**这是最容易串味的地方**：

| 项 | Gate | Binance |
|---|---|---|
| 摘要 | **HMAC-SHA512** | HMAC-SHA256 |
| 待签串 | `METHOD\\nPATH\\nQUERY\\nSHA512(body)\\nTIMESTAMP`（**body 的哈希也在串里**） | query + `signature` |
| 时间戳 | **秒**（整数） | 毫秒 |

另：私有面一切响应过 `_normalize_gate_ids`（US-004：id 从此恒为字符串）。
"""

import hashlib
import hmac
import io
import json
import time as _time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from r20_backend.exchanges.gate import GateAdapter, GateAPIError


class _Resp:
    def __init__(self, payload):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = GateAdapter.__new__(GateAdapter)
        self.ad.base_url = "https://api.gateio.ws"
        self.ad.environment = "demo"
        BinanceLike = patch.object(GateAdapter, "_keys", return_value=("KEY", "SECRET"))
        self._keys = BinanceLike
        self._keys.start()
        self.addCleanup(self._keys.stop)
        self.sent = []

    def _capture(self, payload=b"{}"):
        def _open(req, timeout=None):
            self.sent.append(req)
            if isinstance(payload, Exception):
                raise payload
            return _Resp(payload)
        p = patch("r20_backend.exchanges.gate.urlopen", side_effect=_open)
        p.start()
        self.addCleanup(p.stop)
        return self.ad


class SigningTest(_Base):
    def test_sign_string_layout_and_body_hash(self):
        body = '{"a":1}'
        out = GateAdapter.sign_string("post", "/x/y", "b=2", body, "1700000000")
        self.assertEqual(out, "%s\n%s\n%s\n%s\n%s" % (
            "POST", "/x/y", "b=2",
            hashlib.sha512(body.encode()).hexdigest(), "1700000000"),
            "方法大写、五段用换行相连、body 以 SHA512 摘要入串")

    def test_sign_is_hmac_sha512_over_the_message(self):
        msg = GateAdapter.sign_string("GET", "/p", "q=1", "", "123")
        want = hmac.new(b"SECRET", msg.encode(), hashlib.sha512).hexdigest()
        self.assertEqual(self.ad.sign("GET", "/p", "q=1", "", "123", "SECRET"), want)

    def test_request_headers_use_seconds_and_recomputable_signature(self):
        """★ 时间戳是**秒**（Binance 是毫秒）—— 串味会直接被交易所拒签。"""
        self._capture()
        self.ad.signed_request("GET", "/api/v4/futures/usdt/accounts",
                              params={"a": 1, "drop": None, "empty": ""})
        req = self.sent[-1]
        h = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(h["key"], "KEY")
        ts = h["timestamp"]
        self.assertAlmostEqual(int(ts), int(_time.time()), delta=5)
        self.assertLess(len(ts), 12, "秒级时间戳（10 位上下），不是毫秒 13 位")
        query = req.full_url.split("?", 1)[1]
        self.assertEqual(query, "a=1", "None/空串参数必须被剔除")
        self.assertEqual(h["sign"], self.ad.sign("GET", "/api/v4/futures/usdt/accounts",
                                                "a=1", "", ts, "SECRET"),
                         "签名必须按其待签串可重算")
        self.assertIsNone(h.get("content-type"), "无 body ⇒ 不带 Content-Type")


class PayloadAndErrorsTest(_Base):
    def test_body_is_compact_json_without_none_values(self):
        self._capture(payload={"id_string": "9007199254740993", "id": 1})
        out = self.ad.signed_request("POST", "/api/v4/futures/usdt/orders",
                                     body={"size": 1, "note": None, "c": "中文"})
        req = self.sent[-1]
        sent = json.loads(req.data.decode())
        self.assertEqual(sent, {"size": 1, "c": "中文"}, "None 值不入 body")
        self.assertNotIn(b" ", req.data, "紧凑分隔符（无空格）")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(out["id"], "9007199254740993", "响应过 id_string 归一（US-004）")

    def test_empty_response_body_is_none(self):
        self._capture(payload=b"")
        self.assertIsNone(self.ad.signed_request("GET", "/p"))

    def test_http_error_carries_label_and_message(self):
        body = json.dumps({"label": "INVALID_KEY", "message": "bad key"}).encode()

        def _boom(code):
            return HTTPError("https://api.gateio.ws/p", code, "err", {}, io.BytesIO(body))

        self._capture(payload=_boom(401))
        with self.assertRaises(GateAPIError) as ctx:
            self.ad.signed_request("GET", "/p")
        self.assertEqual(ctx.exception.label, "INVALID_KEY")
        self.assertIn("bad key", str(ctx.exception))
        self.assertEqual(ctx.exception.status, 401)

    def test_non_json_error_body_falls_back_to_http_status(self):
        err = HTTPError("https://api.gateio.ws/p", 503, "unavailable", {},
                        io.BytesIO(b"<html>gateway</html>"))
        self._capture(payload=err)
        with self.assertRaises(GateAPIError) as ctx:
            self.ad.signed_request("GET", "/p")
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.label, "503")

    def test_network_error_is_wrapped(self):
        self._capture(payload=OSError("reset by peer"))
        with self.assertRaises(GateAPIError) as ctx:
            self.ad.signed_request("GET", "/p")
        self.assertEqual(ctx.exception.label, "network")
        self.assertIn("reset by peer", str(ctx.exception))


class AccountSnapshotTest(_Base):
    def test_bad_structure_is_a_bad_response(self):
        self.ad.signed_request = lambda *a, **k: ["not", "a", "dict"]
        with self.assertRaises(GateAPIError) as ctx:
            self.ad.account_snapshot()
        self.assertEqual(ctx.exception.label, "bad_response")

    def test_fields_are_mapped(self):
        self.ad.signed_request = lambda *a, **k: {
            "currency": "USDT", "total": "1000.5", "available": "800",
            "position_margin": "100", "order_margin": "5",
            "unrealised_pnl": "-3.5", "in_dual_mode": True}
        out = self.ad.account_snapshot()
        self.assertEqual(out["equity_usdt"], 1000.5)
        self.assertEqual(out["available_usdt"], 800.0)
        self.assertEqual(out["unrealized_pnl"], -3.5)
        self.assertIs(out["in_dual_mode"], True)
        self.assertEqual(out["venue"], "gate")

    def test_missing_credentials_are_reported(self):
        from r20_backend.exchanges import ExchangeCapabilityError
        self._keys.stop()
        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=("", "")):
            with self.assertRaises(ExchangeCapabilityError):
                GateAdapter._keys(self.ad)


if __name__ == "__main__":
    unittest.main()

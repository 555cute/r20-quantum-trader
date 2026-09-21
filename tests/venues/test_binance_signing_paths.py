"""Binance 私有请求的签名与校时（第二百六十刀）。

这一族是「钱离开账户」的**前置条件**：签名不对 ⇒ 单被拒；时间戳超 `recvWindow` ⇒ `-1021`。

| 语义 | 纪律 |
|---|---|
| 校时 | 用 `GET /fapi/v1/time` 按 **RTT/2** 折算偏差；按**域**分键缓存 300s；**任何失败 ⇒ 0.0 且只告警**（fail-open：**绝不因校时不可用而阻断交易**）|
| 校时告警 | 偏差 ≥ 3000ms ⇒ 告警并说明 `recvWindow=5000ms` 的余量被吃掉多少 |
| `-1021` | 重测偏差后**重试一次**（重新取时间戳与签名）；**再失败就抛**，不无限重试 |
| 错误载荷 | HTTP 错误要把官方 `code`/`msg` 带出来；非 JSON 响应退回 HTTP 状态与 reason |
| 凭证 | 未配置 ⇒ `ExchangeCapabilityError`（**不静默**）|
"""

import hashlib
import hmac
import io
import json
import re
import time as _time
import unittest
import warnings
from unittest.mock import patch
from urllib.error import HTTPError

from r20_backend.exchanges.binance import BinanceAdapter, BinanceAPIError
from r20_backend.exchanges.binance_signing import build_signed_query


class _Resp:
    def __init__(self, payload):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return HTTPError("https://fapi.binance.com/x", code, "err", {}, io.BytesIO(body))


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = BinanceAdapter.__new__(BinanceAdapter)
        self.ad.base_url = "https://fapi.binance.com"
        self.ad.environment = "demo"
        BinanceAdapter._SERVER_TIME_CACHE.clear()
        self.addCleanup(BinanceAdapter._SERVER_TIME_CACHE.clear)
        self._keys = patch.object(BinanceAdapter, "_keys", return_value=("KEY", "SECRET"))
        self._keys.start()
        self.addCleanup(self._keys.stop)

    def _urlopen(self, side_effect):
        p = patch("r20_backend.exchanges.binance.urlopen", side_effect=side_effect)
        return p.start(), p.stop


class ServerTimeOffsetTest(_Base):
    """⚠️ 不打桩 `time.time`：`server_time_offset_ms` 内部是 `import time as _time`，
    即**模块本体** —— 打桩等于全局改写（本仓已记为坑）。改为让假服务器回报
    「真实当前 + 已知偏差」，再按容差断言。"""

    def _server(self, delta_ms, *, counter=None):
        def _fake(req, timeout=None):
            if counter is not None:
                counter["n"] = counter.get("n", 0) + 1
            return _Resp({"serverTime": _time.time() * 1000 + delta_ms})

        return _fake

    def test_offset_is_measured_and_cached_within_ttl(self):
        counter = {}
        with patch("r20_backend.exchanges.binance.urlopen", self._server(2100.0, counter=counter)):
            first = self.ad.server_time_offset_ms()
            second = self.ad.server_time_offset_ms()
        self.assertAlmostEqual(first, 2100.0, delta=300.0, msg="偏差应约等于服务器与本机的差")
        self.assertEqual(second, first, "TTL 内必须走缓存")
        self.assertEqual(counter["n"], 1, "缓存命中时不得再发请求（否则每单多一次往返）")

    def test_force_bypasses_cache(self):
        counter = {}
        with patch("r20_backend.exchanges.binance.urlopen", self._server(100.0, counter=counter)):
            self.ad.server_time_offset_ms()
            self.ad.server_time_offset_ms(force=True)
        self.assertEqual(counter["n"], 2, "force 必须真的重测（-1021 重试靠它）")

    def test_failure_is_fail_open_with_warning(self):
        """★ 校时不可用 ⇒ `0.0` + 告警，**绝不阻断交易**（刻意的 fail-open）。"""

        def _boom(req, timeout=None):
            raise RuntimeError("校时端点挂了")

        with patch("r20_backend.exchanges.binance.urlopen", _boom), \
                warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            offset = self.ad.server_time_offset_ms()
        self.assertEqual(offset, 0.0, "校时失败必须按本机时钟签名（0 偏差），不许抛")
        self.assertTrue(any(issubclass(w.category, RuntimeWarning) for w in caught),
                        "失败要留痕（-1021 风险仍在）")

    def test_large_offset_warns_about_recvwindow_headroom(self):
        with patch("r20_backend.exchanges.binance.urlopen", self._server(4000.0)), \
                warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.ad.server_time_offset_ms()
        messages = " ".join(str(w.message) for w in caught)
        self.assertIn("NTP", messages, f"偏差过大要提示宿主机启用 NTP：{messages}")
        self.assertIn("recvWindow", messages, "要点明余量被吃掉多少")


class SignedRequestTest(_Base):
    def _call(self, **over):
        kwargs = {"method": "GET", "path": "/fapi/v2/account", "params": {"a": "1"}}
        kwargs.update(over)
        return self.ad.signed_request(**kwargs)

    def test_get_is_signed_with_aligned_timestamp_and_api_key_header(self):
        seen = {}

        def _fake(req, timeout=None):
            seen["url"] = req.full_url
            seen["headers"] = {k.lower(): v for k, v in req.header_items()}
            seen["method"] = req.get_method()
            return _Resp({"ok": True})

        with patch.object(BinanceAdapter, "server_time_offset_ms", return_value=250.0), \
                patch("r20_backend.exchanges.binance.urlopen", _fake):
            out = self._call()
        self.assertEqual(out, {"ok": True})
        query = seen["url"].split("?", 1)[1]
        self.assertIn("timestamp=", query, "时间戳必须用**服务器校时**值")
        ts = int(re.search(r"timestamp=(\d+)", query).group(1))
        self.assertAlmostEqual(ts, _time.time() * 1000 + 250.0, delta=2000.0,
                               msg="时间戳应为本机时间 + 校时偏差")
        # ⚠️ 签名要按**去掉 signature 段的完整 query** 重算（漏掉 recvWindow 会算错——
        #    我第一版就漏了，签名对不上自己写的期望值）
        unsigned = re.sub(r"&signature=[0-9a-f]+$", "", query)
        expected_sig = hmac.new(b"SECRET", unsigned.encode(), hashlib.sha256).hexdigest()
        self.assertIn(f"signature={expected_sig}", query, "HMAC-SHA256 签名要对得上")
        self.assertEqual(seen["headers"].get("x-mbx-apikey"), "KEY")
        self.assertEqual(seen["method"], "GET")
        self.assertIsNone(seen["headers"].get("content-type"),
                          "GET 无 body ⇒ 不得带 Content-Type")

    def test_post_with_body_sends_json_and_content_type(self):
        seen = {}

        def _fake(req, timeout=None):
            seen["data"] = req.data
            seen["headers"] = {k.lower(): v for k, v in req.header_items()}
            return _Resp({"orderId": 1})

        with patch.object(BinanceAdapter, "server_time_offset_ms", return_value=0.0), \
                patch("r20_backend.exchanges.binance.urlopen", _fake):
            self._call(method="POST", path="/fapi/v1/order", body={"symbol": "BTCUSDT"})
        self.assertEqual(json.loads(seen["data"].decode()), {"symbol": "BTCUSDT"})
        self.assertIn("application/json", seen["headers"].get("content-type", ""))

    def test_empty_success_body_returns_none(self):
        with patch.object(BinanceAdapter, "server_time_offset_ms", return_value=0.0), \
                patch("r20_backend.exchanges.binance.urlopen",
                      lambda req, timeout=None: _Resp(b"")):
            self.assertIsNone(self._call())

    def test_http_error_carries_official_code_and_message(self):
        def _fake(req, timeout=None):
            raise _http_error(400, {"code": -2015, "msg": "Invalid API-key"})

        with patch.object(BinanceAdapter, "server_time_offset_ms", return_value=0.0), \
                patch("r20_backend.exchanges.binance.urlopen", _fake):
            with self.assertRaises(BinanceAPIError) as ctx:
                self._call()
        self.assertEqual(ctx.exception.code, -2015)
        self.assertEqual(ctx.exception.message, "Invalid API-key")
        self.assertEqual(ctx.exception.status, 400)

    def test_http_error_with_non_json_body_falls_back_to_http_status(self):
        def _fake(req, timeout=None):
            raise _http_error(503, b"<html>gateway</html>")

        with patch.object(BinanceAdapter, "server_time_offset_ms", return_value=0.0), \
                patch("r20_backend.exchanges.binance.urlopen", _fake):
            with self.assertRaises(BinanceAPIError) as ctx:
                self._call()
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, 503)
        # ⚠️ **实测边界（如实钉住）**：非 JSON 错误体（如网关 HTML）**正文被丢弃** ——
        # `json.loads` 抛错后 `payload = {}`，而 `isinstance(payload, dict)` 为真
        # ⇒ 走 `payload.get("msg")` ⇒ None ⇒ 退回 "request failed"
        # （`raw[:200]` 那条分支只在「JSON 解析成功但不是 dict」时才可达）。
        # 影响：网关 5xx 的真实原因在现场丢失，只能靠 HTTP 状态码判断。列为待议项，未擅自改。
        self.assertEqual(ctx.exception.message, "request failed")

    def test_network_error_is_wrapped_not_leaked(self):
        def _fake(req, timeout=None):
            raise OSError("connection reset")

        with patch.object(BinanceAdapter, "server_time_offset_ms", return_value=0.0), \
                patch("r20_backend.exchanges.binance.urlopen", _fake):
            with self.assertRaises(BinanceAPIError) as ctx:
                self._call()
        self.assertEqual(ctx.exception.code, "network")
        self.assertIn("connection reset", ctx.exception.message)

    def test_minus_1021_retries_once_with_a_fresh_timestamp(self):
        """★ `-1021` ⇒ 重测校时 + **重新取时间戳与签名**后重试一次（不复述旧时间）。"""
        stamps = []
        state = {"remeasured": False, "n": 0}

        def _offset(*, force=False):
            state["n"] += 1
            if force:
                state["remeasured"] = True
            return 1500.0 if state["remeasured"] else 0.0

        def _fake(req, timeout=None):
            stamps.append(req.full_url)
            if len(stamps) == 1:
                raise _http_error(400, {"code": -1021, "msg": "Timestamp outside recvWindow"})
            return _Resp({"ok": True})

        with patch.object(BinanceAdapter, "server_time_offset_ms", side_effect=_offset), \
                patch("r20_backend.exchanges.binance.urlopen", _fake), \
                warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            out = self._call()
        self.assertEqual(out, {"ok": True}, "重试成功就该成功")
        first_ts = int(re.search(r"timestamp=(\d+)", stamps[0]).group(1))
        second_ts = int(re.search(r"timestamp=(\d+)", stamps[1]).group(1))
        self.assertGreater(second_ts - first_ts, 1000,
                           "重试必须用**重测后**的时间戳（这正是修 -1021 的手段）")
        self.assertTrue(state["remeasured"], "重试前必须 force 重测校时")
        self.assertTrue(any("-1021" in str(w.message) for w in caught), "重试要留痕")

    def test_repeated_minus_1021_raises_instead_of_looping(self):
        """两次都 `-1021` ⇒ 抛出（**不无限重试**）。"""

        def _fake(req, timeout=None):
            raise _http_error(400, {"code": -1021, "msg": "Timestamp outside recvWindow"})

        with patch.object(BinanceAdapter, "server_time_offset_ms", return_value=0.0), \
                patch("r20_backend.exchanges.binance.urlopen", _fake), \
                warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            with self.assertRaises(BinanceAPIError) as ctx:
                self._call()
        self.assertEqual(ctx.exception.code, -1021)


class CredentialsTest(_Base):
    def test_real_keys_reads_credentials_and_returns_the_pair(self):
        """走**真** `_keys`（setUp 把它整个打桩了 ⇒ 不打掉这里就永远测不到happy path）。"""
        self._keys.stop()
        with patch("r20_backend.exchanges.registry.venue_credentials",
                   return_value=("K2", "S2")):
            self.assertEqual(BinanceAdapter._keys(self.ad), ("K2", "S2"))

    def test_secret_alone_missing_also_raises(self):
        """只有 key、secret 为空 ⇒ 同样**拒签**（半份凭证等于没有）。"""
        from r20_backend.exchanges import ExchangeCapabilityError
        self._keys.stop()
        with patch("r20_backend.exchanges.registry.venue_credentials",
                   return_value=("K2", "")):
            with self.assertRaises(ExchangeCapabilityError):
                BinanceAdapter._keys(self.ad)

    def test_missing_credentials_raise_capability_error(self):
        """凭证未配置 ⇒ `ExchangeCapabilityError`（**不静默**、也不拿空串去签名）。"""
        from r20_backend.exchanges import ExchangeCapabilityError
        self._keys.stop()      # ⚠️ setUp 把 `_keys` 打桩了 ⇒ 不打掉的话真 `_keys` 永不执行
        with patch("r20_backend.exchanges.registry.venue_credentials", return_value=("", "")):
            with self.assertRaises(ExchangeCapabilityError) as ctx:
                BinanceAdapter._keys(self.ad)
        self.assertIn("凭证未配置", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

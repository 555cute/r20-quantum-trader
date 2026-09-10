"""US-007 环境维合约存在性对账（listing gate）纯单测。

零凭证、零出网：patch ``r20_backend.exchanges.listing.urlopen`` 模块绑定名，
所有网络面由 fake urlopen 响应；域名正确性通过捕获 Request.url 断言。
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from r20_backend.exchanges import listing as listing_mod
from r20_backend.exchanges.listing import ensure_contract_listed


def _resp(payload) -> object:
    body = json.dumps(payload).encode("utf-8")
    return io.BytesIO(body)  # context manager + read() 兼容


class _FakeNet:
    """捕获请求并按 (venue, environment) 回放目录响应。"""

    def __init__(self, payload_by_call=None):
        self.requests = []
        self.payload_by_call = payload_by_call or []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        idx = min(len(self.requests) - 1, len(self.payload_by_call) - 1)
        payload = self.payload_by_call[idx]
        if isinstance(payload, Exception):
            raise payload
        return _resp(payload)


def _reset():
    listing_mod._CACHE.clear()


OKX_LIVE = {"code": "0", "data": [
    {"instId": "BTC-USDT-SWAP", "state": "live"},
    {"instId": "SUI-USDT-SWAP", "state": "live"},
]}
OKX_DELISTED = {"code": "0", "data": [
    {"instId": "BTC-USDT-SWAP", "state": "live"},
    {"instId": "SUI-USDT-SWAP", "state": "suspend"},
]}
BINANCE_TRADING = {"symbols": [
    {"symbol": "BTCUSDT", "status": "TRADING"},
    {"symbol": "SUIUSDT", "status": "TRADING"},
]}
BINANCE_BREAK = {"symbols": [
    {"symbol": "BTCUSDT", "status": "TRADING"},
    {"symbol": "SUIUSDT", "status": "BREAK"},
]}
GATE_OK = [{"name": "BTC_USDT", "in_delisting": False},
           {"name": "SUI_USDT", "in_delisting": False}]
GATE_DELISTING = [{"name": "BTC_USDT", "in_delisting": False},
                  {"name": "SUI_USDT", "in_delisting": True}]


_RESOLVE_MAP = {
    ("okx", "live"): "https://www.okx.com",
    ("okx", "demo"): "https://www.okx.com",
    ("binance", "live"): "https://fapi.binance.com",
    ("binance", "demo"): "https://demo-fapi.binance.com",
    ("gate", "live"): "https://api.gateio.ws",
    ("gate", "sandbox"): "https://fx-api-testnet.gateio.ws",
}

class ListingGateTest(unittest.TestCase):
    def setUp(self):
        _reset()
        # 律②：patch 模块绑定名 + patcher.start/stop，杜绝直接赋值泄漏全进程
        self._p_urlopen = patch.object(listing_mod, "urlopen")
        self.mock_urlopen = self._p_urlopen.start()
        self.addCleanup(self._p_urlopen.stop)
        self._p_resolve = patch.object(
            listing_mod.env_profiles, "resolve_base_url",
            lambda venue, environment, probe_fn=None:
                _RESOLVE_MAP.get((venue, environment),
                                 f"https://{venue}-{environment}.invalid"))
        self._p_resolve.start()
        self.addCleanup(self._p_resolve.stop)

    def test_01_okx_live_pass(self):
        net = _FakeNet([OKX_LIVE])
        self.mock_urlopen.side_effect = net
        chk = ensure_contract_listed("okx", "live", "SUI-USDT-SWAP")
        self.assertTrue(chk.ok)
        self.assertIsNone(chk.reason)
        self.assertEqual(chk.source, "fresh")
        self.assertIn("www.okx.com", net.requests[0].full_url)
        self.assertNotIn("x-simulated-trading", net.requests[0].headers)

    def test_02_okx_delisted_reject(self):
        net = _FakeNet([OKX_DELISTED])
        self.mock_urlopen.side_effect = net
        chk = ensure_contract_listed("okx", "live", "SUI-USDT-SWAP")
        self.assertFalse(chk.ok)
        self.assertIn("state=suspend", chk.reason)

    def test_03_okx_demo_header_and_missing(self):
        net = _FakeNet([OKX_LIVE])
        self.mock_urlopen.side_effect = net
        chk = ensure_contract_listed("okx", "demo", "ETH-USDT-SWAP")
        self.assertFalse(chk.ok)
        self.assertIn("沙盒未上市", chk.reason)
        headers = {k.lower(): v for k, v in net.requests[0].headers.items()}
        self.assertEqual(headers.get("x-simulated-trading"), "1")
        # demo 与 live 同域（env_profiles 契约）
        self.assertIn("www.okx.com", net.requests[0].full_url)

    def test_04_binance_break_reject(self):
        net = _FakeNet([BINANCE_BREAK])
        self.mock_urlopen.side_effect = net
        chk = ensure_contract_listed("binance", "live", "SUIUSDT")
        self.assertFalse(chk.ok)
        self.assertIn("status=BREAK", chk.reason)

    def test_05_binance_domains_live_vs_demo(self):
        net = _FakeNet([BINANCE_TRADING, BINANCE_TRADING])
        self.mock_urlopen.side_effect = net
        self.assertTrue(ensure_contract_listed("binance", "live", "SUIUSDT").ok)
        self.assertTrue(ensure_contract_listed("binance", "demo", "SUIUSDT").ok)
        self.assertIn("https://fapi.binance.com", net.requests[0].full_url)
        self.assertIn("https://demo-fapi.binance.com", net.requests[1].full_url)

    def test_06_gate_in_delisting_reject(self):
        net = _FakeNet([GATE_DELISTING])
        self.mock_urlopen.side_effect = net
        chk = ensure_contract_listed("gate", "live", "SUI_USDT")
        self.assertFalse(chk.ok)
        self.assertIn("in_delisting=true", chk.reason)

    def test_07_gate_sandbox_domain_resolution(self):
        # resolve 由 setUp 的表钉死（sandbox → fx-api-testnet），urlopen 由 fake 接管
        net = _FakeNet([GATE_OK])
        self.mock_urlopen.side_effect = net
        self.assertTrue(ensure_contract_listed("gate", "sandbox", "BTC_USDT").ok)
        self.assertIn("fx-api-testnet.gateio.ws", net.requests[0].full_url)

    def test_08_ttl_cache_no_second_call(self):
        net = _FakeNet([OKX_LIVE])
        self.mock_urlopen.side_effect = net
        first = ensure_contract_listed("okx", "live", "SUI-USDT-SWAP")
        second = ensure_contract_listed("okx", "live", "BTC-USDT-SWAP")
        self.assertTrue(first.ok and second.ok)
        self.assertEqual(first.source, "fresh")
        self.assertEqual(second.source, "cache")
        self.assertEqual(len(net.requests), 1)  # TTL 内二次调用零出网

    def test_09_fetch_failure_fail_open(self):
        net = _FakeNet([TimeoutError("network down")])
        self.mock_urlopen.side_effect = net
        chk = ensure_contract_listed("okx", "live", "SUI-USDT-SWAP")
        self.assertTrue(chk.ok)  # fail-open：不阻塞交易
        self.assertEqual(chk.reason, "行情目录不可用，跳过对账")

    def test_10_ttl_expiry_refetches(self):
        net = _FakeNet([OKX_LIVE, OKX_DELISTED])
        self.mock_urlopen.side_effect = net
        self.assertTrue(ensure_contract_listed("okx", "live", "SUI-USDT-SWAP").ok)
        # 手动把缓存时间戳拨老，模拟 TTL 过期
        key = ("okx", "live")
        ts, directory = listing_mod._CACHE[key]
        listing_mod._CACHE[key] = (ts - listing_mod.TTL_SECONDS - 1, directory)
        chk = ensure_contract_listed("okx", "live", "SUI-USDT-SWAP")
        self.assertFalse(chk.ok)
        self.assertEqual(len(net.requests), 2)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(ListingGateTest)


if __name__ == "__main__":
    unittest.main(verbosity=2)

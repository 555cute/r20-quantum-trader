"""US-007 环境维合约存在性对账（listing gate）纯单测。

零凭证、零出网：patch ``r20_backend.exchanges.listing.urlopen`` 模块绑定名，
所有网络面由 fake urlopen 响应；域名正确性通过捕获 Request.url 断言。
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from r20_backend.exchanges import listing as listing_mod
from r20_backend.exchanges.listing import ensure_contract_listed, listing_snapshot


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


class ListingGateTest(unittest.TestCase):
    def setUp(self):
        super().setUp()
        _reset()
        # 封闭三律：本类曾用直接赋值替换模块属性（urlopen / env_profiles.resolve_base_url），
        # 泄漏进同进程后续测试（曾把 (okx,demo) 解析钉到 Gate 测试域）。统一在 setUp 记录
        # 原值、tearDown 恢复，测试体内的赋值只对当前用例生效。
        self._orig_urlopen = listing_mod.urlopen
        self._orig_resolve = listing_mod.env_profiles.resolve_base_url
        self.addCleanup(setattr, listing_mod, "urlopen", self._orig_urlopen)
        self.addCleanup(setattr, listing_mod.env_profiles,
                        "resolve_base_url", self._orig_resolve)

    def test_01_okx_live_pass(self):
        net = _FakeNet([OKX_LIVE])
        listing_mod.urlopen = net
        chk = ensure_contract_listed("okx", "live", "SUI-USDT-SWAP")
        self.assertTrue(chk.ok)
        self.assertIsNone(chk.reason)
        self.assertEqual(chk.source, "fresh")
        self.assertIn("www.okx.com", net.requests[0].full_url)
        self.assertNotIn("x-simulated-trading", net.requests[0].headers)

    def test_02_okx_delisted_reject(self):
        net = _FakeNet([OKX_DELISTED])
        listing_mod.urlopen = net
        chk = ensure_contract_listed("okx", "live", "SUI-USDT-SWAP")
        self.assertFalse(chk.ok)
        self.assertIn("state=suspend", chk.reason)

    def test_03_okx_demo_header_and_missing(self):
        net = _FakeNet([OKX_LIVE])
        listing_mod.urlopen = net
        chk = ensure_contract_listed("okx", "demo", "ETH-USDT-SWAP")
        self.assertFalse(chk.ok)
        self.assertIn("沙盒未上市", chk.reason)
        headers = {k.lower(): v for k, v in net.requests[0].headers.items()}
        self.assertEqual(headers.get("x-simulated-trading"), "1")
        # demo 与 live 同域（env_profiles 契约）
        self.assertIn("www.okx.com", net.requests[0].full_url)

    def test_04_binance_break_reject(self):
        net = _FakeNet([BINANCE_BREAK])
        listing_mod.urlopen = net
        chk = ensure_contract_listed("binance", "live", "SUIUSDT")
        self.assertFalse(chk.ok)
        self.assertIn("status=BREAK", chk.reason)

    def test_05_binance_domains_live_vs_demo(self):
        net = _FakeNet([BINANCE_TRADING, BINANCE_TRADING])
        listing_mod.urlopen = net
        self.assertTrue(ensure_contract_listed("binance", "live", "SUIUSDT").ok)
        self.assertTrue(ensure_contract_listed("binance", "demo", "SUIUSDT").ok)
        self.assertIn("https://fapi.binance.com", net.requests[0].full_url)
        self.assertIn("https://demo-fapi.binance.com", net.requests[1].full_url)

    def test_06_gate_in_delisting_reject(self):
        net = _FakeNet([GATE_DELISTING])
        listing_mod.urlopen = net
        chk = ensure_contract_listed("gate", "live", "SUI_USDT")
        self.assertFalse(chk.ok)
        self.assertIn("in_delisting=true", chk.reason)

    def test_07_gate_sandbox_domain_resolution(self):
        # patch resolve_base_url 钉死 testnet 域（避免真探测出网），urlopen 仍由 fake 接管
        listing_mod.env_profiles.resolve_base_url = (
            lambda v, e, probe_fn=None: "https://fx-api-testnet.gateio.ws")
        net = _FakeNet([GATE_OK])
        listing_mod.urlopen = net
        self.assertTrue(ensure_contract_listed("gate", "sandbox", "BTC_USDT").ok)
        self.assertIn("fx-api-testnet.gateio.ws", net.requests[0].full_url)

    def test_08_ttl_cache_no_second_call(self):
        net = _FakeNet([OKX_LIVE])
        listing_mod.urlopen = net
        first = ensure_contract_listed("okx", "live", "SUI-USDT-SWAP")
        second = ensure_contract_listed("okx", "live", "BTC-USDT-SWAP")
        self.assertTrue(first.ok and second.ok)
        self.assertEqual(first.source, "fresh")
        self.assertEqual(second.source, "cache")
        self.assertEqual(len(net.requests), 1)  # TTL 内二次调用零出网

    def test_09_fetch_failure_fail_open(self):
        net = _FakeNet([TimeoutError("network down")])
        listing_mod.urlopen = net
        chk = ensure_contract_listed("okx", "live", "SUI-USDT-SWAP")
        self.assertTrue(chk.ok)  # fail-open：不阻塞交易
        self.assertEqual(chk.reason, "行情目录不可用，跳过对账")

    def test_10_ttl_expiry_refetches(self):
        net = _FakeNet([OKX_LIVE, OKX_DELISTED])
        listing_mod.urlopen = net
        self.assertTrue(ensure_contract_listed("okx", "live", "SUI-USDT-SWAP").ok)
        # 手动把缓存时间戳拨老，模拟 TTL 过期
        key = ("okx", "live")
        ts, directory = listing_mod._CACHE[key]
        listing_mod._CACHE[key] = (ts - listing_mod.TTL_SECONDS - 1, directory)
        chk = ensure_contract_listed("okx", "live", "SUI-USDT-SWAP")
        self.assertFalse(chk.ok)
        self.assertEqual(len(net.requests), 2)

    def test_11_snapshot_counts_and_cache(self):
        net = _FakeNet([OKX_LIVE])
        listing_mod.urlopen = net
        snap = listing_snapshot("okx", "live")
        self.assertEqual(snap.listed_count, 2)
        self.assertEqual(snap.source, "fresh")
        # 同 TTL 内第二次：cache 且零新增出网
        snap2 = listing_snapshot("okx", "live")
        self.assertEqual(snap2.source, "cache")
        self.assertEqual(len(net.requests), 1)

    def test_12_snapshot_fail_open_unavailable(self):
        net = _FakeNet([TimeoutError("network down")])
        listing_mod.urlopen = net
        snap = listing_snapshot("gate", "sandbox")
        self.assertTrue(snap.ok)  # fail-open：不阻塞交易
        self.assertIsNone(snap.listed_count)  # 未知≠0
        self.assertEqual(snap.source, "unavailable")
        self.assertEqual(snap.reason, "行情目录不可用，跳过对账")

    def test_13_snapshot_unknown_env_structural_error(self):
        snap = listing_snapshot("okx", "paper")
        self.assertFalse(snap.ok)  # 结构性错误必须显式暴露
        self.assertIn("未知环境档", snap.reason)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(ListingGateTest)


if __name__ == "__main__":
    unittest.main(verbosity=2)

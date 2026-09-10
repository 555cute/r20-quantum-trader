"""US-007 环境维合约存在性对账（listing gate）契约测试。

全 mock、零网络、零凭证、零配置写：
- patch ``r20_backend.exchanges.listing.urlopen`` 模块绑定名（HTTP 边界）；
- 时钟注入 ``listing._now``；缓存逐测清空；
- Gate 沙盒双域探测测试必须把 ``env_profiles.PROFILE_FILE`` 钉到临时目录
  （禁止写真实 data/venue_env_profile.json）。

真因案例钉扎：OKX demo 下架 SUI-USDT-SWAP（"Listing canceled for this crypto"）
必须在下单前被目录对账拦住。
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from r20_backend.exchanges import env_profiles
from r20_backend.exchanges import listing
from r20_backend.exchanges.listing import ListingCheck, ensure_contract_listed


class _Resp:
    """最小 urlopen 替身：with 上下文 + read()。"""
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _okx_dir(sui_state="suspend", btc_state="live"):
    return {"code": "0", "data": [
        {"instId": "SUI-USDT-SWAP", "state": sui_state},
        {"instId": "BTC-USDT-SWAP", "state": btc_state},
    ]}


def _binance_dir(sui_status="BREAK", btc_status="TRADING"):
    return {"symbols": [
        {"symbol": "SUIUSDT", "status": sui_status},
        {"symbol": "BTCUSDT", "status": btc_status},
    ]}


def _gate_dir(sui_delisting=True, btc_delisting=False):
    return [
        {"name": "SUI_USDT", "in_delisting": sui_delisting},
        {"name": "BTC_USDT", "in_delisting": btc_delisting},
    ]


class _Probe:
    """路由式假 urlopen：按 URL 前缀回包，记录全部请求。"""
    def __init__(self, routes):
        self.routes = routes          # list[(prefix, payload|Exception)]
        self.calls = []               # (url, headers)

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.calls.append((url, dict(getattr(req, "headers", {}) or {})))
        for prefix, payload in self.routes:
            if url.startswith(prefix):
                if isinstance(payload, Exception):
                    raise payload
                return _Resp(payload)
        raise AssertionError(f"未声明的 URL：{url}")


class ListingGateTest(unittest.TestCase):
    def setUp(self):
        listing.reset_cache_for_testing()

    # --- OKX：真因案例（demo 下架 SUI） ---
    def test_okx_live_contract_passes(self):
        probe = _Probe([("https://www.okx.com", _okx_dir())])
        with patch.object(listing, "urlopen", probe), \
             patch.object(listing, "_now", lambda: 1000.0):
            chk = ensure_contract_listed("okx", "live", "BTC-USDT-SWAP")
        self.assertIsInstance(chk, ListingCheck)
        self.assertTrue(chk.ok)
        self.assertIsNone(chk.reason)
        self.assertEqual(chk.source, "fresh")
        self.assertEqual(chk.checked_at, 1000.0)

    def test_okx_delisted_contract_rejected(self):
        probe = _Probe([("https://www.okx.com", _okx_dir())])
        with patch.object(listing, "urlopen", probe):
            chk = ensure_contract_listed("okx", "live", "SUI-USDT-SWAP")
        self.assertFalse(chk.ok)
        self.assertIn("非交易中", chk.reason or "")

    def test_okx_missing_contract_rejected(self):
        probe = _Probe([("https://www.okx.com", _okx_dir())])
        with patch.object(listing, "urlopen", probe):
            chk = ensure_contract_listed("okx", "live", "NOPE-USDT-SWAP")
        self.assertFalse(chk.ok)
        self.assertIn("未在目录", chk.reason or "")

    def test_okx_demo_sends_simulated_header(self):
        probe = _Probe([("https://www.okx.com", _okx_dir())])
        with patch.object(listing, "urlopen", probe):
            ensure_contract_listed("okx", "demo", "BTC-USDT-SWAP")
        url, headers = probe.calls[0]
        self.assertTrue(url.startswith("https://www.okx.com/api/v5/public/instruments"))
        got = {k.lower(): v for k, v in headers.items()}
        self.assertEqual(got.get("x-simulated-trading"), "1")

    def test_okx_suspend_state_reason_carries_state(self):
        probe = _Probe([("https://www.okx.com", _okx_dir(sui_state="suspend"))])
        with patch.object(listing, "urlopen", probe):
            chk = ensure_contract_listed("okx", "live", "SUI-USDT-SWAP")
        self.assertFalse(chk.ok)
        self.assertIn("suspend", chk.reason or "")

    # --- Binance：域按环境切换 ---
    def test_binance_demo_domain_and_break_rejected(self):
        probe = _Probe([("https://demo-fapi.binance.com", _binance_dir())])
        with patch.object(listing, "urlopen", probe):
            chk = ensure_contract_listed("binance", "demo", "SUIUSDT")
        url, _ = probe.calls[0]
        self.assertTrue(url.startswith("https://demo-fapi.binance.com/fapi/v1/exchangeInfo"))
        self.assertFalse(chk.ok)
        self.assertIn("BREAK", chk.reason or "")

    def test_binance_live_domain_trading_passes(self):
        probe = _Probe([("https://fapi.binance.com", _binance_dir())])
        with patch.object(listing, "urlopen", probe):
            chk = ensure_contract_listed("binance", "live", "BTCUSDT")
        url, _ = probe.calls[0]
        self.assertTrue(url.startswith("https://fapi.binance.com/fapi/v1/exchangeInfo"))
        self.assertTrue(chk.ok)

    # --- Gate：in_delisting + 沙盒双域择优 ---
    def test_gate_in_delisting_rejected(self):
        tmp = tempfile.mkdtemp()
        with patch.object(env_profiles, "PROFILE_FILE", Path(tmp) / "p.json"), \
             patch.object(env_profiles, "_probe_candidate", lambda url, timeout=None: 10), \
             patch.object(listing, "urlopen", _Probe([("https://", _gate_dir())])):
            chk = ensure_contract_listed("gate", "sandbox", "SUI_USDT")
        self.assertFalse(chk.ok)
        self.assertIn("下架", chk.reason or "")

    def test_gate_live_contract_passes(self):
        tmp = tempfile.mkdtemp()
        with patch.object(env_profiles, "PROFILE_FILE", Path(tmp) / "p.json"), \
             patch.object(env_profiles, "_probe_candidate", lambda url, timeout=None: 10), \
             patch.object(listing, "urlopen", _Probe([("https://", _gate_dir(sui_delisting=False))])):
            chk = ensure_contract_listed("gate", "live", "BTC_USDT")
        self.assertTrue(chk.ok)
        self.assertIsNone(chk.reason)

    # --- 缓存与 fail-open ---
    def test_ttl_cache_hits_without_second_fetch(self):
        probe = _Probe([("https://www.okx.com", _okx_dir())])
        with patch.object(listing, "urlopen", probe), \
             patch.object(listing, "_now", lambda: 1000.0):
            first = ensure_contract_listed("okx", "live", "BTC-USDT-SWAP")
            second = ensure_contract_listed("okx", "live", "BTC-USDT-SWAP")
        self.assertEqual(first.source, "fresh")
        self.assertEqual(second.source, "cache")
        self.assertEqual(len(probe.calls), 1)          # TTL 内零重复出网

    def test_ttl_expiry_refetches(self):
        probe = _Probe([("https://www.okx.com", _okx_dir())])
        clock = {"t": 1000.0}
        with patch.object(listing, "urlopen", probe), \
             patch.object(listing, "_now", lambda: clock["t"]):
            ensure_contract_listed("okx", "live", "BTC-USDT-SWAP")
            clock["t"] += listing.LISTING_TTL_SECONDS + 1
            again = ensure_contract_listed("okx", "live", "BTC-USDT-SWAP")
        self.assertEqual(again.source, "fresh")
        self.assertEqual(len(probe.calls), 2)

    def test_fetch_failure_fails_open(self):
        probe = _Probe([("https://www.okx.com", RuntimeError("network down"))])
        with patch.object(listing, "urlopen", probe):
            chk = ensure_contract_listed("okx", "live", "BTC-USDT-SWAP")
        self.assertTrue(chk.ok)                        # fail-open：对账不冒充风控
        self.assertIn("行情目录不可用", chk.reason or "")
        self.assertEqual(chk.source, "skip-unavailable")

    def test_unknown_venue_skips(self):
        chk = ensure_contract_listed("bybit", "live", "BTCUSDT")
        self.assertTrue(chk.ok)
        self.assertIn("跳过对账", chk.reason or "")
        self.assertEqual(chk.source, "skip-unavailable")


if __name__ == "__main__":
    unittest.main()

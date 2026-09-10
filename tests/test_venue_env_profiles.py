"""US-001 契约测试：环境 profile + Gate 沙盒双域择优 + 钉死禁回退。

封闭三律：全部 mock/注入，零真实网络、零真实文件（持久化钉到临时目录）。
事实锚点 = plan_local/THREE_VENUE_API_FRESHNESS_AUDIT_20260910.md §2。
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from r20_backend.exchanges import env_profiles as ep
from r20_backend.exchanges.base import ExchangeCapabilityError
from r20_backend.exchanges.binance import BinanceAdapter
from r20_backend.exchanges.gate import GateAdapter
from r20_backend.exchanges.okx import OKXPublicAdapter
from r20_backend.exchanges import registry as reg


_ENV_GUARD = None
_PROFILE_GUARD = None


def setUpModule():
    """封闭三律：清掉宿主 .env 注入的 R20_* 旗标（config.load_dotenv 在 import 时
    写入 os.environ），并把探测持久化文件钉到 tmp——本模块测试不得读写真实 data/。"""
    global _ENV_GUARD, _PROFILE_GUARD
    import os as _os
    ambient = {k: "0" for k in _os.environ
               if k.startswith(("R20_BINANCE_TESTNET", "R20_GATE_TESTNET",
                                "R20_OKX_ENV", "R20_OKX_TESTNET"))}
    _ENV_GUARD = patch.dict(_os.environ, ambient, clear=False)
    _ENV_GUARD.start()
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    _PROFILE_GUARD = patch.object(ep, "PROFILE_FILE", Path(tmp.name))
    _PROFILE_GUARD.start()


def tearDownModule():
    global _ENV_GUARD, _PROFILE_GUARD
    if _PROFILE_GUARD is not None:
        _PROFILE_GUARD.stop()
    if _ENV_GUARD is not None:
        _ENV_GUARD.stop()


class ProfileTableTest(unittest.TestCase):
    """审计 §2 事实钉：三域并存、Gate 双候选、OKX 同域头开关。"""

    def test_binance_three_domains_coexist(self):
        self.assertEqual(ep.get_profile("binance", "live").urls, ("https://fapi.binance.com",))
        self.assertEqual(ep.get_profile("binance", "demo").urls, ("https://demo-fapi.binance.com",))
        self.assertEqual(ep.get_profile("binance", "testnet").urls,
                         ("https://testnet.binancefuture.com",))
        # 旧测试域不得被从 profile 中删除（审计：并存，非全球废弃）

    def test_gate_sandbox_keeps_both_candidates(self):
        prof = ep.get_profile("gate", "sandbox")
        self.assertTrue(prof.needs_probe)
        self.assertIn("https://fx-api-testnet.gateio.ws", prof.urls)  # 官方 SDK 仍列
        self.assertIn("https://api-testnet.gateapi.io", prof.urls)    # 实测成功新域
        self.assertNotIn("https://api.gateio.ws", prof.urls)          # 沙盒候选绝不含 live

    def test_okx_demo_same_domain_simulated_bit(self):
        live = ep.get_profile("okx", "live")
        demo = ep.get_profile("okx", "demo")
        self.assertEqual(live.urls, demo.urls)             # 同域
        self.assertTrue(demo.simulated_trading)            # 头开关位（结构表达）
        self.assertFalse(live.simulated_trading)

    def test_unknown_environment_fail_closed(self):
        with self.assertRaises(ExchangeCapabilityError):
            ep.get_profile("gate", "mainnet")


class LegacyFlagCompatTest(unittest.TestCase):
    """R20_{VENUE}_TESTNET 旧布尔开关 → 档位兼容映射，现有调用点语义不破坏。"""

    def test_binance_flag_maps_to_same_url_as_legacy(self):
        with patch.dict("os.environ", {"R20_BINANCE_TESTNET": "1"}):
            self.assertEqual(BinanceAdapter().base_url, "https://demo-fapi.binance.com")
        with patch.dict("os.environ", {"R20_BINANCE_TESTNET": "0"}):
            self.assertEqual(BinanceAdapter().base_url, "https://fapi.binance.com")

    def test_default_is_live_without_flag(self):
        import os
        env = {k: v for k, v in os.environ.items()
               if "TESTNET" not in k.upper()}
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(GateAdapter().base_url, "https://api.gateio.ws")
            self.assertEqual(GateAdapter().environment, "live")
            self.assertEqual(OKXPublicAdapter().base_url, "https://www.okx.com")

    def test_okx_flag_has_no_effect_structurally(self):
        # OKX 从未声明沙盒适配器档：flag 开也维持 live（与旧实现一致）
        with patch.dict("os.environ", {"R20_OKX_TESTNET": "1"}):
            ad = OKXPublicAdapter()
        self.assertEqual(ad.base_url, "https://www.okx.com")
        self.assertEqual(ep.legacy_environment_for("okx"), "live")

    def test_explicit_environment_wins_over_flag(self):
        with patch.dict("os.environ", {"R20_BINANCE_TESTNET": "1"}):
            ad = BinanceAdapter(environment="live")
        self.assertEqual(ad.base_url, "https://fapi.binance.com")
        self.assertEqual(ad.environment, "live")


class GateSandboxProbeTest(unittest.TestCase):
    """探测择优 / 持久化钉死 / 全失败不选（全注入，零网络）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.file = Path(self._tmp.name) / "venue_env_profile.json"
        self.pf = patch.object(ep, "PROFILE_FILE", self.file)
        self.pf.start()

    def test_probe_picks_lowest_latency_and_persists(self):
        lat = {"https://fx-api-testnet.gateio.ws": 900,
               "https://api-testnet.gateapi.io": 120}
        chosen = ep.resolve_base_url("gate", "sandbox", probe_fn=lambda u: lat.get(u))
        self.assertEqual(chosen, "https://api-testnet.gateapi.io")
        doc = json.loads(self.file.read_text(encoding="utf-8"))
        rec = doc["gate"]["sandbox"]
        self.assertEqual(rec["base_url"], chosen)
        self.assertEqual(rec["evidence_ms"], lat)   # 两候选逐域延迟证据（择优可审计）

    def test_pinned_selection_reused_without_reprobe(self):
        good = "https://fx-api-testnet.gateio.ws"
        ep.resolve_base_url("gate", "sandbox",
                            probe_fn=lambda u: 50 if u == good else None)
        def _boom(_u):
            raise AssertionError("钉死后不得再探测")
        again = ep.resolve_base_url("gate", "sandbox", probe_fn=_boom)
        self.assertEqual(again, good)

    def test_all_candidates_fail_no_live_fallback(self):
        with self.assertRaises(ExchangeCapabilityError) as cm:
            ep.resolve_base_url("gate", "sandbox", probe_fn=lambda u: None)
        msg = str(cm.exception)
        self.assertIn("fail-closed", msg)
        self.assertIn("禁止回退 live", msg)
        self.assertFalse(self.file.exists())          # 未选出不落盘
        # live 档完全不受影响
        self.assertEqual(ep.resolve_base_url("gate", "live"), "https://api.gateio.ws")

    def test_stale_pin_outside_candidates_is_invalid(self):
        self.file.write_text(json.dumps(
            {"gate": {"sandbox": {"base_url": "https://evil.example"}}}),
            encoding="utf-8")
        self.assertIsNone(ep.pinned_base_url("gate", "sandbox"))
        chosen = ep.resolve_base_url("gate", "sandbox", probe_fn=lambda u: 10)
        self.assertIn(chosen, ep.get_profile("gate", "sandbox").urls)

    def test_probe_path_is_public_noauth(self):
        # 探测仅公共合约表端点：路径含 contracts、GET、无 Key 头（签名头出现在私有面）
        self.assertTrue(ep.PROBE_PATH.endswith("/contracts"))
        self.assertNotIn("key", ep.PROBE_PATH.lower())


class SignedTrafficPinningTest(unittest.TestCase):
    """铁律 1 的真实流量钉：签名请求只发持久化域；500 不跨域重试不回退 live。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.file = Path(self._tmp.name) / "venue_env_profile.json"
        p1 = patch.object(ep, "PROFILE_FILE", self.file)
        p1.start()
        self.addCleanup(p1.stop)
        pin = "https://api-testnet.gateapi.io"
        ep._persist("gate", "sandbox", pin, {pin: 80, "https://fx-api-testnet.gateio.ws": None})
        self.ad = GateAdapter(environment="sandbox")
        self.assertEqual(self.ad.base_url, pin)

    def test_signed_500_never_retries_other_domain(self):
        seen = []

        def fake_urlopen(req, timeout=None):
            seen.append(req.full_url)
            raise HTTPError(req.full_url, 500, "boom", {},
                            io.BytesIO(b'{"label":"SERVER_ERROR","message":"x"}'))

        with patch("r20_backend.exchanges.gate.urlopen", fake_urlopen), \
             patch.object(self.ad, "_keys", return_value=("k", "s")):
            with self.assertRaises(Exception) as cm:
                self.ad.account_snapshot()
            self.assertIn("SERVER_ERROR", str(cm.exception))
        self.assertEqual(len(seen), 1)                       # 恰一次尝试
        self.assertTrue(seen[0].startswith("https://api-testnet.gateapi.io"))
        self.assertFalse(any("gateio.ws" in u or "api.gateio" in u for u in seen))  # 未碰旧域/live


class RegistryEnvKeyingTest(unittest.TestCase):
    """registry：(venue, environment) 分键缓存，默认档与显式档并存不互串。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.file = Path(self._tmp.name) / "venue_env_profile.json"
        self.p1 = patch.object(ep, "PROFILE_FILE", self.file)
        self.p1.start()
        self.addCleanup(self.p1.stop)
        reg.clear_instances()
        self.addCleanup(reg.clear_instances)

    def test_gate_live_and_sandbox_separated(self):
        pin = "https://api-testnet.gateapi.io"
        ep._persist("gate", "sandbox", pin, {pin: 42, "https://fx-api-testnet.gateio.ws": None})
        with patch.object(ep, "_probe_candidate",
                          lambda u: self.fail("已钉死域不得再探测")):
            live_ad = reg.get_adapter("gate")                   # 默认 live
            sb_ad = reg.get_adapter("gate", "sandbox")          # 显式沙盒
        self.assertEqual(live_ad.base_url, "https://api.gateio.ws")
        self.assertEqual(sb_ad.base_url, pin)
        self.assertIs(live_ad, reg.get_adapter("gate"))         # 缓存命中
        self.assertIsNot(live_ad, sb_ad)

    def test_legacy_flag_via_registry(self):
        pin = "https://fx-api-testnet.gateio.ws"
        ep._persist("gate", "sandbox", pin, {pin: 10, "https://api-testnet.gateapi.io": None})
        with patch.dict("os.environ", {"R20_GATE_TESTNET": "1"}), \
             patch.object(ep, "_probe_candidate", lambda u: self.fail("钉死后不得再探测")):
            ad = reg.get_adapter("gate")
        self.assertEqual(ad.base_url, pin)
        self.assertEqual(ad.environment, "sandbox")
        with patch.dict("os.environ", {"R20_GATE_TESTNET": "1"}):
            self.assertEqual(reg.adapter_environment("gate"), "sandbox")

    def test_import_time_zero_side_effects(self):
        # env_profiles 导入与 profile 表构建不触文件不触网（PROFILE_FILE 不存在亦无碍）
        self.assertFalse(self.file.exists())
        ep.resolve_base_url("binance", "live")
        ep.resolve_base_url("okx", "demo")
        self.assertFalse(self.file.exists())    # 单域档不写持久化


if __name__ == "__main__":
    unittest.main()

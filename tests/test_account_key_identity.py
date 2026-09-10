"""US-002 契约测试：AccountKey 三元缓存键 + 双轴执行门禁 + routing_policy 轴拆分。

封闭三律：全 mock/注入，零真实网络、零真实凭证库、零生产文件。
- 凭证：patch registry.venue_credentials（_account_key 走模块全局，可钉）；
- 沙盒实例化：patch env_profiles.PROFILE_FILE 到临时目录并预写钉死域
  （否则 Gate sandbox 双候选会触发探测——测试绝不允许出网）；
- 密钥库：patch r20_gateway.secrets.load_secrets。
事实锚点 = plan_local/THREE_VENUE_API_FRESHNESS_AUDIT_20260910.md
（「Gate demo + enabled 是真实发送模拟盘订单，绝不能标成 LIVE 实盘」）。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r20_backend.exchanges import env_profiles as ep
from r20_backend.exchanges import registry as reg
from r20_backend.exchanges import routing_policy as rp
from r20_backend.exchanges.identity import (ANON_CREDENTIAL, AccountKey,
                                            credential_fingerprint,
                                            is_sandbox_environment)

GATE_SANDBOX_PIN = "https://api-testnet.gateapi.io"   # 候选域之一（钉死后零探测）


def setUpModule():
    """封闭三律（同 fa417ee / test_gate_lab）：排除宿主 .env 注入的 ambient
    R20_* 旗标——R20_GATE_TESTNET=1 会把「单参 = live 档」契约用例的环境解析
    到 sandbox 档（URL/开闸文案全部错档）。环境只由用例自设旗标决定。"""
    _backup = {k: v for k, v in os.environ.items()
               if k.startswith(("R20_BINANCE_TESTNET", "R20_GATE_TESTNET",
                                "R20_GATE_EXECUTION", "R20_GATE_DEMO_EXECUTION",
                                "R20_OKX_ENV", "R20_OKX_TESTNET"))}
    for k in _backup:
        os.environ.pop(k, None)
    _AMBIENT_BACKUP.append(_backup)


_AMBIENT_BACKUP: list = []


def tearDownModule():
    while _AMBIENT_BACKUP:
        os.environ.update(_AMBIENT_BACKUP.pop())


def _fp(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class _IsolatedRegistryMixin(unittest.TestCase):
    """每个用例：AccountKey 缓存清空 + profile 钉文件指临时目录（预钉 gate 沙盒域）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pin_file = Path(self._tmp.name) / "venue_env_profile.json"
        self.pin_file.write_text(json.dumps(
            {"gate": {"sandbox": {"base_url": GATE_SANDBOX_PIN,
                                  "probed_at": "2026-09-10T00:00:00Z",
                                  "evidence_ms": {GATE_SANDBOX_PIN: 42}}}}),
            encoding="utf-8")
        self.enterContext(patch.object(ep, "PROFILE_FILE", self.pin_file))
        reg.clear_instances()
        self.addCleanup(reg.clear_instances)


# ----------------------------------------------------------------------
# fingerprint 纯函数与键模型
# ----------------------------------------------------------------------
class CredentialFingerprintTest(unittest.TestCase):
    def test_sha256_prefix12(self):
        self.assertEqual(credential_fingerprint("KEY123"), _fp("KEY123"))
        self.assertEqual(len(credential_fingerprint("anything")), 12)

    def test_anon_sentinel_never_impersonates_account(self):
        self.assertEqual(credential_fingerprint(""), _fp(ANON_CREDENTIAL))
        self.assertEqual(credential_fingerprint(None), _fp(ANON_CREDENTIAL))
        self.assertEqual(credential_fingerprint("   "), _fp(ANON_CREDENTIAL))

    def test_distinct_keys_distinct_fingerprints(self):
        self.assertNotEqual(credential_fingerprint("KEY-A"),
                            credential_fingerprint("KEY-B"))

    def test_account_key_frozen_hashable_dict_key(self):
        k1 = AccountKey("gate", "live", _fp("KEY-A"))
        k2 = AccountKey("gate", "live", _fp("KEY-A"))
        self.assertEqual(k1, k2)
        self.assertEqual(hash(k1), hash(k2))
        bag = {k1: "v"}
        self.assertEqual(bag[k2], "v")
        with self.assertRaises(Exception):
            k1.venue = "okx"          # frozen：不可变
        self.assertIn("gate:live:", str(k1))

    def test_sandbox_environment_vocabulary(self):
        for e in ("sandbox", "demo", "testnet", "DEMO", " Sandbox "):
            self.assertTrue(is_sandbox_environment(e), e)
        for e in ("live", "", None, "mainnet"):
            self.assertFalse(is_sandbox_environment(e), e)


# ----------------------------------------------------------------------
# AccountKey 缓存隔离 / 轮换重建 / 兼容签名
# ----------------------------------------------------------------------
class AccountKeyCacheTest(_IsolatedRegistryMixin):
    def test_environment_axis_isolation_base_url_and_session(self):
        live = reg.get_adapter("binance", "live")
        demo = reg.get_adapter("binance", "demo")
        self.assertIsNot(live, demo)
        self.assertEqual(live.base_url, "https://fapi.binance.com")
        self.assertEqual(demo.base_url, "https://demo-fapi.binance.com")
        self.assertEqual(live.environment, "live")
        self.assertEqual(demo.environment, "demo")
        # session/规格缓存互不串台
        live._session = "SESSION-LIVE"
        demo._session = "SESSION-DEMO"
        live._specs_cache["BTCUSDT"] = "X"
        self.assertEqual(demo._session, "SESSION-DEMO")
        self.assertEqual(demo._specs_cache, {})

    def test_same_account_key_returns_cached_singleton(self):
        a = reg.get_adapter("okx", "live")
        self.assertIs(reg.get_adapter("okx", "live"), a)
        self.assertIs(reg.get_adapter(" OKX ", "LIVE"), a)   # 归一化同键

    def test_credential_rotation_rebuilds_instance(self):
        with patch.object(reg, "venue_credentials",
                          return_value=("KEY-A", "sec")) as vc:
            first = reg.get_adapter("gate", "live")
            self.assertIs(reg.get_adapter("gate", "live"), first)  # 同键命中缓存
            self.assertEqual(vc.call_count, 2)
            vc.return_value = ("KEY-B", "sec")                     # 密钥轮换
            second = reg.get_adapter("gate", "live")
            self.assertIsNot(second, first)                        # 新代际新实例
            self.assertIs(reg.get_adapter("gate", "live"), second) # 新键命中
        # 缓存键真的含指纹：三键并存
        keys = {str(k) for k in reg._INSTANCES}
        self.assertTrue(any(k.startswith("gate:live:") for k in keys), keys)
        self.assertEqual(len([k for k in keys if k.startswith("gate:live:")]), 2)

    def test_same_key_different_environment_never_collides(self):
        with patch.object(reg, "venue_credentials", return_value=("GATE-K", "s")):
            live = reg.get_adapter("gate", "live")
            sandbox = reg.get_adapter("gate", "sandbox")
        self.assertIsNot(live, sandbox)
        self.assertEqual(live.base_url, "https://api.gateio.ws")
        self.assertEqual(sandbox.base_url, GATE_SANDBOX_PIN)  # 钉死域，零探测出网

    def test_secrets_failure_failsoft_anon_fingerprint(self):
        def boom(_venue):
            raise RuntimeError("密钥库不可读")
        with patch.object(reg, "venue_credentials", boom):
            ad = reg.get_adapter("gate", "live")
        key = AccountKey("gate", "live", _fp(ANON_CREDENTIAL))
        self.assertIn(key, reg._INSTANCES)   # 降级 anon 哨兵，不冒充账户
        self.assertIsNotNone(ad)

    def test_clear_instances_drops_all_generations(self):
        with patch.object(reg, "venue_credentials", return_value=("K1", "s")):
            a = reg.get_adapter("binance", "live")
            b = reg.get_adapter("gate", "live")
        reg.clear_instances()
        self.assertEqual(reg._INSTANCES, {})
        with patch.object(reg, "venue_credentials", return_value=("K1", "s")):
            self.assertIsNot(reg.get_adapter("binance", "live"), a)  # 重建
            self.assertIsNot(reg.get_adapter("gate", "live"), b)

    def test_legacy_positional_calls_zero_signature_break(self):
        # 现有调用点全部 venue 单参（execution_router/lab/market_data/app…）
        live = reg.get_adapter("gate")                 # 位置参数
        self.assertEqual(live.base_url, "https://api.gateio.ws")
        with patch.dict("os.environ", {"R20_BINANCE_TESTNET": "1"}):
            bn = reg.get_adapter("binance")            # 旧布尔 → demo 档
            self.assertEqual(bn.base_url, "https://demo-fapi.binance.com")
        with patch.dict("os.environ", {"R20_GATE_TESTNET": "1"}):
            gt = reg.get_adapter("gate")               # 旧布尔 → sandbox 钉死域
            self.assertEqual(gt.base_url, GATE_SANDBOX_PIN)
            self.assertEqual(reg.adapter_environment("gate"), "sandbox")
        self.assertEqual(reg.resolve_symbol("BTC-USDT-SWAP", "gate"), "BTC_USDT")

    def test_clear_instances_hot_switch_semantics_preserved(self):
        # app.py PUT /admin/multi-exchange 保存后 clear_instances 热切换。
        # 三元键下「环境轴或凭证代际变化」天然生成新键新实例；clear 仍承担
        # 全量作废（含同键但外部 URL 钉死变化等场景）——旧语义保留不放松。
        with patch.dict("os.environ", {"R20_BINANCE_TESTNET": "0"}):
            before = reg.get_adapter("binance")
            self.assertIs(reg.get_adapter("binance"), before)   # 同键必命中缓存
        with patch.dict("os.environ", {"R20_BINANCE_TESTNET": "1"}):
            hot = reg.get_adapter("binance")                    # 轴变→键变→新实例
            self.assertIsNot(hot, before)
            self.assertEqual(hot.base_url, "https://demo-fapi.binance.com")
            reg.clear_instances()
            after = reg.get_adapter("binance")                  # 清后重建同档
            self.assertIsNot(after, hot)
            self.assertEqual(after.base_url, "https://demo-fapi.binance.com")


# ----------------------------------------------------------------------
# execution_open / require_execution 双轴门禁矩阵
# ----------------------------------------------------------------------
class ExecutionGateMatrixTest(_IsolatedRegistryMixin):
    ENV_KEYS = ("R20_GATE_EXECUTION", "R20_GATE_DEMO_EXECUTION", "R20_GATE_TESTNET")

    def _flag(self, live=None, demo=None):
        env = {}
        if live is not None:
            env["R20_GATE_EXECUTION"] = live
        if demo is not None:
            env["R20_GATE_DEMO_EXECUTION"] = demo
        return patch.dict("os.environ", env, clear=False)

    def test_default_both_axes_closed(self):
        with self._flag("0", "0"):
            self.assertFalse(reg.execution_open("gate"))            # 单参=live
            self.assertFalse(reg.execution_open("gate", "live"))
            self.assertFalse(reg.execution_open("gate", "sandbox"))
            self.assertFalse(reg.execution_open("gate", "demo"))

    def test_live_flag_does_not_open_sandbox(self):
        with self._flag("1", "0"):
            self.assertTrue(reg.execution_open("gate", "live"))
            self.assertFalse(reg.execution_open("gate", "sandbox"))  # 互不越权

    def test_sandbox_flag_does_not_open_live(self):
        with self._flag("0", "1"):
            self.assertTrue(reg.execution_open("gate", "demo"))
            self.assertTrue(reg.execution_open("gate", "testnet"))
            self.assertTrue(reg.execution_open("gate", "SANDBOX"))   # 归一化
            self.assertFalse(reg.execution_open("gate"))             # live 仍关

    def test_demo_flag_default_zero_when_unset(self):
        # AC：R20_GATE_DEMO_EXECUTION 默认 0——变量完全未设置亦视为关
        import os
        saved = {k: os.environ.pop(k, None) for k in
                 ("R20_GATE_EXECUTION", "R20_GATE_DEMO_EXECUTION")}
        try:
            self.assertFalse(reg.execution_open("gate"))            # live 默认关
            self.assertFalse(reg.execution_open("gate", "demo"))    # 沙盒默认关
            self.assertFalse(reg.execution_open("gate", "testnet"))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_other_venues_static_table_both_axes(self):
        with self._flag("1", "1"):
            self.assertFalse(reg.execution_open("binance", "live"))
            self.assertFalse(reg.execution_open("binance", "demo"))
            self.assertFalse(reg.execution_open("okx", "live"))

    def test_require_execution_reject_message_points_right_switch(self):
        with self._flag("0", "0"), self.assertRaises(reg.ExchangeCapabilityError) as cm:
            reg.require_execution("gate")                 # live 档
        self.assertIn("R20_GATE_EXECUTION=1", str(cm.exception))
        self.assertNotIn("R20_GATE_DEMO_EXECUTION", str(cm.exception))
        with self._flag("0", "0"), self.assertRaises(reg.ExchangeCapabilityError) as cm:
            reg.require_execution("gate", "demo")         # 沙盒档
        msg = str(cm.exception)
        self.assertIn("R20_GATE_DEMO_EXECUTION", msg)     # 指路当前缺的闸
        self.assertIn("demo", msg)
        # live 开着、demo 缺 → 沙盒拒绝文案不得诱导去开 live 那把
        with self._flag("1", "0"), self.assertRaises(reg.ExchangeCapabilityError) as cm:
            reg.require_execution("gate", "sandbox")
        self.assertIn("R20_GATE_DEMO_EXECUTION", str(cm.exception))


# ----------------------------------------------------------------------
# routing_policy：本地演算 × 资金环境两条轴
# ----------------------------------------------------------------------
class RoutingPolicyAxisTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.file = Path(self._tmp.name) / "venue_routing.json"
        self.enterContext(patch.object(rp, "ROUTING_FILE", self.file))
        self.enterContext(patch("r20_gateway.secrets.load_secrets",
                                return_value={"GATE_API_KEY": "K",
                                              "GATE_SECRET_KEY": "S"}))
        self.enterContext(patch.dict("os.environ",
                                     {"R20_GATE_EXECUTION": "0",
                                      "R20_GATE_DEMO_EXECUTION": "0",
                                      "R20_GATE_TESTNET": "0"}, clear=False))

    def _pool(self, dry_run=None, assets=("BTC",)):
        doc = {"gate": {"assets": list(assets)}}
        if dry_run is not None:
            doc["gate"]["dry_run"] = dry_run
        self.file.write_text(json.dumps(doc), encoding="utf-8")

    def test_no_fields_legacy_behavior_bit_identical(self):
        # 旧文件（无 environment/新字段）→ 默认与现状逐位一致
        pool = rp.load_gate_pool()
        self.assertEqual(pool, dict(rp.DEFAULT_GATE_POOL))
        self.assertEqual(rp.effective_mode(), "off")

    def test_axes_empty_pool_off(self):
        self._pool(dry_run=False)
        with patch.dict("os.environ", {"R20_GATE_EXECUTION": "1"}):
            self.assertEqual(rp.effective_mode(), "live")   # 有池有闸→live 轴
        self._pool(dry_run=False, assets=())
        self.assertEqual(rp.effective_mode(), "off")        # 无池一票否决

    def test_dry_run_axis_independent_of_environment(self):
        self._pool(dry_run=True)
        with patch.dict("os.environ", {"R20_GATE_EXECUTION": "1",
                                       "R20_GATE_TESTNET": "1",
                                       "R20_GATE_DEMO_EXECUTION": "1"}):
            self.assertEqual(rp.effective_mode(), "dry_run")  # 本地演算不涉所环境

    def test_gate_sandbox_enabled_reports_demo_not_live(self):
        # 审计字面：gate demo+enabled=真实发送模拟单，不得标 LIVE
        self._pool(dry_run=False)
        with patch.dict("os.environ", {"R20_GATE_TESTNET": "1",
                                       "R20_GATE_DEMO_EXECUTION": "1"}):
            self.assertEqual(rp.effective_mode(), "demo")
            self.assertEqual(rp.gate_execution_axis(), "demo")

    def test_sandbox_missing_demo_flag_failsafe_dry_run(self):
        self._pool(dry_run=False)
        with patch.dict("os.environ", {"R20_GATE_TESTNET": "1",
                                       "R20_GATE_DEMO_EXECUTION": "0",
                                       "R20_GATE_EXECUTION": "1"}):
            # live 闸不能越权放行沙盒轴；缺 demo 闸 → 强制 dry_run
            self.assertEqual(rp.effective_mode(), "dry_run")
            self.assertIs(rp.load_gate_pool()["dry_run"], True)

    def test_live_axis_default_unchanged(self):
        self._pool(dry_run=False)
        with patch.dict("os.environ", {"R20_GATE_EXECUTION": "1",
                                       "R20_GATE_DEMO_EXECUTION": "1"}):
            self.assertEqual(rp.effective_mode(), "live")     # 无 TESTNET=现状轴
        with patch.dict("os.environ", {"R20_GATE_EXECUTION": "0"}):
            self.assertEqual(rp.effective_mode(), "dry_run")  # 闸关 fail-safe

    def test_credentials_missing_forces_dry_run(self):
        self._pool(dry_run=False)
        with patch("r20_gateway.secrets.load_secrets", return_value={}), \
                patch.dict("os.environ", {"R20_GATE_EXECUTION": "1",
                                          "R20_GATE_TESTNET": "1",
                                          "R20_GATE_DEMO_EXECUTION": "1"}):
            self.assertEqual(rp.effective_mode(), "dry_run")  # 凭证缺失收敛

    def test_execution_open_consumed_with_axis(self):
        seen = []
        self._pool(dry_run=False)
        with patch.object(rp, "execution_open",
                          side_effect=lambda v, e="live": (seen.append((v, e)), True)[1]):
            rp.load_gate_pool()
        self.assertIn(("gate", "live"), seen)
        with patch.object(rp, "gate_environment_axis", return_value="demo"), \
                patch.object(rp, "execution_open",
                             side_effect=lambda v, e="live": (seen.append((v, e)), False)[1]):
            rp.load_gate_pool()
        self.assertIn(("gate", "demo"), seen)


if __name__ == "__main__":
    unittest.main()

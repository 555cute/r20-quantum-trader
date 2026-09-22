"""多所路由：**确认短语门禁**、健康度解析、账户面三态（第二百四十七刀）。

先打印目标行再动笔。三组语义：

| 组 | 语义 |
|---|---|
| ★ **写开关的确认短语** | `PUT /api/v1/admin/multi-exchange` 里，`gate_execution` / `binance_execution` 等开关**必须**配一句**精确**短语（`confirmation.strip().upper()` 全等比较）⇒ 差一个字符就 **400**，且 detail 里写明该用哪句 |
| **健康度落盘解析** | `venue_health.json` 读不动 ⇒ `health = {}`（不炸）；有 okx 段时**补齐** `testnet`（取 `okx_env.simulated`）与 `avg_ms`（缺失时现场 `diagnose_venue_connection` 兜一次，再失败就 `pass`）|
| ★ **账户面三态** | Gate 解析失败 ⇒ **`degraded`**；Binance 的 `ExchangeCapabilityError` ⇒ **`unavailable`**（能力缺失 ≠ 读失败）、其它异常 ⇒ `degraded`、解析失败 ⇒ `degraded` |

后两组的意义：状态字是**给运维看的**，把「所不支持」与「读失败」混成一个字，等于把
「不可判定」说成「安全」。
"""

import json
import tempfile
import types
import unittest
from unittest import mock

from fastapi import HTTPException

from r20_backend.routers import exchanges as R


def _auth_off(test):
    for name in ("require_admin_header", "require_superadmin"):
        patcher = mock.patch.object(R, name, mock.Mock(), create=True)
        patcher.start()
        test.addCleanup(patcher.stop)


class _Payload:
    """宽容载荷：处理器会读**很多**字段（实测：缺 `binance_api_key` 直接 AttributeError），
    这里对未列出的字段一律给 `None`，避免"我要知道每一个字段名"这种脆弱前提。"""

    def __init__(self, **kw):
        object.__setattr__(self, "_kw", kw)

    def __getattr__(self, name):
        return self._kw.get(name, None)


def _payload(**kw):
    base = {"okx_execution": None, "gate_execution": None, "binance_execution": None,
            "okx_testnet": None, "gate_testnet": None, "binance_testnet": None,
            "confirmation": ""}
    base.update(kw)
    return _Payload(**base)


class ConfirmationPhraseTest(unittest.TestCase):
    def setUp(self):
        _auth_off(self)   # 认证在函数体内做（实测：不关就是 401/403，不是 400）

    def _call(self, **kw):
        try:
            R.admin_multi_exchange_update(_payload(**kw))
        except HTTPException as exc:
            return exc
        return None

    def test_gate_execution_requires_the_exact_phrase(self):
        exc = self._call(gate_execution=True, confirmation="OPEN GATE EXECUTION 的")
        self.assertIsNotNone(exc, "短语不精确 ⇒ 必须拒绝")
        self.assertEqual(exc.status_code, 400)
        self.assertIn("OPEN GATE EXECUTION", exc.detail)

    def test_binance_execution_requires_the_exact_phrase(self):
        exc = self._call(binance_execution=True, confirmation="open binance execution!")
        self.assertIsNotNone(exc)
        self.assertEqual(exc.status_code, 400)
        self.assertIn("OPEN BINANCE EXECUTION", exc.detail)

    # ⚠️ **故意不测正例**：短语正确时处理器会继续走到"落盘写配置"那一步，
    # 而本刀还没确认写入函数名（要先读 274-310 的收尾）⇒ 宁可**不驱动**，
    # 也不在生产配置上做实验。拒绝分支是安全的：它 `raise` 在任何写入之前。


class VenueHealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(R, "DATA_DIR", __import__("pathlib").Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(R, "okx_env", types.SimpleNamespace(simulated=True),
                               create=True)
        p2.start()
        self.addCleanup(p2.stop)
        _auth_off(self)

    def _status(self):
        return R.admin_multi_exchange_status(x_r20_admin_token="t")

    def _write_health(self, payload):
        ( __import__("pathlib").Path(self.tmp.name) / "venue_health.json").write_text(
            payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")

    def test_corrupt_health_file_degrades_to_empty(self):
        self._write_health("{ 坏")
        out = self._status()
        self.assertIsInstance(out, dict, "读不动也要给出响应，不炸")

    def test_okx_section_is_completed_with_testnet_and_latency(self):
        self._write_health({"venues": {"okx": {"avg_ms": None}}})
        with mock.patch("r20_backend.exchanges.diagnostics.diagnose_venue_connection",
                        return_value={"latency_ms": 42}):
            out = self._status()
        dumped = json.dumps(out, ensure_ascii=False, default=str)
        self.assertIn("42", dumped, "现场诊断出的延迟必须出现在响应里")
        self.assertIn('"testnet": true', dumped, "testnet 取 okx_env.simulated")

    def test_latency_diagnosis_failure_is_swallowed(self):
        self._write_health({"venues": {"okx": {}}})
        with mock.patch("r20_backend.exchanges.diagnostics.diagnose_venue_connection",
                        side_effect=RuntimeError("诊断也挂了")):
            out = self._status()
        self.assertIsInstance(out, dict, "诊断失败不影响整体响应")


class _JunkAccount(dict):
    """任何键都返回一个**不能 float()** 的值 ⇒ 逼出"返回解析失败"那条分支。"""

    def get(self, key, default=None):
        return "不是数字"


class _PermissiveAdapter:
    def __init__(self, *, account=None, capability_error=None, generic_error=None):
        self._account = {} if account is None else account
        self._capability_error = capability_error
        self._generic_error = generic_error

    def account_snapshot(self):
        if self._capability_error is not None:
            raise self._capability_error
        if self._generic_error is not None:
            raise self._generic_error
        return self._account

    def __getattr__(self, name):
        def _stub(*a, **k):
            if self._generic_error is not None:
                raise self._generic_error
            return [] if name != "signed_request" else []
        return _stub


class VenueAccountFallbackTest(unittest.TestCase):
    """★ 三态：`unavailable`（所不支持）≠ `degraded`（读失败/解析失败）。"""

    def _creds(self):
        return mock.patch("r20_backend.exchanges.venue_credentials",
                          return_value=("k", "s"))

    def _run(self, fn, adapter):
        with self._creds(), \
                mock.patch.object(R, "get_adapter", return_value=adapter, create=True), \
                mock.patch("r20_backend.exchanges.get_adapter", return_value=adapter):
            return fn("demo")

    def test_gate_parse_failure_is_degraded(self):
        out = self._run(R._venue_accounts_gate, _PermissiveAdapter(account=_JunkAccount()))
        self.assertEqual(out["status"], "degraded")
        self.assertIn("Gate 返回解析失败", out["reason"])

    def test_gate_generic_failure_is_degraded(self):
        out = self._run(R._venue_accounts_gate,
                        _PermissiveAdapter(generic_error=RuntimeError("网络断了")))
        self.assertEqual(out["status"], "degraded")
        self.assertIn("Gate 账户读取失败", out["reason"])

    def test_binance_capability_error_is_unavailable_not_degraded(self):
        """★ 所能力缺失 ⇒ `unavailable`（**不可判定 ≠ 安全**，它与"读失败"是两回事）。"""
        from r20_backend.exchanges import ExchangeCapabilityError
        out = self._run(R._venue_accounts_binance,
                        _PermissiveAdapter(capability_error=ExchangeCapabilityError("不支持逐仓")))
        self.assertEqual(out["status"], "unavailable")
        self.assertIn("Binance 账户面不可用", out["reason"])

    def test_binance_generic_failure_is_degraded(self):
        out = self._run(R._venue_accounts_binance,
                        _PermissiveAdapter(generic_error=RuntimeError("网络断了")))
        self.assertEqual(out["status"], "degraded")
        self.assertIn("Binance 账户读取失败", out["reason"])

    def test_binance_parse_failure_is_degraded(self):
        out = self._run(R._venue_accounts_binance, _PermissiveAdapter(account=_JunkAccount()))
        self.assertEqual(out["status"], "degraded")
        self.assertIn("Binance 返回解析失败", out["reason"])

    def test_missing_credentials_say_unavailable_without_any_request(self):
        """★ 凭证没配 ⇒ `unavailable` 且**明确写"未发起任何请求"**（不谎称试过了）。"""
        with mock.patch("r20_backend.exchanges.venue_credentials",
                        return_value=("", "")), \
                mock.patch.object(R, "get_adapter", create=True) as getter:
            out = R._venue_accounts_gate("demo")
        self.assertEqual(out["status"], "unavailable")
        self.assertIn("未发起任何请求", out["reason"])
        getter.assert_not_called()


if __name__ == "__main__":
    unittest.main()

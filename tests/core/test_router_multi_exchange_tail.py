"""写接口尾段与「测试连接」（第二百二十五刀）。

| 语义 | 口径 |
|---|---|
| `routing_mode` | `save_routing_mode(mode)` 返回假 ⇒ **400**，detail 给出**允许值** |
| `preferred_venue` | 非 `None` 时交给 `routing_policy.save_preferred_venue` |
| 返回体 | `{"ok": True, "saved_secret_keys": [排序后的**键名**]}`（**只有键名，没有值**）|
| ★ 测试连接 | 诊断入参**逐字透传**（venue/environment/api_key/secret_key/passphrase/timeout）|
| ★ 测试连接审计 | `status` 取 `"success"`/`"failed"`（**失败也留痕**），字段只记**事实**（venue/environment/authenticated/mode/ok）——**不含密钥** |

## ⚠️ 两处实测发现（列待议）

1. **`clear_instances()` 的失败被 `except Exception: pass` 静默吞掉** —— 适配器实例清理失败不会
   体现在响应里；若旧实例残留，后续可能仍用**旧凭证/旧档位**，而调用方看到的是 `ok: True`。
2. **同一个函数有"两条调用路径"**：`refresh_settings` 既经 `app_attr(...)` 门面注入调用（写开关处），
   又在请求末尾**直接**调用模块级 `refresh_settings()`；`audit_record` 在写接口走门面、在测试连接里
   **直接**用模块级。门面存在的意义（可替换、可打桩）被第二条路径绕过 —— 同一请求里刷新两次。
"""

import types
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from r20_backend.routers import exchanges as R


def _payload(**over):
    fields = {k: None for k in (
        "binance_api_key", "binance_secret_key", "binance_live_api_key",
        "binance_live_secret_key", "binance_demo_api_key", "binance_demo_secret_key",
        "gate_api_key", "gate_secret_key", "gate_live_api_key", "gate_live_secret_key",
        "gate_demo_api_key", "gate_demo_secret_key", "okx_live_api_key",
        "okx_live_secret_key", "okx_live_passphrase", "okx_demo_api_key",
        "okx_demo_secret_key", "okx_demo_passphrase", "binance_testnet", "gate_testnet",
        "gate_execution", "binance_execution", "okx_execution", "okx_environment",
        "preferred_venue", "routing_mode")}
    fields["confirmation"] = ""
    fields.update(over)
    return types.SimpleNamespace(**fields)


class WriteTailTest(unittest.TestCase):
    def setUp(self):
        self.fns = {name: MagicMock(name=name) for name in
                    ("save_secrets", "update_env", "refresh_settings", "audit_record")}
        p = patch.object(R, "app_attr", side_effect=lambda name, default: self.fns[name])
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(R, "require_superadmin", return_value={"username": "张三"})
        p2.start()
        self.addCleanup(p2.stop)
        p3 = patch.object(R, "refresh_settings")       # 模块级那条路径
        self.module_refresh = p3.start()
        self.addCleanup(p3.stop)

    def _call(self, **over):
        try:
            return R.admin_multi_exchange_update(_payload(**over), "sess"), None
        except HTTPException as exc:
            return None, exc

    def test_illegal_routing_mode_is_400_with_allowed_values(self):
        # ⚠️ 不能 `patch("r20_backend.exchanges.routing_policy")`：它是**惰性子模块**，
        # 包 `__init__` 里没有这个属性 ⇒ mock 直接 AttributeError。改为补丁**子模块的函数**，
        # 并读**真实**的 VALID_ROUTING_MODES（顺带验证 detail 报的就是真实允许值）。
        from r20_backend.exchanges import routing_policy as RP
        allowed = list(RP.VALID_ROUTING_MODES)
        with patch.object(RP, "save_routing_mode", return_value=False):
            _out, exc = self._call(routing_mode="乱写")
        self.assertIsNotNone(exc)
        self.assertEqual(exc.status_code, 400)
        self.assertIn(allowed[0], exc.detail, "detail 要报**真实允许值**")

    def test_legal_routing_mode_and_preferred_venue_are_persisted(self):
        from r20_backend.exchanges import routing_policy as RP
        with patch.object(RP, "save_routing_mode", return_value=True) as srm, \
             patch.object(RP, "save_preferred_venue") as spv:
            out, exc = self._call(routing_mode="auto", preferred_venue="okx",
                                  okx_demo_api_key="K1")
        self.assertIsNone(exc)
        self.assertEqual(srm.call_args.args, ("auto",))
        self.assertEqual(spv.call_args.args, ("okx",))
        self.assertEqual(out, {"ok": True, "saved_secret_keys": ["OKX_DEMO_API_KEY"]},
                         "返回体只报键名（也排序了）")

    def test_a_failing_cache_clear_is_swallowed_and_reported_as_success(self):
        """⚠️ **实测发现（列待议）**：实例清理失败被静默吞，响应仍是 `ok: True`。"""
        with patch("r20_backend.exchanges.clear_instances",
                   side_effect=RuntimeError("清理失败")):
            out, exc = self._call(okx_demo_api_key="K1")
        self.assertIsNone(exc)
        self.assertEqual(out["ok"], True,
                         "清理失败不体现在响应里（现状，列待议：旧实例可能仍在用旧凭证）")

    def test_refresh_settings_is_reached_through_two_paths(self):
        """⚠️ **实测发现（列待议）**：同一请求里既走门面注入、又直接调模块级 —— 刷新两次。

        第二条路径会**绕过门面**（打桩/替换只能拦到第一条），门面的意义被削弱。
        """
        self._call(okx_environment="demo")
        self.assertTrue(self.fns["refresh_settings"].called, "门面那条")
        self.assertTrue(self.module_refresh.called, "模块级那条（绕过门面）")


class TestConnectionRouteTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(R, "require_admin_header")
        self.auth = p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(R, "audit_record")
        self.audit = p2.start()
        self.addCleanup(p2.stop)

    def _req(self, ok, authenticated=True):
        return types.SimpleNamespace(venue="binance", environment="demo",
                                     api_key="AK", secret_key="SK", passphrase="PP",
                                     timeout=8.0, **({} if ok else {}))

    def _run(self, result):
        p = patch("r20_backend.exchanges.diagnostics.diagnose_venue_connection",
                  return_value=result)
        diag = p.start()
        self.addCleanup(p.stop)
        return R.admin_multi_exchange_test_connection(self._req(True), None), diag

    def test_diagnostics_receive_every_field_verbatim(self):
        _out, diag = self._run({"ok": True})
        kw = diag.call_args.kwargs
        self.assertEqual(kw, {"venue": "binance", "environment": "demo", "api_key": "AK",
                              "secret_key": "SK", "passphrase": "PP", "timeout": 8.0})
        self.assertTrue(self.auth.called, "先过鉴权")

    def test_the_diagnosis_is_returned_untouched(self):
        result = {"ok": True, "authenticated": True, "mode": "demo", "extra": 1}
        out, _diag = self._run(result)
        self.assertIs(out, result, "诊断结果原样返回（不加工）")

    def test_audit_status_reflects_the_outcome_and_never_records_secrets(self):
        """★ 失败也留痕；且审计字段**只有事实**，绝不写密钥（安全红线）。"""
        self._run({"ok": False, "authenticated": False, "mode": "demo"})
        event, status, detail = self.audit.call_args.args
        self.assertEqual(event, "multi_exchange.test_connection")
        self.assertEqual(status, "failed", "失败同样留痕")
        self.assertEqual(detail, {"venue": "binance", "environment": "demo",
                                  "authenticated": False, "mode": "demo", "ok": False})
        self.assertNotIn("AK", repr(detail), "api_key 不得进入审计")
        self.assertNotIn("SK", repr(detail), "secret 不得进入审计")

    def test_successful_diagnosis_is_audited_as_success(self):
        self._run({"ok": True, "authenticated": True, "mode": "live"})
        self.assertEqual(self.audit.call_args.args[1], "success")


if __name__ == "__main__":
    unittest.main()

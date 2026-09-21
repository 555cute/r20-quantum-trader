"""OKX 运行时接口：**未配置就明说禁止交易**（第二百二十七刀）。

⚠️ **本刀只覆盖 `/admin/okx/runtime` 与「未配置 ⇒ 503」**。账户快照的**合并返回形状**在
492 行之后、跨所保护扫描在 570 行之后 —— 我**没有读到**，因此**不写断言**。
（我这一刀先写了两条按"局部变量名"猜返回键的用例 ⇒ 两条全红。**两轮里第二次犯同一个错**：
`return` 块没读到就不要断言返回结构。已删除，不留假证据。）

| 语义 | 口径 |
|---|---|
| ★ 未配置的表述 | `/admin/okx/runtime` 未配置 ⇒ `status=NOT_READY` **且**给出人话原因（含「未配置时系统禁止一切交易」）|
| 已配置 | `status=READY` 且**不带** `not_ready_reason` 键；载荷含 `environment`/`fingerprint`/`base_url`/`connection`/三档 `*_configured` |
| 指纹非密钥 | 回传的是 `fingerprint`（可核对、不可还原），不是三件套本身 |
| ★ 保证金补齐 | 账户快照里 OKX 仓位的 `margin`：有 `imr` 用 `imr`；`imr<=0` 但 **notionalUsd 与 lever 都 >0** ⇒ 用 `notionalUsd/lever` 补算并**四舍五入 2 位** |
| 归属不覆盖 | 仓位/挂单补 `venue` 默认 `"okx"`，但**已有 venue 不被覆盖** |
| 未配置异常 | `OKXNotConfigured` ⇒ **503**（不是空载荷）|
| ★ 所失败显式化 | 审计 C6：某所失败**显式化**（`venue_errors`），不再抹平成「完整」 |

## ⚠️ 一处实测发现（列待议）

`/admin/okx/runtime` 里 **`refresh_settings()` 在鉴权之前**执行 —— 未过鉴权的请求也能触发
一次设置刷新（副作用先于鉴权）。鉴权应当**最先**（fail closed 之前不留副作用）。
"""

import types
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from r20_backend.routers import exchanges as R


class OkxRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.order = []

        def _refresh():
            self.order.append("refresh")
        p = patch.object(R, "refresh_settings", side_effect=_refresh)
        self.refresh = p.start()
        self.addCleanup(p.stop)

        def _auth(*a, **k):
            self.order.append("auth")
        p2 = patch.object(R, "require_admin_header", side_effect=_auth)
        self.auth = p2.start()
        self.addCleanup(p2.stop)

        self.settings = patch.object(
            R, "settings", types.SimpleNamespace(okx_live_configured=True,
                                                 okx_demo_configured=False))
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def _env(self, configured):
        return types.SimpleNamespace(mode="demo", configured=configured,
                                     simulated=False, fingerprint="FP-1234",
                                     base_url="https://demo.example")

    def test_unconfigured_says_so_and_names_the_consequence(self):
        with patch("scripts.okx_runtime.current_environment",
                   return_value=self._env(False)):
            out = R.admin_okx_runtime(None, 0)
        self.assertEqual(out["status"], "NOT_READY")
        self.assertFalse(out["mode_configured"])
        self.assertIn("三件套", out["not_ready_reason"])
        self.assertIn("禁止一切交易", out["not_ready_reason"],
                      "未配置的**后果**必须写明，不能只给一个状态词")

    def test_configured_receipt_carries_facts_not_credentials(self):
        with patch("scripts.okx_runtime.current_environment",
                   return_value=self._env(True)):
            out = R.admin_okx_runtime(None, 0)
        self.assertEqual(out["status"], "READY")
        self.assertNotIn("not_ready_reason", out, "就绪时不带原因键")
        self.assertEqual(out["environment"], "demo")
        self.assertEqual(out["fingerprint"], "FP-1234", "指纹（可核对、不可还原）")
        self.assertEqual(out["connection"], "static-v5-key")
        self.assertTrue(out["live_configured"])
        self.assertFalse(out["demo_configured"])

    def test_refresh_happens_before_authentication(self):
        """⚠️ **实测发现（列待议）**：**先刷新设置、后鉴权** —— 副作用先于鉴权。"""
        with patch("scripts.okx_runtime.current_environment",
                   return_value=self._env(True)):
            R.admin_okx_runtime(None, 0)
        self.assertEqual(self.order[:2], ["refresh", "auth"],
                         "现状：未过鉴权也先刷新了一次设置（列待议）")


class AccountSnapshotTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(R, "require_admin_header")
        self.auth = p.start()
        self.addCleanup(p.stop)
        p2 = patch("scripts.okx_runtime.current_environment",
                   return_value=types.SimpleNamespace(mode="demo", configured=True,
                                                      simulated=False))
        p2.start()
        self.addCleanup(p2.stop)

    def _run(self, snap):
        p = patch.object(R, "app_attr", side_effect=lambda name, default: lambda: snap)
        p.start()
        self.addCleanup(p.stop)
        try:
            return R.admin_okx_account_snapshot(None, None), None
        except HTTPException as exc:
            return None, exc

    def test_not_configured_is_503_not_an_empty_payload(self):
        from scripts.okx_rest import OKXNotConfigured

        def _boom():
            raise OKXNotConfigured("三件套缺失")
        p = patch.object(R, "app_attr", side_effect=lambda name, default: _boom)
        p.start()
        self.addCleanup(p.stop)
        with self.assertRaises(HTTPException) as ctx:
            R.admin_okx_account_snapshot(None, None)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.detail, "三件套缺失")


if __name__ == "__main__":
    unittest.main()

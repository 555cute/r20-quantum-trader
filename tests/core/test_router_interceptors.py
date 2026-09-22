"""拦截器文件级 CRUD 与**沙箱试跑**（第二百五十三刀，开新面：strategy/interceptors.py）。

先打印整个文件再动笔。八条路由的骨架一致：

| 路由 | 权限 | 失败映射 |
|---|---|---|
| GET 列表 / GET 详情 | 管理员 | 详情：`FileNotFoundError` ⇒ **404**（不是 500，也不是空对象）|
| PUT 开关 / PUT 代码 / POST 新建 / DELETE / POST 重排 | **超管** | 代码：`ValueError` ⇒ **400**；新建/删除：**任何异常** ⇒ 400 |
| POST 试跑 | 管理员 | 走 `run_sandbox_test(scenario)` —— **沙箱**试跑 |

写操作一律**审计留痕**（`actor.username` + 目标文件名），读操作不留痕。
"""

import unittest
from unittest import mock

from fastapi import HTTPException

from r20_backend.routers.strategy import interceptors as I
from r20_backend.schemas import (InterceptorCodeRequest, InterceptorCreateRequest,
                                 InterceptorReorderRequest, InterceptorTestRequest,
                                 InterceptorToggleRequest)


class InterceptorRoutesTest(unittest.TestCase):
    def setUp(self):
        self.audits = []
        p = mock.patch.object(I, "audit_record",
                              side_effect=lambda *a, **k: self.audits.append(a))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(I, "require_admin_header", mock.Mock(), create=True)
        p.start()
        self.addCleanup(p.stop)
        self.superadmin = mock.Mock(return_value={"username": "root"})
        p = mock.patch.object(I, "require_superadmin", self.superadmin, create=True)
        p.start()
        self.addCleanup(p.stop)

    # ── 读 ────────────────────────────────────────────────
    def test_list_is_read_only_and_wrapped(self):
        with mock.patch("r20_backend.interceptor_manager.list_plugins",
                        return_value=[{"filename": "a.py"}]):
            out = I.admin_list_interceptors(x_r20_session="t")
        self.assertEqual(out, {"plugins": [{"filename": "a.py"}]})
        self.assertEqual(self.audits, [], "读操作不写审计")
        self.superadmin.assert_not_called()

    def test_missing_plugin_is_404_not_a_silent_empty_object(self):
        with mock.patch("r20_backend.interceptor_manager.get_plugin_detail",
                        side_effect=FileNotFoundError("没有这个拦截器")):
            with self.assertRaises(HTTPException) as ctx:
                I.admin_get_interceptor("nope.py", x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 404)

    # ── 写（超管 + 审计）──────────────────────────────────
    def test_toggle_requires_superadmin_and_audits_the_target(self):
        with mock.patch("r20_backend.interceptor_manager.toggle_plugin",
                        return_value={"enabled": True}) as toggler:
            out = I.admin_toggle_interceptor("a.py", InterceptorToggleRequest(enabled=True),
                                             x_r20_session="t")
        self.superadmin.assert_called_once_with("t")
        toggler.assert_called_once_with("a.py", True)
        self.assertEqual(out, {"enabled": True})
        self.assertEqual(self.audits[0][0], "interceptor.toggle")
        self.assertEqual(self.audits[0][2]["actor"], "root")
        self.assertEqual(self.audits[0][2]["filename"], "a.py")
        self.assertIs(self.audits[0][2]["enabled"], True)

    def test_code_value_error_is_400(self):
        with mock.patch("r20_backend.interceptor_manager.save_plugin_code",
                        side_effect=ValueError("语法不合法")):
            with self.assertRaises(HTTPException) as ctx:
                I.admin_save_interceptor_code("a.py", InterceptorCodeRequest(code="坏"),
                                              x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("语法不合法", ctx.exception.detail)
        self.assertEqual(self.audits, [], "失败**不**记 success 审计")

    def test_code_success_audits_without_the_body(self):
        with mock.patch("r20_backend.interceptor_manager.save_plugin_code",
                        return_value={"saved": True}):
            I.admin_save_interceptor_code("a.py", InterceptorCodeRequest(code="print(1)"),
                                          x_r20_session="t")
        payload = self.audits[0][2]
        self.assertEqual(self.audits[0][0], "interceptor.code.update")
        self.assertNotIn("code", payload, "**代码正文不入审计**（只记谁改了哪个文件）")

    def test_create_maps_any_failure_to_400(self):
        with mock.patch("r20_backend.interceptor_manager.create_plugin",
                        side_effect=RuntimeError("磁盘满了")):
            with self.assertRaises(HTTPException) as ctx:
                I.admin_create_interceptor(
                    InterceptorCreateRequest(filename="b.py", code="x"), x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("磁盘满了", ctx.exception.detail)

    def test_delete_returns_the_deleted_filename(self):
        with mock.patch("r20_backend.interceptor_manager.delete_plugin",
                        return_value=None):
            out = I.admin_delete_interceptor("a.py", x_r20_session="t")
        self.assertEqual(out, {"deleted": True, "filename": "a.py"})
        self.assertEqual(self.audits[0][0], "interceptor.delete")

    def test_delete_failure_is_400(self):
        with mock.patch("r20_backend.interceptor_manager.delete_plugin",
                        side_effect=PermissionError("只读文件系统")):
            with self.assertRaises(HTTPException) as ctx:
                I.admin_delete_interceptor("a.py", x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_reorder_passes_the_pipeline_order_and_wraps_the_result(self):
        with mock.patch("r20_backend.interceptor_manager.reorder_plugins",
                        return_value=[{"filename": "a.py"}]) as reorder:
            out = I.admin_reorder_interceptors(
                InterceptorReorderRequest(pipeline_order=["a.py", "b.py"]),
                x_r20_session="t")
        reorder.assert_called_once_with(["a.py", "b.py"])
        self.assertEqual(out, {"plugins": [{"filename": "a.py"}]})
        self.assertEqual(self.audits[0][0], "interceptor.reorder")

    # ── 沙箱试跑 ─────────────────────────────────────────
    def test_sandbox_test_only_needs_admin_and_does_not_audit(self):
        # ⚠️ `scenario` 在 schema 里是 `dict[str, Any] | None`，**不是字符串**
        # （我第一版传了字符串 ⇒ pydantic ValidationError）。
        scenario = {"market": "触发条件"}
        with mock.patch("r20_backend.interceptor_manager.run_sandbox_test",
                        return_value={"ok": True}) as runner:
            out = I.admin_test_interceptors(InterceptorTestRequest(scenario=scenario),
                                            x_r20_session="t")
        self.assertEqual(out, {"ok": True})
        runner.assert_called_once_with(scenario)
        self.superadmin.assert_not_called()
        self.assertEqual(self.audits, [], "试跑是只读演练，不写审计")


if __name__ == "__main__":
    unittest.main()

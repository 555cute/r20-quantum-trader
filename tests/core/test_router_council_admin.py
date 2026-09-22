"""投委会**管理面**路由：配置同源、超管权限、审计留痕（第二百五十刀）。

先打印目标行再动笔。

| 语义 | 口径 |
|---|---|
| ★ **超管 vs 管理员** | 改配置/套用套件/重置角色/导入 都必须 `require_superadmin`（返回 `actor`，写进审计）；**只读**的取配置与导出只要 `require_admin_header` |
| ★ **默认值同源（审计 P2-13）** | `CouncilConfigUpdateRequest.timeout_seconds` 的默认**必须**等于 `council_manager.DEFAULT_COUNCIL_TIMEOUT`（注释：旧默认 60 与引擎默认 240 不一致 ⇒ 管理员不改这一项时前后端口径不同）|
| ★ **模型健康摊开（审计 P1-4b）** | 配置响应带 `model_health` + 说明文案（旧版**静默回落主脑**，页面照旧宣称多模型 ⇒ 现在载荷带 `model_fallback` 标记）|
| ★ **约束内建** | `timeout_seconds` 有 `ge=30, le=420` ⇒ 越界在**模型层**就被拒（不是等到引擎）|
| 失败即 400 | 套用套件 / 导入 抛 `ValueError` ⇒ **400**（不 500），成功则写审计 |
"""

import unittest
from unittest import mock

from fastapi import HTTPException

from r20_backend.routers.strategy import council as C
from r20_backend.schemas import (CouncilApplySuiteRequest, CouncilConfigUpdateRequest,
                                 CouncilImportRequest, CouncilResetRoleRequest)


class TimeoutSingleSourceTest(unittest.TestCase):
    def test_schema_default_matches_the_engine_default(self):
        """★ 审计 P2-13 的守卫：前后端默认必须**同源**。"""
        from r20_backend import council_manager as CM
        default = CouncilConfigUpdateRequest(enabled=True, roles={}).timeout_seconds
        self.assertEqual(default, CM.DEFAULT_COUNCIL_TIMEOUT,
                         "schema 默认与引擎默认**必须相等**（旧版 60 vs 240 就是事故）")

    def test_bounds_are_enforced_at_the_model_layer(self):
        from pydantic import ValidationError
        for bad in (29.0, 421.0):
            with self.subTest(value=bad):
                with self.assertRaises(ValidationError):
                    CouncilConfigUpdateRequest(enabled=True, roles={}, timeout_seconds=bad)


class CouncilAdminRoutesTest(unittest.TestCase):
    def setUp(self):
        self.audits = []
        p = mock.patch.object(C, "audit_record",
                              side_effect=lambda *a, **k: self.audits.append((a, k)))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(C, "require_admin_header", mock.Mock(), create=True)
        p.start()
        self.addCleanup(p.stop)
        self.superadmin = mock.Mock(return_value={"username": "root"})
        p = mock.patch.object(C, "require_superadmin", self.superadmin, create=True)
        p.start()
        self.addCleanup(p.stop)

    # ── 读配置 ────────────────────────────────────────────
    def test_get_config_exposes_model_health_and_the_fallback_note(self):
        """★ 审计 P1-4b：把「席位绑定模型是否已登记」摊开给 UI，别让页面照旧宣称多模型。"""
        with mock.patch("r20_backend.council_manager.load_council_config",
                        return_value={"roles": {"a": {}}}), \
                mock.patch("r20_backend.council_manager.get_available_presets",
                           return_value=["p1"]), \
                mock.patch("r20_backend.council_manager.get_preset_suites",
                           return_value=["s1"]), \
                mock.patch("r20_backend.council_manager.seat_model_health",
                           return_value={"a": {"registered": False}}):
            out = C.admin_get_council_config(x_r20_session="t")
        self.assertEqual(out["available_presets"], ["p1"])
        self.assertEqual(out["available_suites"], ["s1"])
        self.assertEqual(out["model_health"], {"a": {"registered": False}})
        self.assertIn("model_fallback", out["model_health_note"],
                      "说明文案要告诉前端：回落是**带标记**的")

    # ── 改配置（超管）─────────────────────────────────────
    def test_update_requires_superadmin_and_saves_the_whitelist(self):
        payload = CouncilConfigUpdateRequest(enabled=True, consensus_mode="cross_examination",
                                             timeout_seconds=300.0, roles={"a": {"name": "甲"}})
        with mock.patch("r20_backend.council_manager.save_council_config",
                        return_value={"saved": True}) as saver:
            out = C.admin_update_council_config(payload, x_r20_session="t")
        self.superadmin.assert_called_once_with("t")
        self.assertEqual(out, {"status": "ok", "config": {"saved": True}})
        sent = saver.call_args.args[0]
        self.assertEqual(sent["timeout_seconds"], 300.0)
        self.assertEqual(sent["consensus_mode"], "cross_examination")
        self.assertEqual(sent["roles"], {"a": {"name": "甲"}})
        self.assertEqual(sorted(sent), ["consensus_mode", "enabled", "roles",
                                        "timeout_seconds"], "只落这几项，不把请求体整包塞进去")

    # ── 套用套件 / 重置角色 ────────────────────────────────
    def test_apply_suite_writes_an_audit_record(self):
        with mock.patch("r20_backend.council_manager.apply_preset_suite",
                        return_value={"ok": 1}):
            out = C.admin_apply_council_suite(CouncilApplySuiteRequest(suite_id="s1"),
                                              x_r20_session="t")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["suite_id"], "s1")
        self.assertEqual(self.audits[0][0][0], "council.suite.apply")
        self.assertEqual(self.audits[0][0][1], "success")
        self.assertEqual(self.audits[0][0][2]["actor"], "root")

    def test_apply_suite_failure_is_400_not_500(self):
        with mock.patch("r20_backend.council_manager.apply_preset_suite",
                        side_effect=ValueError("没有这个套件")):
            with self.assertRaises(HTTPException) as ctx:
                C.admin_apply_council_suite(CouncilApplySuiteRequest(suite_id="x"),
                                            x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("没有这个套件", ctx.exception.detail)

    def test_reset_role_writes_audit_and_returns_the_role(self):
        with mock.patch("r20_backend.council_manager.reset_role_template",
                        return_value={"roles": {}}):
            out = C.admin_reset_council_role(CouncilResetRoleRequest(role_id="cio"),
                                             x_r20_session="t")
        self.assertEqual(out["role_id"], "cio")
        self.assertEqual(self.audits[0][0][0], "council.role.reset")

    # ── 导出 / 导入 ──────────────────────────────────────
    def test_export_is_read_only_and_needs_no_superadmin(self):
        with mock.patch("r20_backend.council_manager.export_council_config",
                        return_value={"exported": True}) as exporter:
            out = C.admin_export_council_config(x_r20_session="t")
        self.assertEqual(out, {"exported": True})
        self.superadmin.assert_not_called()

    def test_import_bad_payload_is_400(self):
        with mock.patch("r20_backend.council_manager.import_council_config",
                        side_effect=ValueError("格式不对")):
            with self.assertRaises(HTTPException) as ctx:
                C.admin_import_council_config(CouncilImportRequest(payload={}),
                                              x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("格式不对", ctx.exception.detail)

    def test_import_success_is_audited_and_spread_into_the_response(self):
        with mock.patch("r20_backend.council_manager.import_council_config",
                        return_value={"roles_imported": 3, "backup_file": "b.json"}):
            out = C.admin_import_council_config(CouncilImportRequest(payload={"a": 1}),
                                                x_r20_session="t")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["roles_imported"], 3, "结果**展开**到顶层")
        self.assertEqual(out["backup_file"], "b.json")
        self.assertEqual(self.audits[0][0][0], "council.config.import")

    def test_update_and_reset_both_require_superadmin(self):
        """★ 权限语义：管理面写操作一律超管，**不能**只当普通管理员放行。"""
        with mock.patch("r20_backend.council_manager.save_council_config",
                        return_value={}):
            C.admin_update_council_config(CouncilConfigUpdateRequest(enabled=True, roles={}),
                                          x_r20_session="t")
        with mock.patch("r20_backend.council_manager.reset_role_template", return_value={}):
            C.admin_reset_council_role(CouncilResetRoleRequest(role_id="a"),
                                       x_r20_session="t")
        self.assertEqual(self.superadmin.call_count, 2)


if __name__ == "__main__":
    unittest.main()

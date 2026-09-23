"""策略提示词路由（`r20_backend/routers/strategy/prompts.py`）残余分支收口测试 —— 第 341 刀。

本模块 378 行，负责方案库（Prompt Profiles）、系统提示词实时覆盖与自进化复盘起始时间配置：
- 方案 CRUD 异常分流：非法标识拦截（正则格式）、400 校验异常、404 不存在、409 冲突与 500 异常；
- 方案生命周期：激活、删除、回滚、校验、历史版本与导入导出；
- 提示词热覆盖（`prompt_override`）：原子写入临时文件与清理、推演统一单源对齐；
- 自进化起始时间配置（`evolution/config`）：起始时间过滤与历史订单回测统计。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import r20_backend.routers.strategy.prompts as sp


class StrategyPromptsTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_prompts_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.temp_override = Path(self.tmp.name) / "system_prompt_override.txt"

        # 1. 物理重定向覆盖文件，严防测试向生产 data/ 写入
        p_file = patch.object(sp, "PROMPT_OVERRIDE_FILE", self.temp_override)
        p_file.start()
        self.addCleanup(p_file.stop)

        # 2. 鉴权打桩为已授权
        p_admin = patch.object(sp, "require_admin_header", return_value=True)
        p_admin.start()
        self.addCleanup(p_admin.stop)

        p_super = patch.object(sp, "require_superadmin", return_value={"username": "superadmin"})
        p_super.start()
        self.addCleanup(p_super.stop)

        p_audit = patch.object(sp, "audit_record")
        p_audit.start()
        self.addCleanup(p_audit.stop)

    # -------------------------------------------------------------------------
    # 1. 方案列表与创建 (prompt_profiles & create_prompt_profile_api)
    # -------------------------------------------------------------------------
    def test_prompt_profiles_internal_error_raises_500(self):
        with patch.object(sp, "load_library", side_effect=RuntimeError("disk read error")):
            with self.assertRaises(HTTPException) as ctx:
                sp.prompt_profiles()
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertIn("获取提示词方案列表失败", ctx.exception.detail)

    def test_create_prompt_profile_value_error_raises_400(self):
        payload = sp.PromptProfileCreateRequest(name="Invalid Profile")
        with patch.object(sp, "create_profile", side_effect=ValueError("名称格式不符")):
            with self.assertRaises(HTTPException) as ctx:
                sp.create_prompt_profile_api(payload)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("名称格式不符", ctx.exception.detail)

    def test_create_prompt_profile_generic_error_raises_500(self):
        payload = sp.PromptProfileCreateRequest(name="Crash Profile")
        with patch.object(sp, "create_profile", side_effect=RuntimeError("database crash")):
            with self.assertRaises(HTTPException) as ctx:
                sp.create_prompt_profile_api(payload)
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertIn("创建提示词方案失败", ctx.exception.detail)

    # -------------------------------------------------------------------------
    # 2. 方案更新与激活 (update & activate)
    # -------------------------------------------------------------------------
    def test_update_prompt_profile_invalid_id_raises_400(self):
        payload = sp.PromptProfileUpdateRequest()
        for bad_id in ("", "bad id with space", "bad@id!"):
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(HTTPException) as ctx:
                    sp.update_prompt_profile_api(bad_id, payload)
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertIn("无效的提示词方案标识", ctx.exception.detail)

    def test_update_prompt_profile_value_error_raises_400(self):
        payload = sp.PromptProfileUpdateRequest()
        with patch.object(sp, "update_profile", side_effect=ValueError("无效参数变更")):
            with self.assertRaises(HTTPException) as ctx:
                sp.update_prompt_profile_api("valid_id", payload)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("无效参数变更", ctx.exception.detail)

    def test_update_prompt_profile_generic_error_raises_500(self):
        payload = sp.PromptProfileUpdateRequest()
        with patch.object(sp, "update_profile", side_effect=RuntimeError("io error")):
            with self.assertRaises(HTTPException) as ctx:
                sp.update_prompt_profile_api("valid_id", payload)
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertIn("更新提示词方案失败", ctx.exception.detail)

    def test_activate_prompt_profile_invalid_id_raises_400(self):
        for bad_id in ("", "!bad!"):
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(HTTPException) as ctx:
                    sp.activate_prompt_profile_api(bad_id)
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertIn("无效的提示词方案标识", ctx.exception.detail)

    def test_activate_prompt_profile_conflict_raises_409(self):
        with patch.object(sp, "activate_profile", side_effect=ValueError("方案已被占用且锁定")):
            with self.assertRaises(HTTPException) as ctx:
                sp.activate_prompt_profile_api("locked_id")
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertIn("方案已被占用且锁定", ctx.exception.detail)

    def test_activate_prompt_profile_generic_error_raises_500(self):
        with patch.object(sp, "activate_profile", side_effect=RuntimeError("activation boom")):
            with self.assertRaises(HTTPException) as ctx:
                sp.activate_prompt_profile_api("valid_id")
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertIn("启用提示词方案失败", ctx.exception.detail)

    # -------------------------------------------------------------------------
    # 3. 方案删除与校验 (delete & validate)
    # -------------------------------------------------------------------------
    def test_delete_prompt_profile_success(self):
        with patch.object(sp, "delete_profile", return_value=None):
            res = sp.delete_prompt_profile_api("valid_id")
            self.assertEqual(res, {"deleted": True})

    def test_delete_prompt_profile_conflict_raises_409(self):
        with patch.object(sp, "delete_profile", side_effect=ValueError("当前正在使用的主脑方案不可删除")):
            with self.assertRaises(HTTPException) as ctx:
                sp.delete_prompt_profile_api("in_use_id")
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertIn("当前正在使用的主脑方案不可删除", ctx.exception.detail)

    def test_delete_prompt_profile_generic_error_raises_500(self):
        with patch.object(sp, "delete_profile", side_effect=RuntimeError("delete failed")):
            with self.assertRaises(HTTPException) as ctx:
                sp.delete_prompt_profile_api("valid_id")
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertIn("删除提示词方案失败", ctx.exception.detail)

    def test_validate_prompt_profile_generic_error_raises_400(self):
        payload = sp.PromptProfileUpdateRequest()
        with patch.object(sp, "validate_profile", side_effect=RuntimeError("validation syntax boom")):
            with self.assertRaises(HTTPException) as ctx:
                sp.validate_prompt_profile_api(payload)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("提示词方案校验失败", ctx.exception.detail)

    # -------------------------------------------------------------------------
    # 4. 历史版本与回滚 (history & rollback)
    # -------------------------------------------------------------------------
    def test_prompt_profile_history_invalid_id_raises_400(self):
        with self.assertRaises(HTTPException) as ctx:
            sp.prompt_profile_history_api("bad profile id*")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("无效的提示词方案标识", ctx.exception.detail)

    def test_prompt_profile_history_generic_error_raises_500(self):
        with patch.object(sp, "profile_history", side_effect=RuntimeError("git history error")):
            with self.assertRaises(HTTPException) as ctx:
                sp.prompt_profile_history_api("valid_id")
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertIn("获取方案历史失败", ctx.exception.detail)

    def test_rollback_prompt_profile_invalid_id_raises_400(self):
        payload = sp.PromptRollbackRequest(revision_id="rev_1")
        with self.assertRaises(HTTPException) as ctx:
            sp.rollback_prompt_profile_api("", payload)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("无效的提示词方案标识", ctx.exception.detail)

    def test_rollback_prompt_profile_success(self):
        payload = sp.PromptRollbackRequest(revision_id="rev_1")
        mock_profile = {"id": "p1", "version": 1}
        with patch.object(sp, "rollback_profile", return_value=mock_profile):
            res = sp.rollback_prompt_profile_api("p1", payload)
            self.assertEqual(res, {"profile": mock_profile})

    def test_rollback_prompt_profile_not_found_raises_404(self):
        payload = sp.PromptRollbackRequest(revision_id="rev_1")
        with patch.object(sp, "rollback_profile", side_effect=ValueError("版本号不存在")):
            with self.assertRaises(HTTPException) as ctx:
                sp.rollback_prompt_profile_api("p1", payload)
            self.assertEqual(ctx.exception.status_code, 404)
            self.assertIn("版本号不存在", ctx.exception.detail)

    def test_rollback_prompt_profile_value_error_raises_400(self):
        payload = sp.PromptRollbackRequest(revision_id="rev_1")
        with patch.object(sp, "rollback_profile", side_effect=ValueError("版本号格式非法")):
            with self.assertRaises(HTTPException) as ctx:
                sp.rollback_prompt_profile_api("p1", payload)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("版本号格式非法", ctx.exception.detail)

    def test_rollback_prompt_profile_generic_error_raises_500(self):
        payload = sp.PromptRollbackRequest(revision_id="rev_1")
        with patch.object(sp, "rollback_profile", side_effect=RuntimeError("fs failure")):
            with self.assertRaises(HTTPException) as ctx:
                sp.rollback_prompt_profile_api("p1", payload)
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertIn("回滚提示词方案失败", ctx.exception.detail)

    # -------------------------------------------------------------------------
    # 5. 导入与导出 (export & import)
    # -------------------------------------------------------------------------
    def test_export_prompt_profile_generic_error_raises_500(self):
        with patch.object(sp, "export_profile", side_effect=RuntimeError("serialization boom")):
            with self.assertRaises(HTTPException) as ctx:
                sp.export_prompt_profile_api("p1")
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertIn("导出提示词方案失败", ctx.exception.detail)

    def test_import_prompt_profile_generic_error_raises_400(self):
        payload = sp.PromptImportRequest(payload={"version": 1})
        with patch.object(sp, "import_profile", side_effect=RuntimeError("unsupported schema")):
            with self.assertRaises(HTTPException) as ctx:
                sp.import_prompt_profile_api(payload)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("导入提示词方案失败", ctx.exception.detail)

    # -------------------------------------------------------------------------
    # 6. 提示词热覆盖 (prompt_override & update_prompt_override)
    # -------------------------------------------------------------------------
    def test_prompt_override_read_error_raises_500(self):
        with patch("scripts.ai_brain_trader.get_effective_system_prompt", side_effect=RuntimeError("read failed")):
            with self.assertRaises(HTTPException) as ctx:
                sp.prompt_override()
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertIn("获取提示词覆盖配置失败", ctx.exception.detail)

    def test_update_prompt_override_write_and_clear(self):
        # 1) 写入非空覆盖内容
        res1 = sp.update_prompt_override(sp.PromptOverrideRequest(content="只做多趋势"))
        self.assertTrue(res1["saved"])
        self.assertTrue(res1["enabled"])
        self.assertEqual(self.temp_override.read_text(encoding="utf-8").strip(), "只做多趋势")

        # 2) 写入空字符串清空
        res2 = sp.update_prompt_override(sp.PromptOverrideRequest(content=""))
        self.assertTrue(res2["saved"])
        self.assertFalse(res2["enabled"])
        self.assertFalse(self.temp_override.exists())

    def test_update_prompt_override_write_error_raises_500(self):
        with patch("os.replace", side_effect=OSError("permission denied")):
            with self.assertRaises(HTTPException) as ctx:
                sp.update_prompt_override(sp.PromptOverrideRequest(content="test"))
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertIn("更新提示词覆盖失败", ctx.exception.detail)

    # -------------------------------------------------------------------------
    # 7. 自进化起始时间配置 (evolution/config)
    # -------------------------------------------------------------------------
    def test_get_evolution_config(self):
        with patch("r20_backend.account_baseline.load_account_baseline", return_value={"evolution_start_time": "2026-09-10 12:00:00"}):
            with patch("scripts.self_improvement_engine.load_closed_trades", return_value=[{"id": 1}, {"id": 2}]):
                res = sp.get_evolution_config()
                self.assertEqual(res["evolution_start_time"], "2026-09-10 12:00:00")
                self.assertEqual(res["active_trades_count"], 2)
                self.assertIn("历史人工合约订单", res["note"])

    def test_update_evolution_config(self):
        payload = sp.EvolutionConfigUpdate(start_time="2026-09-15 00:00:00")
        with patch("r20_backend.account_baseline.update_evolution_start_time", return_value={"evolution_start_time": "2026-09-15 00:00:00"}):
            with patch("scripts.self_improvement_engine.load_closed_trades", return_value=[{"id": 1}]):
                res = sp.update_evolution_config(payload)
                self.assertTrue(res["ok"])
                self.assertEqual(res["evolution_start_time"], "2026-09-15 00:00:00")
                self.assertEqual(res["active_trades_count"], 1)
                self.assertIn("自进化复盘起始时间已更新", res["effect"])


if __name__ == "__main__":
    unittest.main()

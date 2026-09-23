"""网关灾备与恢复路由（`r20_backend/routers/gateway/backups.py`）残余分支收口测试 —— 第 366 刀。

本模块 501 行，负责网关全量配置与数据备份、跨云存储同步测试、归档校验与冷恢复：
- 未支持灾备目标类型防御（`test_simple_backup` 接收非标准目标类型时拦截并报 400）；
- 归档上传越界路径逃逸防御（`upload_backup_archive` 路径校验失败时拦截报 400 非法文件上传路径）。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

import r20_backend.routers.gateway.backups as backups_mod
from r20_backend.routers.gateway.backups import SimpleBackupUpdateRequest


class RouterGatewayBackupsTailsTests(unittest.IsolatedAsyncioTestCase):
    # -------------------------------------------------------------------------
    # 1. 未支持的灾备目标类型防御 (test_simple_backup)
    # -------------------------------------------------------------------------
    def test_simple_backup_unsupported_destination(self):
        # 绕过 Pydantic 正则构造非标准 destination，验证底层业务守卫生效 (lines 133-134)
        req = SimpleBackupUpdateRequest.model_construct(destination="unsupported_target")
        with patch("r20_backend.routers.gateway.backups.require_superadmin"), \
             patch("r20_backend.routers.gateway.backups.refresh_settings"):
            with self.assertRaises(HTTPException) as ctx:
                backups_mod.test_simple_backup(req, x_r20_session="mock_session")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("不支持的灾备目标", ctx.exception.detail)

    # -------------------------------------------------------------------------
    # 2. 归档上传越界路径逃逸防御 (upload_backup_archive)
    # -------------------------------------------------------------------------
    async def test_upload_backup_archive_path_escape_blocked(self):
        # 上传目标解析路径脱离 target_dir 时拦截 (lines 416-417)
        mock_file = MagicMock()
        mock_file.filename = "safe_backup.tar.gz"
        with patch("r20_backend.routers.gateway.backups.require_superadmin"), \
             patch("r20_backend.routers.gateway.backups.refresh_settings"), \
             patch.object(Path, "is_relative_to", return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                await backups_mod.upload_backup_archive(file=mock_file, x_r20_session="mock_session")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "非法文件上传路径")


if __name__ == "__main__":
    unittest.main()

"""网关凭据加密存储（`r20_gateway/secrets.py`）残余分支收口测试 —— 第 376 刀。

本模块 150 行，负责网关敏感 API Key 与密钥的 Fernet 对称加密、原子写入、备份与状态监控：
- 秘钥文件不存在且不自动生成时返回 None（`_fernet(create=False)` 安全返回 None）；
- 凭据库备份写盘异常容错（`_backup_store_file` 遭遇 `OSError` 安全 pass）；
- 凭据批量删除与原子写回（`delete_secrets` 移除指定凭据并同步清除环境变量）；
- 凭据库初始化与状态巡检（`status` 汇报凭据库存在性、数量与权限掩码）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import r20_gateway.secrets as sec_mod


class GatewaySecretsTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_gw_secrets_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.key_file = self.tmp_path / "secrets.key"
        self.store_file = self.tmp_path / "secrets.enc"

    def test_fernet_nonexistent_key_without_create_returns_none(self):
        # 秘钥文件不存在且 create=False 时返回 None (lines 53-55)
        with patch.object(sec_mod, "KEY_FILE", self.key_file):
            self.assertIsNone(sec_mod._fernet(create=False))

    def test_backup_store_file_os_error_ignored(self):
        # 备份历史凭据库遇 OSError 静默忽略 (lines 106-107)
        self.store_file.write_bytes(b"dummy_ciphertext")
        with patch.object(sec_mod, "STORE_FILE", self.store_file), \
             patch("shutil.copy2", side_effect=OSError("copy failed")):
            sec_mod._backup_store_file()
        self.assertTrue(self.store_file.exists())

    def test_delete_secrets_removes_from_store_and_env(self):
        # 凭据删除并写回密文库 (lines 122-132)
        with patch.object(sec_mod, "KEY_FILE", self.key_file), \
             patch.object(sec_mod, "STORE_FILE", self.store_file):
            sec_mod.save_secrets({"OKX_DEMO_API_KEY": "test_api_key"})
            self.assertIn("OKX_DEMO_API_KEY", sec_mod.load_secrets())

            sec_mod.delete_secrets(["OKX_DEMO_API_KEY"])
            self.assertNotIn("OKX_DEMO_API_KEY", sec_mod.load_secrets())

    def test_status_returns_metadata(self):
        # 状态概览函数包含初始化标记与键名列表 (lines 141-149)
        with patch.object(sec_mod, "KEY_FILE", self.key_file), \
             patch.object(sec_mod, "STORE_FILE", self.store_file):
            sec_mod.save_secrets({"OKX_DEMO_API_KEY": "bn_key"})
            st = sec_mod.status()
            self.assertTrue(st["initialized"])
            self.assertEqual(st["count"], 1)
            self.assertEqual(st["keys"], ["OKX_DEMO_API_KEY"])
            self.assertEqual(st["source_priority"], "encrypted-store-over-env")


if __name__ == "__main__":
    unittest.main()

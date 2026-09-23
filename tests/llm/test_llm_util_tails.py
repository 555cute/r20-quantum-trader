"""LLM 通用工具层（`r20_backend/llm/util.py`）残余分支收口测试 —— 第 369 刀。

本模块 44 行，负责 LLM 配置原子落盘与凭据脱敏转接：
- 异常中断清理与临时文件解绑容错（`_atomic_write_json` 序列化失败时清理临时文件，且 `os.unlink` 异常时静默 pass）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r20_backend.llm.util import _atomic_write_json, mask_secret


class LLMUtilTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_llm_util_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def test_atomic_write_json_cleanup_on_error(self):
        # 写入不可序列化对象触发异常，验证 finally 中存在检查与 unlink OSError 容错 (lines 28, 31)
        target_path = self.tmp_path / "atomic_error.json"

        class UnserializableObject:
            pass

        with patch("os.unlink", side_effect=OSError("disk unlink error")):
            with self.assertRaises(TypeError):
                _atomic_write_json(target_path, UnserializableObject())

        # 临时文件处理完毕，目标文件未生成
        self.assertFalse(target_path.exists())

    def test_mask_secret_passthrough(self):
        # 验证凭据脱敏门面透明转发 (line 44)
        masked = mask_secret("sk-1234567890abcdef", visible=4)
        self.assertTrue(masked.startswith("sk-1"))
        self.assertTrue(masked.endswith("cdef"))


if __name__ == "__main__":
    unittest.main()

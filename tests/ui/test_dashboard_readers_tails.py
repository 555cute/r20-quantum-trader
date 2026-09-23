"""看板基础读取原语（`r20_backend/dashboard_payload/readers.py`）残余分支收口测试 —— 第 370 刀。

本模块 120 行，负责看板安全只读加载、坏数据告警披露与行尾切片：
- 保留行尾空格模式（`read_text_lines` 在 `strip=False` 时仅剔除换行符 `\n`，保留行内首尾有效空白字符）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from r20_backend.dashboard_payload.readers import read_text_lines


class DashboardReadersTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_readers_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def test_read_text_lines_strip_false_preserves_internal_spacing(self):
        # 验证 strip=False 分支仅 rstrip("\n") 而不 strip() (line 86)
        log_file = self.tmp_path / "app.log"
        log_file.write_text("   indented line 1  \n   indented line 2\n", encoding="utf-8")
        lines = read_text_lines(log_file, limit=10, strip=False)
        self.assertEqual(lines, ["   indented line 1  ", "   indented line 2"])


if __name__ == "__main__":
    unittest.main()

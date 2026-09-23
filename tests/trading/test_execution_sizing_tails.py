"""仓位测算与下单步长量化（`r20_backend/execution/sizing.py`）残余分支收口测试 —— 第 371 刀。

本模块 44 行，负责持仓规模测算、单笔风险预算与交易所步长量化：
- 非法输入张数量化容错（`quantize_size` 接收非法非数值字符串时捕获 `(TypeError, ValueError)` 安全回退 0.0）。
"""
from __future__ import annotations

import unittest

from r20_backend.execution.sizing import quantize_size


class ExecutionSizingTailsTests(unittest.TestCase):
    def test_quantize_size_invalid_type_or_value_returns_zero(self):
        # 传入无法转换为 float 的非法非数值字符串或非法类型，捕获异常并返回 0.0 (lines 32-33)
        self.assertEqual(quantize_size("invalid_float_string", 1.0), 0.0)
        self.assertEqual(quantize_size([123], 1.0), 0.0)


if __name__ == "__main__":
    unittest.main()

"""本方在管仓位台账判别（`r20_backend/execution/own_records.py`）残余分支收口测试 —— 第 354 刀。

本模块 242 行，是跨所实盘仓位是否属于量化程序在管的判别核心：
- 台账文件路径自适应（`ledger_path`）：指定工作区目录时优先解析 `<workspace>/data/trading_ledger.json`；
- 币种名称规范化（`canonical_inst`）：冒号所前缀（如 `GATE:BTC_USDT`）剥除并提取基准币名；
- 方向规范化（`_norm_ex_side` / `_norm_ledger_side`）：做空/卖出多词别名标准化与非法方向安全回退空串；
- 容错解析（`_to_float` / `_size_match`）：脏值浮点安全转 None、空头寸尺寸比对安全返回 False；
- 台账多格式解析（`read_ledger_rows`）：下钻解析嵌套字典（如 `trades` 包装层）与自动跳过非字典项。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from r20_backend.execution.own_records import (
    _norm_ex_side,
    _norm_ledger_side,
    _size_match,
    _to_float,
    canonical_inst,
    ledger_path,
    read_ledger_rows,
)


class OwnPositionRecordsTailsTests(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. 台账路径与币种归一 (ledger_path & canonical_inst)
    # -------------------------------------------------------------------------
    def test_ledger_path_with_explicit_workspace_dir(self):
        # 显式传入 workspace_dir 时指向该工作区下的 data 目录 (line 62)
        p = ledger_path("/data/custom_workspace")
        self.assertEqual(p, Path("/data/custom_workspace/data/trading_ledger.json"))

    def test_canonical_inst_with_venue_prefix_colon(self):
        # 包含所前缀的写法（如 GATE:BTC_USDT）剥离前缀并解析出标准基准币名 (lines 75-76)
        self.assertEqual(canonical_inst("GATE:BTC_USDT"), "BTC")
        self.assertEqual(canonical_inst("OKX:ETH-USDT-SWAP"), "ETH")
        self.assertEqual(canonical_inst("BINANCE:SOLUSDT"), "SOL")

    # -------------------------------------------------------------------------
    # 2. 方向规范化 (_norm_ex_side & _norm_ledger_side)
    # -------------------------------------------------------------------------
    def test_norm_ex_side_short_variants(self):
        # 交易所空头/卖出方向别名解析为 short (lines 94-95)
        for s in ("short", "sell", "s", "空"):
            self.assertEqual(_norm_ex_side(s), "short")

    def test_norm_ledger_side_invalid_returns_empty(self):
        # 台账未识别的方向字符串安全回退为空串 (line 105)
        self.assertEqual(_norm_ledger_side("unknown_side_str"), "")
        self.assertEqual(_norm_ledger_side(None), "")

    # -------------------------------------------------------------------------
    # 3. 浮点与尺寸比对容错 (_to_float & _size_match)
    # -------------------------------------------------------------------------
    def test_to_float_invalid_returns_none(self):
        # 非数值类型与脏串安全转 None (line 112)
        self.assertIsNone(_to_float("invalid_float"))
        self.assertIsNone(_to_float(None))

    def test_size_match_none_row_sz_returns_false(self):
        # row_sz 为 None 时比对返回 False (line 163)
        self.assertFalse(_size_match(None, 10.0))

    # -------------------------------------------------------------------------
    # 4. 台账层级与条目解析 (read_ledger_rows)
    # -------------------------------------------------------------------------
    def test_read_ledger_rows_nested_wrapper_dict(self):
        # 支持外层包装 trades / rows / ledger 键的嵌套结构并递归下钻 (lines 124-127)
        wrapped = {
            "trades": [
                {"id": "t1", "inst": "BTC-USDT-SWAP", "sz": 1.0, "status": "holding"},
                {"id": "t2", "inst": "ETH-USDT-SWAP", "sz": 2.0, "status": "holding"},
            ]
        }
        rows = read_ledger_rows(wrapped)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "t1")
        self.assertEqual(rows[1]["id"], "t2")

    def test_read_ledger_rows_skips_non_dict_values(self):
        # 顶层映射中包含非字典值时自动跳过 (line 131)
        data = {
            "valid_order": {"inst": "SOL-USDT", "sz": 5.0},
            "meta_count": 10,
            "version": "1.0",
        }
        rows = read_ledger_rows(data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "valid_order")


if __name__ == "__main__":
    unittest.main()

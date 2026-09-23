"""回测沙箱数据仓库（`r20_backend/sandbox/data_warehouse.py`）残余分支收口测试 —— 第 367 刀。

本模块 157 行，负责回测沙箱 SQLite 历史 K 线存储、批量导入、时间范围查询与覆盖区间统计：
- 空数据批量插入防御（`ingest_candles` 传入空列表 `rows=[]` 时直接返回 0）；
- 残缺 K 线记录过滤（`ingest_candles` 遍历行记录时跳过长度小于 5 的残缺数据）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

# 优先导入 exchanges 避免模块级循环引用
import r20_backend.exchanges  # noqa: F401
from r20_backend.sandbox.data_warehouse import CandleWarehouse


class SandboxDataWarehouseTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_warehouse_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test_candles.db"
        self.warehouse = CandleWarehouse(self.db_path)

    def test_ingest_candles_empty_rows_returns_zero(self):
        # 传入空行列表直接返回 0 (lines 53-54)
        n = self.warehouse.ingest_candles("okx", "BTC-USDT", "1m", [])
        self.assertEqual(n, 0)

    def test_ingest_candles_short_row_skipped(self):
        # 记录行长度不足 5 的残缺条目被跳过 (lines 61-62)
        rows = [
            [1700000000000, 50000.0],  # len < 5 被跳过
            [1700000060000, 50100.0, 50200.0, 50050.0, 50150.0, 10.5],  # 正常入库
        ]
        n = self.warehouse.ingest_candles("okx", "BTC-USDT", "1m", rows)
        self.assertEqual(n, 1)
        self.assertEqual(self.warehouse.count_candles("okx", "BTC-USDT", "1m"), 1)


if __name__ == "__main__":
    unittest.main()

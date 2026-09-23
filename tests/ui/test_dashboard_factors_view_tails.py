"""看板因子视图装配（`r20_backend/dashboard_payload/factors_view.py`）残余分支收口测试 —— 第 362 刀。

本模块 141 行，负责操盘看板标的因子列表、AI 决策研判与多维度信号提取：
- 状态与因子库文件非迭代格式容错（`state_data` / `lib_data` 的 `instruments` 键为非列表脏数据时安全捕获 pass，不影响其他标的装配）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from r20_backend.dashboard_payload.factors_view import build_factors_list


class DashboardFactorsViewTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_dashboard_factors_view_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.dec_file = self.tmp_path / "decisions.json"
        self.dec_file.write_text("{}", encoding="utf-8")

    def test_build_factors_list_non_iterable_instruments_keys_handled(self):
        # 当 state_file 或 factor_file 中的 instruments 字段为非列表（如整数/脏类型）时，
        # 捕获 TypeError/Exception 并静默忽略 (lines 35-36, 44-45)
        state_file = self.tmp_path / "trading_state.json"
        state_file.write_text(json.dumps({"instruments": 123}), encoding="utf-8")

        factor_file = self.tmp_path / "factor_lib.json"
        factor_file.write_text(json.dumps({"instruments": 456}), encoding="utf-8")

        factors_list, state_data = build_factors_list(
            decisions_file=self.dec_file,
            state_file=state_file,
            factor_file=factor_file,
            positions=[],
            timestamp_full="2026-09-22 12:00:00",
        )

        self.assertIsInstance(factors_list, list)
        self.assertGreater(len(factors_list), 0)
        # 验证默认标的均已生成且包含完整字段
        self.assertEqual(factors_list[0]["instId"], "BTC-USDT-SWAP")
        self.assertIn("strategy_tag", factors_list[0])

    def test_build_factors_list_valid_instruments_populated(self):
        # 正常字典列表形式装配 inst_state_map 与 factor_lib_map (lines 33-34, 42-43)
        state_file = self.tmp_path / "trading_state_valid.json"
        state_file.write_text(json.dumps({"instruments": [{"instId": "BTC-USDT-SWAP", "action": "BUY_LONG", "vwap_bias": 1.25}]}), encoding="utf-8")

        factor_file = self.tmp_path / "factor_lib_valid.json"
        factor_file.write_text(json.dumps({"instruments": [{"instId": "BTC-USDT-SWAP", "price": 60000.0}]}), encoding="utf-8")

        factors_list, state_data = build_factors_list(
            decisions_file=self.dec_file,
            state_file=state_file,
            factor_file=factor_file,
            positions=[],
            timestamp_full="2026-09-22 12:00:00",
        )
        self.assertEqual(factors_list[0]["instId"], "BTC-USDT-SWAP")
        self.assertEqual(factors_list[0]["vwap_bias"], 1.25)
        self.assertEqual(factors_list[0]["price"], 60000.0)


if __name__ == "__main__":
    unittest.main()

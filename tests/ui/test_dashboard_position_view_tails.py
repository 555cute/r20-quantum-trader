"""看板持仓视图装配（`r20_backend/dashboard_payload/position_view.py`）残余分支收口测试 —— 第 364 刀。

本模块 165 行，负责操盘看板活跃持仓规范化、名义价值与保证金折算、持仓方向判定：
- 全局环境轴解析异常回退（`okx_runtime.current_environment` 异常时保守回退 `DEMO` 模拟盘与 `demo` 档，防持仓环境抬升至实盘）。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from r20_backend.dashboard_payload.position_view import collect_position_rows


class DashboardPositionViewTailsTests(unittest.TestCase):
    def test_collect_position_rows_env_exception_falls_back_to_demo(self):
        # 当 okx_runtime 解析抛异常时，安全回退 account_mode="DEMO" / environment="demo" (lines 130-132)
        pos_data = [
            {
                "instId": "BTC-USDT-SWAP",
                "pos": "1",
                "posSide": "net",
                "notionalUsd": "60000",
                "lever": "3",
            }
        ]
        positions: list = []
        trackers = {"total_imr": 0.0}

        with patch("scripts.okx_runtime.current_environment", side_effect=RuntimeError("env unresolvable")):
            added = collect_position_rows(pos_data, positions, trackers, load_instruments=lambda: [])

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["instId"], "BTC-USDT-SWAP")
        self.assertEqual(positions[0]["account_mode"], "DEMO")
        self.assertEqual(positions[0]["environment"], "demo")


if __name__ == "__main__":
    unittest.main()

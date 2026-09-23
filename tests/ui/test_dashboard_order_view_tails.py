"""看板委托挂单视图装配（`r20_backend/dashboard_payload/order_view.py`）残余分支收口测试 —— 第 363 刀。

本模块 162 行，负责操盘看板活跃挂单规范化、止盈止损提取、杠杆与保证金折算：
- 全局环境轴解析异常回退（`okx_runtime.current_environment` 异常时保守回退 `DEMO` 模拟盘与 `demo` 档，防挂单环境抬升至实盘）。
"""
from __future__ import annotations

import datetime
import unittest
from unittest.mock import patch

from r20_backend.dashboard_payload.order_view import collect_pending_order_rows


class DashboardOrderViewTailsTests(unittest.TestCase):
    def test_collect_pending_order_rows_env_exception_falls_back_to_demo(self):
        # 当 okx_runtime 解析抛异常时，安全回退 account_mode="DEMO" / environment="demo" (lines 110-112)
        tz_bj = datetime.timezone(datetime.timedelta(hours=8))
        orders = [
            {
                "ordId": "12345",
                "instId": "BTC-USDT-SWAP",
                "side": "buy",
                "sz": "1",
                "px": "60000",
                "cTime": "1720000000000",
            }
        ]
        pending: list = []

        with patch("scripts.okx_runtime.current_environment", side_effect=RuntimeError("env unresolvable")):
            added = collect_pending_order_rows(orders, pending, tz_beijing=tz_bj, datetime=datetime)

        self.assertEqual(added, 1)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["ordId"], "12345")
        self.assertEqual(pending[0]["account_mode"], "DEMO")
        self.assertEqual(pending[0]["environment"], "demo")


if __name__ == "__main__":
    unittest.main()

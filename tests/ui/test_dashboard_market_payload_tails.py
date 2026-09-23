"""行情与环境轴看板装配（`r20_backend/dashboard_payload/market.py`）残余分支收口测试 —— 第 346 刀。

本模块 186 行，是操盘控制台行情资产、调度周期、全站环境轴与多所组合资产装配核心：
- 目标合约读取（`get_target_instruments`）：转调 `load_instruments`；
- Trader 决策周期解析（`_trader_cycle_minutes`）：0 周期跳出与异常捕获回退 None；
- 全站资金/行情环境轴（`_global_env_axis`）：okx_runtime 解析异常安全回退 "demo"；
- 组合风险数据读取（`_load_portfolio_risk_data`）：非法预算转 0.0、合约池异常参考上限设 None、底层管理器异常降级为 unavailable 结构；
- 多所组合资产聚合（`_load_multi_venue_portfolio`）：模块缺失降级、各所账户异常隔离披露与聚合异常返回空组合。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from r20_backend.dashboard_payload.market import (
    _TRADER_CYCLE_MINUTES_MEMO,
    _global_env_axis,
    _load_multi_venue_portfolio,
    _load_portfolio_risk_data,
    _trader_cycle_minutes,
    get_target_instruments,
)


class DashboardMarketPayloadTailsTests(unittest.TestCase):
    def setUp(self):
        _TRADER_CYCLE_MINUTES_MEMO.clear()

    def tearDown(self):
        _TRADER_CYCLE_MINUTES_MEMO.clear()

    # -------------------------------------------------------------------------
    # 1. 目标合约与调度周期 (get_target_instruments & _trader_cycle_minutes)
    # -------------------------------------------------------------------------
    def test_get_target_instruments_calls_loader(self):
        # 转调 load_instruments 且返回列表结构 (line 29)
        instruments = get_target_instruments()
        self.assertIsInstance(instruments, list)
        self.assertGreater(len(instruments), 0)

    def test_trader_cycle_minutes_zero_interval_breaks_and_returns_none(self):
        # 存在 trader 任务但 interval_seconds 为 0/None 时跳出并返回 None (lines 53, 56)
        job = MagicMock()
        job.name = "trader"
        job.interval_seconds = 0
        with patch("r20_gateway.scheduler.current_jobs", return_value=[job]):
            res = _trader_cycle_minutes()
            self.assertIsNone(res)

    def test_trader_cycle_minutes_exception_handled_returns_none(self):
        # 调度器读取异常时捕获并安全返回 None (lines 55-56)
        with patch("r20_gateway.scheduler.current_jobs", side_effect=RuntimeError("scheduler offline")):
            res = _trader_cycle_minutes()
            self.assertIsNone(res)

    # -------------------------------------------------------------------------
    # 2. 全站环境轴 (_global_env_axis)
    # -------------------------------------------------------------------------
    def test_global_env_axis_exception_falls_back_to_demo(self):
        # okx_runtime 解析异常时保守回退为 "demo" 档 (line 78)
        with patch("scripts.okx_runtime.current_environment", side_effect=RuntimeError("env error")):
            self.assertEqual(_global_env_axis(), "demo")

    # -------------------------------------------------------------------------
    # 3. 组合风险数据读取 (_load_portfolio_risk_data)
    # -------------------------------------------------------------------------
    def test_load_portfolio_risk_data_invalid_budget_and_pool_exception(self):
        # 预算非数值时回退 budget_val=0.0 (line 98)；合约池异常时 reference_cap=None (line 115)
        with patch.dict(os.environ, {"R20_PORTFOLIO_RISK_BUDGET_USDT": "invalid_value"}):
            with patch("scripts.instrument_pool.load_instruments", side_effect=RuntimeError("pool error")):
                res = _load_portfolio_risk_data()
                self.assertEqual(res["status"], "ok")
                self.assertEqual(res["budget_mode"], "uncapped")
                self.assertIsNone(res["total_budget_usdt"])
                self.assertIsNone(res["reference_cap_usdt"])

    def test_load_portfolio_risk_data_outer_exception_returns_unavailable_payload(self):
        # 风险管理器抛出异常时降级为 unavailable 字典结构并携带错误 (lines 139-146)
        with patch("r20_backend.risk_reservation.get_manager", side_effect=RuntimeError("mgr crash")):
            res = _load_portfolio_risk_data()
            self.assertEqual(res["status"], "unavailable")
            self.assertIsNone(res["budget_mode"])
            self.assertIn("mgr crash", res.get("error", ""))

    # -------------------------------------------------------------------------
    # 4. 多所组合资产聚合 (_load_multi_venue_portfolio)
    # -------------------------------------------------------------------------
    def test_load_multi_venue_portfolio_module_import_error_handled(self):
        # routers.exchanges 模块缺失时 gate 与 binance 标记 unavailable (lines 168-169)
        with patch.dict(sys.modules, {"r20_backend.routers.exchanges": None}):
            with patch("r20_backend.portfolio_aggregator.aggregate_venue_accounts", side_effect=lambda m, e: m):
                res = _load_multi_venue_portfolio(100.0, 50.0, [], [])
                self.assertEqual(res["gate"]["status"], "unavailable")
                self.assertIn("账户模块缺失", res["gate"]["reason"])
                self.assertEqual(res["binance"]["status"], "unavailable")
                self.assertIn("账户模块缺失", res["binance"]["reason"])

    def test_load_multi_venue_portfolio_individual_venue_exceptions_handled(self):
        # gate / binance 各自账户面抛异常时独立捕获隔离 (lines 174, 178)
        with patch("r20_backend.routers.exchanges._venue_accounts_gate", side_effect=RuntimeError("gate query boom")):
            with patch("r20_backend.routers.exchanges._venue_accounts_binance", side_effect=RuntimeError("binance query boom")):
                with patch("r20_backend.portfolio_aggregator.aggregate_venue_accounts", side_effect=lambda m, e: m):
                    res = _load_multi_venue_portfolio(100.0, 50.0, [], [])
                    self.assertEqual(res["gate"]["status"], "unavailable")
                    self.assertIn("Gate 账户面异常: gate query boom", res["gate"]["reason"])
                    self.assertEqual(res["binance"]["status"], "unavailable")
                    self.assertIn("Binance 账户面异常: binance query boom", res["binance"]["reason"])

    def test_load_multi_venue_portfolio_outer_exception_returns_empty_dict(self):
        # 聚合器自身发生异常时捕获并安全返回空字典 (line 186)
        with patch("r20_backend.routers.exchanges._venue_accounts_gate", return_value={"status": "ok"}):
            with patch("r20_backend.routers.exchanges._venue_accounts_binance", return_value={"status": "ok"}):
                with patch("r20_backend.portfolio_aggregator.aggregate_venue_accounts", side_effect=RuntimeError("aggregator boom")):
                    res = _load_multi_venue_portfolio(100.0, 50.0, [], [])
                    self.assertEqual(res, {})


if __name__ == "__main__":
    unittest.main()

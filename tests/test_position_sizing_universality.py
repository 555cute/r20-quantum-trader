"""仓位规模按「交易所最小下单量 + 可用余额」自适应的回归测试。

背景（v7.5.6 修复）：执行层曾用 `max(1, int(round(sz)))` 强制至少 1 张合约，
而 OKX 多数永续的 minSz 实为 0.01 张。对小资金账户这会把仓位向上放大最多 100 倍
（BTC 0.01 张 = 7.92U 名义被抬成 1 张 = 792U 名义 / 5x 需 158U 保证金），
导致 20U 账户要么被交易所拒单、要么在中等账户上静默开出远超风险预算的仓位。
同时基准风险额写死 15 USDT，对 20U 账户等于单笔押上 75% 本金。
"""
from __future__ import annotations

import importlib
import math
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ai_factor_trader as aft  # noqa: E402
from tests.risk_test_env import pin_baseline_risk_env  # noqa: E402


def setUpModule():
    # 断言的是基线风控语义；生产 .env 挂进取/均衡套件时不得污染本文件
    pin_baseline_risk_env()


class QuantizeSizeTests(unittest.TestCase):
    def test_floors_to_exchange_step_and_never_inflates_to_one(self):
        self.assertAlmostEqual(aft.quantize_size(0.025, 0.01), 0.02, places=10)
        self.assertAlmostEqual(aft.quantize_size(1.9, 0.01), 1.9, places=10)     # 已是步长整数倍，原样保留
        self.assertAlmostEqual(aft.quantize_size(1.899, 0.01), 1.89, places=10)  # 向下量化，不四舍五入进位
        # 关键：低于最小步长时归零（上层跳过），而不是放大成 1 张
        self.assertEqual(aft.quantize_size(0.005, 0.01), 0.0)
        self.assertEqual(aft.quantize_size(0.4, 1), 0.0)

    def test_zero_and_invalid_inputs(self):
        self.assertEqual(aft.quantize_size(0, 0.01), 0.0)
        self.assertEqual(aft.quantize_size(-5, 0.01), 0.0)
        self.assertEqual(aft.quantize_size(None, 0.01), 0.0)
        self.assertAlmostEqual(aft.quantize_size(2.0, 0), 2.0, places=10)  # 无步长退化为原值

    def test_result_is_always_multiple_of_step(self):
        for raw in (0.013, 0.077, 0.5, 1.0, 3.33, 12.9):
            sz = aft.quantize_size(raw, 0.01)
            self.assertGreaterEqual(sz, 0)
            self.assertAlmostEqual(sz / 0.01 - round(sz / 0.01), 0.0, places=6)


class AdaptiveRiskPerTradeTests(unittest.TestCase):
    def test_small_account_risk_is_scaled_down(self):
        self.assertAlmostEqual(aft.effective_risk_per_trade(15.0, 20.0), 0.4, places=4)   # 20U × 2%
        self.assertAlmostEqual(aft.effective_risk_per_trade(15.0, 80.0), 1.6, places=4)   # 80U × 2%

    def test_large_account_keeps_pool_absolute_cap(self):
        self.assertEqual(aft.effective_risk_per_trade(15.0, 4000.0), 15.0)
        self.assertEqual(aft.effective_risk_per_trade(15.0, None), 15.0)

    def test_env_override(self):
        import r20_backend.config as backend_config
        import risk_constants
        os.environ["R20_RISK_PER_TRADE_RATIO"] = "0.05"
        original_loader = backend_config.load_dotenv
        backend_config.load_dotenv = lambda path: None  # 屏蔽仓库 .env 覆盖测试环境变量
        try:
            importlib.reload(risk_constants)  # v7.6 起常量单一事实源在 risk_constants
            mod = importlib.reload(aft)
            self.assertAlmostEqual(mod.effective_risk_per_trade(15.0, 20.0), 1.0, places=4)
        finally:
            backend_config.load_dotenv = original_loader
            del os.environ["R20_RISK_PER_TRADE_RATIO"]
            importlib.reload(risk_constants)
            importlib.reload(aft)


class MarginHardCapTests(unittest.TestCase):
    """20U 账户下 BTC 必须能开出合规小仓位，而不是 1 张 792U 名义。"""

    BTC_PRICE, BTC_CTVAL, MIN_SZ = 79207.0, 0.01, 0.01

    def test_btc_size_within_20u_balance(self):
        sz = aft.max_size_within_margin(20.0, 5.0, self.BTC_PRICE, self.BTC_CTVAL, self.MIN_SZ)
        self.assertGreater(sz, 0, "20U 账户应能开出 BTC 最小仓位")
        self.assertLess(sz, 1.0, "必须小于 1 张，否则说明旧的整数下限回归了")
        margin = sz * self.BTC_CTVAL * self.BTC_PRICE / 5.0
        self.assertLessEqual(margin, 20.0 * 0.20 + 1e-6, "单笔保证金不得超过可用余额 20%")

    def test_legacy_one_contract_would_blow_the_account(self):
        legacy_notional = 1 * self.BTC_CTVAL * self.BTC_PRICE  # 旧实现强制 1 张
        self.assertGreater(legacy_notional / 5.0, 20.0 * 5, "对照：1 张 BTC 保证金远超 20U")

    def test_unaffordable_instrument_returns_zero_not_one(self):
        # 1U 余额买不起任何 BTC 最小仓位 -> 归零跳过，绝不放大
        sz = aft.max_size_within_margin(1.0, 3.0, self.BTC_PRICE, self.BTC_CTVAL, self.MIN_SZ)
        self.assertEqual(sz, 0.0)

    def test_no_balance_returns_infinite(self):
        self.assertEqual(aft.max_size_within_margin(None, 3.0, 100.0, 1.0, 0.01), float("inf"))


class NoIntegerFloorLeftInSourceTests(unittest.TestCase):
    def test_sizing_path_has_no_max_1_int_pattern(self):
        src = (ROOT / "scripts" / "ai_factor_trader.py").read_text(encoding="utf-8")
        offenders = [
            line.strip() for line in src.splitlines()
            if "max(1, int(" in line.replace(" ", "") or "max(1,int(" in line.replace(" ", "")
        ]
        self.assertEqual(offenders, [], f"整数仓位下限回归: {offenders}")

    def test_minSz_is_actually_consumed(self):
        src = (ROOT / "scripts" / "ai_factor_trader.py").read_text(encoding="utf-8")
        self.assertIn('item.get("minSz"', src, "minSz 必须被执行层实际读取，不能只存在池配置里")


if __name__ == "__main__":
    unittest.main()

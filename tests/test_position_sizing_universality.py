"""仓位规模按「交易所最小下单量 + 可用余额」自适应的回归测试。

背景（v7.5.6 修复）：执行层曾用 `max(1, int(round(sz)))` 强制至少 1 张合约，
而 OKX 多数永续的 minSz 实为 0.01 张。对小资金账户这会把仓位向上放大最多 100 倍
（BTC 0.01 张 = 7.92U 名义被抬成 1 张 = 792U 名义 / 5x 需 158U 保证金），
导致 20U 账户要么被交易所拒单、要么在中等账户上静默开出远超风险预算的仓位。
同时基准风险额写死 15 USDT，对 20U 账户等于单笔押上 75% 本金。
"""
from __future__ import annotations

import importlib
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






if __name__ == "__main__":
    unittest.main()

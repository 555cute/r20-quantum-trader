"""量化指标纯数学引擎（`r20_backend/execution/indicators.py`）残余分支收口测试 —— 第 350 刀。

本模块 140 行，是策略特征提取纯数学技术指标计算核心：
- RSI（相对强弱指标）：样本不足（<= period）安全返回 50.0；
- ATR（真实波幅）：单 K 线样本不足返回 0.0、不满足整周期时使用平局真实波幅兜底；
- MACD：样本不足（< slow + signal）返回全零元组；
- OBV（能量潮）：样本不足返回 NEUTRAL、精准研判牛市吸筹（BULL_ACCUMULATION）与熊市派发（BEAR_DISTRIBUTION）背离形态；
- 布林带挤压（Bollinger Squeeze）：样本不足返回 (0.0, 0.0, False)。
"""
from __future__ import annotations

import unittest

from r20_backend.execution.indicators import (
    calc_atr,
    calc_bollinger_squeeze,
    calc_macd_histogram_acceleration,
    calc_obv_trend,
    calc_rsi,
)


class ExecutionIndicatorsTailsTests(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. RSI 边界分支
    # -------------------------------------------------------------------------
    def test_calc_rsi_insufficient_samples_returns_neutral(self):
        # 价格列表长度 <= period 时返回 50.0 中性值 (lines 17-18)
        self.assertEqual(calc_rsi([], period=14), 50.0)
        self.assertEqual(calc_rsi([100.0] * 10, period=14), 50.0)
        self.assertEqual(calc_rsi([100.0] * 14, period=14), 50.0)

    # -------------------------------------------------------------------------
    # 2. ATR 真实波幅边界分支
    # -------------------------------------------------------------------------
    def test_calc_atr_insufficient_candles_returns_zero(self):
        # K 线不足 2 根返回 0.0 (lines 44-45)
        self.assertEqual(calc_atr([]), 0.0)
        self.assertEqual(calc_atr([[0, 0, 100, 90, 95]]), 0.0)

    def test_calc_atr_partial_period_averages_available_trs(self):
        # 样本数大于 2 根但不足 period 时计算当前可用 TR 均值 (lines 55-56)
        candles = [
            [0, 0, 10, 5, 8],
            [0, 0, 12, 6, 9],  # tr = max(12-6, |12-8|, |6-8|) = 6
            [0, 0, 11, 7, 10], # tr = max(11-7, |11-9|, |7-9|) = 4
        ]
        # (6 + 4) / 2 = 5.0
        self.assertEqual(calc_atr(candles, period=14), 5.0)

    # -------------------------------------------------------------------------
    # 3. MACD 样本不足兜底
    # -------------------------------------------------------------------------
    def test_calc_macd_histogram_acceleration_insufficient_samples(self):
        # 价格数 < slow + signal (26 + 9 = 35) 返回全零 (lines 65-66)
        res = calc_macd_histogram_acceleration([100.0] * 20)
        self.assertEqual(res, (0.0, 0.0, 0.0, 0.0))

    # -------------------------------------------------------------------------
    # 4. OBV 背离与流量形态研判
    # -------------------------------------------------------------------------
    def test_calc_obv_trend_insufficient_samples_returns_neutral(self):
        # 样本不足 period 返回 (0.0, "NEUTRAL") (lines 97-98)
        self.assertEqual(calc_obv_trend([100.0] * 5, [10.0] * 5, period=14), (0.0, "NEUTRAL"))

    def test_calc_obv_trend_bull_accumulation(self):
        # 价格整体走弱但成交量推动 OBV 走强 -> BULL_ACCUMULATION (line 115)
        closes = [100.0] * 10 + [105.0, 106.0, 105.0, 107.0, 104.0]
        vols = [10.0] * 10 + [10.0, 100.0, 1.0, 100.0, 1.0]
        val, state = calc_obv_trend(closes, vols, period=14)
        self.assertEqual(state, "BULL_ACCUMULATION")

    def test_calc_obv_trend_bear_distribution(self):
        # 价格整体走强但 OBV 走弱 -> BEAR_DISTRIBUTION (line 117)
        closes = [100.0] * 10 + [105.0, 104.0, 106.0, 103.0, 108.0]
        vols = [10.0] * 10 + [10.0, 100.0, 1.0, 100.0, 1.0]
        val, state = calc_obv_trend(closes, vols, period=14)
        self.assertEqual(state, "BEAR_DISTRIBUTION")

    # -------------------------------------------------------------------------
    # 5. 布林带挤压
    # -------------------------------------------------------------------------
    def test_calc_bollinger_squeeze_insufficient_samples(self):
        # 样本不足 period (20) 返回全零与 False (lines 128-129)
        self.assertEqual(calc_bollinger_squeeze([100.0] * 10, period=20), (0.0, 0.0, False))


if __name__ == "__main__":
    unittest.main()

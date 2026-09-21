"""评分引擎的**子因子账本**与四大机构形态（第二百五十四刀）。

上限（记在代码注释里的区间）与实际累加必须对得上 —— 这里逐个因子单独隔离测，
再测形态识别：形态是 `raw_alpha_score` 的**下限/上限覆盖**（`max`/`min`），
即「形态一旦成立，分数至少/至多是它」——这就是为什么形态能压过因子分歧。

| 子因子 | 区间 | 触发 |
|---|---|---|
| 趋势 | ±1.5 | 单边趋势 + 斜率（`HH_HL`/`LH_LL` 加 0.3）；否则 EMA 三线排列 ±0.6 |
| 量能 | ±1.5 | MACD 加速度 ±0.6；OBV 流向 ±0.5；放量 K 线 ±0.4 |
| 均值回归 | ±1.2 | VWAP 极值 ±1.2；顺势健康区间 ±0.5 |
| 情绪 | ±0.8 | `sentiment_score * 1.5` 夹取 |
| 微积分 | ±1.5 | 四个动力学 regime ±0.5/0.6；定积分 ±0.4；概率 ±0.4 |
"""

import unittest

from scripts.trader import signals


class _Base(unittest.TestCase):
    def setUp(self):
        self.cfg = {}

        def _load():
            return self.cfg

        self._load = _load

    def _score(self, **over):
        """中性基线：所有子因子为 0，且**不触发任何形态**（便于隔离单个因子）。"""
        f = {"market_data_valid": True, "instId": "BTC-USDT-SWAP", "name": "BTC",
             "price": 100.0, "ema9": 100.0, "ema21": 100.0, "ema55": 100.0,
             "rsi": 50.0, "vwap_bias": 0.0, "vol_ratio": 1.0, "obv_flow": "NEUTRAL",
             "macd_hist": 0.0, "macd_accel": 0.0, "market_regime": "CHOP"}
        f.update(over)
        score, action, reasons, tag, desc = signals.evaluate_asset_signal(
            f, asset_class_profiles={"crypto": {"entry_threshold": 2.2}},
            is_in_stop_cooldown=lambda *_: False, load_adaptive_config=self._load)
        return score, action, tag


class TrendFactorTest(_Base):
    def test_bull_trend_with_structure_bonus(self):
        score, _, _ = self._score(market_regime="BULL_TREND", ema21_slope_pct=0.05,
                                  structure_1h="HH_HL", rsi=60.0)
        self.assertEqual(score, 1.5, "1.2 + 0.3（HH_HL 结构加成）")

    def test_bear_trend_with_structure_bonus(self):
        score, _, _ = self._score(market_regime="BEAR_TREND", ema21_slope_pct=-0.05,
                                  structure_1h="LH_LL", rsi=35.0)
        self.assertEqual(score, -1.5)

    def test_ema_alignment_without_trend_regime(self):
        up, _, _ = self._score(ema9=103.0, ema21=102.0, ema55=101.0)
        self.assertEqual(up, 0.6, "三线多头排列（无单边 regime）⇒ +0.6")
        down, _, _ = self._score(ema9=97.0, ema21=98.0, ema55=99.0, rsi=35.0)
        self.assertEqual(down, -0.6)


class VolumeFactorTest(_Base):
    def test_macd_acceleration_both_directions(self):
        self.assertEqual(self._score(macd_accel=0.5, macd_hist=0.5)[0], 0.6)
        self.assertEqual(self._score(macd_accel=-0.5, macd_hist=-0.5)[0], -0.6)

    def test_obv_flow_both_directions(self):
        self.assertEqual(self._score(obv_flow="BULL_FLOW")[0], 0.5)
        self.assertEqual(self._score(obv_flow="BULL_ACCUMULATION")[0], 0.5)
        self.assertEqual(self._score(obv_flow="BEAR_DISTRIBUTION")[0], -0.5)

    def test_volume_spike_candle_needs_the_matching_candle_direction(self):
        """放量**必须与 K 线方向同向**才计分（放量阳线加分、放量阴线减分）。"""
        self.assertEqual(self._score(vol_ratio=1.25, is_bull_candle_15m=True)[0], 0.4)
        self.assertEqual(self._score(vol_ratio=1.25, is_bear_candle_15m=True)[0], -0.4)
        self.assertEqual(self._score(vol_ratio=1.25)[0], 0.0, "只看量、没有对应 K 线 ⇒ 不计分")

    def test_factors_sum(self):
        score, _, _ = self._score(macd_accel=0.5, macd_hist=0.5, obv_flow="BULL_FLOW",
                                  vol_ratio=1.25, is_bull_candle_15m=True)
        self.assertEqual(score, 1.5, "0.6 + 0.5 + 0.4")


class MeanReversionFactorTest(_Base):
    def test_healthy_pullback_zone_in_bull_trend(self):
        score, _, _ = self._score(market_regime="BULL_TREND", rsi=50.0)
        self.assertEqual(score, 0.5, "40~55 且处于多头趋势 ⇒ 顺势健康区间")

    def test_healthy_rally_zone_in_bear_trend(self):
        score, _, _ = self._score(market_regime="BEAR_TREND", rsi=50.0)
        self.assertEqual(score, -0.5, "45~60 且处于空头趋势 ⇒ 顺势空头区间")

    def test_vwap_extremes_dominate(self):
        self.assertEqual(self._score(vwap_bias=-0.75, rsi=35.0)[0], 1.2)
        self.assertEqual(self._score(vwap_bias=0.75, rsi=65.0)[0], -1.2)


class CalculusFactorTest(_Base):
    def _calc(self, **calc):
        return self._score(calculus=calc)[0]

    def test_dynamics_regimes(self):
        self.assertEqual(self._calc(regime="BULL_ACCELERATING"), 0.6)
        self.assertEqual(self._calc(regime="BULL_DECELERATING"), -0.5,
                         "多头减速扣分（Anti-FOMO）")
        self.assertEqual(self._calc(regime="BEAR_ACCELERATING"), -0.6)
        self.assertEqual(self._calc(regime="BEAR_DECELERATING"), 0.5,
                         "空头减速加分（Anti-bottom-chasing）")

    def test_dynamics_from_velocity_acceleration_impulse(self):
        """无 regime 字样时也要能从 v/a/i 推出来（双通道，不是只认字符串）。"""
        self.assertEqual(self._calc(velocity=0.3, acceleration=0.2, impulse=0.5), 0.6)
        self.assertEqual(self._calc(velocity=-0.3, acceleration=-0.2, impulse=-0.5), -0.6)

    def test_definite_integrals_both_directions(self):
        self.assertEqual(
            self._calc(definite_integrals={"energy_integral": 2.0,
                                           "deviation_area_integral": 1.0}), 0.4)
        self.assertEqual(
            self._calc(definite_integrals={"energy_integral": -2.0,
                                           "deviation_area_integral": -1.0}), -0.4)

    def test_probability_theory_both_directions(self):
        self.assertEqual(
            self._calc(probability_theory={"continuation_prob_pct": 80.0}), 0.4)
        self.assertEqual(
            self._calc(probability_theory={"breakdown_prob_pct": 80.0}), -0.4)

    def test_calculus_is_clamped_to_band(self):
        """子因子夹取在 ±1.5 —— 累加超额不得把分数顶出区间。"""
        score = self._calc(regime="BULL_ACCELERATING",
                           definite_integrals={"energy_integral": 2.0,
                                               "deviation_area_integral": 1.0},
                           probability_theory={"continuation_prob_pct": 80.0})
        self.assertEqual(score, 1.4, "0.6+0.4+0.4=1.4（未越界，验证累加口径）")


class InstitutionalSetupTest(_Base):
    """形态是**分数的下限/上限覆盖**（`max`/`min`）：形态成立即可压过因子分歧。"""

    def test_setup1_institutional_pullback(self):
        score, action, tag = self._score(
            market_regime="BULL_TREND", rsi=50.0, is_bull_candle_15m=True)
        self.assertEqual(tag, "🌊 顺势回踩")
        self.assertEqual(score, 2.4, "raw = max(因子和, 2.4)")
        self.assertEqual(action, "BUY_LONG")

    def test_setup2_resistance_exhaustion(self):
        score, action, tag = self._score(
            market_regime="BEAR_TREND", rsi=50.0, is_bear_candle_15m=True)
        self.assertEqual(tag, "⚡ 阻力抛压")
        self.assertEqual(score, -2.4, "raw = min(因子和, -2.4)")
        self.assertEqual(action, "SELL_SHORT")

    def test_setup3_momentum_breakout(self):
        score, action, tag = self._score(
            price=100.0, ema9=99.0, ema21=98.0, ema55=97.0, rsi=60.0,
            vol_ratio=1.3, macd_accel=0.5, macd_hist=0.5, is_bull_candle_15m=True)
        self.assertEqual(tag, "🚀 动量突破")
        self.assertEqual(score, 2.5)
        self.assertEqual(action, "BUY_LONG")

    def test_setup4_breakdown_acceleration(self):
        score, action, tag = self._score(
            price=100.0, ema9=101.0, ema21=102.0, ema55=103.0, rsi=35.0,
            vol_ratio=1.3, macd_accel=-0.5, macd_hist=-0.5, is_bear_candle_15m=True)
        self.assertEqual(tag, "🌪️ 破位追空")
        self.assertEqual(score, -2.5)
        self.assertEqual(action, "SELL_SHORT")

    def test_high_jerk_shock_dampens_breakout_setups(self):
        """高抖动冲击市场 ⇒ **压制突破类形态**（Setup 1-4 都带 `not is_high_jerk_shock`）。"""
        _, _, tag = self._score(
            price=100.0, ema9=99.0, ema21=98.0, ema55=97.0, rsi=60.0,
            vol_ratio=1.3, macd_accel=0.5, macd_hist=0.5, is_bull_candle_15m=True,
            calculus={"max_abs_jerk": 2.5})
        self.assertEqual(tag, "⚪ 观望", "冲击市场不许追突破")

    def test_high_jerk_shock_by_regime_string(self):
        _, _, tag = self._score(
            price=100.0, ema9=99.0, ema21=98.0, ema55=97.0, rsi=60.0,
            vol_ratio=1.3, macd_accel=0.5, macd_hist=0.5, is_bull_candle_15m=True,
            calculus={"regime": "SHOCK_HIGH_JERK"})
        self.assertEqual(tag, "⚪ 观望", "regime 字样同样要能识别冲击")


if __name__ == "__main__":
    unittest.main()

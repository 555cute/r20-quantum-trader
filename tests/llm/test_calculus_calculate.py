"""微积分计算主体：**顺序语义、样本不足的两种拒答、多周期 regime 聚合**（第 305 刀，收口 scripts/calculus/calculate.py）。

本模块是 `calculus_engine` 门面背后的**计算主体**（第四十五刀从门面逐字搬出），
四个函数全是纯函数。它的模块 docstring 把最重要的一条写在最后：

> ⚠️ **顺序语义**：所有函数都要求**按时间正序**（最新观测在最后）、只用已收盘 K 线、
> **无未来函数**。这些不是可选项 —— 打乱顺序会**静默产生前视偏差**，数值仍然"看起来正常"。

"静默"是关键词：顺序错了不会报错，只会让回测和实盘算出一个偏乐观的数。所以本刀
第一条就是把这个方向钉死（门面按 OKX 的 newest-first 传入，主体内部翻转）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.calculus import calculate as calc  # noqa: E402


def _series(n: int, start: float = 100.0, step: float = 0.5):
    return [start + i * step for i in range(n)]


def _candles(closes_chronological, *, spread: float = 1.0):
    """把**时间正序**的收盘价转成 OKX 真实回报形态（`[ts, high, low, close, vol]`，**新→旧**）。

    ⚠️ 顺序是这里的语义核心：OKX 回报**最新在前**，而 `calculate_multi_timeframe`
    内部会翻转回正序。所以夹具必须先构造正序、再翻成新→旧，才能断言"翻转是对的"。
    """
    rows = [[1_700_000_000 - i, c + spread, c - spread, c, 10.0]
            for i, c in enumerate(closes_chronological)]
    return list(reversed(rows))


def _feature(*, direction=1, acceleration=0.2, impulse=0.5, velocity=0.3, jerk=0.1,
             quality=0.9, integral=None, prob=None):
    """受控的 `calculate_calculus` 返回值，用来隔离多周期聚合逻辑。"""
    return {
        "valid": True,
        "direction": direction,
        "impulse": impulse,
        "velocity": velocity,
        "acceleration": acceleration,
        "jerk": jerk,
        "curvature": 0.1,
        "power": 0.2,
        "quality": quality,
        "definite_integrals": integral if integral is not None else {
            "valid": True, "energy_integral": 1.0, "deviation_area_integral": 2.0,
            "volume_action_integral": 3.0, "integral_regime": "X"},
        "probability_theory": prob if prob is not None else {
            "valid": True, "skewness": 0.5, "kurtosis": 3.0,
            "continuation_prob_pct": 60.0, "breakdown_prob_pct": 40.0,
            "var_95_pct": 1.5, "cvar_95_pct": 2.2, "is_fat_tail": False,
            "prob_regime": "Y"},
    }


class OrderingSemanticsTests(unittest.TestCase):
    """★ 顺序即正确性：打乱顺序不报错，只会静默给出前视偏差的数。"""

    def test_single_series_requires_chronological_input(self):
        up = _series(20, step=1.0)
        down = list(reversed(up))
        self.assertEqual(calc.calculate_calculus(up)["direction"], 1)
        self.assertEqual(calc.calculate_calculus(down)["direction"], -1)

    def test_multi_timeframe_requires_newest_first_input(self):
        # ★★★ 本刀最重要的一条：该函数**无条件** `list(reversed(candles))`
        #     （源码注释：`# OKX packages are newest-first; reverse to chronological order.`）
        #     ⇒ 调用方**必须**传新→旧。传正序不会报错，只会**静默反向**：
        #     一段上涨变成"下跌"，回测与实盘据此算出的方向、速度、加速度全部翻号。
        newest_first = _candles(_series(20, step=1.0))       # 正序上涨 → 翻成新→旧
        already_chronological = list(reversed(newest_first))

        good = calc.calculate_multi_timeframe({"15m": newest_first})
        self.assertEqual(good["timeframes"]["15m"]["direction"], 1)
        self.assertGreater(good["velocity"], 0)

        flipped = calc.calculate_multi_timeframe({"15m": already_chronological})
        self.assertEqual(flipped["timeframes"]["15m"]["direction"], -1)   # 静默反向
        self.assertLess(flipped["velocity"], 0)
        self.assertNotEqual(flipped["velocity"], good["velocity"])

    def test_rows_shorter_than_four_fields_are_dropped(self):
        rows = _candles(_series(20))
        rows[0] = [1, 2]                                     # 残缺行
        result = calc.calculate_multi_timeframe({"15m": rows})
        self.assertTrue(result["valid"])
        self.assertEqual(result["timeframes"]["15m"]["sample_size"], 19)

    def test_empty_input_is_data_unreliable(self):
        result = calc.calculate_multi_timeframe({})
        self.assertFalse(result["valid"])
        self.assertEqual(result["regime"], "DATA_UNRELIABLE")
        self.assertEqual(result["quality"], 0.0)
        self.assertEqual(result["timeframes"], {})

    def test_all_invalid_timeframes_are_data_unreliable(self):
        result = calc.calculate_multi_timeframe({"15m": _candles([1, 2, 3])})
        self.assertFalse(result["valid"])
        self.assertEqual(result["regime"], "DATA_UNRELIABLE")
        # 时间线明细仍要保留（否则运维看不到"哪条腿坏了"）
        self.assertIn("15m", result["timeframes"])


class InsufficientSampleTests(unittest.TestCase):
    """样本不足有两种**可区分**的拒答原因 —— 不合并成一个模糊的 valid=False。"""

    def test_too_few_closed_candles(self):
        result = calc.calculate_calculus(_series(5))
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "insufficient_closed_candles")
        self.assertEqual(result["sample_size"], 5)
        self.assertEqual(result["definite_integrals"], {})
        self.assertEqual(result["probability_theory"], {})

    def test_enough_candles_but_too_few_derivative_samples(self):
        # lag 大于样本量 ⇒ 平滑后做差分只剩不到 4 个点
        result = calc.calculate_calculus(_series(6), lag=5)
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "insufficient_derivative_samples")
        self.assertEqual(result["sample_size"], 6)
        self.assertEqual(result["definite_integrals"], {})

    def test_non_finite_values_do_not_count_toward_sample_size(self):
        values = _series(8) + [float("nan"), float("inf"), float("-inf"), None]
        result = calc.calculate_calculus(values)
        self.assertEqual(result["sample_size"], 8)

    def test_non_numeric_string_raises_instead_of_being_filtered(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：`_finite` 的过滤条件里
        #    `math.isfinite(float(v))` 会**先转再判** —— `None`/`nan`/`inf` 都能被剔除，
        #    但一个**非数字字符串**会直接抛 ValueError（不是被忽略）。
        #    也就是说数据源混进字符串时，整条微积分链路会崩而不是降级。
        with self.assertRaises(ValueError):
            calc.calculate_calculus(_series(8) + ["junk"])


class SingleSeriesContractTests(unittest.TestCase):
    def test_valid_result_exposes_the_documented_keys(self):
        result = calc.calculate_calculus(_series(30))
        self.assertTrue(result["valid"])
        for key in ("direction", "velocity", "acceleration", "jerk", "impulse",
                    "curvature", "power", "power_regime", "regime", "quality",
                    "atr_pct", "volatility", "sample_size"):
            self.assertIn(key, result, key)
        self.assertEqual(result["sample_size"], 30)
        for key in ("valid", "energy_integral", "deviation_area_integral",
                    "volume_action_integral", "integral_regime"):
            self.assertIn(key, result["definite_integrals"], key)
        for key in ("valid", "skewness", "kurtosis", "continuation_prob_pct",
                    "breakdown_prob_pct", "var_95_pct", "cvar_95_pct",
                    "is_fat_tail", "prob_regime"):
            self.assertIn(key, result["probability_theory"], key)

    def test_direction_sign_follows_the_trend(self):
        self.assertEqual(calc.calculate_calculus(_series(30, step=1.0))["direction"], 1)
        self.assertEqual(calc.calculate_calculus(_series(30, step=-1.0))["direction"], -1)

    def test_quality_is_bounded(self):
        for n in (6, 20, 100, 500):
            quality = calc.calculate_calculus(_series(n))["quality"]
            self.assertGreaterEqual(quality, 0.0)
            self.assertLessEqual(quality, 1.0)

    def test_is_deterministic(self):
        series = _series(40)
        self.assertEqual(calc.calculate_calculus(series), calc.calculate_calculus(list(series)))


class AtrToleranceTests(unittest.TestCase):
    """ATR 逐根计算：**单根坏数据只丢那一根**，不许把整段 ATR 归零。"""

    def test_atr_computed_when_highs_and_lows_line_up(self):
        closes = _series(20)
        weights = [c + 2 for c in closes], [c - 2 for c in closes]
        result = calc.calculate_calculus(closes, weights[0], weights[1])
        self.assertGreater(result["atr_pct"], 0.0)

    def test_bad_high_row_is_skipped_but_atr_still_computed(self):
        closes = _series(20)
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        bad_highs = list(highs)
        bad_highs[3] = "not-a-number"          # float() 抛 ValueError ⇒ continue
        result = calc.calculate_calculus(closes, bad_highs, lows)
        self.assertTrue(result["valid"])
        self.assertGreater(result["atr_pct"], 0.0)
        # 与"把那根换成合法值"相比只应有极小差异（确证是跳过而不是归零）
        baseline = calc.calculate_calculus(closes, highs, lows)["atr_pct"]
        self.assertNotEqual(result["atr_pct"], 0.0)
        self.assertAlmostEqual(result["atr_pct"], baseline, delta=baseline)

    def test_all_rows_bad_leaves_atr_at_zero(self):
        closes = _series(20)
        result = calc.calculate_calculus(closes, ["x"] * 20, ["y"] * 20)
        self.assertEqual(result["atr_pct"], 0.0)

    def test_length_mismatch_leaves_atr_at_zero(self):
        closes = _series(20)
        result = calc.calculate_calculus(closes, [c + 1 for c in closes], [c - 1 for c in closes[:-1]])
        self.assertEqual(result["atr_pct"], 0.0)

    def test_missing_highs_or_lows_leaves_atr_at_zero(self):
        closes = _series(20)
        self.assertEqual(calc.calculate_calculus(closes)["atr_pct"], 0.0)
        self.assertEqual(calc.calculate_calculus(closes, highs=[c + 1 for c in closes])["atr_pct"], 0.0)


class MultiTimeframeRegimeTests(unittest.TestCase):
    """三条 regime 分支的**聚合判据**（在隔离掉 `calculate_calculus` 后逐条钉）。"""

    def _run(self, features_by_tf):
        with patch.object(calc, "calculate_calculus",
                          side_effect=lambda closes, highs=None, lows=None, vols=None:
                          features_by_tf.pop(0)):
            return calc.calculate_multi_timeframe({"15m": _candles(_series(20)),
                                                   "1H": _candles(_series(20))})

    def test_bull_accelerating(self):
        result = self._run([_feature(direction=1, acceleration=0.2),
                            _feature(direction=1, acceleration=0.2)])
        self.assertEqual(result["regime"], "BULL_ACCELERATING")

    def test_bear_accelerating(self):
        result = self._run([_feature(direction=-1, acceleration=-0.2),
                            _feature(direction=-1, acceleration=-0.2)])
        self.assertEqual(result["regime"], "BEAR_ACCELERATING")

    def test_range_low_velocity_when_votes_are_inconclusive(self):
        result = self._run([_feature(direction=1, acceleration=0.2),
                            _feature(direction=-1, acceleration=-0.2)])
        self.assertEqual(result["regime"], "RANGE_LOW_VELOCITY")

    def test_bull_accelerating_needs_both_votes_and_acceleration(self):
        # 票数够但加速不足 ⇒ 不该判 BULL_ACCELERATING
        result = self._run([_feature(direction=1, acceleration=0.01),
                            _feature(direction=1, acceleration=0.01)])
        self.assertNotEqual(result["regime"], "BULL_ACCELERATING")

    def test_decaying_and_transition_branches(self):
        # votes>0 且 acceleration<0 ⇒ BULL_DECELERATING
        result = self._run([_feature(direction=1, acceleration=-0.2),
                            _feature(direction=1, acceleration=-0.2)])
        self.assertEqual(result["regime"], "BULL_DECELERATING")

    def test_aggregates_are_averaged_over_valid_timeframes(self):
        result = self._run([_feature(velocity=0.2, acceleration=0.1, quality=0.8),
                            _feature(velocity=0.4, acceleration=0.3, quality=1.0)])
        self.assertEqual(result["velocity"], 0.3)
        self.assertEqual(result["acceleration"], 0.2)
        self.assertEqual(result["quality"], 0.9)
        # jerk 取的是**绝对值最大**那个，不是平均
        self.assertEqual(result["max_abs_jerk"], 0.1)

    def test_max_abs_jerk_takes_the_largest_magnitude(self):
        result = self._run([_feature(jerk=-0.9), _feature(jerk=0.2)])
        self.assertEqual(result["max_abs_jerk"], 0.9)

    def test_integral_and_probability_aggregation(self):
        result = self._run([
            _feature(integral={"valid": True, "energy_integral": 2.0,
                               "deviation_area_integral": 4.0,
                               "volume_action_integral": 6.0, "integral_regime": "A"},
                     prob={"valid": True, "skewness": 1.0, "kurtosis": 4.0,
                           "continuation_prob_pct": 70.0, "breakdown_prob_pct": 30.0,
                           "var_95_pct": 2.0, "cvar_95_pct": 3.0,
                           "is_fat_tail": True, "prob_regime": "B"}),
            _feature(integral={"valid": True, "energy_integral": 4.0,
                               "deviation_area_integral": 6.0,
                               "volume_action_integral": 8.0, "integral_regime": "A"},
                     prob={"valid": True, "skewness": 3.0, "kurtosis": 6.0,
                           "continuation_prob_pct": 50.0, "breakdown_prob_pct": 50.0,
                           "var_95_pct": 3.0, "cvar_95_pct": 4.0,
                           "is_fat_tail": False, "prob_regime": "B"}),
        ])
        integrals = result["definite_integrals"]
        self.assertEqual(integrals["energy_integral"], 3.0)
        self.assertEqual(integrals["deviation_area_integral"], 5.0)
        self.assertEqual(integrals["volume_action_integral"], 7.0)
        probs = result["probability_theory"]
        self.assertEqual(probs["skewness"], 2.0)
        self.assertEqual(probs["kurtosis"], 5.0)
        self.assertEqual(probs["continuation_prob_pct"], 60.0)
        self.assertEqual(probs["var_95_pct"], 3.0)       # 取 max
        self.assertEqual(probs["cvar_95_pct"], 4.0)
        self.assertTrue(probs["is_fat_tail"])            # any()

    def test_missing_integral_or_probability_blocks_use_neutral_defaults(self):
        result = self._run([_feature(integral={"valid": False}, prob={"valid": False}),
                            _feature(integral={"valid": False}, prob={"valid": False})])
        self.assertEqual(result["definite_integrals"]["energy_integral"], 0.0)
        self.assertEqual(result["probability_theory"]["continuation_prob_pct"], 50.0)
        self.assertEqual(result["probability_theory"]["var_95_pct"], 1.5)
        self.assertFalse(result["probability_theory"]["is_fat_tail"])

    def test_invalid_timeframes_are_excluded_from_aggregation(self):
        seen = []

        def fake(closes, highs=None, lows=None, vols=None):
            seen.append(len(closes))
            return _feature() if len(seen) == 2 else {"valid": False, "reason": "nope"}

        with patch.object(calc, "calculate_calculus", side_effect=fake):
            result = calc.calculate_multi_timeframe({"15m": _candles(_series(20)),
                                                     "1H": _candles(_series(20))})
        self.assertTrue(result["valid"])
        self.assertFalse(result["timeframes"]["15m"]["valid"])
        self.assertEqual(result["quality"], 0.9)          # 只按有效周期平均


class RealSeriesIntegrationTests(unittest.TestCase):
    """不打桩、走真实数学原语，确证多周期链路端到端可用。"""

    def test_uptrend_series_yields_valid_aggregate(self):
        result = calc.calculate_multi_timeframe({
            "15m": _candles(_series(40, step=0.8)),
            "1H": _candles(_series(40, step=1.5)),
        })
        self.assertTrue(result["valid"])
        self.assertIn(result["regime"], {
            "BULL_ACCELERATING", "BEAR_ACCELERATING", "RANGE_LOW_VELOCITY",
            "BULL_DECELERATING", "BEAR_DECELERATING", "MIXED_TRANSITION"})
        self.assertEqual(result["timeframes"]["15m"]["sample_size"], 40)

    def test_downtrend_votes_negative(self):
        result = calc.calculate_multi_timeframe({
            "15m": _candles(_series(40, step=-0.8)),
            "1H": _candles(_series(40, step=-1.5)),
        })
        self.assertTrue(result["valid"])
        self.assertEqual(result["timeframes"]["15m"]["direction"], -1)

    def test_timeframe_keys_are_preserved_verbatim(self):
        result = calc.calculate_multi_timeframe({"5m": _candles(_series(30)),
                                                 "4H": _candles(_series(30))})
        self.assertEqual(sorted(result["timeframes"]), ["4H", "5m"])


if __name__ == "__main__":
    unittest.main()

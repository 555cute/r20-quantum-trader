#!/usr/bin/env python3
"""
Comprehensive Quant System Mathematical & Probabilistic Test Suite
Validates causal calculus engine, definite integrals, probability theory,
factor library integration, multi-factor scoring and pyramiding gateways.
"""

import os
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

# Add scripts directory
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from calculus_engine import (
    calculate_calculus,
    calculate_multi_timeframe,
    calculate_definite_integrals,
    calculate_probability_theory,
    classify_regime,
    classify_integral_regime,
    classify_probability_regime,
    _normal_cdf,
    _ema,
    _diff,
    _normalise
)
import factor_library
import ai_factor_trader
from tests.okx_algo_http_fixture import install_http


class CalculusEngineMathTest(unittest.TestCase):
    """Test mathematical accuracy and causality of calculus computations."""

    def test_monotonic_bullish_acceleration(self):
        prices = [100.0, 101.0, 103.0, 106.0, 110.0, 115.0, 122.0, 131.0, 142.0, 155.0]
        res = calculate_calculus(prices)
        self.assertTrue(res["valid"])
        self.assertGreater(res["velocity"], 0.0)
        self.assertGreater(res["impulse"], 0.0)
        self.assertEqual(res["direction"], 1)

    def test_monotonic_bearish_acceleration(self):
        prices = [155.0, 142.0, 131.0, 122.0, 115.0, 110.0, 106.0, 103.0, 101.0, 98.0]
        res = calculate_calculus(prices)
        self.assertTrue(res["valid"])
        self.assertLess(res["velocity"], 0.0)
        self.assertLess(res["impulse"], 0.0)
        self.assertEqual(res["direction"], -1)

    def test_decelerating_top_fomo_detection(self):
        prices = [100.0, 110.0, 118.0, 123.0, 125.0, 125.5, 125.6, 125.65]
        res = calculate_calculus(prices)
        self.assertTrue(res["valid"])
        self.assertLess(res["acceleration"], 0.0, "Decelerating rally must yield negative acceleration")

    def test_decelerating_bottom_panics_detection(self):
        prices = [200.0, 190.0, 182.0, 178.0, 177.0, 176.8, 176.7]
        res = calculate_calculus(prices)
        self.assertTrue(res["valid"])
        self.assertGreater(res["acceleration"], 0.0, "Decelerating plunge must yield positive acceleration")

    def test_strict_causality(self):
        history = [100.0, 100.2, 100.5, 100.9, 101.4, 102.0, 102.7]
        res_t1 = calculate_calculus(history)
        
        future_candle = [101.5]
        res_t2 = calculate_calculus(history + future_candle)
        
        self.assertTrue(res_t1["valid"])
        self.assertTrue(res_t2["valid"])
        self.assertNotEqual(res_t1["velocity"], res_t2["velocity"])

    def test_calculus_curvature_and_power_dynamics(self):
        # Monotonic accelerating prices: velocity > 0, acceleration > 0 => power > 0
        accel_prices = [100.0, 101.0, 103.0, 106.0, 110.0, 115.0, 122.0, 131.0, 142.0]
        res_acc = calculate_calculus(accel_prices)
        self.assertTrue(res_acc["valid"])
        self.assertGreater(res_acc["power"], 0.0, "Accelerating uptrend must have positive kinetic power flux")
        self.assertGreaterEqual(res_acc["curvature"], 0.0)

        # Decelerating top: velocity > 0, acceleration < 0 => power < 0 (kinetic exhaustion)
        decel_prices = [100.0, 110.0, 118.0, 123.0, 125.0, 125.5, 125.6, 125.65]
        res_dec = calculate_calculus(decel_prices)
        self.assertTrue(res_dec["valid"])
        self.assertLess(res_dec["power"], 0.0, "Decelerating rally must yield negative kinetic power (exhaustion)")
        self.assertIn(res_dec["power_regime"], ["KINETIC_EXHAUSTION", "HIGH_CURVATURE_INFLECTION", "STEADY_FLUX"])


class DefiniteIntegralsTest(unittest.TestCase):
    """Test trapezoidal definite integration of displacement energy and deviation area."""

    def test_positive_displacement_energy_integral(self):
        # Monotonically rising prices: trapezoidal integral of velocity must be positive
        prices = [100.0, 102.0, 105.0, 109.0, 114.0, 120.0, 127.0, 135.0]
        res = calculate_definite_integrals(prices, window=8)
        self.assertTrue(res["valid"])
        self.assertGreater(res["energy_integral"], 0.0)
        self.assertGreater(res["deviation_area_integral"], 0.0)

    def test_negative_displacement_energy_integral(self):
        # Monotonically falling prices: trapezoidal integral of velocity must be negative
        prices = [135.0, 127.0, 120.0, 114.0, 109.0, 105.0, 102.0, 100.0]
        res = calculate_definite_integrals(prices, window=8)
        self.assertTrue(res["valid"])
        self.assertLess(res["energy_integral"], 0.0)
        self.assertLess(res["deviation_area_integral"], 0.0)

    def test_volume_action_integral(self):
        prices = [100.0, 105.0, 110.0, 115.0]
        vols = [1000.0, 2000.0, 3000.0, 4000.0]
        res = calculate_definite_integrals(prices, vols=vols, window=4)
        self.assertTrue(res["valid"])
        self.assertGreater(res["volume_action_integral"], 0.0)


class ProbabilityTheoryTest(unittest.TestCase):
    """Test stochastic moments, fat tails and conditional continuation probability."""

    def test_normal_cdf_function(self):
        self.assertAlmostEqual(_normal_cdf(0.0), 0.5, places=4)
        self.assertGreater(_normal_cdf(1.96), 0.97)
        self.assertLess(_normal_cdf(-1.96), 0.03)

    def test_skewness_and_kurtosis_calculation(self):
        # Right-skewed returns with positive outlier
        returns = [0.01, 0.02, -0.01, 0.005, 0.012, -0.008, 0.08] # 0.08 is fat right tail
        res = calculate_probability_theory(returns, velocity=0.5, acceleration=0.2)
        self.assertTrue(res["valid"])
        self.assertGreater(res["skewness"], 0.0, "Positive outlier must induce positive skewness")
        self.assertGreater(res["kurtosis"], 0.0, "Outlier must induce positive excess kurtosis")
        self.assertGreater(res["continuation_prob_pct"], 50.0)
        self.assertGreater(res["var_95_pct"], 0.0)

    def test_fat_tail_detection(self):
        # Extreme fat tail shock: small variance background with large shock outlier
        shock_returns = [0.001, -0.001, 0.002, -0.002, 0.001, 0.002, -0.001, 0.15]
        res = calculate_probability_theory(shock_returns)
        self.assertTrue(res["valid"])
        self.assertTrue(res["is_fat_tail"], f"Kurtosis {res.get('kurtosis')} should trigger fat tail")


class MultiTimeframeIntegrationTest(unittest.TestCase):
    """Test 15M, 1H, 4H confluence and OKX reverse candle order handling."""

    def test_okx_order_inversion(self):
        chronological = [[str(i), "101", "99", str(100.0 + i), "10"] for i in range(10)]
        okx_payload = list(reversed(chronological))
        
        res = calculate_multi_timeframe({
            "15M": okx_payload,
            "1H": okx_payload,
            "4H": okx_payload
        })
        self.assertTrue(res["valid"])
        self.assertGreater(res["velocity"], 0.0)
        self.assertIn("15M", res["timeframes"])
        self.assertTrue(res["timeframes"]["15M"]["valid"])
        self.assertIn("definite_integrals", res)
        self.assertIn("probability_theory", res)
        self.assertEqual(res["definite_integrals"]["regime"], "POSITIVE_ENERGY_EXPANSION")
        self.assertIn(res["probability_theory"]["regime"], {
            "HIGH_PROB_BULL_CONTINUATION", "POSITIVE_SKEW_UPSIDE", "NEGATIVE_SKEW_DOWNSIDE", "EXTREME_FAT_TAIL_RISK"
        })

    def test_aggregate_regime_classifiers_prioritise_risk(self):
        self.assertEqual(classify_integral_regime(1.2, 0.8), "POSITIVE_ENERGY_EXPANSION")
        self.assertEqual(classify_integral_regime(0.2, 3.0), "OVERSTRETCHED_MEAN_REVERSION")
        self.assertEqual(
            classify_probability_regime(-0.8, 1.0, 78.0, 22.0, False),
            "NEGATIVE_SKEW_DOWNSIDE",
        )
        self.assertEqual(
            classify_probability_regime(0.1, 0.2, 78.0, 22.0, False),
            "HIGH_PROB_BULL_CONTINUATION",
        )


class FactorLibraryIntegrationTest(unittest.TestCase):
    """Test Pillar 6 integration in factor_library.py."""

    def test_factor_library_structure_contains_math_prob_foundations(self):
        item = {"instId": "BTC-USDT-SWAP", "name": "BTC", "type": "crypto", "precision": 1}
        factors = factor_library.compute_instrument_factors(item, {})
        self.assertIn("calculus_dynamics", factors)
        self.assertIn("curvature", factors["calculus_dynamics"])
        self.assertIn("power", factors["calculus_dynamics"])
        self.assertIn("power_regime", factors["calculus_dynamics"])
        self.assertIn("definite_integrals", factors)
        self.assertIn("probability_theory", factors)
        
        d_int = factors["definite_integrals"]
        self.assertIn("energy_integral", d_int)
        self.assertIn("deviation_area_integral", d_int)
        
        p_th = factors["probability_theory"]
        self.assertIn("continuation_prob_pct", p_th)
        self.assertIn("var_95_pct", p_th)


class AiFactorTraderMathProbTest(unittest.TestCase):
    """Test scoring and strategy setup filters in ai_factor_trader.py."""

    def test_evaluate_signal_with_calculus_and_prob(self):
        f = {
            "instId": "BTC-USDT-SWAP",
            "name": "BTC",
            "type": "crypto",
            "precision": 1,
            "price": 60500.0,
            "ema9": 60100.0,
            "ema21": 59800.0,
            "ema55": 59000.0,
            "ema21_slope_pct": 0.05,
            "rsi": 62.0,
            "rsi_7": 65.0,
            "vwap_bias": 0.2,
            "macd_hist": 15.0,
            "macd_accel": 3.0,
            "obv_flow": "BULL_FLOW",
            "vol_ratio": 1.5,
            "market_regime": "BULL_TREND",
            "structure_1h": "HH_HL",
            "is_bull_candle_15m": True,
            "is_bear_candle_15m": False,
            "lower_wick_ratio": 0.1,
            "upper_wick_ratio": 0.1,
            "sentiment_score": 0.5,
            "market_data_valid": True,
            "calculus": {
                "valid": True,
                "velocity": 0.65,
                "acceleration": 0.45,
                "impulse": 1.20,
                "max_abs_jerk": 0.2,
                "regime": "BULL_ACCELERATING",
                "quality": 0.9,
                "definite_integrals": {
                    "energy_integral": 1.5,
                    "deviation_area_integral": 0.8
                },
                "probability_theory": {
                    "continuation_prob_pct": 78.0,
                    "breakdown_prob_pct": 22.0,
                    "var_95_pct": 1.2,
                    "is_fat_tail": False
                }
            }
        }
        score, action, reasons, strat_tag, strat_desc = ai_factor_trader.evaluate_asset_signal(f)
        self.assertGreater(score, 2.2)
        self.assertEqual(action, "BUY_LONG")
        self.assertEqual(strat_tag, "🚀 动量突破")


class AiFactorTraderPositionProtectionTest(unittest.TestCase):
    def setUp(self):
        self.http = install_http(self, ai_factor_trader)
        self.http.rows = [self.http.row(inst='SOL-USDT-SWAP', size='4', sl='101')]

    def _factor(self, price=99.0):
        return {
            "market_data_valid": True, "instId": "SOL-USDT-SWAP", "name": "SOL",
            "price": price, "type": "crypto", "atr": 1.0, "precision": 2, "ctVal": 1.0,
        }

    def test_losing_position_closes_when_tracker_hard_stop_is_breached(self):
        position={"pos":4.0,"side":"long","avgPx":103.55,"upl":-18.0}
        trackers={"SOL-USDT-SWAP_long":{"entryTs":1,"trailingStopPx":101.81,"highWaterMark":104.2,"lowWaterMark":99.0}}
        actions=[]
        with patch.object(ai_factor_trader,"record_trade"), patch.object(ai_factor_trader,"add_stop_cooldown"), patch.object(ai_factor_trader,"notify_trade_close") as notify_close:
            closed,reason=ai_factor_trader.manage_position_tp_and_trailing(self._factor(),position,trackers,"2026-09-02 15:00:00",actions)
        self.assertTrue(closed); self.assertEqual(reason,"已硬止损")
        self.assertEqual(self.http.calls('/api/v5/trade/close-position'), [
            ('POST', {'instId': 'SOL-USDT-SWAP', 'mgnMode': 'cross', 'posSide': 'long', 'autoCxl': 'true'})])
        self.assertNotIn("SOL-USDT-SWAP_long",trackers)
        self.assertTrue(any("触发硬止损" in item for item in actions))
        if notify_close is not None:
            notify_close.assert_called_once_with(inst="SOL", pnl=-18.0, stage="硬止损平仓", exit_px=99.0)

    def test_losing_position_above_hard_stop_remains_open(self):
        position={"pos":4.0,"side":"long","avgPx":103.55,"upl":-4.0}
        now=int(ai_factor_trader.time.time())
        trackers={"SOL-USDT-SWAP_long":{"entryTs":now,"trailingStopPx":101.81,"takeProfitPx":106.45,"highWaterMark":104.2,"lowWaterMark":102.5}}
        actions=[]
        with patch.object(ai_factor_trader,"notify_trade_close"):
            closed,reason=ai_factor_trader.manage_position_tp_and_trailing(self._factor(102.5),position,trackers,"2026-09-02 15:00:00",actions)
        self.assertFalse(closed); self.assertEqual(reason,"持仓监控中"); self.assertEqual(self.http.calls('/api/v5/trade/close-position'), [])

    def test_cloud_oco_gap_is_repaired_and_verified(self):
        self.http.rows = []
        ok, detail = ai_factor_trader.ensure_cloud_position_protection("SOL-USDT-SWAP", "long", 4, 106, 101)
        self.assertTrue(ok); self.assertIn("repaired and verified", detail)
        method, body = self.http.calls('/api/v5/trade/order-algo')[0]
        self.assertEqual(method, 'POST')
        self.assertEqual(body, dict(instId='SOL-USDT-SWAP', side='sell', sz='4', posSide='long',
            tdMode='cross', ordType='oco', tpTriggerPx='106', slTriggerPx='101', tpOrdPx='-1',
            slOrdPx='-1', reduceOnly='true', cxlOnClosePos='true'))
        self.assertEqual(len(self.http.calls('/api/v5/trade/orders-algo-pending')), 2)

    def test_stale_order_query_failure_aborts_cleanup(self):
        with patch.object(ai_factor_trader,"okx_rest") as rest:
            rest.pending_orders.side_effect=RuntimeError("timeout")
            ok,detail=ai_factor_trader.clean_stale_open_orders()
        self.assertFalse(ok); self.assertIn("timeout",detail)

    def test_stale_order_cancel_fail_closed_after_rest_migration(self):
        order={"instId":"SOL-USDT-SWAP","ordId":"11","state":"live","cTime":"1"}
        with patch.object(ai_factor_trader,"okx_rest") as rest, patch.object(ai_factor_trader.time,"time",return_value=1000):
            rest.pending_orders.return_value=[order]
            rest.cancel_order.side_effect=RuntimeError("rejected")
            ok,detail=ai_factor_trader.clean_stale_open_orders()
        self.assertFalse(ok); self.assertIn("rejected",detail)
        rest.cancel_order.assert_called_once_with("SOL-USDT-SWAP","11")

    def test_cloud_oco_failure_closes_position_fail_closed(self):
        position={"pos":4.0,"side":"long","avgPx":103.55,"upl":-4.0}
        now=int(ai_factor_trader.time.time())
        trackers={"SOL-USDT-SWAP_long":{"entryTs":now,"trailingStopPx":101.81,"takeProfitPx":106.45,"highWaterMark":104.2,"lowWaterMark":102.5}}
        actions=[]
        self.http.failures['/api/v5/trade/orders-algo-pending'] = {'code':'50011', 'msg':'repair failed'}
        with patch.object(ai_factor_trader,"record_trade"), patch.object(ai_factor_trader,"add_stop_cooldown"), patch.object(ai_factor_trader,"notify_trade_close") as notify_close:
            closed,reason=ai_factor_trader.manage_position_tp_and_trailing(self._factor(102.5),position,trackers,"2026-09-02 15:00:00",actions)
        self.assertTrue(closed); self.assertEqual(reason,"保护失效安全退出")
        self.assertEqual(self.http.calls('/api/v5/trade/close-position'), [
            ('POST', {'instId': 'SOL-USDT-SWAP', 'mgnMode': 'cross', 'posSide': 'long', 'autoCxl': 'true'})])
        self.assertNotIn("SOL-USDT-SWAP_long",trackers)
        if notify_close is not None:
            notify_close.assert_called_once_with(inst="SOL", pnl=-4.0, stage="云端保护失效退出", exit_px=102.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)

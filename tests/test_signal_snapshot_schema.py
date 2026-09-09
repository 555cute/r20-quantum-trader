"""开仓数理快照 schema 兼容回归钉扎（2026-09-09）。

事故：build_signal_snapshot 只认 factor_library 块结构（calculus_dynamics 等），
而开仓路径传入的执行层 f 携带的是 calculate_multi_timeframe 聚合结构
（f["calculus"]），导致 signal_journal 全部条目 22/24 字段为 None，
自进化复盘对所有样本输出「数理快照不可观测」、逐单因果归因失效。
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import ai_factor_trader as aft

EXEC_LAYER_MULTI = {
    "valid": True,
    "velocity": 1.61, "acceleration": 0.42, "max_abs_jerk": 2.5, "impulse": -0.9,
    "curvature": 0.2, "power": 1.4, "power_regime": "KINETIC_ACCELERATING",
    "regime": "BULL_ACCELERATING", "quality": 0.87,
    "definite_integrals": {"energy_integral": -0.61, "deviation_area_integral": -3.2, "volume_action_integral": 0.1},
    "probability_theory": {
        "continuation_prob_pct": 66.9, "breakdown_prob_pct": 8.1,
        "var_95_pct": 0.57, "cvar_95_pct": 0.71, "prob_regime": "TREND_PERSISTENT", "is_fat_tail": False,
    },
}

FACTOR_LIBRARY_F = {
    "instId": "SOL-USDT-SWAP", "name": "SOL", "price": 100.0, "atr": 1.0,
    "calculus_dynamics": {"velocity": 0.3, "acceleration": 0.64, "jerk": 0.22, "impulse": -1.05,
                          "quality": 0.8, "regime": "RANGE"},
    "probability_theory": {"continuation_prob_pct": 66.9, "var_95_pct": 0.57, "cvar_95_pct": 0.71},
    "definite_integrals": {"energy_integral": -1.0, "deviation_area_integral": -4.9},
    "trend_momentum": {"adx": 25.0, "rsi": 55.0},
    "microstructure": {"funding_rate": 0.01},
    "smart_money_derivatives": {"net_flow": 123.0},
    "composite_alpha_score": 3.2,
}


class SignalSnapshotSchemaTests(unittest.TestCase):
    def test_exec_layer_calculus_schema_populates_math(self):
        f = {"instId": "BTC-USDT-SWAP", "name": "BTC", "price": 78000.0, "atr": 370.0,
             "rsi": 55.0, "calculus": EXEC_LAYER_MULTI}
        snap = aft.build_signal_snapshot(f)
        self.assertEqual(snap["velocity"], 1.61)
        self.assertEqual(snap["jerk"], 2.5)               # max_abs_jerk → jerk
        self.assertEqual(snap["energy_integral"], -0.61)
        self.assertEqual(snap["deviation_area_integral"], -3.2)
        self.assertEqual(snap["continuation_prob_pct"], 66.9)
        self.assertEqual(snap["var_95_pct"], 0.57)
        self.assertEqual(snap["cvar_95_pct"], 0.71)
        self.assertEqual(snap["is_fat_tail"], False)
        self.assertEqual(snap["dynamics_quality"], 0.87)
        self.assertEqual(snap["rsi"], 55.0)
        none_keys = [k for k, v in snap.items() if v is None]
        self.assertLessEqual(len(none_keys), 6)           # 至少 18/24 有值（旧版只有 2）

    def test_factor_library_schema_still_works(self):
        snap = aft.build_signal_snapshot(dict(FACTOR_LIBRARY_F))
        self.assertEqual(snap["velocity"], 0.3)
        self.assertEqual(snap["adx"], 25.0)
        self.assertEqual(snap["funding_rate"], 0.01)
        self.assertEqual(snap["smart_money_net"], 123.0)
        self.assertEqual(snap["composite_alpha_score"], 3.2)

    def test_enrichment_from_factor_snapshot_file(self):
        lib = {"instruments": [{
            "instId": "BTC-USDT-SWAP", "name": "BTC",
            "trend_momentum": {"adx_1h": 24.4},
            "smart_money_derivatives": {"funding_rate_pct": 0.0094, "smart_money_flow_usd": 123.0},
            "composite_alpha_score": 12.0,
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "factor_library_snapshot.json"), "w", encoding="utf-8") as f:
                json.dump(lib, f)
            f_in = {"instId": "BTC-USDT-SWAP", "name": "BTC", "price": 1.0, "atr": 1.0,
                    "calculus": EXEC_LAYER_MULTI}
            with patch.object(aft, "DATA_DIR", tmp):
                snap = aft.build_signal_snapshot(f_in)
        self.assertEqual(snap["adx"], 24.4)               # 执行层无 ADX → 因子库补齐
        self.assertEqual(snap["funding_rate"], 0.0094)
        self.assertEqual(snap["composite_alpha_score"], 12.0)
        self.assertEqual(snap["smart_money_net"], 123.0)

    def test_enrichment_failure_is_silent(self):
        f_in = {"instId": "X-USDT-SWAP", "name": "X", "price": 1.0, "atr": 1.0}
        with patch.object(aft, "DATA_DIR", "/nonexistent-dir-xyz"):
            snap = aft.build_signal_snapshot(f_in)       # 不得抛异常
        self.assertIn("velocity", snap)


if __name__ == "__main__":
    unittest.main()

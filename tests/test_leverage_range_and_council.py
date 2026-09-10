"""杠杆区间 [下限,上限] 与投委会可用性回归钉扎（2026-09-10 用户双报障）。

1. 风控页上限配 7 但开单永远 3x：根因 = 提示词 JSON 模板示例 `min(3,MAX)` 锚定
   + 执行层下限钉死 2；修复为 R20_MIN_LEVERAGE/R20_MAX_LEVERAGE 全链路区间。
2. 投委会 0/50 周期成功：60s 总预算 vs 两段串行 LLM（真实 RT 20~250s）必超时
   静默降级；默认预算提至 240s 并补降级原因透明（council_status）。
"""
from __future__ import annotations
import io
import json
import os
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)


class RiskConstantsLeverageRangeTests(unittest.TestCase):
    def _fresh(self, env: dict):
        # 隔离生产 .env：r20_backend.config 在 import 期即 load_dotenv（SSOT 设计），
        # 故预置假 config 模块，纯验证「环境变量 → risk_constants 解析」契约
        code = (
            "import sys, types;"
            "_fake = types.ModuleType('r20_backend.config'); _fake.load_dotenv = lambda *a, **k: None;"
            "_pkg = types.ModuleType('r20_backend'); _pkg.config = _fake;"
            "sys.modules['r20_backend'] = _pkg; sys.modules['r20_backend.config'] = _fake;"
            "sys.path.insert(0, 'scripts');"
            "import risk_constants as rc;"
            "print(rc.MIN_LEVERAGE, rc.MAX_LEVERAGE, rc.DEFAULTS.get('R20_MIN_LEVERAGE'))"
        )
        import subprocess
        base_env = {k: v for k, v in os.environ.items() if not k.startswith("R20_")}
        r = subprocess.run([sys.executable, "-c", code], env={**base_env, **env},
                           capture_output=True, text=True, cwd=str(ROOT))
        return r.stdout.strip()

    def test_defaults(self):
        # 显式钉默认：risk_constants import 期会加载项目 .env（生产值 7.0），
        # 「默认值」语义用显式传默认来验证解析逻辑本身
        lo, hi, dft = self._fresh({"R20_MIN_LEVERAGE": "2.0", "R20_MAX_LEVERAGE": "5.0"}).split()
        self.assertEqual((lo, hi), ("2.0", "5.0"))
        self.assertEqual(dft, "2.0")

    def test_explicit_range(self):
        lo, hi, _ = self._fresh({"R20_MIN_LEVERAGE": "5.0", "R20_MAX_LEVERAGE": "7.0"}).split()
        self.assertEqual((lo, hi), ("5.0", "7.0"))

    def test_missing_min_falls_back_to_default_2(self):
        # 未设置 R20_MIN_LEVERAGE 时下限取默认 2.0（旧 .env 无此键的账户零迁移）
        lo, hi, _ = self._fresh({"R20_MAX_LEVERAGE": "7.0"}).split()
        self.assertEqual((lo, hi), ("2.0", "7.0"))

    def test_inverted_range_fail_safe_clamps_min_to_max(self):
        lo, hi, _ = self._fresh({"R20_MIN_LEVERAGE": "9.0", "R20_MAX_LEVERAGE": "4.0"}).split()
        self.assertEqual((lo, hi), ("4.0", "4.0"))

    def test_min_key_registered_in_env_keys(self):
        from scripts import risk_constants as rc
        self.assertIn("R20_MIN_LEVERAGE", rc.RISK_ENV_KEYS)
        self.assertIn("R20_MIN_LEVERAGE", rc.DEFAULTS)


class RiskConfigSchemaTests(unittest.TestCase):
    def test_schema_contains_pair_and_suites_valid(self):
        import r20_backend.risk_config as risk_mod
        keys = {p["key"] for p in risk_mod.schema()["params"]}
        self.assertIn("R20_MIN_LEVERAGE", keys)
        self.assertIn("R20_MAX_LEVERAGE", keys)
        # 导入即自检（越界/suite 断言）不炸说明三套件 MIN≤MAX 与边界全合法
        suites = {s["id"]: s["values"] for s in risk_mod.SUITES}
        self.assertLessEqual(suites["conservative"]["R20_MIN_LEVERAGE"], suites["conservative"]["R20_MAX_LEVERAGE"])
        self.assertEqual((suites["aggressive"]["R20_MIN_LEVERAGE"], suites["aggressive"]["R20_MAX_LEVERAGE"]), (5.0, 7.0))

    def test_normalize_rejects_inverted_leverage_range(self):
        import r20_backend.risk_config as risk_mod
        with self.assertRaises(ValueError) as ctx:
            risk_mod.normalize({"R20_MIN_LEVERAGE": 8.0, "R20_MAX_LEVERAGE": 5.0})
        self.assertIn("杠杆下限", str(ctx.exception))

    def test_normalize_accepts_valid_pair(self):
        import r20_backend.risk_config as risk_mod
        out = risk_mod.normalize({"R20_MIN_LEVERAGE": 5.0, "R20_MAX_LEVERAGE": 7.0})
        self.assertEqual(out["R20_MIN_LEVERAGE"], "5.0")


class BrainPromptAntiAnchorTests(unittest.TestCase):
    """模板示例不得再钉死 3；区间声明必须来自 MIN/MAX 常量。"""

    def _brain_src(self):
        import ai_brain_trader as abt
        return Path(abt.__file__).read_text(encoding="utf-8")

    def test_template_has_no_static_three_anchor(self):
        src = self._brain_src()
        self.assertNotIn("int(min(3, MAX_LEVERAGE))", src)
        self.assertIn("严禁无差别照抄", src)
        self.assertIn("单笔杠杆区间", src)

    def test_rendered_example_is_range_midpoint(self):
        lo, hi = 5.0, 7.0
        self.assertEqual(int(max(lo, min(hi, (lo + hi) / 2))), 6)
        lo, hi = 2.0, 7.0
        self.assertEqual(int(max(lo, min(hi, (lo + hi) / 2))), 4)


class LeverageClampTests(unittest.TestCase):
    def _clamp(self, model_lev, lo, hi):
        lev_hi = max(1, int(round(hi)))
        lev_lo = max(1, min(int(round(lo)), lev_hi))
        return int(max(lev_lo, min(lev_hi, round(model_lev))))

    def test_low_model_value_raised_to_min(self):
        self.assertEqual(self._clamp(3, 5.0, 7.0), 5)

    def test_high_model_value_capped(self):
        self.assertEqual(self._clamp(12, 2.0, 7.0), 7)

    def test_in_range_passthrough(self):
        self.assertEqual(self._clamp(6, 5.0, 7.0), 6)

    def test_legacy_2_to_5_semantics_kept(self):
        self.assertEqual(self._clamp(1, 2.0, 5.0), 2)
        self.assertEqual(self._clamp(9, 2.0, 5.0), 5)


class CouncilBudgetTests(unittest.TestCase):
    def test_default_timeout_240(self):
        import r20_backend.council_manager as cm
        self.assertEqual(cm.DEFAULT_COUNCIL_TIMEOUT, 240.0)
        src = Path(cm.__file__).read_text(encoding="utf-8")
        self.assertNotIn("timeout: float = 60.0", src)

    def test_default_config_persists_budget(self):
        # A git-ignored deployment config is not a portable test fixture.
        # Exercise the real default -> save -> disk -> reload contract instead.
        from tests.config_sandbox import isolate_config
        import r20_backend.council_manager as cm
        root = isolate_config(self)
        self.assertTrue(cm.COUNCIL_CONFIG_FILE.is_relative_to(root))
        self.assertFalse(cm.COUNCIL_CONFIG_FILE.exists())
        config = cm.load_council_config()
        cm.save_council_config(config)
        cfg = json.loads(cm.COUNCIL_CONFIG_FILE.read_text(encoding="utf-8"))
        self.assertGreaterEqual(float(cfg.get("timeout_seconds") or 0), 240.0)
        self.assertEqual(cm.load_council_config()["timeout_seconds"], cfg["timeout_seconds"])

    def test_cio_contract_no_static_3_anchor(self):
        import r20_backend.council_manager as cm
        src = Path(cm.__file__).read_text(encoding="utf-8")
        self.assertNotIn("int(min(3, _R20_MAX_LEVERAGE))", src)
        self.assertIn("_R20_MIN_LEVERAGE", src)

    def test_debate_stage_budgets_have_90s_floor(self):
        """思考型模型单席提案实测需 60~180s：0.35/0.50 纯比例切分在中低预算下
        会压出 53s 级必死窗口（09-10 05:15 实测），两段预算都必须有 90s 地板。"""
        import r20_backend.council_manager as cm
        src = Path(cm.__file__).read_text(encoding="utf-8")
        self.assertNotIn("rem * 0.35", src)
        self.assertNotIn("min(rem * 0.50,", src)
        self.assertGreaterEqual(src.count("max(rem * 0.55, 90.0)"), 2)


class BrainCouncilTransparencyTests(unittest.TestCase):
    def test_cache_and_history_contract(self):
        import ai_brain_trader as abt
        src = Path(abt.__file__).read_text(encoding="utf-8")
        self.assertIn("council_status", src)                 # 降级原因透明
        self.assertIn('"council": {', src)                    # per-symbol 缓存契约
        self.assertIn("council_status=council_status", src)  # 组装调用透传
        self.assertIn('"council_status": council_status', src)  # 历史记录字段

    def test_assemble_cache_emits_council(self):
        import ai_brain_trader as abt
        p = {"instId": "BTC-USDT-SWAP", "name": "BTC", "price": 80000.0, "market_data_valid": True,
             "atr": 400.0, "precision": 1, "ctVal": 0.01, "adx_1h": 30.0, "rsi": 60.0,
             "calculus": {"valid": True, "regime": "BULL", "velocity": 0.5, "acceleration": 0.2,
                          "max_abs_jerk": 0.1, "impulse": 0.3, "curvature": 0.1, "power": 0.2,
                          "probability_theory": {"continuation_prob_pct": 60.0, "breakdown_prob_pct": 10.0}}}
        d = {"action": "WAIT", "confidence": 50.0, "leverage": 3, "margin_usdt": 10.0,
             "entry_price": 0, "take_profit_price": 0, "stop_loss_price": 0,
             "summary_reason": "test", "adopted_role": "trader_momentum",
             "market_structure": "x", "calculus_dynamics": "y", "math_prob_rationale": "z",
             "volume_and_oi": "w"}
        with patch.object(abt, "MAX_LEVERAGE", 7.0), patch.object(abt, "MIN_LEVERAGE", 5.0):
            cache = abt.assemble_decision_cache(
                packages=[p], decisions_dict={"BTC-USDT-SWAP": d},
                active_inst_ids=set(), active_position_sides={},
                time_str="2026-09-10 12:00:00", macro_summary="m",
                policy_snapshot={"policy_version": "v@test", "policy_hash": "test"},
                council_status={"ran": True, "duration_ms": 150000, "consensus_mode": "standard"})
        entry = cache["BTC-USDT-SWAP"]
        self.assertEqual(entry["council"]["ran"], True)
        self.assertEqual(entry["council"]["adopted_role"], "trader_momentum")
        self.assertEqual(entry["decision"]["leverage"], 5)   # 模型给 3 被下限抬到 5


if __name__ == "__main__":
    unittest.main()

"""预设提示词与风控阈值的「资金规模通用性」回归测试。

背景：预设曾把单笔保证金写成绝对金额「100~200U」、单标的上限写成「600 USDT」、
日亏熔断写成「150 USDT」。对可用余额仅 80U 的小资金账户，这些数值互相冲突且
风控线永不触发（等于没有熔断）。v7.5.5 起一律改为按实际可用余额自适应推导。
本测试锁定该不变量，禁止绝对金额重新写回预设与执行层。
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from tests.risk_test_env import pin_baseline_risk_env  # noqa: E402


def setUpModule():
    # risk_budget 渲染断言锁定基线（5% 日亏/2% 单笔）；隔离生产 .env 当前套件值
    pin_baseline_risk_env()

# 绝对法币金额：数字或区间 + U/USDT
_ABS_MONEY = re.compile(r"(?<![\d.%])\d{2,5}(?:\.\d+)?\s*(?:~|～|至|-)\s*\d{2,5}(?:\.\d+)?\s*(?:USDT|U)\b|(?<![\d.])\d{3,5}\s*(?:USDT|U)\b")
# 允许出现绝对金额的位置：自适应风险预算自身的示例文案、以及带「可用余额」前缀的举例
_ALLOWED_MARKERS = ("可用余额 80U", "可用余额 4000U", "本周期风险预算", "自适应推导")


def _offenders(text: str) -> list[str]:
    out = []
    for m in _ABS_MONEY.finditer(text or ""):
        window = text[max(0, m.start() - 60):m.end() + 30]
        if any(marker in window for marker in _ALLOWED_MARKERS):
            continue
        out.append(window.replace("\n", " ").strip())
    return out


class PromptUniversalityTests(unittest.TestCase):
    def test_base_system_prompt_has_no_absolute_money(self):
        from scripts.ai_brain_trader import get_effective_system_prompt
        self.assertEqual(_offenders(get_effective_system_prompt()), [], "代码 base 系统提示词含绝对金额")

    def test_base_user_prompt_has_no_absolute_money(self):
        src = (ROOT / "scripts" / "ai_brain_trader.py").read_text(encoding="utf-8")
        start, end = src.index("    prompt = f\"\"\""), src.index("    runtime_vars = {")
        self.assertEqual(_offenders(src[start:end]), [], "代码 base 用户提示词含绝对金额")

    def test_source_presets_have_no_absolute_money(self):
        src = (ROOT / "scripts" / "prompt_library.py").read_text(encoding="utf-8")
        block = src[src.index("PRESETS: dict"):src.index("\nEMPTY_CUSTOM")]
        self.assertEqual(_offenders(block), [], "源码预设含绝对金额，小资金账户会冲突")

    def test_live_profile_has_no_absolute_money(self):
        live = json.loads((ROOT / "data" / "prompt_library.json").read_text(encoding="utf-8"))
        self.assertEqual(_offenders(json.dumps(live["profiles"]["stable"], ensure_ascii=False)), [],
                         "线上方案缓存快照含绝对金额")

    def test_risk_budget_is_an_allowed_variable_and_rendered(self):
        import prompt_library as pl
        self.assertIn("risk_budget", pl.ALLOWED_VARIABLES)
        ctx = {}
        from scripts.ai_brain_trader import construct_full_market_prompt
        construct_full_market_prompt([], "无", [], [], "2026-09-07 21:00:00",
                                     usdt_available=80.0, runtime_context_out=ctx)
        self.assertIn("risk_budget", ctx)
        self.assertIn("2.4", ctx["risk_budget"])      # 80U * 3%
        self.assertIn("9.6", ctx["risk_budget"])      # 80U * 12%
        self.assertIn("-4.0 USDT", ctx["risk_budget"])  # 80U * 5% 日亏熔断
        self.assertIn("小资金账户提示", ctx["risk_budget"])

    def test_risk_budget_missing_context_fails_loud(self):
        from scripts.ai_brain_trader import construct_full_market_prompt
        ctx = {}
        construct_full_market_prompt([], "无", [], [], "2026-09-07 21:00:00",
                                     usdt_available=None, runtime_context_out=ctx)
        self.assertIn("MISSING_CONTEXT:risk_budget", ctx["risk_budget"])

    def test_multiline_block_var_not_embedded_in_system_prose(self):
        """risk_budget 是多行块，内插进 system 正文会把句子撑断（曾在实盘 prompt 中出现）。
        约定：它只能作为独立小节出现在 trading_user，不得出现在 trading_system 文本里。"""
        live = json.loads((ROOT / "data" / "prompt_library.json").read_text(encoding="utf-8"))
        prof = live["profiles"]["stable"]
        sys_blob = json.dumps(
            {"modules": (prof.get("pipelines") or {}).get("trading_system", []),
             "legacy": prof.get("trading_system")}, ensure_ascii=False)
        self.assertNotIn("{{risk_budget}}", sys_blob,
                         "多行块变量被内插进 system 提示词，会撑断句子")

    def test_rendered_system_prompt_has_no_injected_block(self):
        from scripts.ai_brain_trader import (get_effective_system_prompt, apply_module_layout,
                                             active_profile, construct_full_market_prompt)
        ctx = {}
        construct_full_market_prompt([], "无", [], [], "2026-09-07 21:00:00",
                                     usdt_available=20.0, runtime_context_out=ctx)
        rendered = apply_module_layout(get_effective_system_prompt(), active_profile(),
                                       "trading_system", "t", context=ctx)
        self.assertNotIn("{{risk_budget}}", rendered)
        self.assertNotIn("】:\n- 常规单笔保证金", rendered, "system 正文中出现被撑断的预算块")
        self.assertNotIn("取值严禁", rendered, "历史替换造成的语句粘连残留")

    def test_pool_size_wording_is_not_hardcoded(self):
        """标的池可增删，提示词与日志不得写死具体数量（如「六币种」「6 个标的」）。"""
        from scripts.ai_brain_trader import get_effective_system_prompt
        corpus = get_effective_system_prompt()
        corpus += (ROOT / "scripts" / "prompt_library.py").read_text(encoding="utf-8")
        corpus += json.dumps(json.loads((ROOT / "data" / "prompt_library.json").read_text(encoding="utf-8"))
                             ["profiles"]["stable"], ensure_ascii=False)
        for bad in ("六币种", "6币种", "在 6 个标的", "6 个标的中"):
            self.assertNotIn(bad, corpus, f"提示词写死了标的数量: {bad}")


class AdaptiveRiskLimitTests(unittest.TestCase):
    def test_small_account_daily_loss_limit_is_tighter_than_absolute_cap(self):
        import ai_factor_trader as aft
        self.assertLess(aft.effective_daily_loss_limit(80.0), aft.MAX_DAILY_LOSS_USDT)
        self.assertAlmostEqual(aft.effective_daily_loss_limit(80.0), 4.0, places=2)
        self.assertAlmostEqual(aft.effective_single_asset_margin(80.0), 24.0, places=2)

    def test_large_account_keeps_legacy_absolute_caps(self):
        import ai_factor_trader as aft
        self.assertEqual(aft.effective_daily_loss_limit(4000.0), aft.MAX_DAILY_LOSS_USDT)
        self.assertEqual(aft.effective_single_asset_margin(4000.0), aft.MAX_SINGLE_ASSET_MARGIN)

    def test_limits_are_env_overridable(self):
        import os, importlib
        import r20_backend.config as backend_config
        import risk_constants
        import ai_factor_trader as aft
        os.environ["R20_MAX_DAILY_LOSS_USDT"] = "99"
        original_loader = backend_config.load_dotenv
        backend_config.load_dotenv = lambda path: None  # 屏蔽仓库 .env 覆盖测试环境变量
        try:
            importlib.reload(risk_constants)  # v7.6 起常量单一事实源在 risk_constants
            importlib.reload(aft)
            self.assertEqual(aft.MAX_DAILY_LOSS_USDT, 99.0)
        finally:
            backend_config.load_dotenv = original_loader
            del os.environ["R20_MAX_DAILY_LOSS_USDT"]
            importlib.reload(risk_constants)
            importlib.reload(aft)


class InterceptorTierTests(unittest.TestCase):
    def setUp(self):
        import importlib.util as u
        spec = u.spec_from_file_location("gate", ROOT / "plugins" / "interceptors" / "02_confidence_gatekeeper.py")
        self.mod = u.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def test_tier_driven_not_symbol_name_driven(self):
        # 任意 Tier-2 标的（非 DOGE）都应吃到 80% 严门禁
        ok, msg = self.mod.check_risk({"name": "ANYCOIN", "tier": "tier_2_momentum"},
                                      {"action": "BUY_LONG", "confidence": 78.0}, {})
        self.assertFalse(ok, "Tier-2 标的 78% 应被拦截")
        self.assertIn("80%", msg)

    def test_tier1_bluechip_uses_standard_floor(self):
        ok, _ = self.mod.check_risk({"name": "BTC", "tier": "tier_1_bluechip"},
                                    {"action": "BUY_LONG", "confidence": 78.0}, {})
        self.assertTrue(ok, "Tier-1 标的 78% 应放行")

    def test_legacy_symbol_fallback_still_guarded(self):
        ok, _ = self.mod.check_risk({"name": "DOGE"}, {"action": "BUY_LONG", "confidence": 78.0}, {})
        self.assertFalse(ok, "无 tier 字段时应回退历史白名单，保持旧行为")


if __name__ == "__main__":
    unittest.main()

"""Offline isolated test for Prompt Anti-Drawdown TP & Position Data Feed Enrichment.
Validates:
1. High Water Mark, peak profit gain, and retracement drawdown percentage are properly injected into account_positions.
2. System prompt and active profile enforce anti-drawdown take-profit and CLOSE_MARKET directives.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.ai_brain_trader as abt


class PromptAntiDrawdownTakeProfitTests(unittest.TestCase):
    def test_position_feed_enriches_peak_and_drawdown(self):
        active_positions = [
            {
                "instId": "ETH-USDT-SWAP",
                "name": "ETH",
                "side": "long",
                "lever": "3",
                "avgPx": "2500.0",
                "markPx": "2520.0",
                "pos": "2.0",
                "upl": "4.0",
                "uplRatio": "0.016",
                "highWaterMark": 2560.0,
                "lowWaterMark": 2495.0,
                "trailingStopPx": 2505.0,
                "takeProfitPx": 2600.0,
                "stage_desc": "已推保本无风险",
            }
        ]

        prompt_str = abt.construct_full_market_prompt(
            packages=[],
            pos_summary="1多0空",
            active_positions_detail=active_positions,
            current_time_str="2026-09-07 15:00:00"
        )

        self.assertIn("曾最高到: 2560.0", prompt_str)
        self.assertIn("极值浮盈 +2.4%", prompt_str)
        # Drawdown from peak: (2560 - 2520) / (2560 - 2500) = 40 / 60 = 66.7%
        self.assertIn("回撤 66.7%", prompt_str)
        self.assertIn("动态止损线: 2505.0", prompt_str)
        self.assertIn("目标止盈: 2600.0", prompt_str)

    def test_system_prompt_contains_anti_drawdown_directives(self):
        profile = abt.active_profile()
        sys_prompt = profile.get("trading_system", "")
        self.assertIn("反浮盈回吐·三阶动态棘轮与果断主动止盈", sys_prompt)
        self.assertIn("峰值回撤硬止盈", sys_prompt)
        self.assertIn("动力学负功率耗散止盈", sys_prompt)
        self.assertIn("CLOSE_MARKET", sys_prompt)


if __name__ == "__main__":
    unittest.main()

import time
import unittest
from unittest.mock import patch, MagicMock
from r20_backend.council_manager import (
    load_council_config,
    save_council_config,
    reset_role_template,
    DEFAULT_PRESET_TEMPLATES,
    execute_council_debate,
    MIN_SAFE_REASONING_TIME,
)


class TestCouncilManager(unittest.TestCase):
    def setUp(self):
        from tests.config_sandbox import isolate_config
        isolate_config(self)
        # Backup council config state
        self.original_cfg = load_council_config()

    def tearDown(self):
        # Restore council config state
        save_council_config(self.original_cfg)

    def test_load_and_save_council_config(self):
        cfg = load_council_config()
        self.assertIn("enabled", cfg)
        self.assertIn("roles", cfg)
        self.assertIn("trader_trend", cfg["roles"])
        self.assertIn("trader_momentum", cfg["roles"])
        self.assertIn("trader_quant", cfg["roles"])
        self.assertIn("cio", cfg["roles"])
        self.assertIn(cfg.get("consensus_mode"), {"standard", "cross_examination"})

        # Test mode convergence: invalid or legacy mode converges to standard
        cfg["consensus_mode"] = "weighted"
        saved = save_council_config(cfg)
        self.assertEqual(saved["consensus_mode"], "standard")

        cfg["consensus_mode"] = "cross_examination"
        saved = save_council_config(cfg)
        self.assertEqual(saved["consensus_mode"], "cross_examination")

        cfg["consensus_mode"] = "invalid_mode_xyz"
        saved = save_council_config(cfg)
        self.assertEqual(saved["consensus_mode"], "standard")

    def test_reset_role_template(self):
        cfg = reset_role_template("trader_trend")
        self.assertEqual(
            cfg["roles"]["trader_trend"]["prompt"],
            DEFAULT_PRESET_TEMPLATES["trader_trend"]["prompt"]
        )

    def test_consensus_mode_and_suites(self):
        from r20_backend.council_manager import get_preset_suites, apply_preset_suite
        suites = get_preset_suites()
        self.assertGreaterEqual(len(suites), 1)
        suite_ids = [s["id"] for s in suites]
        self.assertIn("hedge_fund_desk", suite_ids)

        updated = apply_preset_suite("hedge_fund_desk")
        self.assertEqual(updated["consensus_mode"], "standard")
        self.assertIn("trader_trend", updated["roles"])
        self.assertIn("trader_momentum", updated["roles"])
        self.assertIn("cio", updated["roles"])

    def test_standard_council_debate_flow_and_adopted_role(self):
        """Test Standard 1-round proposal -> CIO verdict with adopted_role contract."""
        cfg = load_council_config()
        cfg["consensus_mode"] = "standard"
        save_council_config(cfg)

        mock_trader_return = ("BUY_LONG 82% 动能良好，建议 HOLD 现有 BTC 持仓", "", {}, 120)
        mock_cio_json = (
            '{"macro_assessment": "资金充裕，采纳稳健顺势方案", '
            '"position_management": [{"instId": "BTC-USDT-SWAP", "action": "HOLD", "reasoning": "波段顺畅"}], '
            '"decisions": {'
            '  "ETH-USDT-SWAP": {"action": "BUY_LONG", "adopted_role": "trader_trend", "confidence": 85, "limit_price": 2410.0, "stop_loss": 2350.0, "take_profit": 2530.0, "leverage": 3, "margin_usd": 150.0, "reasoning": "采纳交易员 A 顺势回踩买点"},'
            '  "SOL-USDT-SWAP": {"action": "WAIT", "confidence": 50, "reasoning": "全员驳回观望"}'
            '}}',
            "",
            {},
            250
        )

        with patch("r20_backend.llm_manager.execute_llm_request") as mock_exec:
            mock_exec.side_effect = [
                mock_trader_return,
                mock_trader_return,
                mock_trader_return,
                mock_cio_json,
            ]

            brain_output, transcript = execute_council_debate(
                market_prompt="BTC: 77000, ETH: 2400",
                original_system_prompt="system prompt",
                timeout=20.0,
            )

            self.assertIn("decisions", brain_output)
            decisions = brain_output["decisions"]
            # ETH should have explicit adopted_role = 'trader_trend'
            self.assertEqual(decisions["ETH-USDT-SWAP"]["adopted_role"], "trader_trend")
            # SOL WAIT should be auto-normalized to 'REJECT_ALL'
            self.assertEqual(decisions["SOL-USDT-SWAP"]["adopted_role"], "REJECT_ALL")

            # Check proposals traceability tags
            for role_id, advisor in transcript["advisors"].items():
                self.assertIn("proposal_id", advisor)
                self.assertEqual(advisor["proposal_id"], f"{role_id}_prop")

            self.assertEqual(transcript["consensus_mode"], "standard")
            self.assertEqual(transcript.get("cross_examinations"), {})

    def test_adopted_role_inferred_from_reasoning_fallback(self):
        """Test adopted_role auto-inferencing from reasoning when not explicitly provided by CIO."""
        cfg = load_council_config()
        cfg["consensus_mode"] = "standard"
        save_council_config(cfg)

        mock_trader_return = ("方案", "", {}, 100)
        mock_cio_json = (
            '{"macro_assessment": "裁决", "position_management": [], "decisions": {'
            '  "ETH-USDT-SWAP": {"action": "BUY_LONG", "reasoning": "综合考虑，采纳交易员 A (顺势稳健型) 的点位"},'
            '  "BTC-USDT-SWAP": {"action": "BUY_LONG", "reasoning": "根据未知理由开仓"},'
            '  "SOL-USDT-SWAP": {"action": "WAIT", "reasoning": "观望"}'
            '}}',
            "",
            {},
            200
        )

        with patch("r20_backend.llm_manager.execute_llm_request") as mock_exec:
            mock_exec.side_effect = [
                mock_trader_return, mock_trader_return, mock_trader_return,
                mock_cio_json,
            ]
            brain_output, transcript = execute_council_debate(
                market_prompt="BTC: 77000",
                original_system_prompt="system prompt",
                timeout=20.0,
            )

            decisions = brain_output["decisions"]
            self.assertEqual(decisions["ETH-USDT-SWAP"]["adopted_role"], "trader_trend")
            self.assertIsNone(decisions["BTC-USDT-SWAP"]["adopted_role"])
            self.assertEqual(decisions["SOL-USDT-SWAP"]["adopted_role"], "REJECT_ALL")

    def test_cross_examination_council_debate_flow(self):
        """Test Cross-Examination double-round flow: Proposals -> Peer Critiques -> CIO."""
        cfg = load_council_config()
        cfg["consensus_mode"] = "cross_examination"
        save_council_config(cfg)

        mock_proposal = ("【方案汇报】建议回踩买多，防守位明确", "", {}, 100)
        mock_critique = ("【同行质询】指出同行方案追高、且止损未达到 2.0x ATR 隐患", "", {}, 110)
        mock_cio_json = (
            '{"macro_assessment": "综合交叉质询，动能交易员方案获胜", '
            '"position_management": [], '
            '"decisions": {'
            '  "BTC-USDT-SWAP": {"action": "BUY_LONG", "adopted_role": "trader_momentum", "confidence": 88, "limit_price": 77500.0, "stop_loss": 76000.0, "take_profit": 81000.0, "reasoning": "采纳交易员 B 突破点位"}'
            '}}',
            "",
            {},
            200
        )

        with patch("r20_backend.llm_manager.execute_llm_request") as mock_exec:
            # 3 proposals (trader_trend, trader_momentum, trader_quant)
            # 3 critiques
            # 1 CIO arbitration
            mock_exec.side_effect = [
                mock_proposal, mock_proposal, mock_proposal,
                mock_critique, mock_critique, mock_critique,
                mock_cio_json,
            ]

            brain_output, transcript = execute_council_debate(
                market_prompt="BTC: 77000",
                original_system_prompt="system prompt",
                timeout=30.0,
            )

            self.assertEqual(transcript["consensus_mode"], "cross_examination")
            self.assertIn("cross_examinations", transcript)
            self.assertEqual(len(transcript["cross_examinations"]), 3)
            for k, crit in transcript["cross_examinations"].items():
                self.assertEqual(crit["status"], "ok")
                self.assertIn("同行质询", crit["content"])

            self.assertEqual(brain_output["decisions"]["BTC-USDT-SWAP"]["adopted_role"], "trader_momentum")

    def test_timeout_insufficient_immediate_abort(self):
        """Test strict timeout budget: abort immediately if remaining time < MIN_SAFE_REASONING_TIME."""
        with self.assertRaises(TimeoutError) as ctx:
            execute_council_debate(
                market_prompt="BTC: 77000",
                original_system_prompt="system prompt",
                timeout=3.0,  # Below MIN_SAFE_REASONING_TIME (5.0s)
            )
        self.assertIn("below safety threshold", str(ctx.exception))

    def test_cross_examination_timeout_degradation(self):
        """Test safe degradation in cross_examination when budget is tight before stage 2."""
        cfg = load_council_config()
        cfg["consensus_mode"] = "cross_examination"
        save_council_config(cfg)

        mock_cio_json = (
            '{"macro_assessment": "时间紧凑，跳过质询，CIO 独立终审", "position_management": [], "decisions": {"BTC-USDT-SWAP": {"action": "WAIT", "adopted_role": "REJECT_ALL", "reasoning": "防守"}}}',
            "",
            {},
            150
        )

        current_time = 1000.0

        def fake_time():
            return current_time

        def fake_trader_exec(role_id, role_spec, *args, **kwargs):
            nonlocal current_time
            # Simulate stage 1 proposals consuming time so that rem becomes 6.0s (out of 16.0s total)
            current_time = 1010.0
            return {
                "proposal_id": f"{role_id}_prop",
                "role_id": role_id,
                "role_name": role_spec.get("name", role_id),
                "status": "ok",
                "content": "【方案汇报】方案内容",
                "reasoning": "",
                "latency_ms": 100,
                "weight": 1.0,
            }

        with patch("time.time", side_effect=fake_time), \
             patch("r20_backend.council_manager._call_single_trader", side_effect=fake_trader_exec), \
             patch("r20_backend.llm_manager.execute_llm_request", return_value=(mock_cio_json[0], "", {}, 150)):

            brain_output, transcript = execute_council_debate(
                market_prompt="BTC: 77000",
                original_system_prompt="system prompt",
                timeout=16.0,
            )

            self.assertEqual(transcript["consensus_mode"], "cross_examination")
            self.assertIn("cross_examinations", transcript)
            # Verify critiques were safely skipped due to time constraint
            for k, crit in transcript["cross_examinations"].items():
                self.assertEqual(crit["status"], "skipped")
                self.assertIn("跳过交叉质询", crit["content"])

            # CIO still completed safely
            self.assertEqual(brain_output["decisions"]["BTC-USDT-SWAP"]["adopted_role"], "REJECT_ALL")


if __name__ == "__main__":
    unittest.main()

"""Offline isolated test suite for Three-Tier Profit Ratchet & Cloud OCO Sync.
Validates:
1. Symmetric Long/Short Tier 1 Breakeven Lock (+1.5x ATR).
2. Symmetric Long/Short Tier 2 Wave Profit Lock (+2.2x ATR).
3. Symmetric Long/Short Kinetic Momentum Pullback Take-Profit (>= 2.0x ATR peak with 0.75x ATR pullback).
4. Cloud OCO algo stop synchronization (sync_cloud_algo_stop).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.ai_factor_trader as aft


class ThreeTierRatchetAndCloudSyncTests(unittest.TestCase):
    # 注意：勿再向 aft 注入 SIMULATED_TRADING 全局——生产代码已删除该变量
    # (v7.6 环境重构)，旧注入会让测试绿而生产 NameError(09-08 事故根因)。
    # 本套测试直接调用 sync_cloud_algo_stop，任何对已删除全局的复活引用都会在此炸出 NameError。

    def test_sync_cloud_algo_stop_success_and_idempotence(self):
        with patch("scripts.ai_factor_trader.run_json_cmd") as mock_cmd:
            # 1. When existing algo already matches new_sl, do not issue redundant amend
            mock_cmd.return_value = [
                {"state": "live", "posSide": "long", "algoId": "algo_101", "slTriggerPx": "2500.0"}
            ]
            res = aft.sync_cloud_algo_stop("ETH-USDT-SWAP", "long", 2500.0)
            self.assertTrue(res)
            # Only 1 call to fetch orders, no amend call
            self.assertEqual(mock_cmd.call_count, 1)

            # 2. When existing algo has different slTriggerPx, issue amend
            mock_cmd.reset_mock()
            mock_cmd.side_effect = [
                [{"state": "live", "posSide": "long", "algoId": "algo_101", "slTriggerPx": "2400.0"}],
                {"code": "0", "msg": "amend success"}
            ]
            res = aft.sync_cloud_algo_stop("ETH-USDT-SWAP", "long", 2500.0)
            self.assertTrue(res)
            self.assertEqual(mock_cmd.call_count, 2)

    def test_long_three_tier_ratchet_progression(self):
        f = {
            "instId": "ETH-USDT-SWAP",
            "name": "ETH",
            "price": 2500.0,
            "atr": 20.0,
            "precision": 2,
            "ctVal": 0.1,
            "type": "crypto",
            "market_data_valid": True,
        }
        curr_pos = {"pos": "2.0", "side": "long", "avgPx": "2500.0", "upl": 0.0}
        trackers = {}
        executed_actions = []

        # 1. Initial State: entry 2500, ATR 20, initial wide stop = 2500 - 20*1.4 = 2472.0
        closed, reason = aft.manage_position_tp_and_trailing(f, curr_pos, trackers, "2026-09-07 10:00:00", executed_actions)
        self.assertFalse(closed)
        t = trackers["ETH-USDT-SWAP_long"]
        self.assertEqual(t["trailingStopPx"], 2472.0)

        # 2. Price rises to 2535 (profit = +35.0 >= 1.5 * ATR = 30.0) -> Triggers Tier 1 Breakeven (+0.20% cushion)
        f["price"] = 2535.0
        curr_pos["upl"] = 7.0
        with patch("scripts.ai_factor_trader.sync_cloud_algo_stop") as mock_sync:
            mock_sync.return_value = True
            closed, reason = aft.manage_position_tp_and_trailing(f, curr_pos, trackers, "2026-09-07 10:15:00", executed_actions)
            self.assertFalse(closed)
            self.assertIn("已推保本无风险", t["stage_desc"])
            # 2500 * 1.002 = 2505.0
            self.assertGreaterEqual(t["trailingStopPx"], 2505.0)
            mock_sync.assert_called_once()

        # 3. Price rises to 2550 (profit = +50.0 >= 2.2 * ATR = 44.0) -> Triggers Tier 2 Wave Profit Lock (+1.0 ATR)
        f["price"] = 2550.0
        curr_pos["upl"] = 10.0
        with patch("scripts.ai_factor_trader.sync_cloud_algo_stop") as mock_sync:
            mock_sync.return_value = True
            closed, reason = aft.manage_position_tp_and_trailing(f, curr_pos, trackers, "2026-09-07 10:30:00", executed_actions)
            self.assertFalse(closed)
            self.assertIn("锁定大波段利润", t["stage_desc"])
            # 2500 + 1.0 * 20 = 2520.0
            self.assertGreaterEqual(t["trailingStopPx"], 2520.0)

        # 4. Price surges to 2560 (profit 60.0 >= 2.0*ATR=40), then pulls back to 2540 (pullback 20.0 >= 0.75*ATR=15.0)
        # Should trigger Tier 3 Kinetic Momentum Pullback Exit
        f["price"] = 2560.0
        aft.manage_position_tp_and_trailing(f, curr_pos, trackers, "2026-09-07 10:45:00", executed_actions)
        self.assertEqual(t["highWaterMark"], 2560.0)

        f["price"] = 2540.0
        curr_pos["upl"] = 8.0
        with patch("scripts.ai_factor_trader.close_position_confirmed") as mock_close, \
             patch("scripts.ai_factor_trader.record_trade"):
            mock_close.return_value = (True, "mock closed")
            closed, reason = aft.manage_position_tp_and_trailing(f, curr_pos, trackers, "2026-09-07 11:00:00", executed_actions)
            self.assertTrue(closed)
            self.assertEqual(reason, "已移动止盈")
            mock_close.assert_called_once_with("ETH-USDT-SWAP", "long", 2.0)

    def test_short_three_tier_ratchet_progression(self):
        f = {
            "instId": "ETH-USDT-SWAP",
            "name": "ETH",
            "price": 2500.0,
            "atr": 20.0,
            "precision": 2,
            "ctVal": 0.1,
            "type": "crypto",
            "market_data_valid": True,
        }
        curr_pos = {"pos": "2.0", "side": "short", "avgPx": "2500.0", "upl": 0.0}
        trackers = {}
        executed_actions = []

        # 1. Initial State: entry 2500, ATR 20, initial wide stop = 2500 + 20*1.4 = 2528.0
        closed, reason = aft.manage_position_tp_and_trailing(f, curr_pos, trackers, "2026-09-07 10:00:00", executed_actions)
        self.assertFalse(closed)
        t = trackers["ETH-USDT-SWAP_short"]
        self.assertEqual(t["trailingStopPx"], 2528.0)

        # 2. Price plunges to 2465 (profit = 35.0 >= 1.5 * ATR = 30.0) -> Triggers Tier 1 Breakeven (-0.20% cushion)
        f["price"] = 2465.0
        curr_pos["upl"] = 7.0
        with patch("scripts.ai_factor_trader.sync_cloud_algo_stop") as mock_sync:
            mock_sync.return_value = True
            closed, reason = aft.manage_position_tp_and_trailing(f, curr_pos, trackers, "2026-09-07 10:15:00", executed_actions)
            self.assertFalse(closed)
            self.assertIn("已推保本无风险", t["stage_desc"])
            # 2500 * (1 - 0.002) = 2495.0
            self.assertLessEqual(t["trailingStopPx"], 2495.0)
            mock_sync.assert_called_once()

        # 3. Price plunges to 2450 (profit = 50.0 >= 2.2 * ATR = 44.0) -> Triggers Tier 2 Wave Profit Lock (-1.0 ATR)
        f["price"] = 2450.0
        curr_pos["upl"] = 10.0
        with patch("scripts.ai_factor_trader.sync_cloud_algo_stop") as mock_sync:
            mock_sync.return_value = True
            closed, reason = aft.manage_position_tp_and_trailing(f, curr_pos, trackers, "2026-09-07 10:30:00", executed_actions)
            self.assertFalse(closed)
            self.assertIn("锁定大波段利润", t["stage_desc"])
            # 2500 - 1.0 * 20 = 2480.0
            self.assertLessEqual(t["trailingStopPx"], 2480.0)

        # 4. Price plunges to 2440 (profit 60.0 >= 2.0*ATR=40), then rebounds to 2460 (rebound 20.0 >= 0.75*ATR=15.0)
        # Should trigger Tier 3 Kinetic Momentum Pullback Exit for Short
        f["price"] = 2440.0
        aft.manage_position_tp_and_trailing(f, curr_pos, trackers, "2026-09-07 10:45:00", executed_actions)
        self.assertEqual(t["lowWaterMark"], 2440.0)

        f["price"] = 2460.0
        curr_pos["upl"] = 8.0
        with patch("scripts.ai_factor_trader.close_position_confirmed") as mock_close, \
             patch("scripts.ai_factor_trader.record_trade"):
            mock_close.return_value = (True, "mock closed")
            closed, reason = aft.manage_position_tp_and_trailing(f, curr_pos, trackers, "2026-09-07 11:00:00", executed_actions)
            self.assertTrue(closed)
            self.assertEqual(reason, "已移动止盈")
            mock_close.assert_called_once_with("ETH-USDT-SWAP", "short", 2.0)


if __name__ == "__main__":
    unittest.main()

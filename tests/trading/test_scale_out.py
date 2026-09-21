"""Unit tests for Scale-Out Execution Engine (scripts/trader/scale_out.py)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from scripts.trader.scale_out import execute_scale_out_if_eligible


class ScaleOutExecutionTests(unittest.TestCase):
    def setUp(self):
        self.mock_okx = MagicMock()
        self.mock_record_trade = MagicMock()
        self.mock_notify = MagicMock()
        self.mock_ensure_oco = MagicMock()
        self.mock_close_fee = MagicMock(return_value=0.5)
        self.mock_payload = MagicMock(return_value={"action": "close"})

        self.sample_f_long = {
            "instId": "BTC-USDT-SWAP",
            "name": "BTC",
            "price": 82000.0,
            "atr": 1000.0,
            "precision": 2,
            "ctVal": 1.0,
            "minSz": 0.01,
            "market_data_valid": True,
        }

        self.sample_pos_long = {
            "side": "long",
            "avgPx": 80000.0,
            "pos": 10.0,
            "venue": "okx",
        }

        self.sample_trackers = {
            "BTC-USDT-SWAP_long": {
                "initialSz": 10.0,
                "currentSz": 10.0,
                "takeProfitPx": 85000.0,
                "trailingStopPx": 78000.0,
                "scale_out_phase": 0,
                "scale_count": 0,
            }
        }

    def test_disabled_by_switch(self):
        with patch("scripts.trader.scale_out.SCALE_OUT_ENABLED", False):
            actions = []
            ok, reason = execute_scale_out_if_eligible(
                self.sample_f_long, self.sample_pos_long, self.sample_trackers,
                "2026-09-20 12:00:00", actions,
                okx_rest=self.mock_okx,
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "分批止盈未启用")
            self.assertEqual(len(actions), 0)
            self.mock_okx.place_order.assert_not_called()

    def test_profit_below_trigger_threshold(self):
        # cur_px = 80500, entry = 80000 -> profit = 500 < 1.2 * 1000 (1200)
        f_low_profit = dict(self.sample_f_long, price=80500.0)
        actions = []
        ok, reason = execute_scale_out_if_eligible(
            f_low_profit, self.sample_pos_long, self.sample_trackers,
            "2026-09-20 12:00:00", actions,
            okx_rest=self.mock_okx,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "浮盈未达分批止盈门槛")
        self.mock_okx.place_order.assert_not_called()
        # 验证未达标时已为操盘手和主脑计算并存入首批止盈位 TP1
        t = self.sample_trackers["BTC-USDT-SWAP_long"]
        self.assertEqual(t.get("scale_out_tp"), 81200.0)
        self.assertIn("首批止盈目标 TP1: 81200", t.get("stage_desc", ""))

    def test_insufficient_size_graceful_degrade(self):
        # pos = 0.01, minSz = 0.01 -> 0.01 < 2 * 0.01, cannot split
        pos_tiny = dict(self.sample_pos_long, pos=0.01)
        actions = []
        ok, reason = execute_scale_out_if_eligible(
            self.sample_f_long, pos_tiny, self.sample_trackers,
            "2026-09-20 12:00:00", actions,
            okx_rest=self.mock_okx,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "张数不足以切分")
        self.assertIn("降级为全仓追踪", actions[0])
        self.mock_okx.place_order.assert_not_called()

    def test_notification_failure_does_not_change_the_outcome(self):
        """**通知是 best-effort**：平仓单已经打到交易所 ⇒ 通知（Telegram/邮件等）失败
        只吞掉，返回值仍是成功。否则上层会误以为平仓失败而重试。
        """
        self.mock_okx.place_order.return_value = [{"ordId": "12345"}]
        self.mock_okx.pending_algo_orders.return_value = [
            {"algoId": "algo_1", "posSide": "long", "state": "live"}
        ]
        boom = MagicMock(side_effect=RuntimeError("通知通道炸了"))
        actions = []
        ok, reason = execute_scale_out_if_eligible(
            self.sample_f_long, self.sample_pos_long, self.sample_trackers,
            "2026-09-20 12:00:00", actions,
            okx_rest=self.mock_okx,
            record_trade=self.mock_record_trade,
            notify_trade_close=boom,
            close_fee=self.mock_close_fee,
            close_trade_payload=self.mock_payload,
            ensure_cloud_position_protection=self.mock_ensure_oco,
        )
        self.assertTrue(ok, f"通知失败不得改变平仓结果：{reason}")
        self.assertEqual(reason, "首批分批平仓成功")
        boom.assert_called()

    def test_trade_recording_failure_currently_propagates(self):
        """⚠️ **实测边界（如实钉住现状，未擅自改）**：与「通知」不同，**台账记账是裸调用**
        （`record_trade(...)` 没有 `try` 包裹，只有 `notify_trade_close` 那一段有）。
        于是台账写失败会把异常抛给调用方 —— **而平仓单此刻已经打到交易所了**。

        为什么值得记：这一族（本仓反复出现的形态）是「**成交已发生，副作用失败不该改变结果**」；
        通知做到了 best-effort，台账没有，两者**不一致**。改它属钱路行为变更
        （涉及是否重试、如何披露记账缺口）⇒ 列为待议项。
        """
        self.mock_okx.place_order.return_value = [{"ordId": "12345"}]
        self.mock_okx.pending_algo_orders.return_value = [
            {"algoId": "algo_1", "posSide": "long", "state": "live"}
        ]
        boom = MagicMock(side_effect=RuntimeError("台账库锁住了"))
        actions = []
        with self.assertRaises(RuntimeError):
            execute_scale_out_if_eligible(
                self.sample_f_long, self.sample_pos_long, self.sample_trackers,
                "2026-09-20 12:00:00", actions,
                okx_rest=self.mock_okx,
                record_trade=boom,
                notify_trade_close=self.mock_notify,
                close_fee=self.mock_close_fee,
                close_trade_payload=self.mock_payload,
                ensure_cloud_position_protection=self.mock_ensure_oco,
            )

    def test_successful_long_scale_out_and_state_lock(self):
        # profit = 82000 - 80000 = 2000 >= 1.2 * 1000
        self.mock_okx.place_order.return_value = [{"ordId": "12345"}]
        self.mock_okx.pending_algo_orders.return_value = [
            {"algoId": "algo_1", "posSide": "long", "state": "live"}
        ]

        actions = []
        ok, reason = execute_scale_out_if_eligible(
            self.sample_f_long, self.sample_pos_long, self.sample_trackers,
            "2026-09-20 12:00:00", actions,
            okx_rest=self.mock_okx,
            record_trade=self.mock_record_trade,
            notify_trade_close=self.mock_notify,
            close_fee=self.mock_close_fee,
            close_trade_payload=self.mock_payload,
            ensure_cloud_position_protection=self.mock_ensure_oco,
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "首批分批平仓成功")
        
        # 验证下达平仓市价单（reduceOnly=True，卖出 5 张）
        self.mock_okx.place_order.assert_called_once_with(
            "BTC-USDT-SWAP", "sell", "5",
            pos_side="long", td_mode="cross", ord_type="market", reduce_only=True
        )

        # 验证旧 OCO 被撤销
        self.mock_okx.cancel_algo_orders.assert_called_once_with(["algo_1"], inst_id="BTC-USDT-SWAP")

        # 验证新 OCO 重挂：剩余 5 张，保本止损价 80200 (80000 + 0.25%)
        self.mock_ensure_oco.assert_called_once_with(
            "BTC-USDT-SWAP", "long", 5.0, 85000.0, 80200.0
        )

        # 验证 tracker 状态变更与金字塔加仓互斥锁定
        t = self.sample_trackers["BTC-USDT-SWAP_long"]
        self.assertEqual(t["scale_out_phase"], 1)
        self.assertEqual(t["currentSz"], 5.0)
        self.assertEqual(t["scale_count"], 999)
        self.assertEqual(t["trailingStopPx"], 80200.0)

        # 验证台账双写与通知触发
        self.mock_record_trade.assert_called_once()
        self.mock_notify.assert_called_once()

    def test_idempotent_no_duplicate_scale_out(self):
        # scale_out_phase 已为 1 时，再次调用直接拒绝，绝不重复平仓
        self.sample_trackers["BTC-USDT-SWAP_long"]["scale_out_phase"] = 1
        actions = []
        ok, reason = execute_scale_out_if_eligible(
            self.sample_f_long, self.sample_pos_long, self.sample_trackers,
            "2026-09-20 12:00:00", actions,
            okx_rest=self.mock_okx,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "已执行过分批平仓")
        self.mock_okx.place_order.assert_not_called()

    def test_ledger_holding_row_reflects_scale_out_status(self):
        import sys
        from pathlib import Path
        scripts_dir = str(Path(__file__).resolve().parents[2] / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from scripts.sync_full_ledger import _holding_row
        import datetime

        trackers = {
            "BTC-USDT-SWAP_long": {
                "scale_out_phase": 1,
                "stage_desc": "已分批止盈50% (余5张 · 保本止损 80200)",
            }
        }
        mock_env = MagicMock(mode="live")
        tz = datetime.timezone(datetime.timedelta(hours=8))
        pos_raw = {
            "instId": "BTC-USDT-SWAP",
            "pos": "5.0",
            "posSide": "long",
            "avgPx": "80000",
            "markPx": "82000",
            "upl": "10000",
            "lever": "3",
            "cTime": "1789857503880",
        }
        row = _holding_row(
            pos_raw, "okx",
            env=mock_env,
            trackers=trackers,
            tz_bj=tz,
            allowed={"BTC-USDT-SWAP"},
            council_by_inst={},
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "holding")
        self.assertIn("已分批止盈50%", row["exit_reason"])
        self.assertEqual(row["scale_out_phase"], 1)

    def test_binance_scale_out_cancels_old_protective_and_sets_reduce_only(self):
        mock_venue_registry = MagicMock()
        mock_bn_adapter = MagicMock()
        mock_venue_registry.get_adapter.return_value = mock_bn_adapter
        mock_bn_adapter.place_order.return_value = {"id": "bn_order_1"}

        pos_bn = {
            "side": "long",
            "avgPx": 80000.0,
            "pos": 10.0,
            "venue": "binance",
            "raw": {"positionSide": "BOTH"},
        }
        trackers = {
            "BTC-USDT-SWAP_long": {
                "initialSz": 10.0,
                "currentSz": 10.0,
                "takeProfitPx": 85000.0,
                "trailingStopPx": 78000.0,
                "scale_out_phase": 0,
                "scale_count": 0,
            }
        }
        actions = []
        ok, reason = execute_scale_out_if_eligible(
            self.sample_f_long, pos_bn, trackers,
            "2026-09-20 12:00:00", actions,
            venue_registry=mock_venue_registry,
            record_trade=self.mock_record_trade,
            notify_trade_close=self.mock_notify,
            close_fee=self.mock_close_fee,
            close_trade_payload=self.mock_payload,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "首批分批平仓成功")
        # 验证 Binance 减仓传递 reduce_only=True
        mock_bn_adapter.place_order.assert_called_once_with(
            "BTC", "sell", 5.0, reduce_only=True
        )
        # 验证 Binance 撤销了旧保护单
        mock_bn_adapter.cancel_protective_orders.assert_called_once_with("BTC")
        # 验证 Binance 为余仓挂载了新保护单
        mock_bn_adapter.attach_protective_orders.assert_called_once_with(
            "BTC", "long", tp_px=85000.0, sl_px=80200.0, contracts=5.0
        )

    def test_gate_scale_out_cancels_old_protective_and_sets_reduce_only(self):
        mock_venue_registry = MagicMock()
        mock_gate_adapter = MagicMock()
        mock_venue_registry.get_adapter.return_value = mock_gate_adapter
        mock_gate_adapter.place_order.return_value = {"id": "gt_order_1"}

        pos_gate = {
            "side": "long",
            "avgPx": 80000.0,
            "pos": 10.0,
            "venue": "gate",
        }
        trackers = {
            "BTC-USDT-SWAP_long": {
                "initialSz": 10.0,
                "currentSz": 10.0,
                "takeProfitPx": 85000.0,
                "trailingStopPx": 78000.0,
                "scale_out_phase": 0,
                "scale_count": 0,
            }
        }
        actions = []
        ok, reason = execute_scale_out_if_eligible(
            self.sample_f_long, pos_gate, trackers,
            "2026-09-20 12:00:00", actions,
            venue_registry=mock_venue_registry,
            record_trade=self.mock_record_trade,
            notify_trade_close=self.mock_notify,
            close_fee=self.mock_close_fee,
            close_trade_payload=self.mock_payload,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "首批分批平仓成功")
        # 验证 Gate 减仓传递 reduce_only=True
        mock_gate_adapter.place_order.assert_called_once_with(
            "BTC", "sell", 5.0, reduce_only=True
        )
        # 验证 Gate 撤销了旧保护单
        mock_gate_adapter.cancel_protective_orders.assert_called_once_with("BTC")
        # 验证 Gate 为余仓挂载了新保护单
        mock_gate_adapter.attach_protective_orders.assert_called_once_with(
            "BTC", "long", tp_px=85000.0, sl_px=80200.0, contracts=5.0
        )

    def test_cycle_parts_scale_out_tp_derivation(self):
        from scripts.brain.cycle_parts import _calculate_scale_out_tp, build_history_record

        # 多头：80000 + 1.2 * 1000 = 81200
        tp_long = _calculate_scale_out_tp(80000.0, "BUY_LONG", 1000.0, precision=2)
        self.assertEqual(tp_long, 81200.0)

        # 空头：80000 - 1.2 * 1000 = 78800
        tp_short = _calculate_scale_out_tp(80000.0, "SELL_SHORT", 1000.0, precision=2)
        self.assertEqual(tp_short, 78800.0)

        # WAIT 或无价格返回 None
        self.assertIsNone(_calculate_scale_out_tp(0.0, "WAIT", 1000.0))
        self.assertIsNone(_calculate_scale_out_tp(80000.0, "WAIT", 0.0))

        # build_history_record 集成测试
        pkgs = [{"name": "BTC", "instId": "BTC-USDT-SWAP", "atr": 1000.0, "precision": 2}]
        std_cache = {
            "BTC-USDT-SWAP": {
                "decision": {
                    "action": "BUY_LONG",
                    "confidence": 88,
                    "entry_price": 80000.0,
                    "stop_loss_price": 79000.0,
                    "take_profit_price": 85000.0,
                    "risk_reward_ratio": 5.0,
                    "summary_reason": "4H结构突破做多",
                },
                "data_quality": {"status": "ok"},
            }
        }
        rec = build_history_record(
            time_str="2026-09-20 10:00:00",
            policy_version="v8.1.1",
            policy_hash="test_hash",
            policy_snapshot={},
            policy_summary="",
            macro_summary="情绪健康",
            council_status={"ran": True},
            ai_last_prompt="",
            pos_mgmt_list=[],
            council_transcript=None,
            packages=pkgs,
            standard_cache=std_cache,
        )
        self.assertEqual(len(rec["top_opportunities"]), 1)
        opp = rec["top_opportunities"][0]
        self.assertEqual(opp["scale_out_tp"], 81200.0)
        self.assertEqual(opp["take_profit_price"], 85000.0)


if __name__ == "__main__":
    unittest.main()

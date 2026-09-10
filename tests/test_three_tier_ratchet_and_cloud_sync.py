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

    def setUp(self):
        # 封闭性隔离(09-08 事故)：manage_position_tp_and_trailing 每周期真实调用
        # ensure_cloud_position_protection → okx CLI，曾把 sz=2 的 ETH 垃圾 OCO 打进
        # demo 账户，且测试通过与否取决于该残留单的死活(11:38 绿、12:00 红)。
        # 一律 mock，测试永不触碰真实交易所。
        patcher = patch(
            "scripts.ai_factor_trader.ensure_cloud_position_protection",
            return_value=(True, "mocked: cloud OCO coverage verified"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sync_cloud_algo_stop_success_and_idempotence(self):
        # US-007 后 sync 面走 okx_rest 直签；函数边界断言调用形态
        with patch.object(aft.okx_rest, "pending_algo_orders") as pending, \
             patch.object(aft.okx_rest, "amend_algo_sl") as amend:
            # 1. When existing algo already matches new_sl, do not issue redundant amend
            pending.return_value = [
                {"state": "live", "posSide": "long", "algoId": "algo_101", "slTriggerPx": "2500.0"}
            ]
            res = aft.sync_cloud_algo_stop("ETH-USDT-SWAP", "long", 2500.0)
            self.assertTrue(res)
            pending.assert_called_once_with("ETH-USDT-SWAP")
            amend.assert_not_called()

            # 2. When existing algo has different slTriggerPx, issue amend (market SL px)
            pending.reset_mock()
            pending.return_value = [
                {"state": "live", "posSide": "long", "algoId": "algo_101", "slTriggerPx": "2400.0"}
            ]
            amend.return_value = [{"algoId": "algo_101", "sCode": "0"}]
            res = aft.sync_cloud_algo_stop("ETH-USDT-SWAP", "long", 2500.0)
            self.assertTrue(res)
            amend.assert_called_once_with("algo_101", 2500.0)

    def test_sync_cloud_algo_stop_fail_closed_without_key(self):
        with patch.object(aft.okx_rest, "pending_algo_orders",
                          side_effect=aft.okx_rest.OKXNotConfigured("not configured")):
            self.assertFalse(aft.sync_cloud_algo_stop("ETH-USDT-SWAP", "long", 2500.0))

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


class CloudOcoHttpBoundaryTests(unittest.TestCase):
    """US-007 律①零容忍：OCO 补挂/复查全链路必须落在直签 HTTP 边界，
    绝不复活任何子进程路径（曾把垃圾 OCO 打进真实 demo 账户）。"""

    def setUp(self):
        from scripts.okx_runtime import freeze_environment, unfreeze_environment
        freeze_environment({
            "R20_OKX_ENV": "demo",
            "OKX_DEMO_API_KEY": "AK-T", "OKX_DEMO_SECRET_KEY": "SK-T", "OKX_DEMO_PASSPHRASE": "PP-T",
        })
        self.addCleanup(unfreeze_environment)

    def test_ensure_protection_gap_repair_oversigned_rest_at_http_edge(self):
        import json as _json
        import scripts.okx_rest as okx_rest
        live_row = {"instId": "SOL-USDT-SWAP", "state": "live", "posSide": "long", "side": "sell",
                    "ordType": "oco", "reduceOnly": "true", "sz": "4", "actualSz": "4",
                    "tpTriggerPx": "106", "slTriggerPx": "101"}
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(req)
            if req.full_url.find("/api/v5/trade/order-algo") >= 0:
                payload = {"code": "0", "data": [{"algoId": "900", "sCode": "0"}]}
            else:  # orders-algo-pending
                first = not any(c.full_url.find("/order-algo") >= 0 for c in captured)
                payload = {"code": "0", "data": [] if first else [live_row]}
            body = _json.dumps(payload).encode()

            class _R:
                def read(self): return body
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return _R()

        with patch.object(okx_rest, "urlopen", fake_urlopen), patch.object(aft.time, "sleep"):
            ok, detail = aft.ensure_cloud_position_protection("SOL-USDT-SWAP", "long", 4.0, 106.0, 101.0)
        self.assertTrue(ok, detail)
        self.assertIn("repaired and verified", detail)
        places = [c for c in captured if "/api/v5/trade/order-algo" in c.full_url]
        self.assertEqual(len(places), 1)
        post = places[0]
        self.assertEqual(post.get_method(), "POST")
        body = _json.loads(post.data.decode())
        self.assertEqual(body["instId"], "SOL-USDT-SWAP")
        self.assertEqual(body["side"], "sell")            # 平仓方向对偶
        self.assertEqual(body["posSide"], "long")
        self.assertEqual(body["ordType"], "oco")
        self.assertEqual(body["sz"], "4")
        self.assertEqual(body["tpTriggerPx"], "106")
        self.assertEqual(body["tpOrdPx"], "-1")           # 市价执行腿
        self.assertEqual(body["slTriggerPx"], "101")
        self.assertEqual(body["slOrdPx"], "-1")
        self.assertEqual(body["reduceOnly"], "true")
        self.assertEqual(body["cxlOnClosePos"], "true")
        self.assertEqual(body["tdMode"], "cross")
        lowered = {k.lower(): v for k, v in post.headers.items()}
        self.assertEqual(lowered.get("x-simulated-trading"), "1")
        self.assertTrue({"ok-access-key", "ok-access-sign", "ok-access-timestamp", "ok-access-passphrase"} <= set(lowered))
        gets = [c for c in captured if "orders-algo-pending" in c.full_url]
        self.assertGreaterEqual(len(gets), 2)             # 首查 + 至少一次复查
        self.assertIn("ordType=oco", gets[0].full_url)

    def test_ensure_protection_fail_closed_without_key_zero_network(self):
        from scripts.okx_runtime import unfreeze_environment, freeze_environment
        unfreeze_environment()
        freeze_environment({"R20_OKX_ENV": "demo"})       # 无任何键
        import scripts.okx_rest as okx_rest
        with patch.object(okx_rest, "urlopen") as spy:
            ok, detail = aft.ensure_cloud_position_protection("SOL-USDT-SWAP", "long", 4.0, 106.0, 101.0)
        self.assertFalse(ok)
        self.assertIn("NOT READY", detail)
        spy.assert_not_called()


if __name__ == "__main__":
    unittest.main()

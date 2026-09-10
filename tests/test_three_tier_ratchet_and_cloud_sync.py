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
        # US-007：CLI 包装函数已随迁移删除；改在 okx_rest 函数边界 mock。
        with patch.object(aft.okx_rest, "pending_algo_orders") as pend, \
             patch.object(aft.okx_rest, "amend_algo_sl") as amend:
            # 1. When existing algo already matches new_sl, do not issue redundant amend
            pend.return_value = [
                {"state": "live", "posSide": "long", "algoId": "algo_101", "slTriggerPx": "2500.0"}
            ]
            res = aft.sync_cloud_algo_stop("ETH-USDT-SWAP", "long", 2500.0)
            self.assertTrue(res)
            pend.assert_called_once_with("ETH-USDT-SWAP")
            amend.assert_not_called()

            # 2. When existing algo has different slTriggerPx, issue amend (market SL px)
            pend.return_value = [
                {"state": "live", "posSide": "long", "algoId": "algo_101", "slTriggerPx": "2400.0"}
            ]
            amend.return_value = [{"algoId": "algo_101", "sCode": "0"}]
            res = aft.sync_cloud_algo_stop("ETH-USDT-SWAP", "long", 2500.0)
            self.assertTrue(res)
            amend.assert_called_once_with("algo_101", 2500.0, new_sl_ord_px="-1")

            # 3. No live algo → False without touching amend
            pend.return_value = []
            self.assertFalse(aft.sync_cloud_algo_stop("ETH-USDT-SWAP", "long", 2600.0))
            amend.assert_called_once()  # unchanged from case 2

            # 4. Query/amend failures must fail closed (False), never raise past the cycle
            pend.side_effect = RuntimeError("OKX 50011: signature error")
            self.assertFalse(aft.sync_cloud_algo_stop("ETH-USDT-SWAP", "long", 2600.0))
            pend.side_effect = None
            pend.return_value = [
                {"state": "live", "posSide": "long", "algoId": "algo_101", "slTriggerPx": "2400.0"}
            ]
            amend.side_effect = RuntimeError("OKX 51088: algo not found")
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
    """US-007 终审边界：冻结假 DEMO 凭证，patch okx_rest 模块绑定的 urlopen，
    让 ensure_cloud_position_protection / sync_cloud_algo_stop 全链路真实执行到
    HTTP 边界——断言端点、签名头、x-simulated-trading 与请求体形状（ordType=oco、
    newSlTriggerPx/newSlOrdPx）。毫秒级，零联网。"""

    def setUp(self):
        import json as _json  # noqa: F401
        from scripts.okx_runtime import freeze_environment, unfreeze_environment
        freeze_environment({
            "R20_OKX_ENV": "demo",
            "OKX_DEMO_API_KEY": "AK-demo", "OKX_DEMO_SECRET_KEY": "SK-demo",
            "OKX_DEMO_PASSPHRASE": "PP-demo",
        })
        self.addCleanup(unfreeze_environment)
        self.captured = []

    def _fake(self, rows_for):
        import json
        def urlopen(req, timeout=None):
            self.captured.append(req)
            url = req.full_url
            rows = rows_for(url, req)
            m = MagicMock()
            m.read.return_value = json.dumps({"code": "0", "data": rows}).encode()
            m.__enter__.return_value = m
            m.__exit__.return_value = False
            return m
        return urlopen

    def _headers(self, req):
        return {k.lower(): v for k, v in req.header_items()}

    def test_coverage_gap_places_v5_algo_oco_then_verifies(self):
        import json
        rows_covered = [{
            "algoId": "777", "instId": "SOL-USDT-SWAP", "state": "effective",
            "posSide": "long", "side": "sell", "reduceOnly": "true", "sz": "4",
            "actualSz": "4", "tpTriggerPx": "106", "slTriggerPx": "101",
        }]
        calls = {"n": 0}
        def rows_for(url, req):
            calls["n"] += 1
            if "/api/v5/trade/order-algo" in url:
                return [{"algoId": "777", "sCode": "0"}]
            # first pending query: no coverage; subsequent: full coverage
            return [] if calls["n"] == 1 else rows_covered
        with patch.object(aft.okx_rest, "urlopen", side_effect=self._fake(rows_for)), \
             patch.object(aft.time, "sleep"):
            ok, detail = aft.ensure_cloud_position_protection("SOL-USDT-SWAP", "long", 4.0, 106, 101)
        self.assertTrue(ok, detail)
        self.assertIn("repaired and verified", detail)
        get0, post1 = self.captured[0], self.captured[1]
        self.assertIn("/api/v5/trade/orders-algo-pending", get0.full_url)
        self.assertIn("instType=SWAP", get0.full_url)
        self.assertIn("ordType=oco", get0.full_url)
        h = self._headers(post1)
        self.assertEqual(h["ok-access-key"], "AK-demo")
        self.assertEqual(h["x-simulated-trading"], "1")
        self.assertIn("ok-access-sign", h)
        body = json.loads(post1.data.decode())
        self.assertEqual(post1.get_method(), "POST")
        self.assertEqual(body["ordType"], "oco")
        self.assertEqual(body["instId"], "SOL-USDT-SWAP")
        self.assertEqual(body["side"], "sell")
        self.assertEqual(body["posSide"], "long")
        self.assertEqual(body["tdMode"], "cross")
        self.assertEqual(str(body["reduceOnly"]).lower(), "true")
        self.assertEqual(str(body["cxlOnClosePos"]).lower(), "true")
        self.assertEqual(str(body["sz"]), "4")
        self.assertEqual(str(body["tpTriggerPx"]), "106")
        self.assertEqual(str(body["slOrdPx"]), "-1")
        self.assertIn("instId=SOL-USDT-SWAP", get0.full_url)

    def test_ratchet_amend_hits_v5_amend_algos_with_market_sl_px(self):
        import json
        rows_live = [{
            "algoId": "algo_500", "instId": "ETH-USDT-SWAP", "state": "live",
            "posSide": "long", "slTriggerPx": "2400.0", "tpTriggerPx": "2600",
        }]
        def rows_for(url, req):
            if "/api/v5/trade/amend-algos" in url:
                return [{"algoId": "algo_500", "sCode": "0"}]
            return rows_live
        with patch.object(aft.okx_rest, "urlopen", side_effect=self._fake(rows_for)):
            res = aft.sync_cloud_algo_stop("ETH-USDT-SWAP", "long", 2500.0)
        self.assertTrue(res)
        get0, post1 = self.captured
        self.assertIn("/api/v5/trade/orders-algo-pending", get0.full_url)
        self.assertEqual(post1.get_method(), "POST")
        self.assertIn("/api/v5/trade/amend-algos", post1.full_url)
        body = json.loads(post1.data.decode())
        self.assertIsInstance(body, list)
        self.assertEqual(body[0]["algoId"], "algo_500")
        self.assertEqual(str(body[0]["newSlTriggerPx"]), "2500")
        self.assertEqual(body[0]["newSlOrdPx"], "-1")
        self.assertEqual(self._headers(post1)["x-simulated-trading"], "1")

    def test_missing_credentials_fail_closed_zero_http(self):
        from scripts.okx_runtime import unfreeze_environment, freeze_environment
        unfreeze_environment()
        freeze_environment({"R20_OKX_ENV": "demo"})  # no keys at all
        self.captured.clear()
        with patch.object(aft.okx_rest, "urlopen", side_effect=AssertionError("network!")):
            ok1, detail1 = aft.ensure_cloud_position_protection("SOL-USDT-SWAP", "long", 4.0, 106, 101)
            ok2 = aft.sync_cloud_algo_stop("SOL-USDT-SWAP", "long", 100.0)
        self.assertFalse(ok1)
        self.assertIn("unable to verify", detail1)
        self.assertFalse(ok2)


if __name__ == "__main__":
    unittest.main()

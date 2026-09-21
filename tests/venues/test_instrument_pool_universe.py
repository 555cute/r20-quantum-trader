"""Offline isolated test suite for Dynamic Instrument Pool & Universe Tiering.
Validates:
1. Tier classification (Tier-1 Bluechip vs Tier-2 Momentum).
2. Universe candidate scoring across liquidity, volatility, and funding rate.
3. Default universe integrity and automatic parameter backfill.
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch
import json
import sys
import unittest
from pathlib import Path

scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.instrument_pool as ip
from tests.risk_test_env import pin_baseline_risk_env


_POOL_SCOPE = None
#: 夹具池路径（用例内**局部**再 patch 一次：`pin_baseline_risk_env()` 会重载门面，
#: 模块级 patch 会被重载冲掉 ⇒ 实测"单跑绿、全量红"）
_POOL_PATH = None


def _fixture_pool(path):
    """夹具池（第二百三十四刀）：**同形**（version + instruments，字段与线上一致），
    但内容固定 —— 这样"加载器是否补齐 tier/max_leverage/sl_atr_mult"才是**确定性**断言，
    不再随线上池漂移。BTC/ETH 走 tier_1（5x），其余 tier_2（3x）；条目**故意不给**
    tier/max_leverage/sl_atr_mult，交给加载器补。
    """
    names = ["BTC", "ETH", "SOL", "DOGE", "SUI", "ADA", "XRP"]
    insts = [{"instId": f"{n}-USDT-SWAP", "name": n, "type": "crypto", "ccy": n,
              "base_sz": 1, "precision": 2, "ctVal": 1.0, "tickSz": "0.001", "minSz": "0.1"}
             for n in names]
    path.write_text(json.dumps({"version": 2, "instruments": insts}), encoding="utf-8")
    return path


def setUpModule():
    """PoolCapacity 断言同向上限=3（基线）；隔离生产 .env 当前套件值；池换成夹具。"""
    global _POOL_SCOPE
    pin_baseline_risk_env()
    tmp = tempfile.TemporaryDirectory()
    global _POOL_PATH
    pool_file = _fixture_pool(Path(tmp.name) / "instrument_pool.json")
    _POOL_PATH = pool_file
    patcher = patch.object(ip, "POOL_FILE", pool_file)
    patcher.start()
    _POOL_SCOPE = (tmp, patcher)


def tearDownModule():
    global _POOL_SCOPE
    if _POOL_SCOPE is not None:
        tmp, patcher = _POOL_SCOPE
        patcher.stop()
        tmp.cleanup()
        _POOL_SCOPE = None


class InstrumentPoolUniverseTests(unittest.TestCase):
    def setUp(self):
        self._orig_min = os.environ.get("R20_MIN_LEVERAGE")
        self._orig_max = os.environ.get("R20_MAX_LEVERAGE")
        os.environ["R20_MIN_LEVERAGE"] = "2.0"
        os.environ["R20_MAX_LEVERAGE"] = "5.0"

    def tearDown(self):
        if self._orig_min is not None:
            os.environ["R20_MIN_LEVERAGE"] = self._orig_min
        else:
            os.environ.pop("R20_MIN_LEVERAGE", None)
        if self._orig_max is not None:
            os.environ["R20_MAX_LEVERAGE"] = self._orig_max
        else:
            os.environ.pop("R20_MAX_LEVERAGE", None)

    def test_evaluate_instrument_tier(self):
        self.assertEqual(ip.evaluate_instrument_tier("BTC-USDT-SWAP", "BTC"), "tier_1_bluechip")
        self.assertEqual(ip.evaluate_instrument_tier("ETH-USDT-SWAP", "ETH"), "tier_1_bluechip")
        self.assertEqual(ip.evaluate_instrument_tier("SOL-USDT-SWAP", "SOL"), "tier_2_momentum")
        self.assertEqual(ip.evaluate_instrument_tier("DOGE-USDT-SWAP", "DOGE"), "tier_2_momentum")
        self.assertEqual(ip.evaluate_instrument_tier("SUI-USDT-SWAP", "SUI"), "tier_2_momentum")

    def test_score_universe_candidate(self):
        cand = {"instId": "SOL-USDT-SWAP", "name": "SOL"}
        # High liquidity, optimal volatility (3.5%), neutral funding
        scored = ip.score_universe_candidate(cand, vol_24h_usd=80_000_000, atr_pct=3.5, funding_rate=0.0001)
        self.assertEqual(scored["tier"], "tier_2_momentum")
        self.assertEqual(scored["max_leverage"], 3)
        self.assertEqual(scored["sl_atr_mult"], 2.2)
        self.assertGreaterEqual(scored["universe_score"], 80.0)

        # Extreme high funding rate penalty
        cand_crowded = {"instId": "DOGE-USDT-SWAP", "name": "DOGE"}
        scored_crowded = ip.score_universe_candidate(cand_crowded, vol_24h_usd=50_000_000, atr_pct=3.0, funding_rate=0.0009)
        self.assertLess(scored_crowded["universe_score"], scored["universe_score"])

    def test_load_instruments_ensures_tiers_and_risk_parameters(self):
        insts = ip.load_instruments()
        self.assertGreaterEqual(len(insts), 6)
        for item in insts:
            self.assertIn("tier", item)
            self.assertIn(item["tier"], ["tier_1_bluechip", "tier_2_momentum"])
            self.assertIn("max_leverage", item)
            self.assertIn("sl_atr_mult", item)
            if item["name"] in ("BTC", "ETH"):
                self.assertEqual(item["tier"], "tier_1_bluechip")
                self.assertEqual(item["max_leverage"], 5)
            else:
                self.assertEqual(item["tier"], "tier_2_momentum")
                self.assertEqual(item["max_leverage"], 3)


class PoolCapacityNotHardcodedTests(unittest.TestCase):
    """后台曾把标的池上限硬编码为 6，加第 7 个币直接被 409 拒绝 —— 部署者反馈「扩容跑不起来」的真凶。"""

    APP = Path(__file__).resolve().parent.parent.parent / "r20_backend" / "app.py"
    SEC = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "views" / "admin" / "SecurityPage.vue"

    def test_backend_uses_configurable_pool_cap(self):
        src = self.APP.read_text(encoding="utf-8")
        self.assertIn('os.getenv("R20_MAX_POOL_SIZE"', src, "池容量上限必须可由环境变量配置")
        self.assertIn("len(current) >= MAX_POOL_SIZE", src, "添加校验必须使用常量而非字面量 6")
        self.assertNotIn("len(current) >= 6", src, "检测到硬编码的 6 个币种上限回归")
        self.assertNotIn("最多允许 6 个币种", src, "检测到硬编码错误文案回归")

    def test_frontend_default_cap_not_stuck_at_six(self):
        src = self.SEC.read_text(encoding="utf-8")
        self.assertNotIn("maximum: 6", src, "前端默认池容量占位仍写死 6")

    def test_concurrent_positions_follow_pool_size(self):
        """并发上限**随池伸缩**（原先硬编码 6 ⇒ 第 7 个币被 409 拒）。

        ⚠️ 第二百三十四刀：原断言是 `MAX_CONCURRENT_POSITIONS == len(load_instruments())`，
        而常量是 `ai_factor_trader` **import 期**按当时的池算出的 —— 一旦本文件的池换成夹具
        （或线上加个币），这条就红，且红得与代码对错无关。改为断言**规则本身**：

        ① 用夹具池的条数走一遍规则 ⇒ 上限必须等于池容量、同向必须固定 3（确定性）；
        ② import 期那个常量必须等于「用**它自己那份**池走同一规则」的结果
           ⇒ 常量不得被硬编码成 6（这正是本用例要防的事）。
        """
        # ⚠️ 本条在**全量**运行时仍会读到线上池：`import ai_factor_trader` 会触发门面重载，
        # 重载会重新 import `scripts.instrument_pool` ⇒ 我的 `POOL_FILE` patch 被冲掉
        # （单跑本文件时该门面已在夹具生效期导入过 ⇒ 绿；全量时红 ⇒ 典型的重载顺序坑）。
        # 规则断言已保留（下面三条是确定性的），仅此一处显式放开生产读。
        from tests import allow_real_data_reads
        self._read_scope = allow_real_data_reads()
        self._read_scope.__enter__()
        self.addCleanup(self._read_scope.__exit__, None, None, None)

        import ai_factor_trader as aft
        from scripts.risk_constants import effective_max_positions

        # 局部再 patch 一次（见 `_POOL_PATH` 注释）：模块级 patch 会被重载冲掉
        with patch.object(ip, "POOL_FILE", _POOL_PATH):
            fixture_pool = len(ip.load_instruments())
            self.assertGreaterEqual(fixture_pool, 6, "夹具池至少 6 条（本用例的基线前提）")
            cap, same = effective_max_positions(fixture_pool)
            self.assertEqual(cap, fixture_pool, "并发持仓上限应随标的池自动伸缩")
            self.assertEqual(same, 3, "同向持仓上限应固定为 3(防 Beta 踩踏)，不随池扩容放大")

        live_cap, live_same = effective_max_positions(len(aft.TARGET_INSTRUMENTS))
        self.assertEqual(aft.MAX_CONCURRENT_POSITIONS, live_cap,
                         "常量必须由「随池伸缩」规则算出，不得硬编码")
        self.assertEqual(aft.MAX_SAME_DIRECTION_POSITIONS, live_same)


if __name__ == "__main__":
    unittest.main()

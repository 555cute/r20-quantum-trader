"""标的池（`scripts/instrument_pool.py`）的闸门与降级收口 —— 第 322 刀。

本模块 488 行 / 18 个公开名，此前**没有专属测试文件**。它管的是"**哪些标的能交易**"
与"每个标的的杠杆上限"，所以它的坏路径比好路径重要：池文件坏了、条目非法、
杠杆区间反了、锁不可用 —— 每一种都有明确的 fail-closed 纪律。

## 沙箱纪律

`POOL_FILE` / `TRADING_STATE_FILE` / `FACTOR_LIBRARY_FILE` / `NEWS_SENTIMENT_FILE` /
`DASHBOARD_CACHE_FILE` 全部在 `setUp` 指向临时目录 —— 本模块真会建文件、`os.replace`、
`chmod(0o600)`，并**起后台线程跑子脚本**（`_run_bg`），绝不能在真实 `data/` 上跑。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.instrument_pool as ip  # noqa: E402
from scripts.local_lock import local_file_lock  # noqa: E402


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pool = self.root / "instrument_pool.json"
        self.state = self.root / "trading_state.json"
        self.factor = self.root / "factor_library_snapshot.json"
        self.news = self.root / "news_sentiment.json"
        self.dash = self.root / "dashboard_last_good.json"
        for name, value in (("POOL_FILE", self.pool), ("TRADING_STATE_FILE", self.state),
                            ("FACTOR_LIBRARY_FILE", self.factor),
                            ("NEWS_SENTIMENT_FILE", self.news),
                            ("DASHBOARD_CACHE_FILE", self.dash)):
            p = patch.object(ip, name, value)
            p.start()
            self.addCleanup(p.stop)
        # `sync_instruments_state` 会起后台线程跑子脚本 ⇒ 默认拦掉
        self.spawned: list = []
        sp = patch.object(ip, "_run_captured",
                          lambda script, **kw: self.spawned.append((script, kw)))
        sp.start()
        self.addCleanup(sp.stop)
        self.printed: list = []
        pr = patch.object(ip, "print", lambda *a, **k: self.printed.append(" ".join(map(str, a))))
        pr.start()
        self.addCleanup(pr.stop)
        # ⚠️ `sync_instruments_state` 末尾会 `threading.Thread(...).start()`。
        #    若让它真起线程，**patch 早已退出**、线程才执行 ⇒ 会拿真实 ROOT 去拉起
        #    生产脚本（本刀实测到过：日志里出现 `/data/dsh/home/r20/scripts/…`）。
        #    这里把 `Thread` 换成"start 即同步执行"，让后台路径确定且零副作用。
        self._install_sync_thread()

    def _install_sync_thread(self):
        """把 `threading.Thread` 换成"start 即同步执行"。

        ⚠️ `threading` 是 `sync_instruments_state` **函数体内**才导入的
        （第 470 行），所以它不是 `instrument_pool` 的模块属性 ——
        必须打**真实 `threading` 模块**上的同名属性。
        """
        import threading as _threading

        class _SyncThread:
            def __init__(self, target=None, daemon=None, **kw):
                self._target = target

            def start(self):
                if self._target is not None:
                    self._target()

        th = patch.object(_threading, "Thread", _SyncThread)
        th.start()
        self.addCleanup(th.stop)

    def _write_pool(self, payload):
        self.pool.write_text(json.dumps(payload), encoding="utf-8")

    def _item(self, **over):
        item = {"instId": "BTC-USDT-SWAP", "name": "BTC", "ctVal": 1.0, "tier": "tier_1_bluechip",
                "max_leverage": 5, "sl_atr_mult": 2.0, "base_sz": 1, "precision": 2,
                "tickSz": "0.1", "minSz": "0.01", "risk_per_trade_usd": 15.0}
        item.update(over)
        return item


# ───────────────────── 分层与杠杆派生 ─────────────────────
class TierTests(unittest.TestCase):
    def test_btc_and_eth_are_bluechip(self):
        for name in ("BTC", "ETH"):
            with self.subTest(name=name):
                self.assertEqual(ip.evaluate_instrument_tier("X-USDT-SWAP", name),
                                 "tier_1_bluechip")
                self.assertEqual(ip.evaluate_instrument_tier(f"{name}-USDT-SWAP"),
                                 "tier_1_bluechip")

    def test_everything_else_is_momentum(self):
        for name in ("SOL", "DOGE", "XRP", ""):
            with self.subTest(name=name):
                self.assertEqual(ip.evaluate_instrument_tier("SOL-USDT-SWAP", name),
                                 "tier_2_momentum")

    def test_the_tier_comes_from_the_inst_id_when_the_name_is_blank(self):
        self.assertEqual(ip.evaluate_instrument_tier("eth-usdt-swap"), "tier_1_bluechip")

    def test_the_name_is_case_insensitive(self):
        self.assertEqual(ip.evaluate_instrument_tier("X", "btc"), "tier_1_bluechip")


class LeverageCapTests(unittest.TestCase):
    def test_bluechip_follows_the_global_ceiling(self):
        self.assertEqual(ip.derive_instrument_leverage_cap("tier_1_bluechip",
                                                           min_leverage=2, max_leverage=7), 7)

    def test_momentum_takes_forty_percent_of_the_span(self):
        # 出厂基线 2~5 ⇒ 2 + 3*0.4 = 3.2 ⇒ round → 3（与 DEFAULT_INSTRUMENTS 对齐）
        self.assertEqual(ip.derive_instrument_leverage_cap("tier_2_momentum",
                                                           min_leverage=2, max_leverage=5), 3)

    def test_momentum_never_drops_below_the_floor(self):
        self.assertEqual(ip.derive_instrument_leverage_cap("tier_2_momentum",
                                                           min_leverage=3, max_leverage=3), 3)

    def test_momentum_never_exceeds_the_ceiling(self):
        cap = ip.derive_instrument_leverage_cap("tier_2_momentum",
                                                min_leverage=2, max_leverage=4)
        self.assertLessEqual(cap, 4)
        self.assertGreaterEqual(cap, 2)

    def test_an_inverted_range_is_repaired_rather_than_obeyed(self):
        # `hi = max(lo, max_leverage)` —— 上限低于下限时抬到下限
        self.assertEqual(ip.derive_instrument_leverage_cap("tier_2_momentum",
                                                           min_leverage=5, max_leverage=1), 5)

    def test_a_zero_bound_is_falsy_and_falls_back_to_the_factory_band(self):
        # `float(min_leverage or 2.0)` —— **0 是 falsy** ⇒ 回落到 2.0/5.0，
        # 不是"把下限抬到 1"。要显式拿到 1 必须传 1。
        self.assertEqual(ip.derive_instrument_leverage_cap("tier_2_momentum",
                                                           min_leverage=0, max_leverage=0), 3)
        self.assertEqual(ip.derive_instrument_leverage_cap("tier_2_momentum",
                                                           min_leverage=1, max_leverage=1), 1)

    def test_the_result_is_always_an_int(self):
        for tier in ("tier_1_bluechip", "tier_2_momentum"):
            with self.subTest(tier=tier):
                self.assertIsInstance(
                    ip.derive_instrument_leverage_cap(tier, 2.0, 5.0), int)

    def test_missing_bounds_fall_back_to_risk_constants(self):
        # 不传区间 ⇒ 读 risk_constants / 环境变量
        self.assertIsInstance(ip.derive_instrument_leverage_cap("tier_2_momentum"), int)

    def test_an_unimportable_risk_constants_falls_back_to_the_factory_band(self):
        # ★ 第 94 行 —— `from scripts.risk_constants import ...` 抛 ⇒ (2.0, 5.0)。
        # ⚠️ 环境变量优先于回落常量（`os.getenv("R20_MIN_LEVERAGE")`）⇒
        #    必须把它清掉，否则会读到**别的测试留下的**值（本刀在全量里就因此在
        #    单独跑绿、合起来红 —— 典型的测试顺序依赖）。
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("R20_MIN_LEVERAGE", "R20_MAX_LEVERAGE")}
        with patch.dict(sys.modules, {"scripts.risk_constants": None}), \
             patch.dict(os.environ, clean, clear=True):
            self.assertEqual(ip.derive_instrument_leverage_cap("tier_2_momentum"), 3)


# ───────────────────── 候选评分 ─────────────────────
class UniverseScoreTests(unittest.TestCase):
    def _score(self, **kw):
        return ip.score_universe_candidate({"instId": "SOL-USDT-SWAP", "name": "SOL"}, **kw)

    def test_the_neutral_baseline_is_fifty(self):
        self.assertEqual(self._score()["universe_score"], 50.0)

    def test_good_liquidity_adds_twenty(self):
        self.assertEqual(self._score(vol_24h_usd=1e9)["universe_score"], 70.0)

    def test_poor_liquidity_subtracts_thirty(self):
        # ★ 第 141 行
        score = self._score(vol_24h_usd=1.0)["universe_score"]
        self.assertEqual(score, 20.0)

    def test_a_healthy_volatility_band_adds_twenty(self):
        for atr in (1.5, 3.0, 6.0):
            with self.subTest(atr=atr):
                self.assertEqual(self._score(atr_pct=atr)["universe_score"], 70.0)

    def test_a_sleepy_market_subtracts_fifteen(self):
        # ★ 第 147/148 行
        self.assertEqual(self._score(atr_pct=0.5)["universe_score"], 35.0)

    def test_a_rug_risk_market_subtracts_twenty_five(self):
        # ★ 第 149/150 行
        self.assertEqual(self._score(atr_pct=12.0)["universe_score"], 25.0)

    def test_the_middle_band_neither_rewards_nor_punishes(self):
        # 1.0 <= atr <= 1.5 或 6.0 < atr <= 9.0 ⇒ 不加不减
        for atr in (1.2, 8.0):
            with self.subTest(atr=atr):
                self.assertEqual(self._score(atr_pct=atr)["universe_score"], 50.0)

    def test_extreme_funding_subtracts_fifteen(self):
        self.assertEqual(self._score(funding_rate=0.001)["universe_score"], 35.0)
        self.assertEqual(self._score(funding_rate=-0.001)["universe_score"], 35.0)

    def test_normal_funding_is_ignored(self):
        self.assertEqual(self._score(funding_rate=0.0004)["universe_score"], 50.0)

    def test_the_score_is_clamped_at_zero(self):
        # 50 - 30(流动性差) - 15(太睡) - 15(费率极端) = -10 ⇒ 夹到 0
        self.assertEqual(self._score(vol_24h_usd=1.0, atr_pct=0.5,
                                     funding_rate=0.01)["universe_score"], 0.0)

    def test_the_score_is_clamped_at_one_hundred(self):
        # 上限侧目前够不到 100（最高 50+20+20 = 90）—— 断言这个事实，防止误以为有加分项
        best = self._score(vol_24h_usd=1e9, atr_pct=3.0)["universe_score"]
        self.assertEqual(best, 90.0)
        self.assertLessEqual(best, 100.0)

    def test_zero_inputs_mean_not_measured_and_are_not_penalised(self):
        # `> 0` 判据 ⇒ 缺测数据不等于"差"，不加不减（缺失≠0）
        self.assertEqual(self._score(vol_24h_usd=0.0, atr_pct=0.0)["universe_score"], 50.0)

    def test_the_candidate_is_enriched_in_place(self):
        cand = {"instId": "ETH-USDT-SWAP", "name": "ETH"}
        out = ip.score_universe_candidate(cand)
        self.assertIs(out, cand)
        for key in ("tier", "max_leverage", "sl_atr_mult", "universe_score"):
            self.assertIn(key, out)
        self.assertEqual(out["tier"], "tier_1_bluechip")
        self.assertEqual(out["sl_atr_mult"], ip.TIER_PROFILES["tier_1_bluechip"]["sl_atr_mult"])


# ───────────────────── OKX 原始行转换 ─────────────────────
class PrecisionTests(unittest.TestCase):
    def test_the_decimals_of_the_tick_size_are_counted(self):
        # ★ 第 164/165 行
        for tick, expected in (("0.1", 1), ("0.01", 2), ("0.0001", 4),
                               ("0.00001000", 5), ("1", 0), ("10", 0), ("0.001", 3)):
            with self.subTest(tick=tick):
                self.assertEqual(ip._precision(tick), expected)

    def test_trailing_zeros_are_stripped_before_counting(self):
        # `rstrip("0")` ⇒ "0.0100" → "0.01" → 2；"1.0000" → "1." → 0
        self.assertEqual(ip._precision("0.0100"), 2)
        self.assertEqual(ip._precision("1.0000"), 0)

    def test_a_tick_size_without_a_dot_is_zero_precision(self):
        self.assertEqual(ip._precision("100"), 0)


class FromOkxInstrumentTests(unittest.TestCase):
    def _convert(self, raw):
        return ip.from_okx_instrument(raw)

    def test_a_full_raw_row_is_converted(self):
        # ★ 第 169–174 行 —— 整个函数此前从未被执行
        out = self._convert({"instId": "sol-usdt-swap", "baseCcy": "sol",
                             "tickSz": "0.001", "ctVal": "2", "minSz": "0.5"})
        self.assertEqual(out["instId"], "SOL-USDT-SWAP")
        self.assertEqual(out["name"], "SOL")
        self.assertEqual(out["ccy"], "SOL")
        self.assertEqual(out["type"], "crypto")
        self.assertEqual(out["precision"], 3)
        self.assertEqual(out["ctVal"], 2.0)
        self.assertEqual(out["minSz"], "0.5")

    def test_the_base_currency_falls_back_to_the_inst_id_prefix(self):
        # ★ 第 170 行
        self.assertEqual(self._convert({"instId": "doge-usdt-swap"})["name"], "DOGE")

    def test_a_missing_tick_size_falls_back_to_the_documented_default(self):
        # ★ 第 171 行
        self.assertEqual(self._convert({"instId": "X-USDT-SWAP"})["tickSz"], "0.0001")
        self.assertEqual(self._convert({"instId": "X-USDT-SWAP"})["precision"], 4)

    def test_a_missing_ct_val_defaults_to_one(self):
        self.assertEqual(self._convert({"instId": "X-USDT-SWAP"})["ctVal"], 1.0)

    def test_a_missing_min_size_defaults_to_one(self):
        self.assertEqual(self._convert({"instId": "X-USDT-SWAP"})["minSz"], "1")

    def test_bluechip_and_momentum_get_their_own_profile(self):
        btc = self._convert({"instId": "BTC-USDT-SWAP"})
        sol = self._convert({"instId": "SOL-USDT-SWAP"})
        self.assertEqual(btc["tier"], "tier_1_bluechip")
        self.assertEqual(sol["tier"], "tier_2_momentum")
        self.assertEqual(btc["sl_atr_mult"],
                         ip.TIER_PROFILES["tier_1_bluechip"]["sl_atr_mult"])
        self.assertEqual(sol["sl_atr_mult"],
                         ip.TIER_PROFILES["tier_2_momentum"]["sl_atr_mult"])

    def test_the_result_passes_the_pool_validator(self):
        out = self._convert({"instId": "SOL-USDT-SWAP"})
        kept, dropped = ip._validate_pool_items([out])
        self.assertEqual(dropped, [])
        self.assertEqual(len(kept), 1)


# ───────────────────── 池文件读取的五态 ─────────────────────
class LoadInstrumentsTests(_Sandbox, unittest.TestCase):
    def test_a_missing_file_is_missing_state_with_the_factory_defaults(self):
        out = ip.load_instruments()
        self.assertEqual(ip.pool_state()["status"], "missing")
        self.assertFalse(ip.pool_is_trustworthy())
        self.assertEqual(len(out), len(ip.DEFAULT_INSTRUMENTS))
        self.assertTrue(any("不开新仓" in line for line in self.printed))

    def test_the_factory_fallback_is_copied_not_aliased(self):
        out = ip.load_instruments()
        out[0]["max_leverage"] = 999
        self.assertNotEqual(ip.DEFAULT_INSTRUMENTS[0].get("max_leverage"), 999)

    def test_corrupt_json_is_corrupt_state(self):
        self.pool.write_text("{ broken", encoding="utf-8")
        out = ip.load_instruments()
        self.assertEqual(ip.pool_state()["status"], "corrupt")
        self.assertFalse(ip.pool_is_trustworthy())
        self.assertEqual(len(out), len(ip.DEFAULT_INSTRUMENTS))

    def test_an_unreadable_file_is_also_corrupt_state(self):
        self.pool.mkdir(parents=True)      # 目录 ⇒ OSError
        ip.load_instruments()
        self.assertEqual(ip.pool_state()["status"], "corrupt")

    def test_a_bare_envelope_and_a_bare_list_are_both_accepted(self):
        self._write_pool({"instruments": [self._item()]})
        self.assertEqual(len(ip.load_instruments()), 1)
        self._write_pool([self._item()])
        self.assertEqual(len(ip.load_instruments()), 1)

    def test_an_empty_instrument_list_is_empty_state(self):
        # ★ 第 249 行
        self._write_pool({"instruments": []})
        out = ip.load_instruments()
        self.assertEqual(ip.pool_state()["status"], "empty")
        self.assertEqual(len(out), len(ip.DEFAULT_INSTRUMENTS))

    def test_a_non_list_instruments_payload_is_empty_state(self):
        self._write_pool({"instruments": "junk"})
        ip.load_instruments()
        self.assertEqual(ip.pool_state()["status"], "empty")

    def test_an_all_invalid_pool_is_invalid_state(self):
        # ★ 第 256 行
        self._write_pool([{"instId": "X"}, {"instId": "Y", "name": "", "ctVal": 1}])
        out = ip.load_instruments()
        self.assertEqual(ip.pool_state()["status"], "invalid")
        self.assertEqual(len(out), len(ip.DEFAULT_INSTRUMENTS))

    def test_a_partially_valid_pool_is_invalid_but_keeps_the_good_entries(self):
        self._write_pool([self._item(), {"instId": "BAD"}])
        out = ip.load_instruments()
        self.assertEqual(ip.pool_state()["status"], "invalid")
        self.assertEqual([i["instId"] for i in out], ["BTC-USDT-SWAP"])
        self.assertEqual(len(ip.pool_state()["dropped"]), 1)

    def test_a_fully_valid_pool_is_ok_and_trustworthy(self):
        self._write_pool([self._item()])
        out = ip.load_instruments()
        self.assertEqual(ip.pool_state()["status"], "ok")
        self.assertTrue(ip.pool_is_trustworthy())
        self.assertEqual(len(out), 1)

    def test_pool_state_returns_a_copy_of_the_dropped_list(self):
        self._write_pool([self._item(), {"instId": "BAD"}])
        ip.load_instruments()
        snapshot = ip.pool_state()
        snapshot["dropped"].append("injected")
        self.assertEqual(len(ip.pool_state()["dropped"]), 1)


class ValidatePoolItemsTests(unittest.TestCase):
    def test_a_non_dict_entry_is_dropped_by_index_and_type(self):
        # ★ 第 217 行
        kept, dropped = ip._validate_pool_items(["junk", 42, None])
        self.assertEqual(kept, [])
        self.assertEqual(dropped, ["#0(str)", "#1(int)", "#2(NoneType)"])

    def test_a_missing_required_field_is_dropped_by_name(self):
        kept, dropped = ip._validate_pool_items([{"instId": "X-USDT-SWAP", "name": "X"}])
        self.assertEqual(kept, [])
        self.assertIn("缺 ctVal", dropped[0])

    def test_a_blank_required_field_counts_as_missing(self):
        _, dropped = ip._validate_pool_items(
            [{"instId": "X-USDT-SWAP", "name": "   ", "ctVal": 1}])
        self.assertIn("缺 name", dropped[0])

    def test_a_non_numeric_ct_val_is_dropped(self):
        # ★ 第 226 行
        kept, dropped = ip._validate_pool_items(
            [{"instId": "X-USDT-SWAP", "name": "X", "ctVal": "abc"}])
        self.assertEqual(kept, [])
        self.assertIn("ctVal 非数值", dropped[0])

    def test_a_numeric_string_ct_val_is_accepted(self):
        kept, dropped = ip._validate_pool_items(
            [{"instId": "X-USDT-SWAP", "name": "X", "ctVal": "2.5"}])
        self.assertEqual(dropped, [])
        self.assertEqual(len(kept), 1)

    def test_an_unnamed_bad_entry_is_labelled_with_a_question_mark(self):
        _, dropped = ip._validate_pool_items([{"ctVal": 1}])
        self.assertIn("?", dropped[0])


class LeverageRealignTests(_Sandbox, unittest.TestCase):
    def test_a_cap_below_the_global_floor_is_realigned(self):
        self._write_pool([self._item(tier="tier_2_momentum", max_leverage=1)])
        with patch.dict(os.environ, {"R20_MIN_LEVERAGE": "2", "R20_MAX_LEVERAGE": "5"}):
            out = ip.load_instruments()
        self.assertEqual(out[0]["max_leverage"], 3)

    def test_a_cap_above_the_global_ceiling_is_realigned(self):
        self._write_pool([self._item(tier="tier_2_momentum", max_leverage=99)])
        with patch.dict(os.environ, {"R20_MIN_LEVERAGE": "2", "R20_MAX_LEVERAGE": "5"}):
            out = ip.load_instruments()
        self.assertEqual(out[0]["max_leverage"], 3)

    def test_a_bluechip_not_tracking_the_ceiling_is_realigned(self):
        self._write_pool([self._item(tier="tier_1_bluechip", max_leverage=2)])
        with patch.dict(os.environ, {"R20_MIN_LEVERAGE": "2", "R20_MAX_LEVERAGE": "5"}):
            out = ip.load_instruments()
        self.assertEqual(out[0]["max_leverage"], 5)

    def test_a_compliant_cap_is_left_untouched(self):
        self._write_pool([self._item(tier="tier_2_momentum", max_leverage=3)])
        with patch.dict(os.environ, {"R20_MIN_LEVERAGE": "2", "R20_MAX_LEVERAGE": "5"}):
            out = ip.load_instruments()
        self.assertEqual(out[0]["max_leverage"], 3)

    def test_a_missing_tier_and_sl_mult_are_backfilled(self):
        item = self._item()
        item.pop("tier")
        item.pop("sl_atr_mult")
        self._write_pool([item])
        out = ip.load_instruments()
        self.assertEqual(out[0]["tier"], "tier_1_bluechip")
        self.assertEqual(out[0]["sl_atr_mult"],
                         ip.TIER_PROFILES["tier_1_bluechip"]["sl_atr_mult"])

    def test_an_inverted_global_range_is_clamped_without_crashing(self):
        # ★ 第 264 行 —— 下限高于上限时把下限压到上限
        self._write_pool([self._item(tier="tier_2_momentum", max_leverage=3)])
        with patch.dict(os.environ, {"R20_MIN_LEVERAGE": "9", "R20_MAX_LEVERAGE": "5"}):
            out = ip.load_instruments()
        self.assertLessEqual(out[0]["max_leverage"], 5)

    def test_an_unimportable_risk_constants_uses_the_factory_band(self):
        # ★ 第 260 行
        self._write_pool([self._item(tier="tier_2_momentum", max_leverage=3)])
        with patch.dict(sys.modules, {"scripts.risk_constants": None}), \
             patch.dict(os.environ, {}, clear=False):
            for key in ("R20_MIN_LEVERAGE", "R20_MAX_LEVERAGE"):
                os.environ.pop(key, None)
            out = ip.load_instruments()
        self.assertEqual(out[0]["max_leverage"], 3)


# ───────────────────── 锁与写入 ─────────────────────
class PoolLockTests(_Sandbox, unittest.TestCase):
    def test_the_backend_lock_is_preferred(self):
        import r20_backend.file_locks as fl
        sentinel = object()
        with patch.object(fl, "file_lock", lambda p: sentinel):
            self.assertIs(ip._pool_lock(), sentinel)

    def test_an_unavailable_backend_lock_falls_back_to_the_local_lock(self):
        # ★ 第 296 行 —— 绝不在"锁不可用"时静默放行
        with patch.dict(sys.modules, {"r20_backend.file_locks": None}):
            self.assertIs(ip._pool_lock().__class__, local_file_lock(ip.POOL_FILE).__class__)

    def test_the_fallback_lock_is_reentrant(self):
        # 兜底不可重入会让 `mutate_instruments` 在锁内调 `save_instruments` 时自锁挂死
        with patch.dict(sys.modules, {"r20_backend.file_locks": None}):
            with ip._pool_lock():
                with ip._pool_lock():
                    reentered = True
        self.assertTrue(reentered)


class MutateInstrumentsTests(_Sandbox, unittest.TestCase):
    def test_the_mutation_is_persisted(self):
        self._write_pool([self._item()])
        out = ip.mutate_instruments(lambda pool: pool + [self._item(instId="ETH-USDT-SWAP",
                                                                   name="ETH")])
        self.assertEqual(len(out), 2)
        self.assertEqual(len(json.loads(self.pool.read_text(encoding="utf-8"))["instruments"]), 2)

    def test_returning_none_means_no_write(self):
        # ★ 第 309 行 —— 变异器返回 None ⇒ 保留当前池（"我不想改"）
        self._write_pool([self._item()])
        before = self.pool.read_text(encoding="utf-8")
        out = ip.mutate_instruments(lambda pool: None)
        self.assertEqual(len(out), 1)
        self.assertEqual(self.pool.read_text(encoding="utf-8"), before)

    def test_the_mutator_receives_a_copy_not_the_live_list(self):
        self._write_pool([self._item()])
        seen = []

        def mutator(pool):
            seen.append(pool)
            pool[0]["name"] = "MUTATED"
            return None
        ip.mutate_instruments(mutator)
        self.assertEqual(seen[0][0]["name"], "MUTATED")
        self.assertEqual(ip.load_instruments()[0]["name"], "BTC")

    def test_the_write_is_atomic_and_owner_only(self):
        self._write_pool([self._item()])
        ip.mutate_instruments(lambda pool: pool)
        self.assertEqual(self.pool.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in self.root.glob("*.tmp")], [])


class WritePoolFileTests(_Sandbox, unittest.TestCase):
    def test_a_sync_failure_does_not_fail_the_save(self):
        # ★ 第 335 行 —— 下游扇出失败不许让已成功的池保存变成异常
        self._write_pool([self._item()])
        with patch.object(ip, "sync_instruments_state", side_effect=RuntimeError("boom")):
            ip.save_instruments([self._item()])
        self.assertEqual(len(ip.load_instruments()), 1)

    def test_the_envelope_carries_the_schema_version(self):
        ip.save_instruments([self._item()])
        self.assertEqual(json.loads(self.pool.read_text(encoding="utf-8"))["version"], 1)

    def test_the_target_directory_is_created(self):
        nested = self.root / "deep" / "pool.json"
        with patch.object(ip, "POOL_FILE", nested):
            ip.save_instruments([self._item()])
        self.assertTrue(nested.exists())

    def test_no_temporary_file_survives(self):
        ip.save_instruments([self._item()])
        self.assertEqual([p.name for p in self.root.glob(".instrument-pool-*")], [])


class WriteJsonAtomicTests(_Sandbox, unittest.TestCase):
    def test_a_payload_is_written_owner_only(self):
        target = self.root / "atomic.json"
        ip._write_json_atomic(target, {"a": 1})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": 1})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_a_write_failure_cleans_up_and_reraises(self):
        # ★ 第 352/355/356 行 —— 失败时删临时文件，并把异常**原样抛出**（不吞）
        target = self.root / "atomic.json"
        with patch.object(ip.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                ip._write_json_atomic(target, {"a": 1})
        self.assertEqual([p.name for p in self.root.glob(".atomic.json-*")], [])
        self.assertFalse(target.exists())

    def test_a_unlink_failure_during_cleanup_is_swallowed(self):
        # ★ 第 355 行 —— 清理失败只吞掉，**原始异常照旧抛出**
        target = self.root / "atomic.json"
        with patch.object(ip.os, "replace", side_effect=OSError("disk full")), \
             patch.object(ip.os, "unlink", side_effect=OSError("read-only")):
            with self.assertRaises(OSError) as ctx:
                ip._write_json_atomic(target, {"a": 1})
        self.assertIn("disk full", str(ctx.exception))

    def test_json_is_written_with_an_indent(self):
        target = self.root / "atomic.json"
        ip._write_json_atomic(target, {"a": 1})
        self.assertIn("\n", target.read_text(encoding="utf-8"))


# ───────────────────── 下游扇出（sync_instruments_state） ─────────────────────
class SyncInstrumentsStateTests(_Sandbox, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._write_pool([self._item(), self._item(instId="SOL-USDT-SWAP", name="SOL",
                                                   tier="tier_2_momentum",
                                                   max_leverage=3)])

    def test_trading_state_is_rebuilt_from_the_active_pool(self):
        ip.sync_instruments_state()
        data = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual([i["instId"] for i in data["instruments"]],
                         ["BTC-USDT-SWAP", "SOL-USDT-SWAP"])
        self.assertEqual(data["max_positions"], 2)

    def test_existing_state_entries_are_preserved_not_reinitialised(self):
        self.state.write_text(json.dumps({"instruments": [
            {"instId": "BTC-USDT-SWAP", "name": "BTC", "price": 61234.0}]}),
            encoding="utf-8")
        ip.sync_instruments_state()
        data = json.loads(self.state.read_text(encoding="utf-8"))
        btc = next(i for i in data["instruments"] if i["instId"] == "BTC-USDT-SWAP")
        self.assertEqual(btc["price"], 61234.0, "既有条目要原样保留")

    def test_a_new_coin_gets_a_neutral_baseline(self):
        self.state.write_text(json.dumps({"instruments": []}), encoding="utf-8")
        ip.sync_instruments_state()
        data = json.loads(self.state.read_text(encoding="utf-8"))
        sol = next(i for i in data["instruments"] if i["instId"] == "SOL-USDT-SWAP")
        self.assertEqual(sol["action"], "WAIT")
        self.assertEqual(sol["strategy"], "⚪ 观望")
        self.assertEqual(sol["price"], "--")
        self.assertIsNone(sol["position"])

    def test_a_corrupt_trading_state_is_treated_as_empty(self):
        # ★ 第 378 行
        self.state.write_text("{ broken", encoding="utf-8")
        ip.sync_instruments_state()
        data = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(len(data["instruments"]), 2)

    def test_a_top_level_list_trading_state_raises_out_of_the_sync(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：第 374–378 行的 try **只包住读+解析**，
        #    第 380 行的 `state_data.get("instruments", [])` 在 try **之外** ⇒
        #    顶层是数组（合法 JSON、非法形状）时抛 `AttributeError` 冒出
        #    `sync_instruments_state()`。它是在 `_write_pool_file` 的 try 里被调用的
        #    （第 332–335 行），所以保存路径只是静默跳过扇出；
        #    但**直接调用**它的地方（如管理端"刷新下游"）会吃到异常。
        self.state.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(AttributeError):
            ip.sync_instruments_state()

    def test_a_blank_trading_state_file_is_treated_as_missing(self):
        self.state.write_text("", encoding="utf-8")
        ip.sync_instruments_state()
        self.assertTrue(self.state.exists())

    def test_a_trading_state_write_failure_is_swallowed(self):
        # ★ 第 418 行
        with patch.object(ip, "_write_json_atomic", side_effect=OSError("boom")):
            ip.sync_instruments_state()
        self.assertFalse(self.state.exists())

    def test_the_factor_library_is_pruned_to_the_active_pool(self):
        self.factor.write_text(json.dumps({"instruments": [
            {"instId": "BTC-USDT-SWAP"}, {"instId": "DELETED-USDT-SWAP"}, "junk"]}),
            encoding="utf-8")
        ip.sync_instruments_state()
        data = json.loads(self.factor.read_text(encoding="utf-8"))
        self.assertEqual([i["instId"] for i in data["instruments"]], ["BTC-USDT-SWAP"])

    def test_a_factor_library_without_an_instruments_key_is_left_alone(self):
        original = json.dumps({"other": 1})
        self.factor.write_text(original, encoding="utf-8")
        ip.sync_instruments_state()
        self.assertEqual(self.factor.read_text(encoding="utf-8"), original)

    def test_a_factor_library_write_failure_is_swallowed(self):
        # ★ 第 431 行
        self.factor.write_text(json.dumps({"instruments": [{"instId": "BTC-USDT-SWAP"}]}),
                               encoding="utf-8")
        with patch.object(ip, "_write_json_atomic", side_effect=OSError("boom")):
            ip.sync_instruments_state()
        self.assertTrue(self.factor.exists())

    def test_news_sentiment_is_pruned_and_backfilled(self):
        self.news.write_text(json.dumps({"coins_sentiment": {
            "BTC": {"ccy": "BTC", "label": "bullish"},
            "DELETED": {"ccy": "DELETED"}}}), encoding="utf-8")
        ip.sync_instruments_state()
        coins = json.loads(self.news.read_text(encoding="utf-8"))["coins_sentiment"]
        self.assertEqual(sorted(coins), ["BTC", "SOL"])
        self.assertEqual(coins["BTC"]["label"], "bullish", "既有情绪要保留")

    def test_a_backfilled_coin_gets_a_neutral_baseline(self):
        # ★ 第 442 行
        self.news.write_text(json.dumps({"coins_sentiment": {}}), encoding="utf-8")
        ip.sync_instruments_state()
        coins = json.loads(self.news.read_text(encoding="utf-8"))["coins_sentiment"]
        self.assertEqual(coins["SOL"]["label"], "neutral")
        self.assertEqual(coins["SOL"]["bullish_ratio"], "50.0%")
        self.assertEqual(coins["SOL"]["mentions"], 0)
        self.assertEqual(coins["SOL"]["sentiment_factor_score"], 0.0)

    def test_news_without_a_coins_key_is_left_alone(self):
        original = json.dumps({"other": 1})
        self.news.write_text(original, encoding="utf-8")
        ip.sync_instruments_state()
        self.assertEqual(self.news.read_text(encoding="utf-8"), original)

    def test_a_news_write_failure_is_swallowed(self):
        # ★ 第 459 行
        self.news.write_text(json.dumps({"coins_sentiment": {}}), encoding="utf-8")
        with patch.object(ip, "_write_json_atomic", side_effect=OSError("boom")):
            ip.sync_instruments_state()
        self.assertTrue(self.news.exists())

    def test_the_dashboard_cache_is_invalidated(self):
        self.dash.write_text("{}", encoding="utf-8")
        ip.sync_instruments_state()
        self.assertFalse(self.dash.exists())

    def test_a_dashboard_unlink_failure_is_swallowed(self):
        # ★ 第 466 行
        self.dash.write_text("{}", encoding="utf-8")
        with patch.object(ip.Path, "unlink", side_effect=OSError("busy")):
            ip.sync_instruments_state()
        self.assertTrue(self.dash.exists())

    def test_the_factor_and_news_scripts_are_launched(self):
        # 线程被换成同步执行 ⇒ ROOT 的 patch 在整个过程中有效
        scripts = self.root / "scripts"
        scripts.mkdir(exist_ok=True)
        for name in ("factor_library.py", "news_sentiment_harvester.py"):
            (scripts / name).write_text("pass", encoding="utf-8")
        with patch.object(ip, "ROOT", self.root):
            ip.sync_instruments_state()
        self.assertEqual(sorted(Path(s).name for s, _ in self.spawned),
                         ["factor_library.py", "news_sentiment_harvester.py"])

    def test_the_background_thread_receives_an_environment_snapshot(self):
        # 第 76 刀：线程启动**前**抓快照，否则会拿到测试沙箱还原后的干净环境
        scripts = self.root / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "factor_library.py").write_text("pass", encoding="utf-8")
        with patch.object(ip, "ROOT", self.root):
            ip.sync_instruments_state()
        self.assertTrue(self.spawned)
        env = self.spawned[0][1].get("env")
        self.assertIsInstance(env, dict)
        self.assertIsNot(env, os.environ, "必须是快照而不是活的 os.environ")

    def test_a_missing_script_is_skipped(self):
        # ROOT 指向没有 scripts/ 的目录 ⇒ 两个脚本都不该被拉起
        with patch.object(ip, "ROOT", self.root):
            ip.sync_instruments_state()
        self.assertEqual(self.spawned, [])

    def test_a_background_failure_is_swallowed(self):
        # ★ 第 486 行
        scripts = self.root / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "factor_library.py").write_text("pass", encoding="utf-8")
        with patch.object(ip, "ROOT", self.root), \
             patch.object(ip, "_run_captured", side_effect=RuntimeError("boom")):
            ip.sync_instruments_state()
        self.assertTrue(self.state.exists(), "后台线程崩了也不许影响主流程")


class SyncPoolLeverageCapsTests(_Sandbox, unittest.TestCase):
    def test_every_entry_is_realigned_and_persisted(self):
        self._write_pool([self._item(tier="tier_1_bluechip", max_leverage=1),
                          self._item(instId="SOL-USDT-SWAP", name="SOL",
                                     tier="tier_2_momentum", max_leverage=99)])
        out = ip.sync_pool_leverage_caps(min_leverage=2, max_leverage=5)
        caps = {i["name"]: i["max_leverage"] for i in out}
        self.assertEqual(caps, {"BTC": 5, "SOL": 3})

    def test_a_missing_tier_is_derived_from_the_inst_id(self):
        item = self._item(instId="SOL-USDT-SWAP", name="SOL")
        item.pop("tier")
        self._write_pool([item])
        out = ip.sync_pool_leverage_caps(min_leverage=2, max_leverage=5)
        self.assertEqual(out[0]["tier"], "tier_2_momentum")
        self.assertEqual(out[0]["max_leverage"], 3)


if __name__ == "__main__":
    unittest.main()

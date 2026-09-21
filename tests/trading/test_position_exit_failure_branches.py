"""持仓退出的**失败分支与锁利分支**（第二百一十二刀）。

`scripts/trader/position_exit.py::manage_position_tp_and_trailing` 是"该退出时真的退出"的落点。
全量探针点出 32 行未执行，集中在四条退出链上：

| 退出链 | 未执行的行 |
|---|---|
| 硬止损 | 平仓**失败** ⇒ 保留仓位并把失败说清楚（131）|
| 云端保护失效 | 保护与退出**都失败** ⇒ 吼出来（157）|
| 时间止损 | 平仓成功/失败、通知、清理（177-192）|
| 阶梯锁利 | 触发锁利平仓、平仓**失败**、通知（225-248）|
| 默认止盈 | tracker 缺 `takeProfitPx` 时补一个（149）|

这四条链的共同点是「**要做的事没做成时绝不假装做成**」：平仓失败时必须 `return False`
并把仓位仍然留着这件事写进 `executed_actions`（而不是静默继续下一步）。
"""

import time
import unittest

from scripts.trader.position_exit import manage_position_tp_and_trailing

HOUR = 3600
PROFILES = {"crypto": {"sl_atr_mult": 1.0, "tp_atr_mult": 2.0, "min_profit_ratio": 0.01}}


def _f(**kw):
    base = dict(name="BTC-USDT-SWAP", instId="BTC-USDT-SWAP", price=70000.0, atr=700.0,
                precision=1, ctVal=0.01, type="crypto", market_data_valid=True)
    base.update(kw)
    return base


def _pos(**kw):
    base = dict(pos=1.0, side="long", avgPx=70000.0, upl=42.0)
    base.update(kw)
    return base


class _Rig:
    """把 `manage_position_tp_and_trailing` 的**全部注入项**都换成记录器。

    该函数的依赖全是关键字注入（抽取时定的形状）⇒ 单元测试不需要任何 patch，
    只需要给全这些 callable。这也正是"可测性来自架构"的例子。
    """

    def __init__(self, *, hard_stop=False, protection=(True, "ok"), close=(True, "ok"),
                 floor=None, stage_desc="", entry_ts=None, high_water=None, old_sl=69000.0,
                 protect_raises=False, side="long", low_water=None):
        self.actions = []
        self.trades = []
        self.notifies = []
        self.cooldowns = []
        self.synced = []
        self.hard_stop = hard_stop
        self.protection = protection
        self.close = close
        self.floor = floor
        self.stage_desc = stage_desc
        self.entry_ts = entry_ts
        self.high_water = high_water
        self.old_sl = old_sl
        self.protect_raises = protect_raises
        self.low_water = low_water
        self._pos = _pos(side=side, avgPx=70000.0)

    # ── 注入项 ────────────────────────────────────────────────────────
    def kwargs(self):
        return dict(
            _float_or_zero=lambda v: float(v or 0.0),
            add_stop_cooldown=lambda *a, **k: self.cooldowns.append(a),
            build_signal_snapshot=lambda f: {"snap": True},
            close_position_confirmed=self._close,
            ensure_cloud_position_protection=self._protect,
            evaluate_asset_signal=lambda f: (0.9, "BUY_LONG", [], "⚪ 观望", ""),
            record_signal_snapshot=lambda payload: None,
            record_trade=lambda payload: self.trades.append(payload),
            sync_cloud_algo_stop=lambda *a, **k: self.synced.append((a, k)),
            ASSET_CLASS_PROFILES=PROFILES,
            TAKER_FEE_RATE=0.0005,
            TIME_STOP_ATR_BAND=0.5,
            TIME_STOP_HOURS=24.0,
            _close_fee=lambda *a: 1.0,
            _close_trade_payload=lambda **k: k,
            notify_trade_close=lambda **k: self.notifies.append(k),
            protection_signals=lambda **k: self.hard_stop,
            ratcheted_trailing_stop=self._ratchet,
        )

    def _close(self, *a, **k):
        return self.close

    def _protect(self, *a, **k):
        if self.protect_raises:
            raise RuntimeError("protect boom")
        return self.protection

    def _ratchet(self, *, is_long, entry_px, atr, prec, peak_profit_px, old_sl, **k):
        if self.floor is None:
            return old_sl, self.stage_desc
        return self.floor, self.stage_desc

    def trackers(self, f):
        key = f"{f['instId']}_{self._pos['side']}"
        tracker = {"instId": f["instId"], "name": f["name"], "side": self._pos["side"],
                   "policy_version": "v1", "policy_hash": "h", "strategy_tag": "t",
                   "entryPx": self._pos["avgPx"],
                   "entryTs": self.entry_ts if self.entry_ts is not None else int(time.time()),
                   "entryTime": "2026-09-21 10:00:00",
                   "initialSz": self._pos["pos"], "currentSz": self._pos["pos"],
                   "highWaterMark": self.high_water if self.high_water is not None else f["price"],
                   "lowWaterMark": self.low_water if self.low_water is not None else f["price"],
                   "trailingStopPx": self.old_sl, "takeProfitPx": 74000.0,
                   "signal_snapshot": {}, "stage_desc": "持有监控中"}
        return key, {key: tracker}


class ExitFailureBranchTest(unittest.TestCase):
    def _run(self, rig, f=None):
        f = f or _f()
        key, trackers = rig.trackers(f)
        ok, detail = manage_position_tp_and_trailing(
            f, rig._pos, trackers, "2026-09-21 12:00:00", rig.actions, **rig.kwargs())
        return ok, detail, trackers, key

    # ── 131：硬止损平仓失败 ────────────────────────────────────────────
    def test_hard_stop_close_failure_keeps_the_position_and_says_so(self):
        rig = _Rig(hard_stop=True, close=(False, "交易所拒绝"))
        ok, detail, trackers, key = self._run(rig)
        self.assertFalse(ok)
        self.assertEqual(detail, "硬止损平仓失败")
        self.assertIn(key, trackers, "平仓失败时**绝不能**把 tracker 删掉（仓位还在）")
        self.assertTrue(any("仓位仍保留" in a for a in rig.actions),
                        f"失败必须写进 executed_actions：{rig.actions}")
        self.assertEqual(rig.trades, [], "没平掉就不许记成交")

    def test_hard_stop_close_success_records_and_clears(self):
        rig = _Rig(hard_stop=True)
        ok, detail, trackers, key = self._run(rig)
        self.assertTrue(ok)
        self.assertEqual(detail, "已硬止损")
        self.assertNotIn(key, trackers, "平仓确认后才能清 tracker")
        self.assertEqual(len(rig.trades), 1)
        self.assertTrue(rig.cooldowns, "硬止损后必须进冷却（不许马上再进）")

    # ── 157：保护与退出均失败 ──────────────────────────────────────────
    def test_protection_lost_and_close_failure_is_escalated(self):
        rig = _Rig(protection=(False, "云端 OCO 缺失"), close=(False, "交易所拒绝"))
        ok, detail, trackers, key = self._run(rig)
        self.assertFalse(ok)
        self.assertEqual(detail, "保护与退出均失败")
        self.assertIn(key, trackers)
        self.assertTrue(any("安全退出失败" in a for a in rig.actions), rig.actions)

    def test_protection_lost_but_close_succeeds_is_a_safe_exit(self):
        rig = _Rig(protection=(False, "云端 OCO 缺失"))
        ok, detail, trackers, key = self._run(rig)
        self.assertTrue(ok)
        self.assertEqual(detail, "保护失效安全退出")
        self.assertNotIn(key, trackers)
        self.assertTrue(rig.notifies, "安全退出也要通知（不能悄悄平掉）")

    # ── 149：缺 takeProfitPx 时补默认 ─────────────────────────────────
    def test_missing_take_profit_gets_a_default(self):
        rig = _Rig()
        key, trackers = rig.trackers(_f())
        del trackers[key]["takeProfitPx"]
        manage_position_tp_and_trailing(_f(), rig._pos, trackers, "2026-09-21 12:00:00",
                                        rig.actions, **rig.kwargs())
        self.assertGreater(trackers[key]["takeProfitPx"], 0,
                           "缺止盈价时必须补一个（不许留空让云端腿没止盈）")

    # ── 177-192：时间止损 ─────────────────────────────────────────────
    def _time_stop_rig(self, **kw):
        # 持仓超过 TIME_STOP_HOURS 且价格几乎没动（|cur_profit| < 0.5*atr）
        return _Rig(entry_ts=int(time.time()) - int(25 * HOUR), **kw)

    def test_time_stop_closes_records_notifies_and_clears(self):
        rig = self._time_stop_rig()
        ok, detail, trackers, key = self._run(rig)
        self.assertTrue(ok)
        self.assertEqual(detail, "时间止损")
        self.assertNotIn(key, trackers)
        self.assertEqual(len(rig.trades), 1)
        self.assertTrue(rig.notifies, "时间止损要通知")
        self.assertTrue(any("时间止损" in a for a in rig.actions), rig.actions)

    def test_time_stop_close_failure_keeps_the_position(self):
        rig = self._time_stop_rig(close=(False, "交易所拒绝"))
        ok, detail, trackers, key = self._run(rig)
        self.assertFalse(ok)
        self.assertEqual(detail, "平仓失败")
        self.assertIn(key, trackers, "时间止损平仓失败 ⇒ 仓位还在 ⇒ tracker 必须保留")
        self.assertEqual(rig.trades, [])

    # ── 225-248：阶梯锁利 ─────────────────────────────────────────────
    def _ratchet_rig(self, **kw):
        # 峰值利润 ≥ 1.5*ATR（触发线），当前价已跌回锁利线 ⇒ 触发阶梯锁利
        return _Rig(floor=71000.0, stage_desc="🔒 锁利一档", old_sl=69000.0,
                    high_water=71500.0, **kw)

    def test_ratchet_floor_stop_locks_profit_and_syncs_the_stop(self):
        rig = self._ratchet_rig()
        f = _f(price=71000.0)
        rig._pos = _pos(avgPx=70000.0)
        ok, detail, trackers, key = self._run(rig, f)
        self.assertTrue(ok)
        self.assertEqual(detail, "已阶梯锁利")
        self.assertNotIn(key, trackers)
        self.assertEqual(len(rig.trades), 1)
        self.assertTrue(rig.notifies, "锁利平仓要通知")
        self.assertTrue(rig.synced, "锁利线上移必须同步到云端 OCO")

    def test_ratchet_floor_stop_close_failure_keeps_the_position(self):
        rig = self._ratchet_rig(close=(False, "交易所拒绝"))
        rig._pos = _pos(avgPx=70000.0)
        ok, detail, trackers, key = self._run(rig, _f(price=71000.0))
        self.assertFalse(ok)
        self.assertEqual(detail, "平仓失败")
        self.assertIn(key, trackers)
        self.assertTrue(any("仓位仍保留" in a for a in rig.actions), rig.actions)

    # ── 53 / 104-105 / 108：不该动手与历史 tracker 补字段 ────────────────
    def test_invalid_market_data_skips_local_trailing_but_keeps_cloud_protection(self):
        """行情无效时**保留云端保护**、跳过本地移动止盈 —— 绝不在盲区里动保护单。"""
        rig = _Rig(hard_stop=True)          # 即便本地判据说"该止损"，也不许在这里动手
        ok, detail, trackers, key = self._run(rig, _f(market_data_valid=False))
        self.assertFalse(ok)
        self.assertEqual(detail, "行情无效")
        self.assertEqual(rig.trades, [], "行情无效时不得平仓")
        self.assertTrue(any("保留云端保护" in a for a in rig.actions), rig.actions)

    def test_legacy_tracker_gets_policy_fields_backfilled(self):
        rig = _Rig()
        f = _f(policy_version="v9", policy_hash="h9")
        key, trackers = rig.trackers(f)
        trackers[key]["policy_version"] = ""      # 历史 tracker 没有版本
        del trackers[key]["policy_hash"]
        manage_position_tp_and_trailing(f, rig._pos, trackers, "2026-09-21 12:00:00",
                                        rig.actions, **rig.kwargs())
        self.assertEqual(trackers[key]["policy_version"], "v9",
                         "历史 tracker 必须被补上策略版本（否则快照对不上号）")
        self.assertEqual(trackers[key]["policy_hash"], "h9")

    def test_tracker_without_entry_ts_gets_one(self):
        rig = _Rig()
        key, trackers = rig.trackers(_f())
        del trackers[key]["entryTs"]
        manage_position_tp_and_trailing(_f(), rig._pos, trackers, "2026-09-21 12:00:00",
                                        rig.actions, **rig.kwargs())
        self.assertAlmostEqual(trackers[key]["entryTs"], int(time.time()), delta=5,
                               msg="缺 entryTs 的 tracker 必须补当前时间（否则时间止损算不出来）")



    # ── 244-261 / 263-320：动能回撤止盈（多空对称）与空头整条链 ─────────────
    def test_long_momentum_pullback_exit(self):
        """峰值利润 >= 2*ATR 后从高点回撤 >= 0.75*ATR ⇒ 动能止盈（多）。"""
        rig = _Rig(floor=69000.0, high_water=71750.0)   # 峰值 1750 = 2.5*ATR
        ok, detail, trackers, key = self._run(rig, _f(price=71200.0))   # 回撤 550
        self.assertTrue(ok)
        self.assertEqual(detail, "已移动止盈")
        self.assertNotIn(key, trackers, "平仓确认后要清 tracker")
        self.assertEqual(len(rig.trades), 1)
        self.assertTrue(rig.notifies, "动能止盈要通知")

    def test_long_momentum_pullback_close_failure_keeps_the_position(self):
        rig = _Rig(floor=69000.0, high_water=71750.0, close=(False, "交易所拒绝"))
        ok, detail, trackers, key = self._run(rig, _f(price=71200.0))
        self.assertFalse(ok)
        self.assertEqual(detail, "平仓失败")
        self.assertIn(key, trackers, "平仓失败 ⇒ 仓位还在 ⇒ tracker 必须保留")
        self.assertEqual(rig.trades, [])

    def test_short_ratchet_lock_exit(self):
        """空头整条链：锁利线**向下**收紧并同步云端，触及即锁利平空。"""
        rig = _Rig(floor=68000.0, side="short", low_water=68250.0, old_sl=71000.0)
        rig._pos = _pos(side="short", avgPx=70000.0)
        ok, detail, trackers, key = self._run(rig, _f(price=68000.0))
        self.assertTrue(ok)
        self.assertEqual(detail, "已阶梯锁利")
        self.assertNotIn(key, trackers)
        self.assertTrue(rig.synced, "空头的锁利线下移必须同步云端 OCO")
        self.assertEqual(rig.synced[0][0][1], "short", f"方向必须是 short：{rig.synced}")
        self.assertTrue(rig.notifies)

    def test_short_momentum_rebound_exit(self):
        """峰值利润 >= 2*ATR 后从低点反弹 ⇒ 动能止盈（空）。"""
        rig = _Rig(floor=76000.0, side="short", low_water=67500.0, old_sl=71000.0)
        rig._pos = _pos(side="short", avgPx=70000.0)
        ok, detail, trackers, key = self._run(rig, _f(price=68100.0))   # 反弹 600
        self.assertTrue(ok)
        self.assertEqual(detail, "已移动止盈")
        self.assertNotIn(key, trackers)



    # ── 273 / 286-287 / 306-307 / 70-71：剩余三行与首次建仓 ────────────────
    def test_short_ratchet_lock_close_failure_keeps_the_position(self):
        rig = _Rig(floor=68000.0, side="short", low_water=68250.0, old_sl=71000.0,
                   stage_desc="🔒 锁利一档", close=(False, "交易所拒绝"))
        rig._pos = _pos(side="short", avgPx=70000.0)
        ok, detail, trackers, key = self._run(rig, _f(price=68000.0))
        self.assertFalse(ok)
        self.assertEqual(detail, "平仓失败")
        self.assertIn(key, trackers)
        self.assertTrue(any("仓位仍保留" in a for a in rig.actions), rig.actions)

    def test_short_momentum_rebound_close_failure_keeps_the_position(self):
        rig = _Rig(floor=76000.0, side="short", low_water=67500.0, old_sl=71000.0,
                   close=(False, "交易所拒绝"))
        rig._pos = _pos(side="short", avgPx=70000.0)
        ok, detail, trackers, key = self._run(rig, _f(price=68100.0))
        self.assertFalse(ok)
        self.assertEqual(detail, "平仓失败")
        self.assertIn(key, trackers)
        self.assertTrue(any("仓位仍保留" in a for a in rig.actions), rig.actions)

    def test_first_time_position_is_registered_in_trackers(self):
        """首次见到的持仓必须建 tracker（含入场价/水位/止盈止损），否则后续全都没得管。"""
        rig = _Rig(floor=69000.0)
        f = _f()
        trackers = {}
        ok, detail = manage_position_tp_and_trailing(
            f, rig._pos, trackers, "2026-09-21 12:00:00", rig.actions, **rig.kwargs())
        key = f"{f['instId']}_{rig._pos['side']}"
        self.assertIn(key, trackers, "首次见到就该建 tracker")
        t = trackers[key]
        self.assertEqual(t["entryPx"], rig._pos["avgPx"])
        self.assertEqual(t["initialSz"], rig._pos["pos"])
        self.assertGreater(t["trailingStopPx"], 0, "建 tracker 时必须带止损线")
        self.assertGreater(t["takeProfitPx"], 0, "建 tracker 时必须带止盈线")
        self.assertEqual(t["entryTime"], "2026-09-21 12:00:00")


if __name__ == "__main__":
    unittest.main()

"""挂单生命周期与归属对账的失败语义（第二百三十八刀）。

这两个函数是**撤单**的执行者，而撤单**不可逆**。它们的共同纪律：

- **读不到 ≠ 没有**：本地意图读不出来 ⇒ **一张都不撤** + fail-closed 拦本周期
  （旧写法把"文件坏了"当成"没有意图" ⇒ 每笔挂单都成了孤儿被撤 ⇒ 撤旧挂新循环）；
- **核验不了就拦轮**（网络/未知异常）；但**凭证已死**是例外：执行闸开着而密钥失效，
  该所不可能再收到新单 ⇒ 记入 `_BROKEN_VENUES`（本轮路由摘除其执行资格）并**跳过**，
  不拿凭证错误拦全链（审计#4 教训：那等于交易停摆）；
- **同向重复单收敛**：按创建时间只保最新一条为候选存活单，其余降级为重复单
  （修复外所"每轮重挂造成成对重复"）；
- **新鲜意图归属 → 保留**；超时/方向不符/无归属 → 撤销，原因必须写清。
"""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.trader.order_lifecycle import clean_stale_open_orders, reconcile_pending_orders

NOW_MS = 1_700_000_000_000


class _Okx:
    def __init__(self, *, pending=None, raises=None, cancel_raises=None):
        self._pending = pending if pending is not None else []
        self._raises = raises
        self.cancel_raises = cancel_raises
        self.cancelled = []

    def pending_orders(self):
        if self._raises is not None:
            raise self._raises
        return self._pending

    def cancel_order(self, inst_id, order_id):
        if self.cancel_raises is not None:
            raise self.cancel_raises
        self.cancelled.append((inst_id, order_id))


class _Adapter:
    def __init__(self, *, rows=None, raises=None, cancel_raises=None):
        self._rows = rows or []
        self._raises = raises
        self.cancel_raises = cancel_raises
        self.cancelled = []
        self.listed = []

    def open_orders(self):
        if self._raises is not None:
            raise self._raises
        return self._rows

    def list_open_orders(self, base):
        self.listed.append(base)
        if self._raises is not None:
            raise self._raises
        return self._rows

    def cancel_order(self, base, order_id):
        if self.cancel_raises is not None:
            raise self.cancel_raises
        self.cancelled.append((base, order_id))


def _registry(open_venues=("gate", "binance"), adapters=None, raises=None):
    adapters = adapters or {}

    def get_adapter(venue, environment=None):
        if raises is not None:
            raise raises
        return adapters[venue]

    return SimpleNamespace(
        execution_open=lambda v, mode: v in open_venues, get_adapter=get_adapter)


def _clean(*, okx=None, intents=(), env_mode="live", registry=None, instruments=None,
           keep=None, now_ms=None, intents_raiser=None):
    """跑一次 clean_stale_open_orders（默认：无外所、无意图、无挂单）。"""
    if intents_raiser is not None:
        load_intents = lambda: (_ for _ in ()).throw(intents_raiser)
    else:
        load_intents = lambda: list(intents)
    env = (lambda: SimpleNamespace(mode=env_mode)) if env_mode else \
        (lambda: (_ for _ in ()).throw(RuntimeError("env 不可读")))
    _now = (now_ms if now_ms is not None else NOW_MS) / 1000
    with patch("scripts.trader.order_lifecycle.time.time", lambda: _now):
        return clean_stale_open_orders(
            keep or set(), load_open_intents=load_intents, OPEN_INTENT_TTL_MS=60_000,
            _BROKEN_VENUES=set(), current_environment=env,
            load_instruments=(instruments if instruments is not None else (lambda: [])),
            okx_rest=okx or _Okx(), venue_registry=registry or _registry(open_venues=()))


class OkxStaleLifecycleTest(unittest.TestCase):
    def test_pending_read_failure_is_fail_closed(self):
        ok, why = _clean(okx=_Okx(raises=RuntimeError("网络断了")))
        self.assertFalse(ok)
        self.assertIn("invalid open-orders response", why)

    def test_stale_live_order_is_cancelled(self):
        old = {"instId": "BTC-USDT-SWAP", "ordId": "o1", "state": "live", "cTime": 1}
        okx = _Okx(pending=[old])
        ok, _ = _clean(okx=okx, now_ms=NOW_MS)
        self.assertTrue(ok)
        self.assertEqual(okx.cancelled, [("BTC-USDT-SWAP", "o1")])

    def test_fresh_or_non_live_or_kept_orders_are_left_alone(self):
        """宽限期内 / 非 live 状态 / 已判定归属（接管）⇒ 一律不动手。"""
        pending = [
            {"instId": "A-USDT-SWAP", "ordId": "fresh", "state": "live", "cTime": NOW_MS - 1000},
            {"instId": "B-USDT-SWAP", "ordId": "filled", "state": "filled", "cTime": 1},
            {"instId": "C-USDT-SWAP", "ordId": "kept", "state": "live", "cTime": 1},
            {"instId": "D-USDT-SWAP", "state": "live", "cTime": 1},      # 无 ordId
        ]
        okx = _Okx(pending=pending)
        ok, _ = _clean(okx=okx, now_ms=NOW_MS, keep={"kept"})
        self.assertTrue(ok)
        self.assertEqual(okx.cancelled, [], "只该撤「陈旧 + live + 无归属」的那种")

    def test_cancel_failure_blocks_the_cycle(self):
        stale = {"instId": "BTC-USDT-SWAP", "ordId": "o1", "state": "live", "cTime": 1}
        okx = _Okx(pending=[stale], cancel_raises=RuntimeError("撤单被拒"))
        ok, why = _clean(okx=okx, now_ms=NOW_MS)
        self.assertFalse(ok)
        self.assertIn("failed to cancel stale order", why)


class IntentsUnreadableTest(unittest.TestCase):
    def test_unreadable_intents_disable_all_cancelling_and_fail_closed(self):
        """**读不到 ≠ 没有意图**：意图读不出来 ⇒ **外所一张都不撤** + 拦本周期。

        ⚠️ 范围要读准：这条纪律管的是**外所**（代码里写的就是「本轮不撤任何**外所**挂单」）——
        OKX 那一半是**纯时间**逻辑（超 240s 就撤），归属保护由上游对账的 `keep` 集提供
        （`reconcile_pending_orders` 先跑、判定接管的 ordId 传进来）。所以这里用外所来验。
        """
        ad = _Adapter(rows=[{"order_id": "x1", "side": "buy", "base": "BTC",
                             "raw": {"symbol": "BTCUSDT", "time": 1}}])
        reg = _registry(open_venues=("binance",), adapters={"binance": ad})
        ok, why = _clean(okx=_Okx(), env_mode="live", registry=reg, now_ms=NOW_MS,
                         intents_raiser=RuntimeError("文件坏了"))
        self.assertFalse(ok)
        self.assertIn("本地意图不可读", why)
        self.assertEqual(ad.cancelled, [], "意图不可读时**外所绝不撤单**（撤单不可逆）")


class CrossVenueTest(unittest.TestCase):
    def _run(self, *, venue="binance", rows=None, adapter_raises=None, cancel_raises=None,
             env_mode="live", intents=(), instruments=None, now_ms=NOW_MS, open_venues=None):
        ad = _Adapter(rows=rows, raises=adapter_raises, cancel_raises=cancel_raises)
        reg = _registry(open_venues=(open_venues or (venue,)), adapters={venue: ad})
        ok, why = _clean(okx=_Okx(), intents=intents, env_mode=env_mode, registry=reg,
                         instruments=instruments, now_ms=now_ms)
        return ok, why, ad

    def test_execution_gate_closed_means_zero_touch(self):
        """闸关＝该所不可能有本系统单 ⇒ 零触碰（连适配器都不取）。"""
        ok, _, ad = self._run(open_venues=())
        self.assertTrue(ok)
        self.assertEqual(ad.cancelled, [])

    def test_unreadable_environment_skips_external_venues(self):
        ok, _, ad = self._run(env_mode="")
        self.assertTrue(ok, "连当前环境都读不出来时，外所回收整个跳过（不猜、也不拦轮）")
        self.assertEqual(ad.cancelled, [])

    def test_credential_death_is_recorded_not_fatal(self):
        """执行闸开着但凭证已死 ⇒ 记入 _BROKEN_VENUES 并跳过；**不拿它拦全链**。"""
        import scripts.trader.order_lifecycle as mod
        broken = set()
        ad = _Adapter(raises=RuntimeError("binance error: Invalid API-key, 50111"))
        reg = _registry(open_venues=("binance",), adapters={"binance": ad})
        ok, _ = clean_stale_open_orders(
            set(), load_open_intents=lambda: [], OPEN_INTENT_TTL_MS=60_000,
            _BROKEN_VENUES=broken, current_environment=lambda: SimpleNamespace(mode="live"),
            load_instruments=lambda: [], okx_rest=_Okx(), venue_registry=reg)
        self.assertTrue(ok, "凭证错误不该拦全链（那等于交易停摆）")
        self.assertIn("binance", broken, "必须记入 _BROKEN_VENUES 供路由摘除执行资格")

    def test_other_adapter_failure_is_fail_closed(self):
        ok, why, _ = self._run(adapter_raises=RuntimeError("socket timeout"))
        self.assertFalse(ok)
        self.assertIn("挂单回收不可用", why)

    def test_stale_external_order_without_intent_is_cancelled(self):
        rows = [{"order_id": "x1", "side": "buy", "base": "BTC",
                 "raw": {"symbol": "BTCUSDT", "time": 1}}]
        ok, _, ad = self._run(rows=rows)
        self.assertTrue(ok)
        self.assertEqual(ad.cancelled, [("BTC", "x1")])

    def test_fresh_intent_keeps_the_order(self):
        rows = [{"order_id": "x1", "side": "buy", "base": "BTC",
                 "raw": {"symbol": "BTCUSDT", "time": 1}}]
        intents = [{"instId": "BTC-USDT-SWAP", "side": "buy", "ts": NOW_MS - 1000}]
        ok, _, ad = self._run(rows=rows, intents=intents)
        self.assertTrue(ok)
        self.assertEqual(ad.cancelled, [], "新鲜意图归属 ⇒ 保留（与 OKX kept 同语义）")

    def test_reduce_only_orders_are_out_of_scope(self):
        rows = [{"order_id": "p1", "side": "sell", "base": "BTC", "reduce_only": True,
                 "raw": {"symbol": "BTCUSDT", "time": 1}}]
        ok, _, ad = self._run(rows=rows)
        self.assertTrue(ok)
        self.assertEqual(ad.cancelled, [], "减仓/保护族不属入场生命周期管辖")

    def test_side_is_inferred_from_signed_size(self):
        rows = [{"order_id": "x1", "size": -2.0, "base": "BTC",
                 "raw": {"symbol": "BTCUSDT", "time": 1}}]
        ok, _, ad = self._run(rows=rows)
        self.assertTrue(ok)
        self.assertEqual(ad.cancelled, [("BTC", "x1")], "负号 ⇒ sell ⇒ 方向可判、可回收")

    def test_newest_same_direction_order_wins_and_older_is_collapsed(self):
        """按创建时间只保最新一条为候选；更老的同向单降级为重复单并被收敛撤销。"""
        rows = [
            {"order_id": "new", "side": "buy", "base": "BTC", "raw": {"symbol": "BTCUSDT", "time": 1_000}},
            {"order_id": "old", "side": "buy", "base": "BTC", "raw": {"symbol": "BTCUSDT", "time": 500}},
        ]
        # now_ts 由 time.time() 决定 ⇒ 两条都远超 240s ⇒ 老的进 dupes、新的进 best
        ok, _, ad = self._run(rows=rows)
        self.assertTrue(ok)
        self.assertIn(("BTC", "old"), ad.cancelled, "重复单要被收敛撤销")
        self.assertIn(("BTC", "new"), ad.cancelled, "最新那条无意图归属 ⇒ 也撤")

    def test_keep_set_spares_externally_verified_orders(self):
        rows = [{"order_id": "x1", "side": "buy", "base": "BTC", "raw": {"symbol": "BTCUSDT", "time": 1}}]
        ad = _Adapter(rows=rows)
        reg = _registry(open_venues=("binance",), adapters={"binance": ad})
        ok, _ = clean_stale_open_orders(
            {"x1"}, load_open_intents=lambda: [], OPEN_INTENT_TTL_MS=60_000,
            _BROKEN_VENUES=set(), current_environment=lambda: SimpleNamespace(mode="live"),
            load_instruments=lambda: [], okx_rest=_Okx(), venue_registry=reg)
        self.assertTrue(ok)
        self.assertEqual(ad.cancelled, [], "对账已判定归属（keep）的单不受生命周期清理")

    def test_unreadable_pool_degrades_to_no_gate_scan(self):
        """Gate 需要按标的扫；池读不出来 ⇒ 只是扫不到（fail-soft），不是拦轮。"""
        ad = _Adapter(rows=[])
        reg = _registry(open_venues=("gate",), adapters={"gate": ad})

        def boom():
            raise RuntimeError("池坏了")

        ok, _ = clean_stale_open_orders(
            set(), load_open_intents=lambda: [], OPEN_INTENT_TTL_MS=60_000,
            _BROKEN_VENUES=set(), current_environment=lambda: SimpleNamespace(mode="live"),
            load_instruments=boom, okx_rest=_Okx(), venue_registry=reg)
        self.assertTrue(ok)
        self.assertEqual(ad.listed, [], "池读不出来 ⇒ 一个标的都扫不到（但轮次照走）")

    def test_gate_scans_each_pool_instrument(self):
        ad = _Adapter(rows=[])
        reg = _registry(open_venues=("gate",), adapters={"gate": ad})
        pool = [{"instId": "BTC-USDT-SWAP"}, {"instId": "ETH-USDT-SWAP"}, {"instId": ""}]
        ok, _ = clean_stale_open_orders(
            set(), load_open_intents=lambda: [], OPEN_INTENT_TTL_MS=60_000,
            _BROKEN_VENUES=set(), current_environment=lambda: SimpleNamespace(mode="live"),
            load_instruments=lambda: pool, okx_rest=_Okx(), venue_registry=reg)
        self.assertTrue(ok)
        self.assertEqual(ad.listed, ["BTC", "ETH"], "逐标的扫，空 instId 跳过")

    def test_cancel_failure_on_duplicate_blocks_the_cycle(self):
        rows = [
            {"order_id": "new", "side": "buy", "base": "BTC", "raw": {"symbol": "BTCUSDT", "time": 1_000}},
            {"order_id": "old", "side": "buy", "base": "BTC", "raw": {"symbol": "BTCUSDT", "time": 500}},
        ]
        ok, why, _ = self._run(rows=rows, cancel_raises=RuntimeError("撤单被拒"))
        self.assertFalse(ok)
        self.assertIn("failed to cancel", why)


class ReconcileTest(unittest.TestCase):
    def _run(self, *, pending="__auto__", trackers=None, intents=(), okx=None,
             intents_raiser=None, side_map=None):
        okx = okx or _Okx()
        if pending == "__auto__":
            pending = okx.pending_orders() if okx._raises is None else None
        if intents_raiser is not None:
            load_intents = lambda: (_ for _ in ()).throw(intents_raiser)
        else:
            load_intents = lambda: list(intents)
        return reconcile_pending_orders(
            trackers if trackers is not None else {}, now_ms=NOW_MS, pending=pending,
            _order_pos_side=side_map or (lambda side: "long" if side == "buy" else "short"),
            load_open_intents=load_intents, load_trackers=lambda: {},
            OPEN_INTENT_TTL_MS=60_000,
            RECONCILE_REASON_SIDE_MISMATCH="方向不一致",
            RECONCILE_REASON_INTENT_STALE="周期意图已失效",
            RECONCILE_REASON_ORPHAN="无对应意图", okx_rest=okx)

    def test_pending_read_failure_is_fail_closed(self):
        ok, kept = self._run(pending=None, okx=_Okx(raises=RuntimeError("网络")))
        self.assertFalse(ok)
        self.assertEqual(kept, set())

    def test_non_list_response_is_fail_closed(self):
        ok, _ = self._run(pending={"instId": "BTC-USDT-SWAP"})
        self.assertFalse(ok, "响应非列表 ⇒ 不可判定 ⇒ 拦本轮，绝不当作空")

    def test_unreadable_intents_are_fail_closed_and_cancel_nothing(self):
        order = {"instId": "BTC-USDT-SWAP", "ordId": "o1", "side": "buy", "state": "live"}
        okx = _Okx()
        ok, kept = self._run(pending=[order], okx=okx, intents_raiser=RuntimeError("坏文件"))
        self.assertFalse(ok)
        self.assertEqual(kept, set())
        self.assertEqual(okx.cancelled, [], "意图不可读时绝不撤单")

    def test_tracker_attribution_takes_over(self):
        order = {"instId": "BTC-USDT-SWAP", "ordId": "o1", "side": "buy", "state": "live"}
        okx = _Okx()
        ok, kept = self._run(pending=[order], okx=okx,
                             trackers={"BTC-USDT-SWAP_long": {"trailingStopPx": 1}})
        self.assertTrue(ok)
        self.assertEqual(kept, {"o1"})
        self.assertEqual(okx.cancelled, [])

    def test_side_mismatch_is_cancelled(self):
        order = {"instId": "BTC-USDT-SWAP", "ordId": "o1", "side": "sell", "state": "live"}
        okx = _Okx()
        intents = [{"instId": "BTC-USDT-SWAP", "side": "buy", "ts": NOW_MS - 1000}]
        ok, kept = self._run(pending=[order], okx=okx, intents=intents)
        self.assertTrue(ok)
        self.assertEqual(okx.cancelled, [("BTC-USDT-SWAP", "o1")])
        self.assertEqual(kept, set())

    def test_stale_intent_is_cancelled_not_taken_over(self):
        order = {"instId": "BTC-USDT-SWAP", "ordId": "o1", "side": "buy", "state": "live"}
        okx = _Okx()
        intents = [{"instId": "BTC-USDT-SWAP", "side": "buy", "ts": NOW_MS - 999_999}]
        ok, _ = self._run(pending=[order], okx=okx, intents=intents)
        self.assertTrue(ok)
        self.assertEqual(okx.cancelled, [("BTC-USDT-SWAP", "o1")], "超 TTL 的意图不作归属")

    def test_orphan_without_any_intent_is_cancelled(self):
        order = {"instId": "BTC-USDT-SWAP", "ordId": "o1", "side": "buy", "state": "live"}
        okx = _Okx()
        ok, _ = self._run(pending=[order], okx=okx)
        self.assertTrue(ok)
        self.assertEqual(okx.cancelled, [("BTC-USDT-SWAP", "o1")])

    def test_newest_intent_decides_attribution(self):
        """审计·PEPE 永动机：必须按**最新**意图判归属（旧写法取最老 ⇒ 撤旧挂新无限循环）。"""
        order = {"instId": "PEPE-USDT-SWAP", "ordId": "o1", "side": "buy", "state": "live"}
        okx = _Okx()
        intents = [
            {"instId": "PEPE-USDT-SWAP", "side": "buy", "ts": NOW_MS - 999_999},   # 老（超 TTL）
            {"instId": "PEPE-USDT-SWAP", "side": "buy", "ts": NOW_MS - 1000},      # 新（有效）
        ]
        ok, kept = self._run(pending=[order], okx=okx, intents=intents)
        self.assertTrue(ok)
        self.assertEqual(kept, {"o1"}, "最新意图有效 ⇒ 接管保留（不得按最老意图撤销）")
        self.assertEqual(okx.cancelled, [])

    def test_non_dict_and_non_live_entries_are_skipped(self):
        okx = _Okx()
        ok, kept = self._run(pending=["垃圾", {"instId": "BTC-USDT-SWAP", "ordId": "o2",
                                              "side": "buy", "state": "filled"}], okx=okx)
        self.assertTrue(ok)
        self.assertEqual(kept, set())
        self.assertEqual(okx.cancelled, [])

    def test_cancel_failure_returns_false_with_partial_kept(self):
        orders = [
            {"instId": "AAA-USDT-SWAP", "ordId": "keep1", "side": "buy", "state": "live"},
            {"instId": "BBB-USDT-SWAP", "ordId": "orphan", "side": "buy", "state": "live"},
        ]
        okx = _Okx(cancel_raises=RuntimeError("撤单被拒"))
        ok, kept = self._run(pending=orders, okx=okx,
                             trackers={"AAA-USDT-SWAP_long": {}})
        self.assertFalse(ok, "撤销失败 ⇒ 对账自身失败 ⇒ fail-closed")
        self.assertEqual(kept, {"keep1"}, "已判定接管的保留集必须随失败一并返回")



class CrossVenueNormalizationEdgeTest(unittest.TestCase):
    """外所挂单的**归一化**边界：脏行不能把循环带偏，更不能把该撤的漏掉。"""

    def _run(self, rows, *, venue="gate", cancel_raises=None, now_ms=NOW_MS, keep=None):
        ad = _Adapter(rows=rows, cancel_raises=cancel_raises)
        reg = _registry(open_venues=(venue,), adapters={venue: ad})
        pool = [{"instId": "BTC-USDT-SWAP"}]
        ok, why = _clean(okx=_Okx(), env_mode="live", registry=reg, instruments=lambda: pool,
                         keep=keep, now_ms=now_ms)
        return ok, why, ad

    def test_non_dict_row_is_skipped(self):
        ok, _, ad = self._run(["垃圾行", {"order_id": "g1", "side": "buy",
                                        "contract": "BTC_USDT", "create_time": 1}])
        self.assertTrue(ok)
        self.assertEqual(ad.cancelled, [("BTC", "g1")], "Gate 行按 contract/create_time 归一")

    def test_amount_field_is_used_when_size_absent(self):
        ok, _, ad = self._run([{"order_id": "g1", "amount": -3, "contract": "BTC_USDT",
                                "create_time": 1}])
        self.assertTrue(ok)
        self.assertEqual(ad.cancelled, [("BTC", "g1")], "缺 size 时用 amount，且负号判为 sell")

    def test_unparsable_amount_leaves_side_unknown_and_is_skipped(self):
        ok, _, ad = self._run([{"order_id": "g1", "amount": "不是数字", "contract": "BTC_USDT",
                                "create_time": 1}])
        self.assertTrue(ok)
        self.assertEqual(ad.cancelled, [], "方向不可判 ⇒ 跳过（不猜方向去撤单）")

    def test_duplicates_within_grace_period_are_left_alone(self):
        """宽限期内（240s）的重复单不动手 —— 可能是同一轮的正常重挂。"""
        rows = [
            {"order_id": "new", "side": "buy", "contract": "BTC_USDT", "create_time": NOW_MS / 1000},
            {"order_id": "old", "side": "buy", "contract": "BTC_USDT", "create_time": NOW_MS / 1000 - 1},
        ]
        ok, _, ad = self._run(rows, now_ms=NOW_MS)
        self.assertTrue(ok)
        self.assertEqual(ad.cancelled, [], "宽限期内的重复单不撤")

    def test_stale_cancel_failure_in_survivor_loop_is_fail_closed(self):
        rows = [{"order_id": "g1", "side": "buy", "contract": "BTC_USDT", "create_time": 1}]
        ok, why, _ = self._run(rows, cancel_raises=RuntimeError("撤单被拒"))
        self.assertFalse(ok)
        self.assertIn("failed to cancel stale order", why)


class ReconcileDegradedInputTest(unittest.TestCase):
    def test_trackers_default_loader_is_used_when_none(self):
        """`trackers=None` ⇒ 回落到 `load_trackers()`（调用点没传时的取数路径）。"""
        order = {"instId": "BTC-USDT-SWAP", "ordId": "o1", "side": "buy", "state": "live"}
        okx = _Okx()
        called = []
        ok, kept = reconcile_pending_orders(
            None, now_ms=NOW_MS, pending=[order],
            _order_pos_side=lambda side: "long" if side == "buy" else "short",
            load_open_intents=lambda: [], load_trackers=lambda: (called.append(1), {})[1],
            OPEN_INTENT_TTL_MS=60_000, RECONCILE_REASON_SIDE_MISMATCH="方向不一致",
            RECONCILE_REASON_INTENT_STALE="周期意图已失效", RECONCILE_REASON_ORPHAN="无对应意图",
            okx_rest=okx)
        self.assertTrue(ok)
        self.assertEqual(called, [1], "trackers=None 时必须走默认取数函数")
        self.assertEqual(okx.cancelled, [("BTC-USDT-SWAP", "o1")], "无归属 ⇒ 孤儿撤销")

    def test_cancel_failure_on_side_mismatch_is_fail_closed(self):
        order = {"instId": "BTC-USDT-SWAP", "ordId": "o1", "side": "sell", "state": "live"}
        okx = _Okx(cancel_raises=RuntimeError("撤单被拒"))
        intents = [{"instId": "BTC-USDT-SWAP", "side": "buy", "ts": NOW_MS - 1000}]
        ok, kept = reconcile_pending_orders(
            {}, now_ms=NOW_MS, pending=[order],
            _order_pos_side=lambda side: "long" if side == "buy" else "short",
            load_open_intents=lambda: intents, load_trackers=lambda: {},
            OPEN_INTENT_TTL_MS=60_000, RECONCILE_REASON_SIDE_MISMATCH="方向不一致",
            RECONCILE_REASON_INTENT_STALE="周期意图已失效", RECONCILE_REASON_ORPHAN="无对应意图",
            okx_rest=okx)
        self.assertFalse(ok, "方向不符的撤销失败 ⇒ 对账失败 ⇒ fail-closed")
        self.assertEqual(kept, set())

    def test_cancel_failure_on_stale_intent_is_fail_closed(self):
        order = {"instId": "BTC-USDT-SWAP", "ordId": "o1", "side": "buy", "state": "live"}
        okx = _Okx(cancel_raises=RuntimeError("撤单被拒"))
        intents = [{"instId": "BTC-USDT-SWAP", "side": "buy", "ts": NOW_MS - 999_999}]
        ok, kept = reconcile_pending_orders(
            {}, now_ms=NOW_MS, pending=[order],
            _order_pos_side=lambda side: "long" if side == "buy" else "short",
            load_open_intents=lambda: intents, load_trackers=lambda: {},
            OPEN_INTENT_TTL_MS=60_000, RECONCILE_REASON_SIDE_MISMATCH="方向不一致",
            RECONCILE_REASON_INTENT_STALE="周期意图已失效", RECONCILE_REASON_ORPHAN="无对应意图",
            okx_rest=okx)
        self.assertFalse(ok, "超时意图的撤销失败 ⇒ 对账失败 ⇒ fail-closed")
        self.assertEqual(kept, set())


if __name__ == "__main__":
    unittest.main()

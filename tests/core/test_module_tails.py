"""跨域长尾收口 —— 12 个模块各 1–3 行的残余分支 —— 第 316 刀。

这批都是"每个模块只剩最后两三行"的零头，散落在 5 个域里，单独成刀会让每刀成本
远高于收益，故合并为**一刀清扫**：

| 模块 | 模块 | 模块 |
|---|---|---|
| `brain/cycle_parts.py` | `brain/account_text.py` | `brain/runtime.py` |
| `brain/snapshots.py` | `trader/scale_out.py` | `trader/reservation_reconcile.py` |
| `trader/signal_snapshot.py` | `trader/venue_evidence.py` | `calculus/regime.py` |
| `news/importance.py` | `ledger/okx_history.py` | `backtest/lifecycle.py` |

它们的共同性质：**只在失败/未知/低波这类"不好走"的路上跑**，所以既有用例
（多为正常路径的集成测试）碰不到。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.backtest import lifecycle  # noqa: E402
from scripts.brain import account_text, cycle_parts, runtime, snapshots  # noqa: E402
from scripts.calculus import regime  # noqa: E402
from scripts.ledger import okx_history  # noqa: E402
from scripts.news import importance  # noqa: E402
from scripts.trader import (reservation_reconcile, scale_out,  # noqa: E402
                            signal_snapshot, venue_evidence)


# ───────────────────────── brain/cycle_parts.py ─────────────────────────
class ScaleOutTpTests(unittest.TestCase):
    """`_calculate_scale_out_tp`：分批止盈触发价（**开关关闭时整条不生效**）。"""

    def _patch_const(self, **values):
        import scripts.risk_constants as rc
        return patch.multiple(rc, **values)

    def test_returns_none_when_the_feature_is_disabled(self):
        # ★ 第 100 行 —— 总开关关 ⇒ 直接 `return None`，连价格都不算
        with self._patch_const(SCALE_OUT_ENABLED=False):
            self.assertIsNone(cycle_parts._calculate_scale_out_tp(100.0, "BUY", 1.0))

    def test_buy_side_targets_above_the_entry(self):
        with self._patch_const(SCALE_OUT_ENABLED=True, SCALE_OUT_TRIGGER_ATR=1.2):
            self.assertEqual(cycle_parts._calculate_scale_out_tp(100.0, "BUY", 2.0), 102.4)

    def test_short_side_targets_below_the_entry(self):
        with self._patch_const(SCALE_OUT_ENABLED=True, SCALE_OUT_TRIGGER_ATR=1.2):
            self.assertEqual(cycle_parts._calculate_scale_out_tp(100.0, "SHORT", 2.0), 97.6)

    def test_invalid_entry_or_atr_returns_none(self):
        with self._patch_const(SCALE_OUT_ENABLED=True, SCALE_OUT_TRIGGER_ATR=1.2):
            self.assertIsNone(cycle_parts._calculate_scale_out_tp(0.0, "BUY", 1.0))
            self.assertIsNone(cycle_parts._calculate_scale_out_tp(100.0, "BUY", 0.0))

    def test_unknown_side_falls_through_to_none(self):
        with self._patch_const(SCALE_OUT_ENABLED=True, SCALE_OUT_TRIGGER_ATR=1.2):
            self.assertIsNone(cycle_parts._calculate_scale_out_tp(100.0, "HOLD", 1.0))

    def test_precision_is_honoured(self):
        with self._patch_const(SCALE_OUT_ENABLED=True, SCALE_OUT_TRIGGER_ATR=1.23456):
            value = cycle_parts._calculate_scale_out_tp(100.0, "BUY", 1.0, precision=4)
        self.assertEqual(value, round(100.0 + 1.23456, 4))

    def test_exception_inside_the_body_returns_none_instead_of_raising(self):
        # ★ 第 112/113 行：`except: pass` 后落到函数尾的 `return None`
        #   —— 常量表读不出来（导入失败）时必须静默降级，不许把开仓链打挂
        with self._patch_const(SCALE_OUT_TRIGGER_ATR="not-a-number"):
            self.assertIsNone(cycle_parts._calculate_scale_out_tp(100.0, "BUY", 1.0))


# ───────────────────────── brain/account_text.py ─────────────────────────
class ProtectionExpiryTextTests(unittest.TestCase):
    """保护档文案里的**到期轴**：`expiring` / `unknown` 两档。"""

    def _text(self, **over):
        p = {"protectionStatus": "fully_protected"}
        p.update(over)
        return account_text._protection_text(p)

    def test_unprotected_without_expiry_field_is_plain(self):
        text = account_text._protection_text({"protectionStatus": "unprotected"})
        self.assertNotIn("临期", text)
        self.assertNotIn("到期未知", text)

    def test_expiring_leg_is_flagged(self):
        # ★ 第 209 行
        self.assertIn("（腿临期）", self._text(protectionExpiry="expiring"))

    def test_unknown_expiry_is_flagged_when_protection_exists(self):
        # ★ 第 211 行 —— `status != "unprotected"` 才说"到期未知"
        self.assertIn("（到期未知）", self._text(protectionExpiry="unknown"))

    def test_unknown_expiry_is_silent_for_an_unprotected_position(self):
        # `unprotected` 档下"没有保护"本身就是结论，再说"到期未知"是噪声
        self.assertNotIn("到期未知", self._text(protectionStatus="unprotected",
                                             protectionExpiry="unknown"))

    def test_expired_takes_precedence_over_expiring(self):
        # 同一个 if/elif 链：`expired` 在前，`expiring` 不会被评估
        self.assertNotIn("临期", self._text(protectionExpiry="expired"))

    def test_unknown_expiry_string_is_ignored(self):
        self.assertNotIn("到期", self._text(protectionExpiry="something-else"))


# ───────────────────────── brain/runtime.py ─────────────────────────
class CapturePolicySnapshotTests(unittest.TestCase):
    """策略快照抓取：**两条导入路径**（顶层优先、后端包兜底）。"""

    def _run(self, **over):
        kw = {"_get_system_version_tag": lambda: "0.0.0", "policy_snapshot": None}
        kw.update(over)
        return runtime.capture_policy_snapshot(**kw)

    def _module(self, name, result):
        mod = ModuleType(name)
        mod.generate_policy_snapshot = lambda: result
        return mod

    def test_explicit_snapshot_is_used_verbatim(self):
        # 返回的是**四元组** `(policy_hash, snapshot, summary, policy_version)`
        given = {"policy_version": "GIVEN", "policy_hash": "H", "summary": "S"}
        self.assertEqual(self._run(policy_snapshot=given),
                         ("H", given, "S", "GIVEN"))

    def test_top_level_policy_snapshot_module_is_preferred(self):
        # ★ 第 29 行
        fake = self._module("policy_snapshot", {"policy_version": "TOP"})
        with patch.dict(sys.modules, {"policy_snapshot": fake}):
            self.assertEqual(self._run()[3], "TOP")

    def test_backend_package_is_used_when_the_top_level_one_is_absent(self):
        # ★ 第 33 行 —— 第一条 import 失败后回落 `r20_backend.policy_snapshot`
        fake = self._module("r20_backend.policy_snapshot", {"policy_version": "BACKEND"})
        with patch.dict(sys.modules, {"policy_snapshot": None,
                                      "r20_backend.policy_snapshot": fake}):
            self.assertEqual(self._run()[3], "BACKEND")

    def test_both_imports_failing_yields_the_tagged_fallback(self):
        with patch.dict(sys.modules, {"policy_snapshot": None,
                                      "r20_backend.policy_snapshot": None}):
            policy_hash, snap, summary, version = self._run(
                _get_system_version_tag=lambda: "v7.9.2")
        self.assertEqual(version, "v7.9.2@unknown")
        self.assertEqual(policy_hash, "unknown")
        self.assertEqual(summary, "policy_snapshot_fallback")


# ───────────────────────── brain/snapshots.py ─────────────────────────
class WritePromptSnapshotTests(unittest.TestCase):
    """提示词快照落盘：**纯观测**，写不进去也不许抛。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "last_prompt.txt")

    def _run(self, **over):
        kw = {"AI_LAST_PROMPT_FILE": self.path,
              "_build_effective_prompt_text": lambda **k: "PROMPT-BODY",
              "effective_system_prompt": "SYS", "os": os, "prompt": "USER",
              "time_str": "T"}
        kw.update(over)
        return snapshots.write_prompt_snapshot(**kw)

    def test_prompt_is_written_atomically(self):
        self._run()
        self.assertEqual(Path(self.path).read_text(encoding="utf-8"), "PROMPT-BODY")

    def test_write_failure_is_swallowed(self):
        # ★ 第 76 行 `pass` —— 提示词快照是调试用观测，失败不许打挂主脑周期
        def boom(path, src=None, dst=None):
            raise OSError("read-only fs")
        self.assertIsNone(self._run(os=SimpleNamespace(replace=boom)))

    def test_a_failed_atomic_write_leaves_the_temp_file_behind(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：第 69–74 行是 `写临时文件 → os.replace`，
        #    但**没有 `finally` 清理** —— `os.replace` 失败时那个 `*.tmp` 会留在
        #    data/ 目录里。与既有的 `_atomic_write_json lacks finally: unlink` 同族。
        #    用例名与断言现在一致（左残留 = True）。
        def boom(path, src=None, dst=None):
            raise OSError("read-only fs")
        self._run(os=SimpleNamespace(replace=boom))
        leftovers = [p for p in os.listdir(self.tmp.name) if p.endswith(".tmp")]
        self.assertTrue(leftovers, "失败后 *.tmp 确实留在目录里")
        self.assertFalse(os.path.exists(self.path), "目标文件不该被创建")


# ───────────────────────── trader/scale_out.py ─────────────────────────
class ExecuteScaleOutTests(unittest.TestCase):
    """分批止盈：**找不到跟踪器**与**交易所调用异常**两条路。"""

    def setUp(self):
        self.actions: list = []
        self.trades: list = []
        self.records: list = []

    def _f(self, **over):
        f = {"instId": "BTC-USDT-SWAP", "name": "BTC", "price": 102.0,
             "market_data_valid": True, "precision": 2, "ctVal": 1.0,
             "minSz": 0.1, "atr": 1.0}
        f.update(over)
        return f

    def _pos(self, **over):
        p = {"pos": 10.0, "side": "long", "avgPx": 100.0, "venue": "okx"}
        p.update(over)
        return p

    def _run(self, f=None, pos=None, trackers=None, **over):
        kw = {"okx_rest": self.okx_rest(), "venue_registry": None,
              "record_trade": self.trades.append,
              "notify_trade_close": None, "close_fee": None,
              "close_trade_payload": None, "TAKER_FEE_RATE": 0.0005,
              "ensure_cloud_position_protection": None}
        kw.update(over)
        return scale_out.execute_scale_out_if_eligible(
            f if f is not None else self._f(),
            pos if pos is not None else self._pos(),
            trackers if trackers is not None else {"BTC-USDT-SWAP_long": {}},
            "2026-09-22 12:00:00", self.actions, **kw)

    def okx_rest(self):
        self.order_calls: list = []

        class _Rest:
            def place_order(_self, *a, **k):
                self.order_calls.append((a, k))
                return {"ordId": "1"}
        return _Rest()

    def test_missing_tracker_is_reported_without_ordering(self):
        # ★ 第 76 行
        ok, detail = self._run(trackers={})
        self.assertFalse(ok)
        self.assertEqual(detail, "未找到持仓跟踪器")
        self.assertEqual(self.order_calls, [], "没跟踪器就绝不能下单")

    def test_tracker_key_uses_the_raw_side_from_the_position(self):
        # `pos_key = f"{inst_id}_{curr_pos['side']}"` —— 用的是**原始** side 字段，
        # 不是归一化后的 `pos_side`。所以 `side="net"` 的仓要 `_net` 键才找得到。
        _, detail = self._run(trackers={"BTC-USDT-SWAP_net": {}},
                              pos=self._pos(side="net"))
        # 过了跟踪器这一关（回落到的下一道门是"浮盈未达门槛"，而不是"未找到跟踪器"）
        self.assertNotEqual(detail, "未找到持仓跟踪器", "原始 side 必须直接进键名")
        self.assertEqual(detail, "浮盈未达分批止盈门槛")

    def test_successful_order_records_the_result(self):
        ok, detail = self._run()
        self.assertTrue(ok)
        self.assertEqual(detail, "首批分批平仓成功")
        self.assertEqual(len(self.order_calls), 1)
        _, kwargs = self.order_calls[0]
        self.assertTrue(kwargs["reduce_only"])
        self.assertEqual(kwargs["pos_side"], "long")
        self.assertEqual(kwargs["td_mode"], "cross")
        self.assertEqual(self.order_calls[0][0][1], "sell", "多头平仓方向必须是 sell")

    def test_exchange_exception_is_captured_into_the_action_log(self):
        # ★ 第 144/145 行：异常写进 `order_detail`，再由第 196 行带进 executed_actions；
        #   对调用方只回一句笼统的 "平仓提交失败"（细节在动作日志里）
        class _Boom:
            def place_order(_self, *a, **k):
                raise RuntimeError("OKX 502")
        ok, detail = self._run(okx_rest=_Boom())
        self.assertFalse(ok)
        self.assertEqual(detail, "平仓提交失败")
        self.assertTrue(any("OKX分批平仓异常" in a and "OKX 502" in a
                            for a in self.actions), self.actions)

    def test_short_position_closes_with_buy(self):
        # 空头要"价格**低于**均价"才算浮盈（触发价在下方）；
        # 跟踪器键也跟着原始 side ⇒ 必须是 `_short`
        self._run(f=self._f(price=98.0), pos=self._pos(side="short"),
                  trackers={"BTC-USDT-SWAP_short": {}})
        self.assertEqual(self.order_calls[0][0][1], "buy")
        self.assertEqual(self.order_calls[0][1]["pos_side"], "short")

    def test_disabled_feature_returns_early(self):
        with patch.object(scale_out, "SCALE_OUT_ENABLED", False):
            ok, detail = self._run()
        self.assertFalse(ok)
        self.assertEqual(detail, "分批止盈未启用")
        self.assertEqual(self.order_calls, [])


# ──────────────────── trader/reservation_reconcile.py ────────────────────
class ReconcileReservationTests(unittest.TestCase):
    """预留对账的方向纪律：**释放不可逆 ⇒ 未知一律保留**。"""

    def _run(self, *, mgr=None, mgr_exc=None, fetch=None, fetch_exc=None, **over):
        def _factory():
            if mgr_exc is not None:
                raise mgr_exc
            return mgr
        def _fetch(environment):
            if fetch_exc is not None:
                raise fetch_exc
            return fetch
        kw = {"real_pos_dict": {}, "reservation_manager": _factory,
              "fetch_other_venue_positions": _fetch,
              "state_closed": "closed", "default_ttl_s": 3600.0}
        kw.update(over)
        with patch.object(reservation_reconcile, "print", lambda *a, **k: None):
            return reservation_reconcile.reconcile_reservation_ledger(
                kw.pop("real_pos_dict", {}), set(), "live", **kw)

    def test_unverified_cross_venue_view_releases_nothing(self):
        self.assertEqual(self._run(venue_snapshot_verified=False), 0)

    def test_unreadable_ledger_skips_the_cycle(self):
        # ★ 第 127 行 —— 台账读不出来 ⇒ 返回 0，**不强行释放**
        self.assertEqual(self._run(mgr_exc=RuntimeError("db locked")), 0)

    def test_failing_cross_venue_fetch_is_recorded_and_skips(self):
        # ★ 第 140 行 —— 自取失败要落成 `(False, {}, 原因)`，而不是静默当成"两清"
        self.assertEqual(self._run(fetch_exc=RuntimeError("gate 超时")), 0)

    def test_successful_fetch_that_reports_failure_also_skips(self):
        self.assertEqual(self._run(fetch=(False, {}, "unknown")), 0)

    def test_verified_snapshot_lets_the_cycle_proceed(self):
        class _Mgr:
            def list_unreleased(self, environment):
                return []
        self.assertEqual(self._run(mgr=_Mgr()), 0)

    def test_explicit_snapshot_skips_the_fetch_entirely(self):
        calls = []
        class _Mgr:
            def list_unreleased(self, environment):
                return []

        def _factory():
            return _Mgr()
        with patch.object(reservation_reconcile, "print", lambda *a, **k: None):
            result = reservation_reconcile.reconcile_reservation_ledger(
                {}, set(), "live", reservation_manager=_factory,
                fetch_other_venue_positions=lambda e: calls.append(e),
                state_closed="closed", default_ttl_s=3600.0, venue_snapshot={})
        self.assertEqual(result, 0)
        self.assertEqual(calls, [], "给了快照就不该再出网")

    def test_okx_live_positions_are_indexed_before_deciding(self):
        class _Mgr:
            def list_unreleased(self, environment):
                return []
        # 主循环持仓字典只有 OKX 直签链 ⇒ 必须能把 instId 归到 "base:side"
        self.assertEqual(
            self._run(mgr=_Mgr(),
                      real_pos_dict={"BTC-USDT-SWAP": {"posSide": "long"}}), 0)


# ───────────────────── trader/signal_snapshot.py ─────────────────────
class SignalSnapshotTests(unittest.TestCase):
    """因子库快照二次补齐：**`instruments` 是 dict 时也要能遍历**。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = os.path.join(self.tmp.name, "factor_library_snapshot.json")

    def _run(self, payload, f=None, write=True):
        if write:
            Path(self.lib).write_text(json.dumps(payload), encoding="utf-8")
        return signal_snapshot.build_signal_snapshot(
            f if f is not None else {"instId": "BTC-USDT-SWAP"}, data_dir=self.tmp.name)

    def _f(self, **over):
        f = {"instId": "BTC-USDT-SWAP"}
        f.update(over)
        return f

    def test_dict_shaped_instruments_is_walked_by_value(self):
        # ★ 第 92 行 —— 顶层是 dict（按 instId 索引）时取 `.values()`
        payload = {"instruments": {"BTC-USDT-SWAP": {
            "instId": "BTC-USDT-SWAP", "trend_momentum": {"adx_1h": 33.0}}}}
        snap = self._run(payload)
        self.assertEqual(snap["adx"], 33.0)

    def test_list_shaped_instruments_still_works(self):
        payload = {"instruments": [{"instId": "BTC-USDT-SWAP",
                                    "trend_momentum": {"adx_1h": 44.0}}]}
        self.assertEqual(self._run(payload)["adx"], 44.0)

    def test_missing_library_file_is_tolerated(self):
        snap = self._run({}, write=False)
        self.assertIsNone(snap["adx"])

    def test_corrupt_library_file_is_swallowed(self):
        # ★ 第 109 行 `pass`
        Path(self.lib).write_text("{ broken", encoding="utf-8")
        snap = signal_snapshot.build_signal_snapshot({"instId": "BTC-USDT-SWAP"},
                                                     data_dir=self.tmp.name)
        self.assertIsNone(snap["adx"])

    def test_other_observations_are_filled_from_the_library(self):
        payload = {"instruments": [{"instId": "BTC-USDT-SWAP",
                                    "smart_money_derivatives": {
                                        "funding_rate_pct": 0.012,
                                        "smart_money_flow_usd": -1234.0},
                                    "composite_alpha_score": 0.77}]}
        snap = self._run(payload)
        self.assertEqual(snap["funding_rate"], 0.012)
        self.assertEqual(snap["smart_money_net"], -1234.0)
        self.assertEqual(snap["composite_alpha_score"], 0.77)


# ───────────────────── trader/venue_evidence.py ─────────────────────
class VenueEvidenceFallbackTests(unittest.TestCase):
    """候选所清单来自能力表；表不可用时**回落 OKX 单候选**（不硬编码）。"""

    def _candidates(self, registry):
        return venue_evidence.build_venue_candidates(
            "BTC-USDT-SWAP", "live", venue_health_stamp=lambda: ("stamp", {}),
            venue_registry=registry, load_preferred_venue=lambda: "auto",
            venue_execution_ready=lambda name, env: True,
            MAKER_FEE_RATE=0.0002, VENUE_HEALTH_MAX_AGE_S=900.0)

    def test_registry_venues_are_used_when_available(self):
        class _Reg:
            def registered_venues(self):
                return ["okx", "gate"]
        names = [c["venue"] for c in self._candidates(_Reg())]
        self.assertEqual(names, ["okx", "gate"])

    def test_registry_failure_falls_back_to_okx_only(self):
        # ★ 第 47 行
        class _Reg:
            def registered_venues(self):
                raise RuntimeError("registry 表损坏")
        names = [c["venue"] for c in self._candidates(_Reg())]
        self.assertEqual(names, ["okx"], "表读不出来只能退回唯一确定能下单的所")

    def test_empty_registry_yields_no_candidates(self):
        class _Reg:
            def registered_venues(self):
                return []
        self.assertEqual(self._candidates(_Reg()), [])


class PersistVenueDecisionTests(unittest.TestCase):
    """选所证据落盘：**best-effort**，失败只打警告不许影响本轮交易。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = os.path.join(self.tmp.name, "cache.json")
        # 缓存是**扁平**的 `{instId: 决策}` —— 不是 `{"symbols": {...}}`
        Path(self.cache).write_text(
            json.dumps({"BTC-USDT-SWAP": {"action": "WAIT"}}), encoding="utf-8")

    def _run(self):
        from contextlib import contextmanager
        import r20_backend.file_locks as file_locks

        @contextmanager
        def _no_lock(path):
            yield
        with patch.object(venue_evidence, "print", lambda *a, **k: None), \
             patch.object(file_locks, "file_lock", _no_lock):
            return venue_evidence.persist_venue_decision(
                "BTC-USDT-SWAP", {"venue": "okx"}, AI_DECISION_CACHE_FILE=self.cache)

    def test_missing_symbol_is_refused_rather_than_invented(self):
        # 缓存里没有该标的 ⇒ 不伪造决策，直接 False
        Path(self.cache).write_text(json.dumps({"ETH-USDT-SWAP": {}}), encoding="utf-8")
        self.assertFalse(self._run())

    def test_successful_persist_returns_true_and_merges(self):
        self.assertTrue(self._run())
        merged = json.loads(Path(self.cache).read_text(encoding="utf-8"))
        self.assertEqual(merged["BTC-USDT-SWAP"]["action"], "WAIT", "既有字段逐键保留")
        self.assertEqual(merged["BTC-USDT-SWAP"]["venue_decision"], {"venue": "okx"})

    def test_failure_returns_false_instead_of_raising(self):
        # ★ 第 123 行
        with patch.object(venue_evidence.os, "replace",
                          lambda src, dst: (_ for _ in ()).throw(OSError("busy"))):
            self.assertFalse(self._run())


# ───────────────────────── calculus/regime.py ─────────────────────────
class LowVolChoppyRegimeTests(unittest.TestCase):
    """体制判定的**兜底档**：既不冲击、也不趋势、也不宽幅震荡 ⇒ 窄幅低波整理。"""

    def _pkg(self, name="BTC-USDT-SWAP"):
        return {"instId": name, "price": 100.0, "atr_1h": 0.2, "adx_1h": 12.0,
                "calculus": {"velocity_1h": 0.0, "accel_1h": 0.0, "jerk_1h": 0.0,
                             "kinematic_regime": "STABLE"},
                "macro_4h": "NEUTRAL"}

    def test_quiet_market_lands_in_low_vol_choppy(self):
        # ★ 第 165/166 行
        out = regime.detect_macro_market_regime([self._pkg()])
        self.assertEqual(out["regime_id"], regime.REGIME_LOW_VOL_CHOPPY)
        self.assertIn("窄幅低波整理", out["recommended_action"])
        self.assertIn("等待放量破位", out["recommended_action"])

    def test_action_text_warns_against_overtrading(self):
        out = regime.detect_macro_market_regime([self._pkg()])
        self.assertIn("磨损手续费", out["recommended_action"])

    def test_regime_name_tag_and_profile_come_from_the_tables(self):
        out = regime.detect_macro_market_regime([self._pkg()])
        self.assertEqual(out["regime_name"], regime._REGIME_NAMES[regime.REGIME_LOW_VOL_CHOPPY])
        self.assertEqual(out["regime_tag"], regime._REGIME_TAGS[regime.REGIME_LOW_VOL_CHOPPY])

    def test_high_adx_and_one_sided_flow_is_trend_expansion(self):
        # 对照：同夹具但 ADX 高 + 全体同向 ⇒ 不该落到兜底档
        pkgs = [dict(self._pkg(), adx_1h=45.0, macro_4h="BULL") for _ in range(3)]
        self.assertEqual(regime.detect_macro_market_regime(pkgs)["regime_id"],
                         regime.REGIME_TREND_EXPANSION)


# ───────────────────────── news/importance.py ─────────────────────────
class CryptoMacroRelevanceTests(unittest.TestCase):
    def test_crypto_keyword_in_title_is_relevant(self):
        # ★ 第 88/89 行
        self.assertTrue(importance.is_crypto_or_macro_relevant("Bitcoin 突破新高", ""))

    def test_macro_keyword_in_the_summary_is_relevant(self):
        self.assertTrue(importance.is_crypto_or_macro_relevant("快讯", "美联储降息预期升温"))

    def test_matching_is_case_insensitive(self):
        self.assertTrue(importance.is_crypto_or_macro_relevant("BTC ETF 获批", ""))
        self.assertTrue(importance.is_crypto_or_macro_relevant("btc etf 获批", ""))

    def test_title_and_summary_are_joined_with_a_space(self):
        # ⚠️ 实测细节：拼接用的是 `f"{title} {summary}"` —— **带一个空格**，
        #    所以关键词**被拆到两个字段里**是命中不了的（"币"+"安" ≠ "币安"）。
        #    但完整关键词落在任一侧都能命中。
        self.assertFalse(importance.is_crypto_or_macro_relevant("币", "安上线新合约"))
        self.assertTrue(importance.is_crypto_or_macro_relevant("币安", "上线新合约"))
        self.assertTrue(importance.is_crypto_or_macro_relevant("某交易所", "币安上线新合约"))

    def test_unrelated_news_is_not_relevant(self):
        self.assertFalse(importance.is_crypto_or_macro_relevant("某地天气晴朗", "适合出游"))

    def test_empty_inputs_are_not_relevant(self):
        self.assertFalse(importance.is_crypto_or_macro_relevant("", ""))


# ───────────────────────── ledger/okx_history.py ─────────────────────────
class OkxExitReasonTests(unittest.TestCase):
    """出场原因推断：**按匹配到的平仓单属性**分派（不是按 pnl 强弱硬编）。"""

    def _h(self, **over):
        h = {"cTime": 1757850000000, "uTime": 1757853600000,
             "instId": "BTC-USDT-SWAP", "direction": "long",
             "openAvgPx": "100", "closeAvgPx": "105", "pnl": "5.0", "fee": "-0.5",
             "lever": "3", "closeTotalPos": "10", "type": "2"}
        h.update(over)
        return h

    def _run(self, h=None, close_orders=None):
        h = h if h is not None else self._h()
        return okx_history.build_okx_trade(
            h=h, reset_time="2000-01-01 00:00:00", allowed={"BTC-USDT-SWAP"},
            close_orders=close_orders if close_orders is not None else [],
            env=SimpleNamespace(mode="live", fingerprint="FP"),
            tz_bj=datetime.timezone(datetime.timedelta(hours=8)),
            id_suffix="_1", datetime=datetime, get_ct_val=lambda inst: 1.0)

    def _order(self, **over):
        o = {"instId": "BTC-USDT-SWAP", "posSide": "long", "uTime": 1757853600000,
             "algoId": None, "clOrdId": "abc123", "tag": ""}
        o.update(over)
        return o

    def test_fallback_branch_classifies_by_pnl(self):
        # ★ 第 122 行 —— 既无 algoId、clOrdId 也不以 "O" 开头、tag 里没有 "CLI"
        self.assertIn("目标止盈达成", self._run(close_orders=[self._order()])["exit_reason"])

    def test_fallback_branch_reports_a_loss(self):
        h = self._h(pnl="-50.0")
        self.assertIn("止损离场", self._run(h, [self._order()])["exit_reason"])

    def test_fallback_branch_reports_breakeven(self):
        h = self._h(pnl="0.5")
        self.assertIn("保本平仓", self._run(h, [self._order()])["exit_reason"])

    def test_unmatched_close_order_uses_the_wider_wording(self):
        # 匹配不到时措辞不同（"止损出场" vs "止损离场"）
        h = self._h(pnl="-50.0")
        self.assertIn("止损出场", self._run(h, [])["exit_reason"])

    def test_algo_order_branch_takes_precedence(self):
        h = self._h(pnl="0.5")
        order = self._order(algoId="ALGO1")
        self.assertIn("移动止损保本出场", self._run(h, [order])["exit_reason"])

    def test_algo_prefixed_client_order_id_uses_the_strategy_wording(self):
        order = self._order(clOrdId="O12345")
        self.assertIn("移动止盈锁利", self._run(close_orders=[order])["exit_reason"])

    def test_cli_tag_is_recognised(self):
        order = self._order(clOrdId="x", tag="CLI")
        h = self._h(pnl="-50.0")
        self.assertIn("策略风控止损", self._run(h, [order])["exit_reason"])

    def test_liquidation_type_short_circuits(self):
        self.assertIn("强平出场", self._run(self._h(type="3"))["exit_reason"])

    def test_blank_pnl_is_breakeven_not_a_crash(self):
        self.assertIn("保本", self._run(self._h(pnl=""))["exit_reason"])


# ───────────────────────── backtest/lifecycle.py ─────────────────────────
class ExitDecisionReprTests(unittest.TestCase):
    def test_repr_shows_every_slot(self):
        # ★ 第 58 行（源码标了 `pragma: no cover - 仅调试用`，但它确实是可达代码）
        d = lifecycle.ExitDecision(True, 105.5, "TP", 100.0)
        text = repr(d)
        self.assertIn("should_exit=True", text)
        self.assertIn("exit_price=105.5", text)
        self.assertIn("exit_reason='TP'", text)
        self.assertIn("initial_stop_loss=100.0", text)

    def test_repr_quotes_the_reason_so_empty_strings_are_visible(self):
        self.assertIn("exit_reason=''", repr(lifecycle.ExitDecision(False, 0.0, "", 0.0)))

    def test_slots_prevent_typo_attributes(self):
        d = lifecycle.ExitDecision(False, 0.0, "", 0.0)
        with self.assertRaises(AttributeError):
            d.should_exitt = True


if __name__ == "__main__":
    unittest.main()

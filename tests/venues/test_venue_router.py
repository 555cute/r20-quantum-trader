"""US-002 venue_router 单测（全 mock，封闭三律：零网络/零凭证/零临时文件）。

patch 模块绑定名：r20_backend.exchanges.listing.ensure_contract_listed
（venue_router 通过 `from .exchanges import listing` 绑定模块对象，
patch 模块属性即可生效）。
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from r20_backend.venue_router import (  # noqa: E402
    RouteDecision, route_signal, split_allocation, RouterConfig,
)
from r20_backend.exchanges import listing as listing_mod  # noqa: E402

NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-09-11T12:00:00Z"


def _ok_listing(venue, environment, contract):
    return listing_mod.ListingCheck(ok=True, reason=None,
                                    checked_at=NOW_ISO, source="cache")


def _failopen_listing(venue, environment, contract):
    return listing_mod.ListingCheck(ok=True, reason="行情目录不可用，跳过对账",
                                    checked_at=NOW_ISO, source="cache")


def _delisted_listing(venue, environment, contract):
    return listing_mod.ListingCheck(ok=False, reason="合约已下架：OKX state=suspend",
                                    checked_at=NOW_ISO, source="fresh")


def _cand(venue="okx", **kw):
    base = dict(
        venue=venue, environment="live", executable=True,
        precision=0.001, min_qty=0.01, min_notional=5.0,
        fee_rate=0.0005, funding_rate=0.0001, stability_penalty=0.0,
        spread_bps=2.0, depth_usd=100000.0, current_venue=False,
        health_updated_utc=NOW_ISO, health_max_age_s=900,
        price=100.0,
    )
    base.update(kw)
    return base


def _signal(**kw):
    base = dict(symbol_canonical="BTC", inst_id="BTC-USDT-SWAP",
                side="long", size_usdt=1000.0, price=100.0)
    base.update(kw)
    return base


def _cfg(**kw):
    kw.setdefault("now_utc", NOW_ISO)
    return RouterConfig(**kw)


class _Budget:
    def __init__(self, available):
        self.available = available


class TestHardFilters(unittest.TestCase):
    """验收 2：硬筛逐项淘汰进 rejected，带 stage + 原因。"""

    def run_route(self, candidates, signal=None, budget=None, listing_fn=_ok_listing):
        with patch.object(listing_mod, "ensure_contract_listed", listing_fn):
            return route_signal(signal or _signal(), candidates,
                                budget_view=budget, config=_cfg())

    def test_executable_false_rejected(self):
        d = self.run_route([_cand("okx", executable=False), _cand("binance")])
        self.assertEqual(d.venue, "binance")
        r = next(x for x in d.rejected if x["venue"] == "okx")
        self.assertEqual(r["stage"], "executable")
        self.assertIn("开闸", r["reason"])

    def test_precision_min_notional_rejected(self):
        d = self.run_route([_cand("okx", min_notional=5000.0), _cand("binance")])
        r = next(x for x in d.rejected if x["venue"] == "okx")
        self.assertEqual(r["stage"], "precision")
        self.assertIn("min_notional", r["reason"])

    def test_qty_step_misaligned_rejected(self):
        # size 1000 / price 100 = 10.0005 币，precision=0.001 不对齐
        d = self.run_route([_cand("okx", precision=0.001),
                            _cand("binance")],
                           signal=_signal(size_usdt=1000.05))
        r = next(x for x in d.rejected if x["venue"] == "okx")
        self.assertEqual(r["stage"], "precision")
        self.assertIn("步进", r["reason"])

    def test_min_qty_rejected(self):
        # qty = 100/100 = 1.0 ≥ 0.01；把 min_qty 提到 2.0 → 淘汰
        d = self.run_route([_cand("okx", min_qty=2.0), _cand("binance")],
                           signal=_signal(size_usdt=100.0))
        r = next(x for x in d.rejected if x["venue"] == "okx")
        self.assertEqual(r["stage"], "precision")
        self.assertIn("min_qty", r["reason"])

    def test_stale_health_rejected(self):
        stale = (NOW - timedelta(seconds=1200)).strftime("%Y-%m-%dT%H:%M:%SZ")
        d = self.run_route([_cand("okx", health_updated_utc=stale), _cand("binance")])
        r = next(x for x in d.rejected if x["venue"] == "okx")
        self.assertEqual(r["stage"], "freshness")
        self.assertIn("新鲜度", r["reason"])

    def test_missing_health_rejected(self):
        d = self.run_route([_cand("okx", health_updated_utc=None), _cand("binance")])
        r = next(x for x in d.rejected if x["venue"] == "okx")
        self.assertEqual(r["stage"], "freshness")

    def test_budget_insufficient_rejected(self):
        d = self.run_route([_cand("okx")], budget=_Budget(500.0))
        self.assertIsNone(d.venue)
        self.assertEqual(d.reason_code, "ALL_REJECTED")
        r = d.rejected[0]
        self.assertEqual(r["stage"], "budget")
        self.assertIn("预算不足", r["reason"])

    def test_rejected_entries_have_stage_and_reason(self):
        d = self.run_route([_cand("okx", executable=False),
                            _cand("binance", health_updated_utc=None)])
        self.assertEqual(len(d.rejected), 2)
        for entry in d.rejected:
            self.assertIn("stage", entry)
            self.assertIn("reason", entry)
            self.assertTrue(entry["reason"])


class TestListingGate(unittest.TestCase):
    """验收 2：listing gate 拒 / fail-open 放行带注记。"""

    def test_listing_fail_rejected(self):
        with patch.object(listing_mod, "ensure_contract_listed", _delisted_listing):
            d = route_signal(_signal(), [_cand("okx"), _cand("binance")],
                             config=_cfg())
        r = next(x for x in d.rejected if x["venue"] == "okx")
        self.assertEqual(r["stage"], "listing")
        self.assertIn("下架", r["reason"])

    def test_listing_failopen_passes_with_note(self):
        with patch.object(listing_mod, "ensure_contract_listed", _failopen_listing):
            d = route_signal(_signal(), [_cand("okx")], config=_cfg())
        self.assertEqual(d.venue, "okx")
        self.assertFalse(d.rejected)
        self.assertTrue(any("fail-open" in r or "跳过对账" in r for r in d.reasons))

    def test_hard_filters_pass_native_contract_not_canonical_base(self):
        """P0 回归（2026-09-16·下单全拒事故）：硬筛必须把**该所原生合约码**交给
        listing gate，而不是 canonical base。

        事故根因：`selection.py` 从 `venue_router.py` 抽进 `venue_routing/` 子包后
        写成单点相对导入 `.exchanges.registry`（应为 `..exchanges.registry`），
        ImportError 被裸 except 吞掉 → `native_contract` 退化成 `BTC` → OKX 目录键
        是 `BTC-USDT-SWAP`，必然「沙盒未上市」→ 全部候选所 ALL_REJECTED →
        **一单都开不出去**。

        本门钉两件事：① 三所各自拿到原生码；② 对齐过程**不得**再发 RuntimeWarning
        （静默降级通道关闭）。
        """
        seen: list[tuple[str, str]] = []

        def _capture(venue, environment, contract):
            seen.append((venue, contract))
            return _ok_listing(venue, environment, contract)

        for venue, native in (("okx", "BTC-USDT-SWAP"),
                              ("binance", "BTCUSDT"),
                              ("gate", "BTC_USDT")):
            with self.subTest(venue=venue):
                seen.clear()
                import warnings as _w
                with patch.object(listing_mod, "ensure_contract_listed", _capture), \
                     _w.catch_warnings(record=True) as caught:
                    _w.simplefilter("always")
                    route_signal(_signal(), [_cand(venue)], config=_cfg())
                self.assertIn((venue, native), seen,
                              f"{venue} 未拿到原生合约码 {native}；实际={seen}")
                self.assertNotIn((venue, "BTC"), seen,
                                 f"{venue} 拿到了 canonical base（listing gate 会误判未上市）")
                self.assertFalse([str(w.message) for w in caught if "对齐失败" in str(w.message)],
                                 f"{venue} 原生合约码对齐发生了静默降级：{[str(w.message) for w in caught]}")


class TestVenuePoolGate(unittest.TestCase):
    """2026-09-16 P0（第二层）：路由必须尊重各所**准入币种清单**。

    事故链：listing gate 修好之后，`_balanced_pick` 仍会把不在 binance/gate
    准入清单里的标的（如 ARB）按 sha256 分到那两所 → 执行层 pool 门禁必拒
    （「不在 BINANCE 准入币种清单」）→ 而 `route_signal` 选中即**不回退**
    ⇒ 主脑发单继续全灭。本组钉：路由阶段就淘汰这类候选，并回退到真正能做的所。
    """

    def test_pool_mismatch_rejected_as_venue_pool_and_falls_back(self):
        from r20_backend.venue_routing import selection as sel
        pool = {"binance": ["BTC"], "gate": ["BTC"], "okx": None}
        with patch.object(listing_mod, "ensure_contract_listed", _ok_listing), \
             patch.object(sel, "_venue_pool_assets", lambda v: pool.get(v)):
            d = route_signal(
                _signal(symbol_canonical="ARB", inst_id="ARB-USDT-SWAP"),
                [_cand("okx"), _cand("binance"), _cand("gate")], config=_cfg())
        self.assertEqual(d.venue, "okx", "必须回退到不受清单限制的所")
        stages = {(r["venue"], r["stage"]) for r in d.rejected}
        self.assertIn(("binance", "venue_pool"), stages)
        self.assertIn(("gate", "venue_pool"), stages)
        for r in d.rejected:
            if r["stage"] == "venue_pool":
                self.assertIn("准入币种清单", r["reason"])

    def test_asset_in_pool_passes(self):
        from r20_backend.venue_routing import selection as sel
        with patch.object(listing_mod, "ensure_contract_listed", _ok_listing), \
             patch.object(sel, "_venue_pool_assets", lambda v: ["BTC"]):
            d = route_signal(_signal(), [_cand("okx")], config=_cfg())
        self.assertEqual(d.venue, "okx")
        self.assertFalse([r for r in d.rejected if r["stage"] == "venue_pool"])

    def test_empty_or_absent_pool_does_not_restrict(self):
        """空清单/未配置 ≠ 新造一条拒绝理由（执行层才有「空池=停发」语义，
        OKX 直下路径更是没有池概念）。"""
        from r20_backend.venue_routing import selection as sel
        with patch.object(listing_mod, "ensure_contract_listed", _ok_listing), \
             patch.object(sel, "_venue_pool_assets", lambda v: None):
            d = route_signal(
                _signal(symbol_canonical="ARB", inst_id="ARB-USDT-SWAP"),
                [_cand("okx"), _cand("binance")], config=_cfg())
        self.assertIsNotNone(d.venue)
        self.assertFalse([r for r in d.rejected if r["stage"] == "venue_pool"])

    def test_pool_loader_reads_and_normalizes_routing_file(self):
        """清单来源 = data/venue_routing.json 的 per-venue assets（去重+大写）。"""
        import json
        import tempfile
        from pathlib import Path
        from r20_backend.exchanges import routing_policy as rp
        from r20_backend.venue_routing import selection as sel
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "venue_routing.json"
            f.write_text(json.dumps({"okx": {"assets": ["BTC", "eth", "btc"]}}),
                         encoding="utf-8")
            with patch.object(rp, "ROUTING_FILE", f):
                self.assertEqual(sel._venue_pool_assets("okx"), ["BTC", "ETH"])
                # 未配置的所 → None（不淘汰）
                self.assertIsNone(sel._venue_pool_assets("gate"))


class TestScoring(unittest.TestCase):
    """验收 3：评分排序正确、资金费按方向/周期估计。"""

    def test_spread_ordering(self):
        # fee/funding 相同，价差低者胜
        cands = [_cand("okx", spread_bps=5.0), _cand("binance", spread_bps=1.0)]
        with patch.object(listing_mod, "ensure_contract_listed", _ok_listing):
            d = route_signal(_signal(), cands, config=_cfg())
        self.assertEqual(d.venue, "binance")
        self.assertEqual(d.reason_code, "OK")

    def test_funding_fee_in_score_and_direction(self):
        # binance 资金费为 0、okx 为 0.01%/8h，持仓 8h、long → okx 多付 1bps
        cands = [_cand("okx", spread_bps=2.0, funding_rate=0.0001),
                 _cand("binance", spread_bps=2.0, funding_rate=0.0)]
        with patch.object(listing_mod, "ensure_contract_listed", _ok_listing):
            d = route_signal(_signal(), cands, config=_cfg())
        self.assertEqual(d.venue, "binance")
        # short 方向则 okx 收资金费 → 反超
        with patch.object(listing_mod, "ensure_contract_listed", _ok_listing):
            d2 = route_signal(_signal(side="short"), cands, config=_cfg())
        self.assertEqual(d2.venue, "okx")

    def test_depth_penalty(self):
        # gate 深度远小于 size*10 → 线性惩罚使其落败
        cands = [_cand("okx", spread_bps=2.0, depth_usd=100000.0),
                 _cand("binance", spread_bps=2.0, depth_usd=100.0)]
        with patch.object(listing_mod, "ensure_contract_listed", _ok_listing):
            d = route_signal(_signal(size_usdt=1000.0), cands, config=_cfg())
        self.assertEqual(d.venue, "okx")
        self.assertTrue(any("深度不足惩罚" in r for r in d.reasons))


class TestHysteresis(unittest.TestCase):
    """验收 4：滞回触发与不触发。"""

    def run_route(self, candidates, signal=None, **cfg_kw):
        # bonus=0 隔离现任 bonus 因子，直接测滞回幅度逻辑
        cfg_kw.setdefault("incumbent_bonus_bps", 0.0)
        with patch.object(listing_mod, "ensure_contract_listed", _ok_listing):
            return route_signal(signal or _signal(), candidates,
                                config=_cfg(**cfg_kw))

    def test_hysteresis_triggered_keeps_incumbent(self):
        # 现任所 gate 微弱落后（领先幅度 < 15%），挑战者 okx 更优 → 保留 gate
        cands = [_cand("okx", spread_bps=1.0),
                 _cand("gate", spread_bps=1.5, current_venue=True)]
        d = self.run_route(cands)
        self.assertEqual(d.venue, "gate")
        self.assertTrue(d.hysteresis_applied)
        self.assertEqual(d.reason_code, "OK_HYSTERESIS")

    def test_hysteresis_not_triggered_when_lead_large(self):
        # 挑战者大幅领先（>15%）→ 正常切换
        cands = [_cand("okx", spread_bps=0.1),
                 _cand("gate", spread_bps=10.0, current_venue=True)]
        d = self.run_route(cands)
        self.assertEqual(d.venue, "okx")
        self.assertFalse(d.hysteresis_applied)

    def test_incumbent_best_no_hysteresis(self):
        cands = [_cand("okx", spread_bps=3.0),
                 _cand("gate", spread_bps=1.0, current_venue=True)]
        d = self.run_route(cands)
        self.assertEqual(d.venue, "gate")
        self.assertFalse(d.hysteresis_applied)


class TestAllocation(unittest.TestCase):
    """验收 5：多所分配 off/on。"""

    def test_split_disabled_returns_none(self):
        alloc = split_allocation(_signal(), [_cand(), _cand("binance")],
                                 _Budget(10000.0), _cfg())
        self.assertIsNone(alloc)
        with patch.object(listing_mod, "ensure_contract_listed", _ok_listing):
            d = route_signal(_signal(), [_cand(), _cand("binance")],
                             budget_view=_Budget(10000.0), config=_cfg())
        self.assertIsNone(d.allocation)

    def test_split_enabled_proportional(self):
        cfg = _cfg(split_enabled=True, min_slice_usdt=100.0)
        cands = [_cand("okx", spread_bps=1.0), _cand("binance", spread_bps=3.0)]
        with patch.object(listing_mod, "ensure_contract_listed", _ok_listing):
            d = route_signal(_signal(size_usdt=10000.0), cands,
                             budget_view=_Budget(10000.0), config=cfg)
        self.assertIsNotNone(d.allocation)
        self.assertEqual({s["venue"] for s in d.allocation}, {"okx", "binance"})
        total = sum(s["amount_usdt"] for s in d.allocation)
        self.assertAlmostEqual(total, 10000.0, delta=1.0)
        okx_slice = next(s for s in d.allocation if s["venue"] == "okx")
        self.assertGreater(okx_slice["amount_usdt"], 5000.0)  # 成本低者占大头

    def test_split_min_slice_drops_small_venue(self):
        # 两所评分悬殊 + min_slice 高 → 小切片被丢 → 退回单所（None）
        cfg = _cfg(split_enabled=True, min_slice_usdt=9000.0)
        cands = [_cand("okx", spread_bps=1.0), _cand("binance", spread_bps=50.0)]
        # pre_alive=None 时 split_allocation 内部会重跑硬筛 → listing 必须钉（封闭律）
        with patch.object(listing_mod, "ensure_contract_listed", _ok_listing):
            alloc = split_allocation(_signal(size_usdt=10000.0), cands,
                                     _Budget(10000.0), cfg)
        self.assertIsNone(alloc)


class TestMisc(unittest.TestCase):
    def test_no_candidates(self):
        d = route_signal(_signal(), [], config=_cfg())
        self.assertIsNone(d.venue)
        self.assertEqual(d.reason_code, "NO_CANDIDATES")

    def test_decision_shape(self):
        with patch.object(listing_mod, "ensure_contract_listed", _ok_listing):
            d = route_signal(_signal(), [_cand()], config=_cfg())
        self.assertIsInstance(d, RouteDecision)
        self.assertTrue(d.reasons)  # 可解释：至少有评分分项


class HealthWindowVsRefreshCadenceTest(unittest.TestCase):
    """健康新鲜度窗口必须**明显大于**刷新周期，否则外所会"闪进闪出"候选集。

    为什么值得单独钉：`health_updated_utc` 的新鲜度闸门是 **fail-closed** 的
    （无记录/不可解析/过期 ⇒ 淘汰，已有 `test_missing_health_rejected` /
    `test_stale_health_rejected` 覆盖）。方向安全，但**阈值太紧会变成可用性事故**：
    刷新发生在 trader 周期里（`scheduler.JOBS["trader"]`），窗口若 ≤ 周期，
    外所会在"刚刷新⇒合格 / 隔一会⇒过期"之间反复闪动，与"三所平权"的路由目标相悖。
    两个数字分布在两个文件里（改任一个都容易忘记另一个）⇒ 用本门把它们绑在一起。
    """

    def _const(self, path, name):
        import ast
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == name:
                        return self._fold(node.value)
        raise AssertionError(f"{path.name} 里找不到 {name}")

    def _fold(self, node):
        """只做字面量四则折叠（`15 * 60` 这种写法很常见，literal_eval 读不了）。"""
        import ast
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            l, r = self._fold(node.left), self._fold(node.right)
            if isinstance(node.op, ast.Mult):
                return l * r
            if isinstance(node.op, ast.Add):
                return l + r
        raise AssertionError(f"不支持的表达式：{ast.dump(node)[:80]}")

    def test_window_is_at_least_twice_the_trader_interval(self):
        root = Path(__file__).resolve().parents[2]
        max_age = self._const(root / "scripts" / "ai_factor_trader.py",
                              "VENUE_HEALTH_MAX_AGE_S")
        schedule = (root / "r20_backend" / "scheduler.py").read_text(encoding="utf-8")
        import ast
        jobs = next(n for n in ast.parse(schedule).body if isinstance(n, ast.Assign)
                    and any(getattr(t, "id", None) == "JOBS" for t in n.targets))
        trader = next(v for k, v in zip(jobs.value.keys, jobs.value.values)
                      if getattr(k, "value", None) == "trader")
        interval = self._fold(trader.elts[1])
        self.assertGreaterEqual(
            float(max_age), 2 * float(interval),
            f"健康新鲜度窗口 {max_age}s 未达 trader 周期 {interval}s 的两倍 —— "
            "外所会在合格/过期之间闪动（阈值太紧＝可用性事故）")

if __name__ == "__main__":
    unittest.main()
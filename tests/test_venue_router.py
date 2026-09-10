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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


if __name__ == "__main__":
    unittest.main()

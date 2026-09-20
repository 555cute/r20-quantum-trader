# -*- coding: utf-8 -*-
"""OKX 持仓的保护判据（`dashboard_payload/algo_protection.py`，2026-09-20 第一百二十刀）。

## 这个门在守什么

这段逻辑此前**一个单元测试都没有**（只在整包测试里被 patch 掉），于是两处判定
长期没人对：跨所路径（`scripts/trader/venue_protection.py`，有专测）与 OKX 面板路径
**各写了一套覆盖口径**，结果不一致。本刀实测的两处分叉：

1. **整仓平腿被算成 0 覆盖（假阴性）**：`protected_size` 只累加 `sz`，而
   「平掉整个仓位」语义的腿（OKX `closePosition=true`）**给不出张数**
   ⇒ 面板/提示词把这笔报成 `partially_protected / 0%`，交易所那条腿其实平掉整仓。
   跨所路径早就有 `_is_full_close` 处理这件事（Gate `close`+`size=0` 同理）。
2. **没有证据的确定结论**：有止损腿但量读不出来时，旧实现给出 `0%`，
   而 `scan_protective_orders` 的口径是 `coverage_ok=None`（**不可判定≠安全**）。

判据会被前端（`KpiRibbon` / `PositionsOrdersPanel`）与提示词消费
（第一百一十九刀起提示词直接渲染 `保护: …`），所以说谎的代价是模型与运营一起被误导。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from r20_backend.dashboard_payload.algo_protection import collect_algo_protection


def _leg(**over):
    row = {"algoId": "a1", "state": "live", "posSide": "long", "reduceOnly": "true",
           "sz": "100", "slTriggerPx": "90", "tpTriggerPx": "110"}
    row.update(over)
    return row


class AlgoProtectionVerdictTest(unittest.TestCase):
    def _run(self, algos, *, pos_sz=100.0, position=None):
        pos = dict(position) if position is not None else {
            "instId": "ETH-USDT-SWAP", "posSide": "long", "pos_sz": pos_sz, "pos": str(pos_sz)}
        errs: list = []
        collect_algo_protection([pos], errs,
                                lambda fn, *a, **k: (True, algos, ""),
                                lambda *a, **k: None, {})
        return pos, errs

    def test_full_size_oco_is_fully_protected(self):
        pos, errs = self._run([_leg()])
        self.assertEqual(pos["protectionStatus"], "fully_protected")
        self.assertEqual(pos["protectionCoveragePct"], 100.0)
        self.assertEqual(pos["exchangeSl"], 90.0)
        self.assertEqual(pos["exchangeTp"], 110.0)
        self.assertEqual(errs, [])

    def test_close_position_leg_covers_the_whole_position(self):
        """⭐ 本刀修的假阴性：`closePosition=true` 的整仓止损腿**平掉整个仓位**。

        它给不出 `sz` ⇒ 旧实现报 `partially_protected / 0%`。
        """
        pos, _ = self._run([_leg(algoId="cp", closePosition=True, sz=None,
                                 tpTriggerPx=None)])
        self.assertEqual(pos["protectionCoveragePct"], 100.0,
                         "整仓平腿必须算作 100% 覆盖，而不是 0%")
        self.assertEqual(pos["exchangeSl"], 90.0)

    def test_close_position_oco_is_fully_protected(self):
        pos, _ = self._run([_leg(closePosition=True, sz=None)])
        self.assertEqual(pos["protectionStatus"], "fully_protected")
        self.assertEqual(pos["protectionCoveragePct"], 100.0)

    def test_gate_style_close_flag_also_counts(self):
        """同一谓词也认 Gate/Binance 形态（复用 `venue_protection._is_full_close`）。"""
        pos, _ = self._run([_leg(close=True, sz=None, tpTriggerPx=None)])
        self.assertEqual(pos["protectionCoveragePct"], 100.0)

    def test_sl_only_with_size_is_partial_but_coverage_is_stated(self):
        pos, _ = self._run([_leg(tpTriggerPx=None)])
        self.assertEqual(pos["protectionStatus"], "partially_protected")
        self.assertEqual(pos["protectionCoveragePct"], 100.0)

    def test_half_sized_leg_is_half_coverage(self):
        pos, _ = self._run([_leg(sz="50", tpTriggerPx=None)])
        self.assertEqual(pos["protectionStatus"], "partially_protected")
        self.assertEqual(pos["protectionCoveragePct"], 50.0)

    def test_canceled_leg_is_not_protection(self):
        pos, _ = self._run([_leg(state="canceled")])
        self.assertEqual(pos["protectionStatus"], "unprotected")
        self.assertEqual(pos["protectionCoveragePct"], 0.0)
        self.assertIsNone(pos["exchangeSl"])

    def test_other_side_leg_is_not_protection(self):
        """对冲模式：另一方向的腿不得算到这笔仓上。"""
        pos, _ = self._run([_leg(posSide="short")])
        self.assertEqual(pos["protectionStatus"], "unprotected")

    def test_unreadable_leg_size_is_unknown_not_zero_percent(self):
        """⭐ 有止损腿但量读不出来 ⇒ **不可判定**（旧实现给"0% 覆盖"这一确定结论）。"""
        pos, _ = self._run([_leg(sz=None, tpTriggerPx=None)])
        self.assertEqual(pos["protectionStatus"], "unknown")
        self.assertIsNone(pos["protectionCoveragePct"])
        self.assertEqual(pos["exchangeSl"], 90.0, "触发价仍然如实展示")

    def test_unreadable_position_size_is_unknown_not_fully_protected(self):
        """持仓规模读不出来时，不得因为"腿覆盖 ≥ 0"就宣称完全保护。"""
        pos, _ = self._run([_leg()], position={"instId": "ETH-USDT-SWAP",
                                               "posSide": "long"})
        self.assertEqual(pos["protectionStatus"], "unknown")
        self.assertIsNone(pos["protectionCoveragePct"])

    def test_missing_pos_sz_key_does_not_raise(self):
        """健壮性：旧实现用 `position["pos_sz"]` 直接下标 ⇒ 缺键会 KeyError。

        KeyError 会打断整包构建（面板转陈旧），而"键可能缺席"是这张 payload 的常态
        （`pos_sz` 由上游多处写入）。
        """
        pos, _ = self._run([_leg()], position={"instId": "ETH-USDT-SWAP",
                                               "posSide": "long", "pos": ""})
        self.assertIn("protectionStatus", pos)

    def test_algo_read_failure_is_reported_and_fail_closed(self):
        pos = {"instId": "ETH-USDT-SWAP", "posSide": "long", "pos_sz": 100.0}
        errs: list = []
        collect_algo_protection([pos], errs,
                                lambda fn, *a, **k: (False, [], "timeout"),
                                lambda *a, **k: None, {})
        self.assertEqual(pos["protectionStatus"], "unprotected")
        self.assertTrue(any("timeout" in e for e in errs), errs)

    def test_no_positions_no_call(self):
        called = []
        collect_algo_protection([], [], lambda *a, **k: called.append(1) or (True, [], ""),
                                lambda *a, **k: None, {})
        self.assertEqual(called, [], "空仓不得发起取数")


if __name__ == "__main__":
    unittest.main()

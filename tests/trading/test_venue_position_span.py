"""「持仓构成」口径回归（2026-09 用户报「现在的通知有bug，平台只有okx」）。

## 缺陷形状（实测）

巡检通知与 AI 提示词把场所**写死**成 `持仓 OKX {n}/{max}`，而系统实际在三个所上跑
（OKX 直签 + Binance/Gate 跨所）。真机原文：

    ⚡ AstraQuant v8.3.1 巡检完成 | 持仓 OKX 1/9 (多0/空1)｜跨所 5 笔 | 动作: …

用户读到的是"只有 OKX 有仓"，另外两所只以「跨所 5 笔」出现 ——
**看不出是哪个所、更看不出各所几笔**。同一句话还进了提示词（`pos_desc`），
于是主脑也以为"只有 OKX 有仓"。

修法：抽出纯函数 `venue_position_span`，通知与提示词共用；口径两条：

1. **绝不装 0**：跨所拉取失败（`xv_total is None`）时总和与多空只报 OKX，
   并显式追加「跨所未知」—— 装 0 等于对用户谎报"外所没仓"；
2. 只列**本轮真拉到的**所（含 0 笔），不为没启用的所凑 0。

本文件既钉纯函数口径，也钉**两个接线点**（只抽函数不接线 = 通知照旧写死）。
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.trader import cycle_stages  # noqa: E402
from scripts.trader.cycle_snapshot import venue_position_span  # noqa: E402


class VenuePositionSpanTest(unittest.TestCase):
    def test_three_venues_are_all_named(self):
        """真机形状：OKX 2 笔 + Binance 4 笔 + Gate 1 笔 —— 三个所都要点名。"""
        span = venue_position_span(
            okx_count=2, okx_long=1, okx_short=1,
            xv_positions_by_venue={"binance": [{"side": "short"}] * 4,
                                   "gate": [{"side": "long"}]},
            xv_total=5, max_positions=9)
        self.assertEqual(span, "持仓 7/9 (多2/空5)｜okx 2 · binance 4 · gate 1")

    def test_hardcoded_okx_label_is_gone(self):
        """回归线：不得再出现写死的 `持仓 OKX`（那正是用户报的措辞）。"""
        span = venue_position_span(
            okx_count=2, okx_long=1, okx_short=1,
            xv_positions_by_venue={"binance": [{"side": "short"}] * 4, "gate": []},
            xv_total=4, max_positions=9)
        self.assertNotIn("持仓 OKX", span)

    def test_fetch_failure_never_pretends_zero(self):
        """跨所读不出来时**只报 OKX** 并写明「跨所未知」——绝不装 0。"""
        span = venue_position_span(
            okx_count=2, okx_long=0, okx_short=2,
            xv_positions_by_venue={}, xv_total=None, max_positions=9)
        self.assertEqual(span, "持仓 2/9 (多0/空2)｜okx 2 · 跨所未知")
        self.assertNotIn("binance 0", span); self.assertNotIn("gate 0", span)

    def test_verified_zeroes_are_shown(self):
        """拉到了但没仓 ⇒ 如实列 0（这正是"平台只有 okx"该看到的形状）。"""
        span = venue_position_span(
            okx_count=1, okx_long=1, okx_short=0,
            xv_positions_by_venue={"binance": [], "gate": []},
            xv_total=0, max_positions=9)
        self.assertEqual(span, "持仓 1/9 (多1/空0)｜okx 1 · binance 0 · gate 0")

    def test_okx_is_first_and_unknown_venues_are_appended(self):
        """OKX 恒在首位（直签链路永远核验）；未登记的所按名补在其后。"""
        span = venue_position_span(
            okx_count=1, okx_long=1, okx_short=0,
            xv_positions_by_venue={"kraken": [{"side": "long"}], "gate": [{"side": "short"}]},
            xv_total=2, max_positions=9)
        self.assertTrue(span.endswith("okx 1 · gate 1 · kraken 1"), span)

    def test_long_short_totals_include_cross_venue(self):
        """多空计数必须是**全场所**合算（此前只报 OKX 的多空）。"""
        span = venue_position_span(
            okx_count=0, okx_long=0, okx_short=0,
            xv_positions_by_venue={"binance": [{"side": "long"}] * 3}, xv_total=3,
            max_positions=9)
        self.assertIn("(多3/空0)", span)


class SpanIsActuallyWiredTest(unittest.TestCase):
    """只抽函数不接线 = 通知照旧写死 ⇒ 接线点必须单独钉住。"""

    def _persist(self, td, **over):
        kw = dict(
            _xv_total=5, xv_positions_by_venue={"binance": [{"side": "short"}] * 4,
                                                "gate": [{"side": "long"}]},
            venue_position_span=venue_position_span,
            active_pos_count=2, all_factors=[], cb_active=False, cb_reason="",
            executed_actions=[], long_count=1, short_count=1,
            timestamp_full="2026-09-26 18:30:00", DATA_DIR=td,
            LEDGER_AUTOSYNC_ENABLED=False, LOG_FILE=os.path.join(td, "t.log"),
            MAX_CONCURRENT_POSITIONS=9, WORKSPACE_DIR=td, __version__="test",
            _atomic_write_json=lambda path, payload: None,
            _run_captured=lambda *a, **k: None,
            build_state_payload=lambda **k: {"ok": True},
            evaluate_asset_signal=lambda *a, **k: None, os=os)
        kw.update(over)
        cycle_stages.persist_state_and_sync_ledger(**kw)

    def test_notification_line_carries_the_venue_composition(self):
        with tempfile.TemporaryDirectory() as td:
            self._persist(td)
            line = Path(td, "t.log").read_text(encoding="utf-8")
        self.assertIn("巡检完成", line)
        self.assertIn("okx 2 · binance 4 · gate 1", line)
        self.assertNotIn("持仓 OKX", line, "巡检头又写死场所了")

    def test_prompt_position_description_carries_the_venue_composition(self):
        """喂给主脑的 `pos_desc` 必须同源，否则模型仍以为"只有 OKX 有仓"。"""
        seen = {}
        cycle_stages.scan_risk_gates_and_ai_brain(
            _xv_total=5, venue_position_span=venue_position_span,
            active_pos_count=2, all_factors=[], executed_actions=[], long_count=1,
            short_count=1, timestamp_full="2026-09-26 18:30:00", trackers={},
            usdt_available=1000.0,
            xv_positions_by_venue={"binance": [{"side": "short"}] * 4,
                                   "gate": [{"side": "long"}]},
            MAX_CONCURRENT_POSITIONS=9,
            _collect_okx_position_payloads=lambda *a, **k: [],
            _merge_cross_venue_positions=lambda *a, **k: [],
            effective_single_asset_margin=lambda u: 123.0,
            execute_ai_position_management=lambda *a, **k: None,
            execute_batch_ai_brain_cycle=lambda pos_desc, *a, **k: seen.update(desc=pos_desc) or {},
            is_circuit_breaker_active=lambda u: (False, ""),
            pool_is_trustworthy=lambda: True, pool_state=lambda: {},
            query_positions=lambda: (True, [], ""), read_cycle_health=lambda: {},
            save_trackers=lambda t: None)
        self.assertIn("desc", seen, "主脑批次没被调用，用例失去意义")
        self.assertIn("okx 2 · binance 4 · gate 1", seen["desc"])
        self.assertNotIn("持仓 OKX", seen["desc"])

    def test_facade_passes_the_pure_helper_not_a_local_copy(self):
        """门面必须把**同一个**纯函数传进去（不许在段内另写一份口径）。"""
        tree = ast.parse((ROOT / "scripts" / "ai_factor_trader.py").read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id in ("persist_state_and_sync_ledger",
                                   "scan_risk_gates_and_ai_brain")]
        self.assertEqual(len(calls), 2, "两个接线点应各恰一处")
        for call in calls:
            passed = {k.arg: ast.unparse(k.value) for k in call.keywords}
            self.assertEqual(passed.get("venue_position_span"), "venue_position_span",
                             f"{call.func.id} 未按同名传入纯函数")


if __name__ == "__main__":
    unittest.main()

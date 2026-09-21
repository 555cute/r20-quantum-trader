"""跨所保护巡检格的接线、防抖与"预演不写单"（第二百一十九刀）。

`venue_protection_watchdog_stage` 是"缺口持续够久才动手"那一格的周期接线，**默认关闭**。
它的每一条保守选择都值得钉住：

- 总闸没开 ⇒ **零网络、零写单**（线上行为逐字不变）；
- 环境轴不可得 ⇒ 本轮跳过；
- 防抖开着时：观察轮（`dry_run=True`）失败 / **状态不可读** / 防抖计算异常 / 状态不可写
  ⇒ 一律**本周期不写单**（状态失真的防抖等于没有防抖；不知道缺口多久了就不动手）；
- `dry_run=True` ⇒ 只判定：把"本来会做"的动作报出来，**且若审计层居然返回 `actions`，
  要当场喊 🔴（预演不得写单）**；
- 缺口未持续够 ⇒ **只观察**，并把「已持续 X 分钟」报进 `executed_actions`（让人看得见它在逼近）；
- 完全没有止损腿的仓位只报 CRITICAL，写明"需人工或用既定策略价位重挂"——**巡检层不臆造价位**；
- 本格任何异常只告警（fail-soft：它是加固层，不该成为新的单点）。
"""

import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.trader.cycle_stages import venue_protection_watchdog_stage
from scripts.trader.venue_protection import watchdog_debounce_step

GAP = {"venue": "gate", "inst": "BTC_USDT", "stage": "expired", "detail": "Gate 腿已过期"}


class _Audit:
    """审计层桩：观察轮（dry_run=True）与真轮返回同一套判定，真轮才带 actions。"""

    def __init__(self, *, would=(), critical=(), errors=(), actions_on_dry=None,
                 actions_on_real=None, raises_on_dry=None, raises_on_real=None):
        self.would = list(would)
        self.critical = list(critical)
        self.errors = list(errors)
        self.actions_on_dry = actions_on_dry
        self.actions_on_real = [GAP] if actions_on_real is None else list(actions_on_real)
        self.raises_on_dry = raises_on_dry
        self.raises_on_real = raises_on_real
        self.calls = []

    def __call__(self, positions, *, venue_registry, environment, dry_run, ledger_rows=None):
        self.calls.append(bool(dry_run))
        if dry_run and self.raises_on_dry is not None:
            raise self.raises_on_dry
        if not dry_run and self.raises_on_real is not None:
            raise self.raises_on_real
        report = {"would": list(self.would), "critical": list(self.critical),
                  "errors": list(self.errors), "actions": []}
        if dry_run:
            report["actions"] = list(self.actions_on_dry or [])
        else:
            report["actions"] = list(self.actions_on_real)
        return report


class WatchdogStageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20-wd-stage-")
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "watchdog.json"
        self.actions = []
        self.printed = io.StringIO()

    def _run(self, audit, **kw):
        params = dict(xv_positions_by_venue={"gate": [{"inst_id": "BTC_USDT"}]},
                      executed_actions=self.actions, venue_registry=object(),
                      current_environment=lambda: type("E", (), {"mode": "demo"})(),
                      R20_VENUE_PROTECTION_WATCHDOG=True, audit_cross_venue_protection=audit)
        params.update(kw)
        with redirect_stdout(self.printed):
            return venue_protection_watchdog_stage(**params)

    # ── 闸门与前置 ────────────────────────────────────────────────────
    def test_gate_off_is_zero_work(self):
        audit = _Audit(would=[GAP])
        out = self._run(audit, R20_VENUE_PROTECTION_WATCHDOG=False)
        self.assertIsNone(out)
        self.assertEqual(audit.calls, [], "总闸没开 ⇒ 零网络零写单（不许偷偷判定）")
        self.assertEqual(self.actions, [])

    def test_unavailable_environment_skips_the_cycle(self):
        def boom():
            raise RuntimeError("env down")
        audit = _Audit(would=[GAP])
        out = self._run(audit, current_environment=boom)
        self.assertIsNone(out)
        self.assertEqual(audit.calls, [])
        self.assertIn("环境轴不可得", self.printed.getvalue())

    def test_without_debounce_it_is_a_single_real_pass(self):
        audit = _Audit(would=[GAP])
        out = self._run(audit)
        self.assertIsNotNone(out)
        self.assertEqual(audit.calls, [False], "不防抖 ⇒ 单轮直通，直接真轮")
        self.assertTrue(any("跨所保护" in a for a in self.actions))

    # ── 防抖：四种"不知道就不动手" ─────────────────────────────────────
    def test_observation_round_failure_blocks_writes(self):
        audit = _Audit(would=[GAP], raises_on_dry=RuntimeError("观察轮炸了"))
        out = self._run(audit, state_path=str(self.state), debounce_step=watchdog_debounce_step,
                        debounce_s=600, now_s=1000.0)
        self.assertIsNone(out, "观察轮失败 ⇒ 本周期不写单（fail-soft 但不冒险）")
        self.assertEqual(audit.calls, [True], "只跑了观察轮，没跑真轮")

    def test_unreadable_state_blocks_writes(self):
        self.state.write_text("{ 坏 JSON", encoding="utf-8")
        audit = _Audit(would=[GAP])
        out = self._run(audit, state_path=str(self.state), debounce_step=watchdog_debounce_step,
                        debounce_s=600, now_s=1000.0)
        self.assertIsNone(out, "状态不可读 ⇒ 不写单（不知道缺口持续多久）")
        self.assertEqual(audit.calls, [True], "只观察、不写单")
        self.assertIn("防抖状态不可读", self.printed.getvalue())

    def test_debounce_step_failure_blocks_writes(self):
        def boom(*a, **k):
            raise RuntimeError("debounce boom")
        audit = _Audit(would=[GAP])
        out = self._run(audit, state_path=str(self.state), debounce_step=boom,
                        debounce_s=600, now_s=1000.0)
        self.assertIsNone(out)
        self.assertIn("防抖计算异常", self.printed.getvalue())

    def test_unsaveable_state_blocks_writes(self):
        audit = _Audit(would=[GAP])
        with patch("scripts.trader.cycle_stages._save_watchdog_state", lambda *a: False):
            out = self._run(audit, state_path=str(self.state),
                            debounce_step=watchdog_debounce_step, debounce_s=600, now_s=1000.0)
        self.assertIsNone(out, "状态写不进去 ⇒ 下轮状态会失真 ⇒ 本轮不写单")
        self.assertIn("防抖状态不可写", self.printed.getvalue())

    # ── 防抖：观察 → 合格 ─────────────────────────────────────────────
    def test_gap_is_observed_until_it_qualifies(self):
        audit = _Audit(would=[GAP])
        out = self._run(audit, state_path=str(self.state), debounce_step=watchdog_debounce_step,
                        debounce_s=600, now_s=1000.0)
        self.assertIsNotNone(out)
        self.assertEqual(audit.calls, [True], "还没持续够 ⇒ 只观察，不许写单")
        self.assertTrue(any("已持续" in a for a in self.actions),
                        f"逼近阈值也要让人看见：{self.actions}")
        self.assertTrue(json.loads(self.state.read_text(encoding="utf-8"))["gaps"],
                        "首次出现要记下时刻")

    def test_qualified_gap_runs_the_real_round(self):
        audit = _Audit(would=[GAP])
        self._run(audit, state_path=str(self.state), debounce_step=watchdog_debounce_step,
                  debounce_s=600, now_s=1000.0)           # 第一轮：记录
        audit.calls.clear(); self.actions.clear()
        out = self._run(audit, state_path=str(self.state), debounce_step=watchdog_debounce_step,
                        debounce_s=600, now_s=1000.0 + 601)  # 第二轮：已持续够
        self.assertIsNotNone(out)
        self.assertIn(False, audit.calls, "持续够久后必须跑真轮（该写单了）")
        self.assertTrue(any("跨所保护] GATE" in a for a in self.actions), self.actions)

    def test_healed_gap_clears_the_state(self):
        audit = _Audit(would=[GAP])
        self._run(audit, state_path=str(self.state), debounce_step=watchdog_debounce_step,
                  debounce_s=600, now_s=1000.0)
        healed = _Audit(would=[])
        self._run(healed, state_path=str(self.state), debounce_step=watchdog_debounce_step,
                  debounce_s=600, now_s=2000.0)
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["gaps"], {},
                         "缺口愈合 ⇒ 状态自清（不攒垃圾、也不会突然补写单）")

    # ── 预演档 ────────────────────────────────────────────────────────
    def test_dry_run_reports_would_and_writes_nothing(self):
        audit = _Audit(would=[GAP])
        out = self._run(audit, dry_run=True)
        self.assertIsNotNone(out)
        self.assertTrue(any("预演" in a for a in self.actions), self.actions)
        self.assertIn("只判定不写单", self.printed.getvalue())

    def test_dry_run_with_actions_shouts_a_contract_violation(self):
        audit = _Audit(would=[GAP], actions_on_dry=[GAP])
        self._run(audit, dry_run=True)
        self.assertIn("预演模式下审计层仍返回了 actions", self.printed.getvalue(),
                      "预演却写单是审计层违约，必须当场喊出来而不是悄悄展示")
        self.assertIn("🔴", self.printed.getvalue())

    # ── 报告口径 ─────────────────────────────────────────────────────
    def test_critical_asks_for_human_or_strategy_price_never_invents_one(self):
        audit = _Audit(would=[], critical=[{"venue": "gate", "inst": "BTC_USDT", "side": "long",
                                            "detail": "无腿"}])
        self._run(audit)
        joined = "\n".join(self.actions)
        self.assertIn("无止损腿", joined)
        self.assertIn("需人工或用既定策略价位重挂", joined,
                      "巡检层不替仓位定价（价位是策略决定）")

    def test_real_round_failure_only_warns(self):
        audit = _Audit(would=[GAP], raises_on_real=RuntimeError("评审炸了"))
        out = self._run(audit)
        self.assertIsNone(out)
        self.assertIn("巡检异常", self.printed.getvalue())
        self.assertEqual(self.actions, [], "异常 ⇒ 不产生任何 action（fail-soft）")

    def test_errors_are_only_warned(self):
        audit = _Audit(would=[], actions_on_real=[],
                       errors=[{"venue": "binance", "inst": "SOL_USDT",
                                "stage": "list", "detail": "读腿失败"}])
        self._run(audit)
        self.assertIn("读腿失败", self.printed.getvalue())
        self.assertEqual(self.actions, [], "读失败只 warn，不该变成巡检动作")


if __name__ == "__main__":
    unittest.main()

"""开仓入口（`execute_entry_scan`）的第一批"拒开"闸门（第二百二十五刀）。

这个模块全量探针只有 **1.6%**（126 行里 124 行从未执行过）—— 而它是**真正开仓**的那条路。
本刀先建 harness 并覆盖"什么都不做"的那一半（最保守、也最该先钉住的）：

- 非流动性标的（tradfi 休市/无深度）⇒ 跳过；
- **AI 大脑没有该标的的新鲜决策 ⇒ 禁止开仓**（"AI 全权"的反面：没有决策就没有仓位）；
- AI 判 WAIT ⇒ 归为 HOLD 并跳过（不把观望当信号）；
- 杠杆硬钳制：超出配置区间 / 超出池内单标的 tier 上限 ⇒ 夹紧并**留痕**；
- 按风险预算推导的数量 ≤ 0（低于交易所最小下单量）⇒ 跳过并说明原因；
- 首开多置信度未达门禁 ⇒ **宁缺毋滥拦截**，绝不提交。

后续批次（提交成功路径、金字塔加仓、多空对称、下单前护栏）留待下一刀 —— 本刀只覆盖
"拒开"这一半，避免把"已开"当成已测。
"""

import io
import unittest
from contextlib import redirect_stdout

from scripts.trader.entry_execution import execute_entry_scan

INST = "BTC-USDT-SWAP"


class Harness:
    """开仓入口的注入依赖工厂：默认全部"保守无害"，用例只改自己关心的一两个。"""

    def __init__(self):
        self.actions = []
        self.submitted = []
        self.printed = io.StringIO()
        self.factor = {
            "name": "BTC", "instId": INST, "type": "crypto", "precision": 2, "ctVal": 1.0,
            "atr": 500.0, "price": 100000.0, "sz": 3.0, "minSz": 1.0, "position": None,
            "risk_per_trade_usd": 50.0, "size_below_exchange_min": False, "max_leverage": 0,
        }
        self.brain = {}
        self.tradfi_ok = True
        self.clamp_returns = (5.0, False)
        self.sized = 3.0
        self.entry_conf_gate = 80.0

    def run(self):
        kwargs = dict(
            all_factors=[self.factor], brain_cache=self.brain, cb_active=False,
            entries_blocked=False, executed_actions=self.actions, pending_inst_ids=set(),
            trackers={}, usdt_available=1000.0, ASSET_MARGIN_CAP=500.0,
            reserved_long_count=0, reserved_short_count=0, reserved_slot_count=0,
            ASSET_CLASS_PROFILES={"crypto": {"min_profit_ratio": 0.008, "tp_atr_mult": 2.2,
                                             "sl_atr_mult": 1.3}},
            MAX_CONCURRENT_POSITIONS=5, MAX_LEVERAGE=10, MAX_SAME_DIRECTION_POSITIONS=3,
            MAX_SCALE_IN_COUNT=2, MIN_ENTRY_CONFIDENCE=self.entry_conf_gate,
            MIN_LEVERAGE=1, MIN_SCALE_IN_CONFIDENCE=70, MIN_SCALE_IN_PROFIT_RATIO=0.002,
            build_order_intent=lambda **k: {"intent": k},
            clamp_ai_leverage=lambda lever, **k: self.clamp_returns,
            entry_action_message=lambda **k: "entry",
            entry_failure_message=lambda **k: "fail",
            equity_margin_cap=lambda **k: 500.0,
            evaluate_asset_signal=lambda f: (80.0, "HOLD", [], "tag", "desc"),
            instrument_profile=lambda f, t: {},
            is_tradfi_market_liquid=lambda t: self.tradfi_ok,
            load_adaptive_config=lambda: {},
            max_size_within_margin=lambda **k: 10.0,
            normalize_bracket_prices=lambda **k: (k.get("tp_px"), k.get("sl_px")),
            notify_trade_open=lambda **k: None,
            order_margin_gate=lambda **k: (True, "ok"),
            pyramiding_gate=lambda **k: (False, "no"),
            quantize_size=lambda sz, step: sz,
            resolve_entry_prices=lambda **k: (100000.0, 105000.0, 95000.0),
            save_trackers=lambda tr: None,
            size_for_decision=lambda **k: self.sized,
            submit_protected_limit_order=lambda *a, **k: (self.submitted.append(a), (True, "ok"))[1],
            trade_open_kwargs=lambda **k: k,
        )
        with redirect_stdout(self.printed):
            return execute_entry_scan(**kwargs)


class RefusalGateTest(unittest.TestCase):
    def test_illiquid_tradfi_is_skipped(self):
        h = Harness()
        h.factor["type"] = "tradfi"
        h.tradfi_ok = False
        h.run()
        self.assertEqual(h.submitted, [], "非流动性标的绝不许开仓")
        self.assertEqual(h.actions, [])

    def test_no_fresh_ai_decision_blocks_entry(self):
        h = Harness()          # brain_cache 里没有该标的的决策
        h.run()
        self.assertEqual(h.submitted, [], "没有新鲜 AI 决策 ⇒ 禁止开仓（AI 全权的前提是有决策）")
        self.assertIn("无有效新鲜 AI 决策", h.printed.getvalue())

    def test_ai_wait_becomes_hold_and_does_not_enter(self):
        h = Harness()
        h.brain = {INST: {"decision": {"action": "WAIT", "confidence": 99}}}
        h.run()
        self.assertEqual(h.submitted, [], "AI 观望不是开仓信号")
        # `strat_tag`/`strat_desc` 是**局部变量**，外部只能看 f 上的 AI 标注与副作用
        self.assertEqual(h.factor["ai_confidence"], 99, "决策已被解析（标注写回了因子）")
        self.assertEqual(h.actions, [], "观望不该产生任何动作")

    def test_leverage_is_clamped_and_disclosed(self):
        h = Harness()
        h.brain = {INST: {"decision": {"action": "BUY_LONG", "confidence": 90, "leverage": 20,
                                       "margin_usdt": 100.0}}}
        h.clamp_returns = (5.0, False)
        h.sized = 0.0                       # 让它在仓位为 0 处停下，只看杠杆那一行
        h.run()
        self.assertIn("超出配置区间", h.printed.getvalue(),
                      "夹了杠杆就必须留痕（否则用户以为 AI 的 20x 生效了）")

    def test_pool_leverage_cap_tightening_is_disclosed(self):
        h = Harness()
        h.brain = {INST: {"decision": {"action": "BUY_LONG", "confidence": 90, "leverage": 8,
                                       "margin_usdt": 100.0}}}
        h.factor["max_leverage"] = 3
        h.clamp_returns = (3.0, True)
        h.sized = 0.0
        h.run()
        self.assertIn("已按池值收紧", h.printed.getvalue())

    def test_size_below_exchange_min_is_explained(self):
        h = Harness()
        h.brain = {INST: {"decision": {"action": "BUY_LONG", "confidence": 90, "leverage": 3,
                                       "margin_usdt": 100.0}}}
        h.sized = 0.0
        h.factor["size_below_exchange_min"] = True
        h.run()
        self.assertEqual(h.submitted, [])
        self.assertIn("低于交易所最小下单量", h.printed.getvalue(),
                      "跳过要说明是「风险预算推不出合法数量」，不是静默")

    def test_initial_entry_below_confidence_is_blocked(self):
        h = Harness()
        h.brain = {INST: {"decision": {"action": "BUY_LONG", "confidence": 50, "leverage": 3,
                                       "margin_usdt": 100.0}}}
        h.run()
        self.assertEqual(h.submitted, [], "置信度未达门禁 ⇒ 宁缺毋滥，不许开仓")
        self.assertIn("未达 80% 门禁", h.printed.getvalue())

    def test_existing_position_blocks_initial_entry_when_slots_full(self):
        h = Harness()
        h.brain = {INST: {"decision": {"action": "BUY_LONG", "confidence": 99, "leverage": 3,
                                       "margin_usdt": 100.0}}}
        h.factor["position"] = {"side": "short", "pos": 1.0}    # 反方向持仓 ⇒ 不是首开也不是加仓
        h.run()
        self.assertEqual(h.submitted, [], "已有反向持仓时不许再开多（没有跨方向加仓这回事）")


if __name__ == "__main__":
    unittest.main()

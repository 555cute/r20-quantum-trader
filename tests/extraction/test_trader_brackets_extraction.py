"""B3（交易员侧第十三块）`scripts/trader/brackets.py` 的抽取回归。

## 这个测试在守什么

限价开仓单要同时给 `limit_px` / `tp_px` / `sl_px`，交易所对三者**大小顺序有硬性要求**：

| 方向 | 必须满足 |
|---|---|
| 做多 | `sl_px < limit_px < tp_px` |
| 做空 | `tp_px < limit_px < sl_px` |

顺序错了的后果不是"亏钱"，是**挂单被拒**（策略静默失效）或**挂出反向保护单**
（止损在入场价错误一侧）。AI 可以自己给这三个价，给出越界或相等的组合是常事。

原实现在长/空两个分支里各写一段修正，同构、方向相反、各自演化。抽成
`normalize_bracket_prices` 后只有一份权威实现。本文件守两件事：

1. **与搬走前逐字一致**：用旧实现的副本做随机 + 边界差分（尤其 `>=` / `<=`
   的**等值**语义 —— 等值也算越界，顺手改成 `>` 就会漏掉一整类输入）；
2. **方向不变量**：修正后必须真的满足该方向的顺序要求（或至少不比修正前更差）。

注意后者的**边界**：兜底距离本身可能不足以掰开（例如 AI 给的 tp 与 limit 只差
极小值、而兜底距离比该差值更小）。原实现同样如此 —— 那是既有行为，本测试**如实**
记录该边界，而不是替它"修好"（那属于行为变更）。
"""
from __future__ import annotations

import random
import unittest
from pathlib import Path

from scripts.trader import brackets

ROOT = Path(__file__).resolve().parents[2]
FACADE = ROOT / "scripts" / "ai_factor_trader.py"
SUBMODULE = ROOT / "scripts" / "trader" / "brackets.py"
ENTRY = ROOT / "scripts" / "trader" / "entry_execution.py"   # 第九十刀：开多/开空两支现住此


def _legacy(is_long, limit_px, tp_px, sl_px, sl_dist, tp_dist, price, prec):
    """搬走前门面里的两段内联实现（逐字原样，加 is_long 参数统一入口）。"""
    if is_long:
        if sl_px >= limit_px:
            sl_px = round(limit_px - max(sl_dist, price * 0.012), prec)
        if tp_px <= limit_px:
            tp_px = round(limit_px + max(tp_dist, price * 0.024), prec)
    else:
        if sl_px <= limit_px:
            sl_px = round(limit_px + max(sl_dist, price * 0.012), prec)
        if tp_px >= limit_px:
            tp_px = round(limit_px - max(tp_dist, price * 0.024), prec)
    return sl_px, tp_px


class ImplementationMovedTest(unittest.TestCase):
    def test_impl_lives_in_submodule_not_facade(self):
        facade = FACADE.read_text(encoding="utf-8")
        sub = SUBMODULE.read_text(encoding="utf-8")
        self.assertIn("def normalize_bracket_prices(", sub)
        # 门面里不应再有内联的兜底距离表达式
        for marker in ("max(sl_dist, f[\"price\"] * 0.012)", "max(tp_dist, f[\"price\"] * 0.024)"):
            self.assertNotIn(marker, facade, f"门面仍留有内联钳制: {marker!r}")

    def test_both_branches_call_the_helper(self):
        entry = ENTRY.read_text(encoding="utf-8")
        self.assertEqual(entry.count("sl_px, tp_px = normalize_bracket_prices("), 2,
                         "开多/开空都必须走同一个钳制实现")
        self.assertIn("is_long=True,", entry)
        self.assertIn("is_long=False,", entry)


class ParityTest(unittest.TestCase):
    def test_random_parity(self):
        rng = random.Random(20260921)
        for _ in range(6000):
            is_long = rng.random() < 0.5
            price = rng.choice([0.0975, 3.418e-06, 100.0, 70000.0, 1.0, 0.001])
            limit = price * rng.uniform(0.8, 1.2)
            tp = price * rng.uniform(0.7, 1.3)
            sl = price * rng.uniform(0.7, 1.3)
            sl_dist = price * rng.uniform(0.0, 0.05)
            tp_dist = price * rng.uniform(0.0, 0.08)
            prec = rng.choice([0, 1, 2, 4, 6, 8])
            got = brackets.normalize_bracket_prices(
                is_long=is_long, limit_px=limit, tp_px=tp, sl_px=sl,
                sl_dist=sl_dist, tp_dist=tp_dist, price=price, prec=prec)
            exp = _legacy(is_long, limit, tp, sl, sl_dist, tp_dist, price, prec)
            self.assertEqual(got, exp,
                             f"分叉 is_long={is_long} limit={limit} tp={tp} sl={sl} "
                             f"sl_dist={sl_dist} tp_dist={tp_dist} prec={prec}")

    def test_equality_is_treated_as_out_of_order(self):
        """`>=` / `<=`：**相等**同样要被掰开。

        这条专门防"顺手把 >= 改成 >"。四组等值输入：
        """
        for is_long in (True, False):
            # sl 与 limit 相等
            sl, tp = brackets.normalize_bracket_prices(
                is_long=is_long, limit_px=100.0, tp_px=110.0, sl_px=100.0,
                sl_dist=2.0, tp_dist=3.0, price=100.0, prec=2)
            self.assertEqual((sl, tp), _legacy(is_long, 100.0, 110.0, 100.0, 2.0, 3.0, 100.0, 2))
            self.assertNotEqual(sl, 100.0, "等值的止损必须被掰开")
            # tp 与 limit 相等
            sl, tp = brackets.normalize_bracket_prices(
                is_long=is_long, limit_px=100.0, tp_px=100.0, sl_px=90.0,
                sl_dist=2.0, tp_dist=3.0, price=100.0, prec=2)
            self.assertEqual((sl, tp), _legacy(is_long, 100.0, 100.0, 90.0, 2.0, 3.0, 100.0, 2))
            self.assertNotEqual(tp, 100.0, "等值的止盈必须被掰开")

    def test_in_order_inputs_are_untouched(self):
        """已合规的三价不得被改动（钳制只做修正，不做归一化）。"""
        sl, tp = brackets.normalize_bracket_prices(
            is_long=True, limit_px=100.0, tp_px=110.0, sl_px=95.0,
            sl_dist=2.0, tp_dist=3.0, price=100.0, prec=2)
        self.assertEqual((sl, tp), (95.0, 110.0))
        sl, tp = brackets.normalize_bracket_prices(
            is_long=False, limit_px=100.0, tp_px=90.0, sl_px=105.0,
            sl_dist=2.0, tp_dist=3.0, price=100.0, prec=2)
        self.assertEqual((sl, tp), (105.0, 90.0))

    def test_out_of_order_is_corrected_in_the_right_direction(self):
        """做多：sl 太低/太高都被拉回下方，tp 拉回上方；做空反之。"""
        # 做多，止损被 AI 放在入场价上方 → 必须拉到 limit 下方
        sl, _ = brackets.normalize_bracket_prices(
            is_long=True, limit_px=100.0, tp_px=110.0, sl_px=105.0,
            sl_dist=2.0, tp_dist=3.0, price=100.0, prec=2)
        self.assertLess(sl, 100.0, "做多止损必须在入场价下方")
        # 做空，止损被 AI 放在入场价下方 → 必须拉到 limit 上方
        sl, _ = brackets.normalize_bracket_prices(
            is_long=False, limit_px=100.0, tp_px=90.0, sl_px=95.0,
            sl_dist=2.0, tp_dist=3.0, price=100.0, prec=2)
        self.assertGreater(sl, 100.0, "做空止损必须在入场价上方")
        # 做空，止盈被 AI 放在入场价上方 → 必须拉到 limit 下方
        _, tp = brackets.normalize_bracket_prices(
            is_long=False, limit_px=100.0, tp_px=105.0, sl_px=110.0,
            sl_dist=2.0, tp_dist=3.0, price=100.0, prec=2)
        self.assertLess(tp, 100.0, "做空止盈必须在入场价下方")

    def test_literal_fallback_ratios_are_preserved(self):
        """兜底比例是原样搬来的字面量：止损 1.2%、止盈 2.4%。不得改值。"""
        src = SUBMODULE.read_text(encoding="utf-8")
        self.assertIn("price * 0.012", src)
        self.assertIn("price * 0.024", src)

    def test_known_limitation_is_documented_not_fixed(self):
        """如实记录既有边界：兜底距离**比越界量还小**时，掰开后的顺序仍可能不合规。

        例：做多，limit=100、sl=100.0001（只越界一点点），而 sl_dist=0 且
        price×0.012=1.2 → 掰成 98.8，顺序合规。但若 limit=100、sl=99.99 却
        tp=99.995（tp 与 limit 只差 0.005，而 tp_dist=0、price×0.024=2.4）→ tp 掰成
        102.4，也合规。真正会不合规的是**修正项比差值更小**的情形，例如
        price=0.000001（→0.012×price≈1.2e-8）配 prec=0 时被 round 回原值。
        这里只断言"除非 round 把修正抹掉，否则方向正确"，即把边界钉成已知行为。
        """
        # price 极小 + prec=0 → round 会把修正抹平（既有行为）
        sl, tp = brackets.normalize_bracket_prices(
            is_long=True, limit_px=100.0, tp_px=100.0, sl_px=100.0,
            sl_dist=0.0, tp_dist=0.0, price=1e-9, prec=0)
        exp = _legacy(True, 100.0, 100.0, 100.0, 0.0, 0.0, 1e-9, 0)
        self.assertEqual((sl, tp), exp, "边界行为必须与搬走前一致（不替它修）")


class ReanchorToMarketTest(unittest.TestCase):
    """市价单保护价重锚（`reanchor_brackets_to_market`）。

    背景：后台可切市价单，但三价是按**限价挂单计划**算的；市价单在现价成交，
    保护价若仍留在计划价上就与真实成本脱节 —— 做多计划若是回踩挂单，
    止盈价会落到真实成交价**下方**（"止盈"变亏损价，成交即触发）。
    """

    def test_long_pullback_plan_gets_tp_above_the_real_fill(self):
        """回踩挂单（计划 100、现价 110）→ 止盈必须落到现价**上方**。"""
        got = brackets.reanchor_brackets_to_market(
            entry=100.0, tp=105.0, sl=95.0, market=110.0, is_long=True, prec=4)
        self.assertIsNotNone(got)
        entry, tp, sl = got
        self.assertAlmostEqual(entry, 110.0, places=4, msg="进场必须贴到现价")
        self.assertGreater(tp, entry, "做多止盈必须在真实成交价上方（否则即亏损价）")
        self.assertLess(sl, entry, "做多止损必须在真实成交价下方")

    def test_short_plan_gets_tp_below_the_real_fill(self):
        got = brackets.reanchor_brackets_to_market(
            entry=100.0, tp=95.0, sl=105.0, market=90.0, is_long=False, prec=4)
        self.assertIsNotNone(got)
        entry, tp, sl = got
        self.assertAlmostEqual(entry, 90.0, places=4)
        self.assertLess(tp, entry, "做空止盈必须在真实成交价下方")
        self.assertGreater(sl, entry, "做空止损必须在真实成交价上方")

    def test_risk_reward_ratio_is_preserved(self):
        """等比缩放：盈亏比与相对成本的距离**逐位不变**（市价单不偷改风险结构）。"""
        for is_long, entry, tp, sl, market in [
            (True, 100.0, 105.0, 95.0, 110.0),
            (True, 100.0, 120.0, 90.0, 137.5),
            (False, 100.0, 90.0, 110.0, 88.0),
        ]:
            with self.subTest(is_long=is_long, market=market):
                new_entry, new_tp, new_sl = brackets.reanchor_brackets_to_market(
                    entry=entry, tp=tp, sl=sl, market=market, is_long=is_long, prec=6)
                orig_rr = abs(tp - entry) / abs(entry - sl)
                new_rr = abs(new_tp - new_entry) / abs(new_entry - new_sl)
                self.assertAlmostEqual(new_rr, orig_rr, places=6, msg="盈亏比被改动")
                orig_tp_pct = abs(tp - entry) / entry
                new_tp_pct = abs(new_tp - new_entry) / new_entry
                self.assertAlmostEqual(new_tp_pct, orig_tp_pct, places=6,
                                       msg="相对成本的止盈距离被改动")

    def test_market_equal_to_plan_is_a_noop(self):
        """现价 == 计划价 ⇒ 原样返回（该点上限价/市价无差别）。"""
        got = brackets.reanchor_brackets_to_market(
            entry=100.0, tp=105.0, sl=95.0, market=100.0, is_long=True, prec=4)
        self.assertEqual(got, (100.0, 105.0, 95.0))

    def test_missing_market_price_fails_closed(self):
        """现价读不到 ⇒ None（调用方拒单）。**绝不**退回计划价继续下单。"""
        for bad_market in (0, 0.0, -1, None, float("nan")):
            with self.subTest(market=bad_market):
                self.assertIsNone(
                    brackets.reanchor_brackets_to_market(
                        entry=100.0, tp=105.0, sl=95.0, market=bad_market,
                        is_long=True, prec=4),
                    f"market={bad_market!r} 必须 fail-closed")

    def test_invalid_plan_price_fails_closed(self):
        for bad_entry in (0, -5, None):
            with self.subTest(entry=bad_entry):
                self.assertIsNone(
                    brackets.reanchor_brackets_to_market(
                        entry=bad_entry, tp=105.0, sl=95.0, market=100.0,
                        is_long=True, prec=4))

    def test_non_numeric_input_fails_closed(self):
        self.assertIsNone(
            brackets.reanchor_brackets_to_market(
                entry="abc", tp=105.0, sl=95.0, market=100.0, is_long=True, prec=4))


if __name__ == "__main__":
    unittest.main()

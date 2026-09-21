"""止盈宽度钳制的**边界分支**（第二百一十刀，覆盖率探针发现从未执行）。

`clamp_take_profit_width` 是"止盈不许挂在天际线"的落点，也是**唯一**会在发单前
改写止盈价的函数。它有几条"参数非法/不该动"的保守分支此前没有被任何用例走到：

- 传入非数字 ⇒ **原样返回**（不猜、不炸）；
- `limit_px`/`sl_px`/`tp_px` 非正 ⇒ 原样返回；
- `risk = |limit - sl|` 为 0 ⇒ 原样返回（没有风险距离就谈不上盈亏比）；
- `curr_reward <= 0`（止盈在入场价的错误一侧）⇒ 原样返回（方向错误交给别的闸门管）；
- 风控常量传入非数字 ⇒ 退化到默认值而不是抛错；
- 止盈确实过远 ⇒ 收窄到 `allowed_max`（真·钳制那一步）。

另：本刀先跑了**更宽的探针**纠正了一个自造的错觉 —— 只用 `tests/trading tests/venues tests/ui`
时 `scripts/trader/protection.py` 只有 21%、`risk_gates.py` 63%，看着像大片缺口；把
`tests/extraction` 纳入后两者都是 **100%**。⇒ **范围决定数字**，报缺口前必须把相关测试目录算全。
"""

import unittest

from scripts.trader.brackets import clamp_take_profit_width


class ClampTakeProfitWidthBranchTest(unittest.TestCase):
    def test_non_numeric_inputs_are_returned_unchanged(self):
        for field in ("limit_px", "sl_px", "tp_px"):
            with self.subTest(field=field):
                kw = {"is_long": True, "limit_px": 70000.0, "sl_px": 68000.0, "tp_px": 74000.0}
                kw[field] = "不是数字"
                self.assertEqual(clamp_take_profit_width(**kw), kw["tp_px"],
                                 f"{field} 非数字 ⇒ 原样返回，不许猜也不许炸")

    def test_non_positive_prices_are_returned_unchanged(self):
        for value in (0.0, -1.0):
            with self.subTest(value=value):
                self.assertEqual(
                    clamp_take_profit_width(is_long=True, limit_px=value, sl_px=68000.0,
                                            tp_px=74000.0), 74000.0,
                    "非正价格 ⇒ 原样返回（参数不可信时不动手）")

    def test_zero_risk_distance_returns_unchanged(self):
        self.assertEqual(
            clamp_take_profit_width(is_long=True, limit_px=70000.0, sl_px=70000.0,
                                    tp_px=74000.0, atr=100.0, max_tp_atr=1.0, max_rr=3.0),
            74000.0, "止损距离为 0 ⇒ 谈不上盈亏比 ⇒ 原样返回")

    def test_take_profit_on_the_wrong_side_is_returned_unchanged(self):
        # 做多但止盈低于入场（方向不对）⇒ 本函数不管方向，交给别的闸门
        self.assertEqual(
            clamp_take_profit_width(is_long=True, limit_px=70000.0, sl_px=68000.0,
                                    tp_px=69000.0), 69000.0)
        # 做空但止盈高于入场，同理
        self.assertEqual(
            clamp_take_profit_width(is_long=False, limit_px=70000.0, sl_px=72000.0,
                                    tp_px=71000.0), 71000.0)

    def test_non_numeric_risk_constants_degrade_to_defaults(self):
        """风控常量传入垃圾 ⇒ 退化到默认值，不许把发单流程炸掉。

        ⚠️ 逐个字段单独喂垃圾：全喂垃圾时 `candidates` 为空 ⇒ 函数在前面就 `return tp_px`
        了，根本走不到"底线"那一段（我第一版就是这样，于是 `min_rr` 的 except 分支仍未被覆盖）。
        """
        base = dict(is_long=True, limit_px=70000.0, sl_px=68000.0, tp_px=430000.0,
                    atr=100.0, max_tp_atr=1.0, max_rr=3.0, min_rr=0.5)
        for field in ("atr", "max_tp_atr", "max_rr", "min_rr"):
            with self.subTest(field=field):
                kw = dict(base, **{field: "垃圾"})
                out = clamp_take_profit_width(**kw)
                self.assertIsInstance(out, float, f"{field} 是垃圾时仍须返回数值")
                self.assertGreaterEqual(out, 70000.0, "做多的止盈不该被推到入场价之下")

    def test_no_usable_cap_returns_unchanged(self):
        """两个上限都算不出来（ATR 上限与 R:R 上限都不可用）⇒ 原样返回。

        注意这是**与上一条相反**的形态：上一条"逐字段喂垃圾"保证还有另一个可用上限，
        所以能走到钳制；这条把两个上限都废掉 ⇒ 函数在下限处就返回（`if not candidates`）。
        """
        out = clamp_take_profit_width(is_long=True, limit_px=70000.0, sl_px=68000.0,
                                      tp_px=430000.0, atr="垃圾", max_tp_atr="垃圾",
                                      max_rr="垃圾", min_rr=0.5)
        self.assertEqual(out, 430000.0, "没有任何可用上限时不许乱收窄（宁可不夹，不可乱夹）")

    def test_far_take_profit_is_clamped_to_the_tightest_cap(self):
        """真·钳制：ATR 上限 100、R:R 上限 150 ⇒ 取较严者 100 ⇒ 止盈收到 70100。

        ⚠️ `min_rr` 必须给得够小：否则 `risk*min_rr`（底线）会**盖过**上限 —— 我第一版
        按 min_rr=2 写期望值 70100，实测却是 74000（= 70000 + 2000*2），即底线生效。
        这条语义本身是对的（"绝不破坏最小盈亏比"），是本用例的期望算错了。
        """
        out = clamp_take_profit_width(is_long=True, limit_px=70000.0, sl_px=68000.0,
                                      tp_px=90000.0, atr=100.0, max_tp_atr=1.0,
                                      max_rr=3.0, min_rr=0.01, prec=2)
        self.assertEqual(out, 70100.0, f"过远的止盈必须被收窄到 allowed_max：{out}")

    def test_floor_keeps_the_minimum_risk_reward(self):
        """底线不可破：allowed_max 低于 risk*min_rr 时抬回底线（不许把盈亏比压穿）。"""
        # ATR 上限 10、R:R 上限 20 ⇒ 较严者 10，但 min_rr=2 ⇒ 底线 = 2000*2 = 4000
        out = clamp_take_profit_width(is_long=True, limit_px=70000.0, sl_px=68000.0,
                                      tp_px=90000.0, atr=10.0, max_tp_atr=1.0,
                                      max_rr=0.01, min_rr=2.0, prec=2)
        self.assertEqual(out, 74000.0, f"钳制不得把盈亏比压到底线之下：{out}")

    def test_short_side_clamps_downward(self):
        out = clamp_take_profit_width(is_long=False, limit_px=70000.0, sl_px=72000.0,
                                      tp_px=50000.0, atr=100.0, max_tp_atr=1.0,
                                      max_rr=3.0, min_rr=0.01, prec=2)
        self.assertEqual(out, 69900.0, f"做空的止盈要向下收窄：{out}")


if __name__ == "__main__":
    unittest.main()

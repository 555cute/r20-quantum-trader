"""杠杆夹取与下单张数推导的**顺序契约**（第二百三十刀）。

这两块是「AI 说 20x、实际只准开 5x」与「AI 想要 100U 保证金、实际按风险预算下」的**唯一落点**。
它们的注释都强调**顺序**：顺序换了会放大仓位，而这两处又都不报错、不会红 —— 所以必须钉住。

`clamp_ai_leverage` 三件事按固定顺序：① 夹配置区间（`MIN_LEVERAGE`/`MAX_LEVERAGE`，
审计 P2-8：旧实现只夹上限）② 再用池内单标的上限收紧（审计 P2-5：该字段此前无人读）
③ `tightened` 回传供调用点留痕。**刻意保留**的行为：池值收紧不做下限保护
（池值低于 `MIN_LEVERAGE` 时结果就低于下限）—— 池文件是本地可信配置，不擅自"修好"。

`size_for_decision`：四道钳制 —— ① 计划名义额推出的张数 ② 围绕自适应基准的 0.5x~2.0x 窗口
③ 可用余额硬顶（付不起就归零**跳过**而不是放大）④ 最小步长量化。
"""

import math
import unittest

from scripts.trader.leverage import clamp_ai_leverage
from scripts.trader.sizing import size_for_decision


def _quantize(sz, step):
    """交易所式量化：按步长向下取整（与 r20_backend.execution.sizing 同义）。"""
    if not step or step <= 0:
        return sz
    return math.floor(sz / step + 1e-9) * step


class ClampLeverageTest(unittest.TestCase):
    def test_missing_config_falls_back_to_one_and_twenty(self):
        """配置缺省/为 0 ⇒ 下限兜底 1x、上限兜底 20x（不是 0x，也不是无上限）。"""
        lev, tightened = clamp_ai_leverage(0.0, min_leverage=None, max_leverage=None,
                                           inst_lever_cap=0)
        self.assertEqual(lev, 1.0)
        self.assertFalse(tightened)
        lev2, _ = clamp_ai_leverage(50.0, min_leverage=0, max_leverage=0, inst_lever_cap=0)
        self.assertEqual(lev2, 20.0, "上限为 0/None ⇒ 兜底 20x，绝不能当作无上限")

    def test_ai_leverage_is_clamped_into_the_configured_band(self):
        lev, _ = clamp_ai_leverage(1.0, min_leverage=3, max_leverage=10, inst_lever_cap=0)
        self.assertEqual(lev, 3.0, "低于配置下限要抬到下限（审计 P2-8：旧实现只夹上限）")
        lev2, _ = clamp_ai_leverage(20.0, min_leverage=3, max_leverage=10, inst_lever_cap=0)
        self.assertEqual(lev2, 10.0, "高于配置上限要夹住")

    def test_pool_cap_tightens_and_reports(self):
        lev, tightened = clamp_ai_leverage(8.0, min_leverage=1, max_leverage=20,
                                           inst_lever_cap=3)
        self.assertEqual(lev, 3.0, "池内单标的 tier 上限要生效（审计 P2-5：此前无人读）")
        self.assertTrue(tightened, "被池值收紧必须回传 True 供调用点留痕")

    def test_absent_pool_cap_does_not_tighten(self):
        for cap in (0, -1, 0.0):
            with self.subTest(cap=cap):
                lev, tightened = clamp_ai_leverage(5.0, min_leverage=1, max_leverage=20,
                                                   inst_lever_cap=cap)
                self.assertEqual((lev, tightened), (5.0, False),
                                 "cap<=0 表示无池内上限（原实现语义）")

    def test_pool_cap_at_or_above_is_not_a_tightening(self):
        lev, tightened = clamp_ai_leverage(5.0, min_leverage=1, max_leverage=20,
                                           inst_lever_cap=5)
        self.assertEqual((lev, tightened), (5.0, False), "相等不算收紧")

    def test_pool_cap_below_the_floor_is_kept_as_is(self):
        """**顺序契约**：先夹配置区间、再用池值收紧 ⇒ 池值低于下限时结果就低于下限。

        这是原实现的行为（池文件是本地可信配置），此处**原样保留**。若哪天有人"顺手修好"
        （先收紧再夹），这条会红 —— 那正是需要评审讨论的时刻。
        """
        lev, tightened = clamp_ai_leverage(5.0, min_leverage=3, max_leverage=10,
                                           inst_lever_cap=1)
        self.assertEqual(lev, 1.0, "池值 1x 直接落地（不抬回 MIN=3x）—— 顺序是刻意的")
        self.assertTrue(tightened)


class SizeForDecisionTest(unittest.TestCase):
    BASE = dict(price=100.0, ct_val=1.0, step_sz=1.0, base_sz=10.0, usdt_available=1000.0)

    def _run(self, *, afford=float("inf"), actual_sz=7.0, **over):
        kw = dict(self.BASE)
        kw.update(over)
        return size_for_decision(
            ai_margin=kw.pop("ai_margin"), ai_lever=kw.pop("ai_lever"),
            price=kw.pop("price"), ct_val=kw.pop("ct_val"), step_sz=kw.pop("step_sz"),
            base_sz=kw.pop("base_sz"), usdt_available=kw.pop("usdt_available"),
            actual_sz=actual_sz,
            quantize_size=_quantize,
            max_size_within_margin=lambda *a, **k: afford)

    def test_guard_returns_the_current_size_untouched(self):
        """四个前提任一不成立 ⇒ 原样返回（不放大、不归零）。"""
        cases = {"ai_margin": 0.0, "ai_lever": 0.5, "price": 0.0, "ct_val": 0.0}
        for field, value in cases.items():
            with self.subTest(field=field):
                kw = {"ai_margin": 20.0, "ai_lever": 5.0}
                kw[field] = value          # 逐项打坏一个前提（不能既当默认值又当覆盖值）
                got = self._run(**kw)
                self.assertEqual(got, 7.0, f"{field} 不合法时不该改动额度")

    def test_no_positive_size_leaves_the_current_value(self):
        """计划名义额推不出正张数 ⇒ 保持当前值（下游 `actual_sz <= 0` 另有判定）。"""
        got = self._run(ai_margin=10.0, ai_lever=5.0)     # 50U/100 ⇒ 0.5 ⇒ 量化为 0
        self.assertEqual(got, 7.0)

    def test_size_is_raised_to_the_half_base_floor(self):
        got = self._run(ai_margin=40.0, ai_lever=5.0)     # 200U ⇒ 2 张 < 0.5*10
        self.assertEqual(got, 5.0, "低于自适应基准 0.5x ⇒ 抬到下限（不是照 AI 的算）")

    def test_size_is_capped_at_twice_the_base(self):
        got = self._run(ai_margin=1000.0, ai_lever=5.0)   # 5000U ⇒ 50 张 > 2*10
        self.assertEqual(got, 20.0, "高于 2x 基准 ⇒ 夹住（防「AI 一把梭」）")

    def test_within_the_window_is_used_as_calculated(self):
        got = self._run(ai_margin=100.0, ai_lever=5.0)    # 500U ⇒ 5 张，落在窗口内
        self.assertEqual(got, 5.0)

    def test_affordability_cap_shrinks_the_size(self):
        """付不起就**缩小**（而不是放大）：单笔保证金不得超过余额占比。"""
        got = self._run(ai_margin=100.0, ai_lever=5.0, afford=3.0)
        self.assertEqual(got, 3.0)

    def test_infinite_affordability_is_ignored(self):
        got = self._run(ai_margin=100.0, ai_lever=5.0, afford=float("inf"))
        self.assertEqual(got, 5.0, "无上限时不该被 infinity 影响")

    def test_none_affordability_is_ignored(self):
        got = self._run(ai_margin=100.0, ai_lever=5.0, afford=None)
        self.assertEqual(got, 5.0)

    def test_result_is_quantized_to_the_step(self):
        got = self._run(ai_margin=100.0, ai_lever=5.0, step_sz=3.0, base_sz=10.0)
        self.assertEqual(got % 3.0, 0.0, f"最终张数必须是步长整数倍：{got}")


if __name__ == "__main__":
    unittest.main()

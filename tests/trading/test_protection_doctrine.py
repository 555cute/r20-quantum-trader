"""止损保护三条铁律（第二百四十八刀）。

这一族决定「**要不要动真实止损单**」——判定放松就是账户裸奔，方向判反就是自伤。
原门面里这些都是内联长空双分支（重构前**零覆盖**），抽成纯函数后必须逐条钉住。

| 函数 | 铁律 |
|---|---|
| `protection_signals` | `hard_stop_px > 0` **必须先判**：缺失/为 0 = 无止损保护，**不得**把任意价格当成击穿（那等于无止损强平）|
| `ratcheted_trailing_stop` | 止损**只朝有利方向单调推进，绝不后退**；`stage_desc` 仅在"确实推进"时才有值 |
| `ai_tightens_stop` | 收紧判定保守：新止损须落在区间内、浮盈 ≥1.2 ATR、与新止损留 ≥0.7 ATR 呼吸垫；方向未知一律 False |
"""

import unittest

from scripts.trader.protection import (
    protection_signals, ai_tightens_stop, ratcheted_trailing_stop, close_fee,
    EARLY_TIGHTEN_MIN_PROFIT_ATR, EARLY_TIGHTEN_BUFFER_ATR, EARLY_TIGHTEN_ATR_FLOOR_RATIO,
)


class ProtectionSignalsTest(unittest.TestCase):
    def test_missing_stop_never_counts_as_breached(self):
        """`hard_stop_px` 缺失/为 0 ⇒ **无止损保护**，绝不判成击穿（否则等于无止损强平）。"""
        for px in (0, 0.0, -1):
            with self.subTest(hard_stop_px=px):
                self.assertFalse(protection_signals(is_long=True, cur_px=100, hard_stop_px=px))
                self.assertFalse(protection_signals(is_long=False, cur_px=100, hard_stop_px=px))

    def test_none_stop_raises_instead_of_being_judged(self):
        """⚠️ **实测边界（如实记录）**：`hard_stop_px=None` 会在 `> 0` 处抛 `TypeError`，
        而不是判成「无止损」返回 False。

        语义上「缺失」应当**保守为不击穿**（同 0 的处理），但本函数默认调用方给的是数值。
        **未擅自改**（属钱路判定件，改法需评估：`hard_stop_px or 0` 会让 None 归 0 ⇒ 不击穿）。
        已列为待议项。
        """
        with self.assertRaises(TypeError):
            protection_signals(is_long=True, cur_px=100, hard_stop_px=None)

    def test_long_breach_at_or_below_stop(self):
        self.assertTrue(protection_signals(is_long=True, cur_px=95, hard_stop_px=95))
        self.assertTrue(protection_signals(is_long=True, cur_px=94, hard_stop_px=95))
        self.assertFalse(protection_signals(is_long=True, cur_px=96, hard_stop_px=95))

    def test_short_breach_at_or_above_stop(self):
        self.assertTrue(protection_signals(is_long=False, cur_px=105, hard_stop_px=105))
        self.assertFalse(protection_signals(is_long=False, cur_px=104, hard_stop_px=105))


class RatchetedTrailingStopTest(unittest.TestCase):
    """止损只能**单调推进**；`max`（多）/`min`（空）是方向正确性的唯一保证。"""

    def test_tier2_locks_one_atr_profit_long(self):
        sl, desc = ratcheted_trailing_stop(
            is_long=True, entry_px=100.0, atr=2.0, prec=2, peak_profit_px=5.0, old_sl=95.0,
            tier1_breakeven_trigger=3.0, tier2_lock_trigger=4.0)
        self.assertEqual(sl, 102.0, "锁 1.0x ATR 大波段利润")
        self.assertIn("大波段", desc)

    def test_tier2_short_moves_stop_down(self):
        sl, _ = ratcheted_trailing_stop(
            is_long=False, entry_px=100.0, atr=2.0, prec=2, peak_profit_px=5.0, old_sl=105.0,
            tier1_breakeven_trigger=3.0, tier2_lock_trigger=4.0)
        self.assertEqual(sl, 98.0, "空头锁利是**往下**推（min）")

    def test_tier1_moves_to_breakeven_plus_cost_buffer(self):
        """保本要**加 0.20% 成本垫**（覆盖 taker 费），不是裸保本。"""
        sl, desc = ratcheted_trailing_stop(
            is_long=True, entry_px=100.0, atr=2.0, prec=2, peak_profit_px=3.5, old_sl=95.0,
            tier1_breakeven_trigger=3.0, tier2_lock_trigger=4.0)
        self.assertEqual(sl, 100.2)
        self.assertIn("保本", desc)

    def test_below_tier1_keeps_old_stop_and_no_stage(self):
        sl, desc = ratcheted_trailing_stop(
            is_long=True, entry_px=100.0, atr=2.0, prec=2, peak_profit_px=1.0, old_sl=95.0,
            tier1_breakeven_trigger=3.0, tier2_lock_trigger=4.0)
        self.assertEqual(sl, 95.0)
        self.assertIsNone(desc, "没推进 ⇒ 不得产生 stage_desc（门面据此决定是否写回）")

    def test_never_moves_backwards_long(self):
        """旧止损**已经更优**（更靠上）⇒ 保持不动 —— 绝不后退。"""
        sl, _ = ratcheted_trailing_stop(
            is_long=True, entry_px=100.0, atr=2.0, prec=2, peak_profit_px=9.0, old_sl=110.0,
            tier1_breakeven_trigger=3.0, tier2_lock_trigger=4.0)
        self.assertEqual(sl, 110.0)

    def test_never_moves_backwards_short(self):
        sl, _ = ratcheted_trailing_stop(
            is_long=False, entry_px=100.0, atr=2.0, prec=2, peak_profit_px=9.0, old_sl=90.0,
            tier1_breakeven_trigger=3.0, tier2_lock_trigger=4.0)
        self.assertEqual(sl, 90.0)


class AiTightensStopTest(unittest.TestCase):
    """收紧判定**保守**：放松就是裸奔，判反就是自伤。"""

    def _pos(self, **over):
        p = {"posSide": "long", "markPx": 110.0, "avgPx": 100.0, "atr": 2.0}
        p.update(over)
        return p

    def test_long_real_tightening_is_accepted(self):
        self.assertTrue(ai_tightens_stop({"suggested_sl_price": 105.0}, self._pos()))

    def test_long_stop_above_current_price_is_rejected(self):
        """方向判反（止损跑到现价之上）⇒ 拒绝（那是**放松**不是收紧）。"""
        self.assertFalse(ai_tightens_stop({"suggested_sl_price": 111.0}, self._pos()))

    def test_long_stop_below_entry_is_rejected(self):
        """止损掉到入场价之下 ⇒ 那是亏损保护位置，不属「收紧」路径。"""
        self.assertFalse(ai_tightens_stop({"suggested_sl_price": 99.0}, self._pos()))

    def test_missing_or_zero_stop_is_rejected(self):
        for val in (0, None):
            with self.subTest(val=val):
                self.assertFalse(ai_tightens_stop({"suggested_sl_price": val}, self._pos()))

    def test_non_numeric_stop_raises_instead_of_being_rejected(self):
        """⚠️ **实测边界（如实记录）**：`suggested_sl_price` 是非数字串时会抛
        `ValueError`（`float()` 转换失败），而不是保守返回 False。同上列为待议项，未擅自改。"""
        with self.assertRaises(ValueError):
            ai_tightens_stop({"suggested_sl_price": "abc"}, self._pos())

    def test_insufficient_profit_is_rejected(self):
        """浮盈不足 1.2x ATR ⇒ 不算「有意义的盈利」⇒ 不收紧。"""
        pos = self._pos(markPx=102.0)
        self.assertFalse(ai_tightens_stop({"suggested_sl_price": 101.0}, pos))
        self.assertGreaterEqual(EARLY_TIGHTEN_MIN_PROFIT_ATR, 1.2)

    def test_insufficient_breathing_buffer_is_rejected(self):
        """现价与新止损之间呼吸垫不足 0.7x ATR ⇒ 拒绝（防噪声打到止损）。"""
        pos = self._pos()
        near = 110.0 - (EARLY_TIGHTEN_BUFFER_ATR * 2.0) + 0.01
        self.assertFalse(ai_tightens_stop({"suggested_sl_price": near}, pos))

    def test_atr_missing_falls_back_to_price_ratio(self):
        """ATR 缺失 ⇒ 用地板比例（现价的 1.2%）兜底，而不是当成 0（那会让判定形同虚设）。"""
        pos = {"posSide": "long", "markPx": 110.0, "avgPx": 100.0}
        floor = 110.0 * EARLY_TIGHTEN_ATR_FLOOR_RATIO
        self.assertGreater(floor, 0)
        # 浮盈 10 远大于 1.2*floor；呼吸垫给足 ⇒ 接受
        self.assertTrue(ai_tightens_stop(
            {"suggested_sl_price": 110.0 - 0.7 * floor - 0.01}, pos))

    def test_short_real_tightening_is_accepted(self):
        pos = self._pos(posSide="short", markPx=90.0, avgPx=100.0)
        self.assertTrue(ai_tightens_stop({"suggested_sl_price": 95.0}, pos))

    def test_unknown_side_is_never_tightening(self):
        """`posSide` 既不是 long 也不是 short（如 `net`）⇒ **一律 False**（原样保留）。"""
        for side in ("net", "", "LONG_LONG"):
            with self.subTest(side=side):
                pos = self._pos(posSide=side)
                self.assertFalse(ai_tightens_stop({"suggested_sl_price": 105.0}, pos))


class CloseFeeTest(unittest.TestCase):
    def test_fee_is_contracts_times_value_times_price_times_rate(self):
        self.assertEqual(close_fee(10, 0.0001, 79000.0, 0.0005), 10 * 0.0001 * 79000.0 * 0.0005)

    def test_zero_rate_is_zero_fee_not_crash(self):
        self.assertEqual(close_fee(10, 0.0001, 79000.0, 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()

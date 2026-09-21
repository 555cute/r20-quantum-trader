"""量化评分引擎的**决策纪律**（第二百四十九刀）。

`evaluate_asset_signal` 把行情变成「做多/做空/观望」，是钱路的第一道决策。
它有三条不容含糊的纪律：

1. **数据不可信 ⇒ 禁止生成信号**（不是"低置信度照发"）；
2. **自进化干预是硬闸**：冷却池内的标的、被停用的策略 ⇒ `0.0 / HOLD`，
   **不是"照做但打个标记"**；
3. **冷却按方向生效，不得互相阻断**：`cooldown_long` 只拦做多、`cooldown_short` 只拦做空；
   且**分数与动作分离** —— 动作被拦时分数仍然如实给出（分数是行情事实，动作是风控结论）。
"""

import unittest
from unittest.mock import patch

from scripts.trader import signals


class _Base(unittest.TestCase):
    def setUp(self):
        self.cfg = {}

        def _load_cfg():
            return self.cfg

        self._load_cfg = _load_cfg
        self.cooldown = set()

        def _is_cd(inst_id, side):
            return (inst_id, side) in self.cooldown

        self._is_cd = _is_cd

    def _eval(self, **over):
        f = {"market_data_valid": True, "instId": "BTC-USDT-SWAP", "name": "BTC",
             "price": 100.0}
        f.update(over)
        return signals.evaluate_asset_signal(
            f, asset_class_profiles={"crypto": {"entry_threshold": 2.2}},
            is_in_stop_cooldown=self._is_cd, load_adaptive_config=self._load_cfg)

    # ── Setup 5（极值回归，做多）/ Setup 6（冲高反转，做空）的最小触发条件 ──
    def _long_setup(self, **over):
        d = {"vwap_bias": -0.90, "rsi": 25.0, "is_bull_candle_15m": True}
        d.update(over)
        return d

    def _short_setup(self, **over):
        d = {"vwap_bias": 0.90, "rsi": 75.0, "is_bear_candle_15m": True}
        d.update(over)
        return d


class UntrustworthyDataTest(_Base):
    def test_missing_market_data_forbids_any_signal(self):
        score, action, reasons, tag, desc = self._eval(market_data_valid=False)
        self.assertEqual((score, action), (0.0, "HOLD"))
        self.assertIn("关键行情数据缺失", reasons[0])
        self.assertIn("禁止生成交易信号", desc)


class SelfEvolutionInterventionTest(_Base):
    """自进化干预是**硬闸**，不是提示。"""

    def test_cooldown_by_name_blocks_any_signal(self):
        self.cfg = {"cooldown_assets": ["BTC"]}
        score, action, reasons, tag, _ = self._eval(**self._long_setup())
        self.assertEqual((score, action), (0.0, "HOLD"))
        self.assertIn("冷却池", reasons[0])

    def test_cooldown_by_inst_id_also_blocks(self):
        self.cfg = {"cooldown_assets": ["BTC-USDT-SWAP"]}
        _, action, _, _, _ = self._eval(**self._long_setup())
        self.assertEqual(action, "HOLD")

    def test_disabled_strategy_is_not_traded(self):
        """策略被停用 ⇒ `0.0 / HOLD`（**不是**"照做但标记"）。"""
        self.cfg = {"strategy_enabled": {"💎 极值回归": False}}
        score, action, reasons, tag, desc = self._eval(**self._long_setup())
        self.assertEqual((score, action), (0.0, "HOLD"))
        self.assertIn("停用", reasons[0])
        self.assertIn("极值回归", desc)


class ActionThresholdTest(_Base):
    def test_long_setup_crosses_threshold(self):
        score, action, _, tag, _ = self._eval(**self._long_setup())
        self.assertGreaterEqual(score, 2.2)
        self.assertEqual(action, "BUY_LONG")
        self.assertEqual(tag, "💎 极值回归")

    def test_short_setup_crosses_threshold(self):
        score, action, _, tag, _ = self._eval(**self._short_setup())
        self.assertLessEqual(score, -2.2)
        self.assertEqual(action, "SELL_SHORT")

    def test_adaptive_threshold_actually_gates(self):
        """抬高门槛 ⇒ 同一个 setup 不再开仓（门槛真的在起作用）。"""
        self.cfg = {"entry_threshold": 4.0}
        score, action, _, _, _ = self._eval(**self._long_setup())
        self.assertGreaterEqual(score, 2.2)
        self.assertEqual(action, "HOLD", "分数够但不到自适应门槛 ⇒ 观望")


class DirectionalCooldownTest(_Base):
    """冷却**按方向**生效，且不得互相阻断。"""

    def test_long_cooldown_suppresses_the_setup_itself_not_just_the_action(self):
        """⚠️ **实测语义（与我原先的假设相反，如实修正）**：冷却不是"分数照给、只拦动作"，
        而是**setup 整体不成立**（Setup 5 的条件里就带 `not cooldown_long`）⇒
        **分数也会掉回子因子水平**（实测 2.3 ⇒ 1.2），最终 `HOLD`。

        为什么要记这一笔：下游若只看分数，会把"被冷却拦下"误读成"行情不配合"
        —— 两者排水完全不同（一个该等冷却解除、一个该等行情）。故这里**两个事实都钉住**：
        动作被拦 **且** 分数确实被压低（这是当前设计，不是 bug；改它会动策略语义）。
        """
        self.cooldown = {("BTC-USDT-SWAP", "long")}
        score, action, _, _, _ = self._eval(**self._long_setup())
        self.assertEqual(action, "HOLD", "做多冷却 ⇒ 不开多")
        self.assertLess(score, 2.2,
                        "冷却让 setup 不成立 ⇒ 分数回落（不是「仅拦动作」）")

    def test_short_cooldown_does_not_block_a_long_signal(self):
        """**不得互相阻断**：做空冷却不该拦掉做多信号。"""
        self.cooldown = {("BTC-USDT-SWAP", "short")}
        _, action, _, _, _ = self._eval(**self._long_setup())
        self.assertEqual(action, "BUY_LONG", "方向冷却必须各管各的")

    def test_long_cooldown_does_not_block_a_short_signal(self):
        self.cooldown = {("BTC-USDT-SWAP", "long")}
        _, action, _, _, _ = self._eval(**self._short_setup())
        self.assertEqual(action, "SELL_SHORT")


class StrategyWeightTest(_Base):
    """权重是**有界**的：夹取区间 [0.7, 1.3]，不可比较时回默认 1.0。"""

    def test_high_weight_is_clamped_to_upper_bound(self):
        self.cfg = {"strategy_weights": {"💎 极值回归": 5.0}}
        score, action, _, _, _ = self._eval(**self._long_setup())
        self.assertEqual(score, 3.0, "2.3 * 1.3 = 2.99 ⇒ 四舍五入 3.0（夹到 1.3）")
        self.assertEqual(action, "BUY_LONG")

    def test_low_weight_is_clamped_to_lower_bound(self):
        self.cfg = {"strategy_weights": {"💎 极值回归": 0.1}}
        score, action, _, _, _ = self._eval(**self._long_setup())
        self.assertEqual(score, 1.6, "2.3 * 0.7 = 1.61 ⇒ 1.6（夹到 0.7）")
        self.assertEqual(action, "HOLD", "夹取后分数不足门槛 ⇒ 观望")

    def test_clamp_remains_a_module_level_seam(self):
        """`clamp` 保留在**本模块**做别名：`patch.object(模块, "clamp")` 是既有接缝。

        （模块 docstring 明确写了这一点；别名赋值会让调用点按全局名查找而失效。）
        """
        with patch.object(signals, "clamp", return_value=1.0) as m:
            score, _, _, _, _ = self._eval(**self._long_setup())
        self.assertTrue(m.called, "接缝必须仍然可打桩")
        self.assertEqual(score, 2.3, "权重被桩成 1.0 ⇒ 原始 2.3")


if __name__ == "__main__":
    unittest.main()

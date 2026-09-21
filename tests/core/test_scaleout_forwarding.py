"""分批平仓徽标：**行为验证**「缺席即缺席」（第二百零三刀）。

上一刀我用 AST 结构检查连续误报三次而撤回；本刀改成**行为验证**：直接构造 positions + trackers
夹具调用 `enrich_position_risk_fields`，断言真正面向 UI 的取值。

| 情形 | 必须的结果 |
|---|---|
| tracker **明确写着 0** | 发 `scaleOutPhase: 0`（存在就说存在；0 是真实状态：已进入分批、首批未完成）|
| 有 tracker 且写着 2 / 123.4 | 发 `2`（int）与 `123.4`（float），按前端契约类型 |
| tracker 里是 `""` / ````None```` 字样 / 真正的 None | **不写这两个键**（读不到 ≠ 有，不得写 0）|
| **没有 tracker**（币安/Gate 行）| **不写这两个键** ★ 写 0 等于**替它们断言「未开始」**——把「不知道」伪装成「知道」|
"""

import unittest
from unittest.mock import patch

from r20_backend.dashboard_payload import factors


def _pos(inst="BTC-USDT-SWAP", side="long", **over):
    p = {"instId": inst, "posSide": side, "pos_sz": 2.0, "markPx": 100.0,
         "notional_usdt": 200.0, "lever": 2.0}
    p.update(over)
    return p


class ScaleOutForwardingTest(unittest.TestCase):
    def setUp(self):
        # 避免读真实合约池：notional 由夹具显式给出，故 contract_values 为空也成立
        self.p = patch.object(factors, "load_instruments", return_value=[])
        self.p.start()
        self.addCleanup(self.p.stop)

    def _run(self, positions, trackers):
        return factors.enrich_position_risk_fields("unused.json", positions, trackers) or positions

    def test_zero_phase_is_emitted_because_it_is_a_real_state(self):
        """★ 明确写着 0 ⇒ **必须发 0**（0 与「没有这一项」是两回事）。"""
        rows = self._run([_pos()], {"BTC-USDT-SWAP_long": {"scale_out_phase": 0,
                                                           "scale_out_tp": 12.5}})
        self.assertIn("scaleOutPhase", rows[0], "明确写着 0 时必须发出来")
        self.assertEqual(rows[0]["scaleOutPhase"], 0)
        self.assertIsInstance(rows[0]["scaleOutPhase"], int, "前端按 >= 1 比较 ⇒ 整数")
        self.assertAlmostEqual(rows[0]["scaleOutTp"], 12.5)

    def test_values_are_cast_to_contract_types(self):
        rows = self._run([_pos()], {"BTC-USDT-SWAP_long": {"scale_out_phase": "2",
                                                           "scale_out_tp": "123.4"}})
        self.assertEqual(rows[0]["scaleOutPhase"], 2)
        self.assertIsInstance(rows[0]["scaleOutPhase"], int)
        self.assertAlmostEqual(rows[0]["scaleOutTp"], 123.4)

    def test_absent_placeholder_and_real_none_are_all_omitted(self):
        """★ 三种「读不到」都**不得**写 0：空串 / None 字样 / 真正的 None。"""
        for placeholder in ("", "None", None):
            with self.subTest(placeholder=placeholder):
                row = self._run([_pos()], {"BTC-USDT-SWAP_long": {
                    "scale_out_phase": placeholder, "scale_out_tp": placeholder}})[0]
                self.assertNotIn("scaleOutPhase", row,
                                 f"{placeholder!r} 是「读不到」⇒ 不得写 0 冒充「未开始」")
                self.assertNotIn("scaleOutTp", row)

    def test_positions_without_a_tracker_get_no_keys_at_all(self):
        """★ 币安/Gate 行没有 tracker ⇒ **一个键都不写**（不替它们断言「未开始」）。"""
        rows = self._run([_pos(inst="ETH-USDT-SWAP", side="short")],
                         {"BTC-USDT-SWAP_long": {"scale_out_phase": 3}})
        self.assertNotIn("scaleOutPhase", rows[0])
        self.assertNotIn("scaleOutTp", rows[0])
        self.assertEqual(rows[0]["posSide"], "short", "其它字段照常装配")

    def test_tracker_key_is_instId_underscore_side_lowercased(self):
        """名字即语义：tracker 按 `instId_side`（小写）认领，认不到就当没有。"""
        for key, expect in (("BTC-USDT-SWAP_long", 1), ("BTC-USDT-SWAP_LONG", None),
                            ("btc-usdt-swap_long", None)):
            with self.subTest(key=key):
                row = self._run([_pos()], {key: {"scale_out_phase": 1}})[0]
                if expect is None:
                    self.assertNotIn("scaleOutPhase", row, f"{key} 不该被认领")
                else:
                    self.assertEqual(row["scaleOutPhase"], 1)


if __name__ == "__main__":
    unittest.main()

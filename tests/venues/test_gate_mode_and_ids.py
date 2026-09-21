"""Gate 持仓模式判定、错误载荷与 ID 归一（第二百七十刀）。

**与 Binance 刻意分开**：两所模式词汇不同（Gate `position_mode`：single/dual/dual_plus；
Binance `dualSidePosition` 布尔），合并枚举迟早会把 `dual` 与 `long_short` 混为一谈。

| 语义 | 纪律 |
|---|---|
| 模式判定 | 首选显式 `position_mode`；缺失退化用 `in_dual_mode` 布尔；**都读不到 ⇒ `unknown`**（宁可判「测不出来」，也不拿默认值冒充事实——审计 §2：检测不支持时**禁新开仓并显示原因**）|
| `dual_plus` | **原样返回，绝不折叠成 dual**（拆仓语义不同）|
| ID 归一 | `id_string` 存在即优先取其字符串回写 `id`；纯 int 转 str；**绝不用 JSON Number→float→str**（int64 精度丢失）|
"""

import unittest

from r20_backend.exchanges.gate import (
    interpret_position_mode, GateAPIError, _normalize_gate_ids,
)


class InterpretPositionModeTest(unittest.TestCase):
    def test_explicit_mode_wins(self):
        self.assertEqual(interpret_position_mode({"position_mode": "dual"}), "dual")
        self.assertEqual(interpret_position_mode({"position_mode": "single"}), "single")

    def test_dual_plus_is_never_folded_into_dual(self):
        """★ `dual_plus`（拆仓）语义与 `dual` 不同 ⇒ **必须原样返回**。"""
        self.assertEqual(interpret_position_mode({"position_mode": "DUAL_PLUS"}), "dual_plus")

    def test_falls_back_to_dual_flags(self):
        self.assertEqual(interpret_position_mode({"in_dual_mode": True}), "dual")
        self.assertEqual(interpret_position_mode({"in_dual_mode": False}), "single")
        self.assertEqual(interpret_position_mode({"enable_new_dual_mode": True}), "dual")
        self.assertEqual(interpret_position_mode({"in_dual_mode": "TRUE"}), "dual")
        self.assertEqual(interpret_position_mode({"in_dual_mode": " False "}), "single")

    def test_explicit_field_beats_the_flag(self):
        out = interpret_position_mode({"position_mode": "dual_plus", "in_dual_mode": False})
        self.assertEqual(out, "dual_plus", "显式枚举优先于布尔兜底")

    def test_unreadable_is_unknown_not_a_default(self):
        """★ 读不到 ⇒ `unknown`（**不拿默认值冒充事实**），调用方据此禁新开仓。"""
        for payload in (None, [], "text", {}, {"position_mode": "weird"},
                        {"in_dual_mode": 1}, {"position_mode": ""}):
            with self.subTest(payload=payload):
                self.assertEqual(interpret_position_mode(payload), "unknown")

    def test_nested_raw_payload_is_supported(self):
        self.assertEqual(interpret_position_mode({"raw": {"position_mode": "dual"}}), "dual")
        self.assertEqual(interpret_position_mode({"raw": "not-a-dict"}), "unknown")


class GateAPIErrorTest(unittest.TestCase):
    def test_message_and_attributes(self):
        err = GateAPIError("INVALID_KEY", "bad key", 401)
        self.assertEqual(err.label, "INVALID_KEY")
        self.assertEqual(err.status, 401)
        self.assertIn("Gate INVALID_KEY: bad key", str(err))
        self.assertIsInstance(err, RuntimeError)

    def test_empty_label_and_message_have_readable_defaults(self):
        self.assertIn("Gate --: request failed", str(GateAPIError("", "")))


class NormalizeGateIdsTest(unittest.TestCase):
    def test_id_string_wins_over_the_numeric_id(self):
        """★ US-004 `id_string` 铁律：存在即优先取它的字符串并回写 `id`。"""
        out = _normalize_gate_ids({"id": 9007199254740993, "id_string": "9007199254740994"})
        self.assertEqual(out["id"], "9007199254740994")

    def test_plain_int_becomes_str(self):
        self.assertEqual(_normalize_gate_ids({"id": 42})["id"], "42")

    def test_integer_float_is_converted_without_inventing_digits(self):
        """上游已是 JSON Number（float）⇒ 用 `int()` 还原，**不发明数字**。"""
        self.assertEqual(_normalize_gate_ids({"id": 42.0})["id"], "42")
        self.assertEqual(_normalize_gate_ids({"id": 42.5})["id"], 42.5,
                         "非整数 float 不是订单 ID ⇒ 原样保留，不硬转")

    def test_empty_id_string_falls_back(self):
        self.assertEqual(_normalize_gate_ids({"id": 7, "id_string": ""})["id"], "7")

    def test_recursion_only_covers_top_level_lists(self):
        """⚠️ **实测边界（如实钉住，列待议）**：docstring 写「递归处理 dict/list」，
        但代码只对**顶层是 list** 的情况递归；**dict 的值不递归** —— 于是形如
        `{"orders": [{"id": 1}]}` 的回包里，嵌套订单的 `id` **保持 int 不变**
        （顶层 `id` 会转 str）。

        影响面：Gate 若把订单列表包在外层 dict 里返回，嵌套 ID 就没有走 `id_string`
        铁律 ⇒ int64 精度风险仍在。改它属钱路语义变更（`id_string` 优先级与 float 兜底
        都会一起生效）⇒ **只钉现状、列为待议，未擅自改**。
        """
        out = _normalize_gate_ids({"orders": [{"id": 1, "sz": 42.5}], "id": 2})
        self.assertEqual(out["id"], "2", "顶层 id 正常转 str")
        self.assertEqual(out["orders"][0]["id"], 1,
                         "嵌套 id 仍是 int ⇒ **dict 值不递归**（与 docstring 的表述不一致）")
        self.assertEqual(out["orders"][0]["sz"], 42.5, "非 ID 字段不动")

    def test_top_level_list_is_recursed(self):
        out = _normalize_gate_ids([{"id": 1}, {"id": 2, "id_string": "9007199254740993"}])
        self.assertEqual([r["id"] for r in out], ["1", "9007199254740993"],
                         "顶层 list 会被逐项递归（这条是 docstring 承诺里成立的那半）")

    def test_scalars_pass_through(self):
        self.assertEqual(_normalize_gate_ids("text"), "text")
        self.assertIsNone(_normalize_gate_ids(None))


if __name__ == "__main__":
    unittest.main()

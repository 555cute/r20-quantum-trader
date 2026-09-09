"""自进化复盘输出 schema 漂移归一回归（2026-09-09 用户截图：逐单归因 [object Object]、
行动清单渲染原始 JSON）。模型偶发把数组项输出为对象或自序列化 JSON 字符串，
后端入库入口必须统一压平为展示字符串。"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from self_improvement_engine import _coerce_display_str, resolve_memory_update


class CoerceDisplayStrTests(unittest.TestCase):
    def test_string_passthrough(self):
        self.assertEqual(_coerce_display_str("【已验证】纯字符串"), "【已验证】纯字符串")
        self.assertEqual(_coerce_display_str(""), "")
        self.assertEqual(_coerce_display_str(None), "")

    def test_json_self_serialized_string_unwrapped(self):
        raw = '{"dimension":"数理证据与数据有效性","analysis":"快照缺失"}'
        self.assertEqual(_coerce_display_str(raw), "【数理证据与数据有效性】快照缺失")

    def test_object_known_keys(self):
        self.assertEqual(
            _coerce_display_str({"dimension": "手续费与退出质量", "observation": "fee 11.53U"}),
            "【手续费与退出质量】fee 11.53U",
        )
        self.assertEqual(
            _coerce_display_str({"action_type": "MEMORY_REVISE", "action": "保本移损阈值修订"}),
            "【MEMORY_REVISE】保本移损阈值修订",
        )

    def test_object_unknown_shape_never_object_object(self):
        out = _coerce_display_str({"weird": "x", "n": 3})
        self.assertNotIn("[object Object]", out)
        self.assertIn("weird:x", out)

    def test_list_flattened(self):
        self.assertEqual(_coerce_display_str(["a", "b"]), "a；b")


class ResolveMemoryUpdateCoercionTests(unittest.TestCase):
    def test_proposed_objects_flattened_before_memory_write(self):
        status, lessons, preserve = resolve_memory_update(
            "ADD",
            [{"dimension": "执行纪律", "observation": "同向敞口超 2 笔禁开"}, "【纯字符串心法】保持"],
            ["旧心法"],
        )
        self.assertEqual(status, "ADD")
        self.assertFalse(preserve)
        self.assertTrue(all(isinstance(x, str) for x in lessons))
        self.assertEqual(lessons[0], "【执行纪律】同向敞口超 2 笔禁开")
        self.assertIn("【纯字符串心法】保持", lessons)

    def test_no_change_still_preserves_existing(self):
        status, lessons, preserve = resolve_memory_update("NO_CHANGE", [{"a": 1}], ["旧心法"])
        self.assertTrue(preserve)
        self.assertEqual(lessons, ["旧心法"])


if __name__ == "__main__":
    unittest.main()

"""变量渲染器：**缺值给标记，不给空**（第二百一十二刀）。

`render_variables` 是提示词与面板共用的**同一个**渲染器，它的文档串写明了三条取向：

- **Pure, single-pass**：单趟替换，不递归；
- **never fetch account / news / memory data**：纯函数，不去取数（取数由调用方负责）；
- ★ **unavailable inputs are markers, not evidence of an empty account** ——
  拿不到就给标记，**绝不静默变成空**（否则「读不到」会被呈现成「没有」）。

本刀把这三条落成用例（上一刀我明确标注了「标记形态未验证」，本刀**先读实现**再断言）。
"""

import re
import unittest

from scripts.prompt_library import ALLOWED_VARIABLES, render_variables


class RenderVariablesTest(unittest.TestCase):
    def test_none_context_is_a_template_preview(self):
        self.assertEqual(render_variables("{{a}} {{b}}", None), "{{a}} {{b}}")
        self.assertEqual(render_variables("", None), "")
        self.assertEqual(render_variables(None, None), "")

    def test_unknown_variable_is_marked_not_dropped(self):
        out = render_variables("未知:{{definitely_not_allowed_xyz}}", {})
        self.assertEqual(out, "未知:[UNKNOWN_VARIABLE:definitely_not_allowed_xyz]",
                         "白名单外的变量必须留标记（绝不静默抹掉）")

    def test_allowed_but_unavailable_variable_is_marked(self):
        """★ 与「未知变量」区分开：白名单内但**这次没给值** ⇒ 另一种标记。"""
        allowed = sorted(ALLOWED_VARIABLES - {"timezone"})[0]
        out = render_variables(f"{{{{{allowed}}}}}", {})
        self.assertEqual(out, f"[MISSING_CONTEXT:{allowed}]")

    def test_explicit_none_is_treated_as_unavailable(self):
        allowed = sorted(ALLOWED_VARIABLES - {"timezone"})[0]
        out = render_variables(f"{{{{{allowed}}}}}", {allowed: None})
        self.assertEqual(out, f"[MISSING_CONTEXT:{allowed}]",
                         "显式 None 也当「读不到」，不写 None 字面量")

    def test_zero_is_a_value_not_a_missing_marker(self):
        """★ 0 与空串是**真实值**：不得像某些 `or` 写法那样被当成「没有」。"""
        allowed = sorted(ALLOWED_VARIABLES - {"timezone"})[0]
        self.assertEqual(render_variables(f"{{{{{allowed}}}}}", {allowed: 0}), "0")
        self.assertEqual(render_variables(f"{{{{{allowed}}}}}", {allowed: ""}), "",
                         "空串是给出来的值 ⇒ 渲染成空（而不是标记）")

    def test_timezone_is_always_available(self):
        """渲染器**恒定注入** timezone，故它永不缺值（时区是展示层的确定事实）。"""
        self.assertEqual(render_variables("{{timezone}}", {}), "Asia/Shanghai")

    def test_substitution_is_single_pass_and_never_recursive(self):
        """★ 单趟替换：**替换值里的花括号不被再次解释**。"""
        allowed = sorted(ALLOWED_VARIABLES - {"timezone"})[0]
        other = sorted(ALLOWED_VARIABLES - {"timezone", allowed})[0]
        out = render_variables(f"{{{{{allowed}}}}}", {allowed: f"{{{{{other}}}}}", other: "X"})
        self.assertEqual(out, f"{{{{{other}}}}}",
                         "替换值原样带出（不递归展开 —— 否则等于让数据当模板）")

    def test_pattern_is_the_documented_double_brace_form(self):
        """形态即契约：只认双花括号；单花括号是正文，原样保留。"""
        self.assertEqual(render_variables("{a}", {"a": 1}), "{a}")
        self.assertTrue(re.search(r"\{\{", "{{x}}"), "双花括号是该渲染器的语法")


if __name__ == "__main__":
    unittest.main()

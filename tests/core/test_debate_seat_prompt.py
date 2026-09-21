"""委员会席位提示词渲染：**绝不静默吞占位符**（第二百一十一刀）。

审计 P1-4d 的线上事故：旧实现把席位提示词**原样**塞进 system prompt，当时线上**4 个席位**
都带着 `{{macro_4h}}` 之类的占位符 ⇒ **模型看到的是花括号字面量**
（`render_variables` 在委员会全文出现 **0 次**）。这与「**UI/提示词不说谎**」直接冲突：
提示词里的「变量」被当成正文喂给了模型。

现修法（本刀钉住）：

| 输入 | 输出 |
|---|---|
| 空提示词 | 空串 |
| `runtime_context=None` | **模板原样**（预览语义：不渲染，也不标错）|
| 有 context | 交给 prompt_library 的**同一个**渲染器（委派已钉住）；未知/缺值的**标记形态**本轮**未验证**（渲染器实现未读）⇒ 不声称 |
| 渲染器两条导入路径都不可用 | **返回原样**（宁可原样，也不要空提示词）|
"""

import importlib
import unittest
from unittest.mock import MagicMock, patch

from r20_backend.council.debate import _render_seat_prompt

TPL = "宏观:{{macro_4h}} | 未知:{{definitely_unknown_xyz}}"


class RenderSeatPromptTest(unittest.TestCase):
    """★ 补丁目标要按**实际可导入的那条路径**选：本仓有 `prompt_library` 与
    `scripts.prompt_library` 两种拼写，函数先试前者、失败再退后者（我第一版只补前者，
    于是三条委派断言全红 —— 又是「先怀疑夹具」的场合）。"""

    @classmethod
    def setUpClass(cls):
        for name in ("prompt_library", "scripts.prompt_library"):
            try:
                importlib.import_module(name)
            except Exception:
                continue
            cls.target = f"{name}.render_variables"
            return
        raise unittest.SkipTest("两条 prompt_library 导入路径都不可用")

    def test_empty_prompt_is_empty(self):
        for value in ("", None):
            with self.subTest(prompt=value):
                self.assertEqual(_render_seat_prompt(value, {"a": 1}), "")

    def test_none_context_keeps_the_template_verbatim(self):
        """★ 预览语义：没有上下文就**原样**返回（既不渲染，也不把变量标成错误）。"""
        self.assertEqual(_render_seat_prompt(TPL, None), TPL)

    def test_context_delegates_to_the_shared_renderer(self):
        """★ 走 prompt_library 的**同一个**渲染器（委员会不再自己发明一套）。"""
        fake = MagicMock(return_value="渲染结果")
        with patch(self.target, fake):
            out = _render_seat_prompt(TPL, {"macro_4h": "趋势向上"})
        self.assertEqual(out, "渲染结果")
        self.assertEqual(fake.call_args.args, (TPL, {"macro_4h": "趋势向上"}),
                         "模板与上下文原样交给渲染器")

    def test_renderer_result_is_returned_unchanged(self):
        with patch(self.target, return_value="X[UNKNOWN_VARIABLE:y]Z"):
            self.assertEqual(_render_seat_prompt("X{{y}}Z", {}), "X[UNKNOWN_VARIABLE:y]Z",
                             "渲染器已标注的文本不得被本函数二次加工")

if __name__ == "__main__":
    unittest.main()

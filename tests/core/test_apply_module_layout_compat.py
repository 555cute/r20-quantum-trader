"""`apply_module_layout` 的**兼容退路**（第二百一十四刀）。

编辑器档案有两种形态：新版是 `profile["pipelines"][pipeline]` 里的**模块列表**，
旧版是**一整段文本**。函数开头就判：**只要拿不到「含 base 源模块的列表」**，就走兼容退路 ——
把 base 模板与旧文本转出的模块拼起来，再交给**同一个**渲染器（并**带上 context**）。

本刀只钉这条退路（主路径分支很多，留待后续按需覆盖）：

| 输入 | 行为 |
|---|---|
| `pipelines` 不是字典 / `pipeline` 项不是列表 | 旧文本经 `text_to_modules` 转模块，与 base 模板合并后渲染 |
| 列表里**一个 base 源模块都没有** | 同上（**空列表/全是 custom 也算旧式**）|
| 列表里有 base 源模块 | **不走**退路（不得顺手把旧文本也塞进来）|

★ 退路也必须**带上 context**：否则旧档案的变量会退回成字面量（正是席位提示词那次事故的形态）。
"""

import unittest
from unittest.mock import MagicMock, patch

from scripts import prompt_library as PL


class CompatPathTest(unittest.TestCase):
    def setUp(self):
        self.compile = patch.object(PL, "compile_modules",
                                    side_effect=lambda mods: "编译:" + repr(mods))
        self.compile.start()
        self.addCleanup(self.compile.stop)
        self.text_to_modules = patch.object(PL, "text_to_modules",
                                            return_value=[{"title": "旧模块",
                                                           "source": "custom",
                                                           "content": "旧文本"}])
        self.ttm = self.text_to_modules.start()
        self.addCleanup(self.text_to_modules.stop)
        self.base = patch.object(PL, "base_template_modules",
                                 return_value=[{"title": "base-1", "source": "base"}])
        self.base_mock = self.base.start()
        self.addCleanup(self.base.stop)
        self.render = patch.object(PL, "render_variables", return_value="渲染结果")
        self.render_mock = self.render.start()
        self.addCleanup(self.render.stop)
        self.ctx = {"k": 1}

    def test_non_dict_pipelines_uses_the_legacy_text(self):
        out = PL.apply_module_layout("BASE", {"pipelines": "旧档案"}, "trading_main", "标签",
                                     self.ctx)
        self.assertEqual(out, "渲染结果")
        self.assertIs(self.render_mock.call_args.args[1], self.ctx, "退路也必须带上 context")
        self.assertTrue(self.ttm.called, "旧文本必须经 text_to_modules 转换")

    def test_pipeline_entry_that_is_not_a_list_uses_the_legacy_text(self):
        PL.apply_module_layout("BASE", {"pipelines": {"trading_main": "旧文本"}},
                               "trading_main", "标签", self.ctx)
        self.assertTrue(self.ttm.called)

    def test_list_without_any_base_source_is_still_legacy(self):
        """★ 关键判据是「**有没有 base 源模块**」，不是「是不是列表」。"""
        out = PL.apply_module_layout("BASE", {"pipelines": {"trading_main": [
            {"title": "自定义", "source": "custom", "content": "x"}]}},
            "trading_main", "标签", self.ctx)
        self.assertEqual(out, "渲染结果")
        self.assertFalse(self.ttm.called,
                         "已经是列表 ⇒ 直接用它，不该再去解析旧文本")

    def test_a_base_source_module_does_not_take_the_legacy_path(self):
        with patch.object(PL, "text_to_modules", side_effect=AssertionError("不该走退路")):
            with self.assertRaises(Exception) as ctx:
                PL.apply_module_layout("BASE", {"pipelines": {"trading_main": [
                    {"title": "base-1", "source": "base", "enabled": True}]}},
                    "trading_main", "标签", self.ctx)
        self.assertNotIsInstance(ctx.exception, AssertionError,
                                 "有 base 源模块 ⇒ 不得调用 text_to_modules（允许后续分支出错，但不许走退路）")

    def test_legacy_path_without_profile_pipeline_text(self):
        """旧档案里连这一段文本都没有 ⇒ 只编 base 模板（不炸）。"""
        out = PL.apply_module_layout("BASE", {}, "trading_main", "标签", self.ctx)
        self.assertEqual(out, "渲染结果")
        self.assertTrue(self.ttm.called, "空文本同样经转换函数（由它决定给不给模块）")


if __name__ == "__main__":
    unittest.main()

"""`apply_module_layout` **主路径**的关键分支（第二百一十五刀）。

| 语义 | 实测口径 |
|---|---|
| ★ **Fail closed** | 只在 `trading_user`：**档案没提到的 base 模块会被补回末尾** —— 漏配 ≠ 不要这一节，实时小节绝不因档案遗漏而消失 |
| 严格性 | 其它 pipeline **不补回**（严格按档案）|
| ★ **禁用 ≠ 缺失** | 档案里显式 `enabled=False` 的 base 项**不输出**，且**不会**被 fail-closed 补回（先把标题记进"已匹配"再跳过）|
| ★ **锁定节** | 标题含「三重滤网裁决协议」或「开仓与价格几何」⇒ **强制用 base 模板原文**，忽略档案里的自定义内容 |
| 内容三态 | 档案 `content` 为 **None ⇒ 回落模板**；为**空串 ⇒ 用空串**（提供了空就是空）；其它 ⇒ 用档案内容 |
| 未知标题 | 档案里写了 base 源但标题对不上模板 ⇒ 有内容才输出，空内容直接跳过 |
| trading_user 编组 | 该节对应**一组**模块（含嵌套实时小节）⇒ 整组编译；档案内容里含变量时**原样保留模板** |

最后统一走 `render_variables(compile_modules(...), context)` —— **context 一定带上**。
"""

import unittest
from unittest.mock import patch

from scripts import prompt_library as PL

BASE = [{"title": "实时一览", "source": "base", "content": "模板A"},
        {"title": "三重滤网裁决协议", "source": "base", "content": "锁定原文"},
        {"title": "没被档案提到的节", "source": "base", "content": "模板C"}]


class MainPathTest(unittest.TestCase):
    def setUp(self):
        self.base = patch.object(PL, "base_template_modules", return_value=list(BASE))
        self.base_mock = self.base.start()
        self.addCleanup(self.base.stop)
        self.compile = patch.object(PL, "compile_modules",
                                    side_effect=lambda mods: "编译:" + "|".join(
                                        m["content"] for m in mods))
        self.compile_mock = self.compile.start()
        self.addCleanup(self.compile.stop)
        self.render = patch.object(PL, "render_variables", return_value="输出")
        self.render_mock = self.render.start()
        self.addCleanup(self.render.stop)

    def _profile(self, pipeline, items):
        return {"pipelines": {pipeline: items}}

    def test_fail_closed_readds_pre_title_modules_in_trading_user(self):
        """★ **Fail closed 真正起作用的地方**：`trading_user` 编组只把「第一个标题之后」的模块
        收进父节；**第一个标题之前**的模块不进任何组 ⇒ 本来会被漏掉 ⇒ 由 fail-closed **补回末尾**。

        ⚠️ 我第一版以为补回的是「档案没提到的同级模块」—— 错：那些模块早被编组**吸收**进父节了
        （见上一刀：编组把后续模块挂到前面最近的父节）。真正需要营救的是**前置模块**。
        这也**修正**了我在上一刀记下的待议：「前置模块静默丢弃」在 `trading_user` 下**并不成立**
        —— 它不进组，但会被补回。
        """
        base = [{"title": "前置节", "source": "base", "content": "模板P"},
                {"title": "实时一览", "source": "base", "content": "模板A"},
                {"title": "没被档案提到的节", "source": "base", "content": "模板C"}]
        with patch.object(PL, "base_template_modules", return_value=base):
            PL.apply_module_layout("BASE",
                                   self._profile("trading_user", [
                                       {"source": "base", "title": "实时一览",
                                        "content": "档案A"}]),
                                   "trading_user", "标签", {"k": 1})
        got = self.compile_mock.call_args.args[0]
        self.assertEqual(len(got), 2, "父节编译结果 + 被补回的前置节")
        self.assertEqual(got[1]["content"], "模板P",
                         "第一个标题之前的模块被 fail-closed 补回（漏配 ≠ 不要）")

    def test_other_pipelines_are_strict(self):
        PL.apply_module_layout("BASE",
                               self._profile("trading_main", [
                                   {"source": "base", "title": "实时一览", "content": "档案A"}]),
                               "trading_main", "标签", {"k": 1})
        got = self.compile_mock.call_args.args[0]
        self.assertEqual([m["content"] for m in got], ["档案A"], "非 trading_user ⇒ 不补回")

    def test_a_disabled_base_item_is_dropped_and_not_readded(self):
        """★ **禁用 ≠ 缺失**：显式关掉的分节真的不出现，也不会被 fail-closed 补回来。"""
        PL.apply_module_layout("BASE",
                               self._profile("trading_user", [
                                   {"source": "base", "title": "实时一览", "enabled": False},
                                   {"source": "base", "title": "没被档案提到的节",
                                    "enabled": False}]),
                               "trading_user", "标签", {"k": 1})
        got = self.compile_mock.call_args.args[0]
        self.assertEqual([m["content"] for m in got], [],
                         "两个都显式禁用 ⇒ 都不输出，也不补回")

    def test_locked_sections_ignore_the_profile_content(self):
        """★ 锁定节强制用模板原文（档案里的自定义内容对它无效）。"""
        PL.apply_module_layout("BASE",
                               self._profile("trading_main", [
                                   {"source": "base", "title": "三重滤网裁决协议",
                                    "content": "被篡改的内容"}]),
                               "trading_main", "标签", {"k": 1})
        got = self.compile_mock.call_args.args[0]
        self.assertEqual(got[0]["content"], "锁定原文", "锁定节不得被档案内容覆盖")

    def test_content_none_falls_back_but_empty_string_is_taken_literally(self):
        for content, expect in ((None, "模板A"), ("", ""), ("档案A", "档案A")):
            with self.subTest(content=content):
                PL.apply_module_layout("BASE",
                                       self._profile("trading_main", [
                                           {"source": "base", "title": "实时一览",
                                            "content": content}]),
                                       "trading_main", "标签", {"k": 1})
                got = self.compile_mock.call_args.args[0]
                self.assertEqual(got[0]["content"], expect,
                                 "None ⇒ 回落模板；空串 ⇒ 就是空（提供了空就是空）")

    def test_unknown_base_title_only_output_when_it_has_content(self):
        PL.apply_module_layout("BASE",
                               self._profile("trading_main", [
                                   {"source": "base", "title": "对不上的标题", "content": "内容"},
                                   {"source": "base", "title": "另一个对不上的", "content": "  "}]),
                               "trading_main", "标签", {"k": 1})
        got = self.compile_mock.call_args.args[0]
        self.assertEqual([m["content"] for m in got], ["内容"],
                         "对不上模板的标题：有内容才输出（空白内容跳过）")


    def test_trading_user_keeps_a_profile_content_that_holds_variables(self):
        """★ `trading_user` 下：档案内容**含变量** ⇒ **原样保留**，不编译。

        理由：它本身就是一块模板（变量留给渲染器统一替换）；若此处编译，变量会被当正文固化 ——
        正是席位提示词那次事故（提示词里出现字面量花括号）的形态。
        """
        PL.apply_module_layout("BASE",
                               self._profile("trading_user", [
                                   {"source": "base", "title": "实时一览",
                                    "content": "实时:{{macro_4h}} 结束"}]),
                               "trading_user", "标签", {"k": 1})
        got = self.compile_mock.call_args.args[0]
        self.assertEqual(got[0]["content"], "实时:{{macro_4h}} 结束",
                         "含变量的档案内容不得被编译（否则变量变成字面量）")

    def test_trading_user_without_variables_compiles_the_whole_group(self):
        """★ `trading_user` 下：档案内容**不含变量** ⇒ 编译**整组**（含嵌套实时小节）。"""
        PL.apply_module_layout("BASE",
                               self._profile("trading_user", [
                                   {"source": "base", "title": "实时一览", "content": "无变量"}]),
                               "trading_user", "标签", {"k": 1})
        got = self.compile_mock.call_args.args[0]
        self.assertIn("模板A", got[0]["content"],
                      "整组编译：本节的 base 模板内容出现在结果里")
        self.assertIn("模板C", got[0]["content"],
                      "整组编译：被编组吸收的后续模块也在结果里")

    def test_a_matched_module_takes_its_identity_from_the_template(self):
        """★ 模块身份以**模板**为准：档案项只提供 `content`，其余字段不参与合并。"""
        PL.apply_module_layout("BASE",
                               self._profile("trading_main", [
                                   {"source": "base", "title": "实时一览",
                                    "content": "档案A", "自定义字段": "应被忽略"}]),
                               "trading_main", "标签", {"k": 1})
        got = self.compile_mock.call_args.args[0]
        self.assertEqual(got[0]["source"], "base", "身份字段来自模板")
        self.assertNotIn("自定义字段", got[0], "档案里的额外字段不进入输出模块")

    def test_context_is_always_passed_to_the_renderer(self):
        ctx = {"k": 2}
        PL.apply_module_layout("BASE", self._profile("trading_main", []), "trading_main",
                               "标签", ctx)
        self.assertIs(self.render_mock.call_args.args[1], ctx,
                      "主路径同样必须带上 context（否则变量退回字面量）")


if __name__ == "__main__":
    unittest.main()

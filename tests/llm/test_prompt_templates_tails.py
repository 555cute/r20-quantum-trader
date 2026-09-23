"""基座文本解析（`scripts/prompt_templates.py`）的残余分支收口 —— 第 332 刀。

本模块 193 行，是提示词方案的**纯函数层**（不落盘、不读环境）：把代码基座文本切成分节
模块、把重建出来的模块与基座**逐字**对齐、把提交的模块缺的 `source`/`locked` 标签
从已存同 id / 同标题模块**继承**。

## 本刀立住的三条纪律

1. **基座文本取不到就返回空串**，调用方必须退化为"不改来源"，**绝不臆造**
   （`base_template_text` 的 docstring）。
2. **只按逐字相同判定亲缘**，避免误认亲（`align_pipeline_sources`）——
   审计 P1-2：丢掉 `source=base` 标签会让 `apply_module_layout` 把整段基座前置 ⇒
   叠加已含同样内容的模块 = **提示词翻倍**。
3. **显式传入的值永远优先**（`_inherit_module_tags` 只补缺失字段，不覆盖）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.prompt_templates as pt  # noqa: E402

MAX = 100_000


def _t(text):
    return pt.text_to_modules(text, "legacy", max_template_chars=MAX)


# ───────────────────── base_template_text ─────────────────────
class BaseTemplateTextTests(unittest.TestCase):
    def test_a_cached_value_short_circuits(self):
        cache = {"trading_system": "已缓存的基座"}
        out = pt.base_template_text("trading_system", sources={}, cache=cache)
        self.assertEqual(out, "已缓存的基座")

    def test_an_unregistered_pipeline_yields_an_empty_string(self):
        # 取不到 ⇒ 返回空串，**不臆造**
        self.assertEqual(pt.base_template_text("nope", sources={}, cache={}), "")

    def test_a_module_already_in_sys_modules_is_used_without_importing(self):
        sources = {"p": ("__doc__", ("sys",))}
        out = pt.base_template_text("p", sources=sources, cache={})
        self.assertIsInstance(out, str)

    def test_a_not_yet_imported_module_is_imported_on_demand(self):
        sys.modules.pop("colorsys", None)
        sources = {"p": ("__name__", ("colorsys",))}
        self.assertEqual(pt.base_template_text("p", sources=sources, cache={}),
                         "colorsys")

    def test_the_first_importable_candidate_wins(self):
        sys.modules.pop("colorsys", None)
        sources = {"p": ("__name__", ("totally_bogus_module_xyz", "colorsys"))}
        self.assertEqual(pt.base_template_text("p", sources=sources, cache={}),
                         "colorsys")

    def test_an_unimportable_candidate_is_skipped(self):
        # ★ 第 119–123 行 —— 每个候选各自 try，失败的 continue 到下一个
        sys.modules.pop("colorsys", None)
        sources = {"p": ("__name__", ("bogus_one_xyz", "bogus_two_xyz", "colorsys"))}
        self.assertEqual(pt.base_template_text("p", sources=sources, cache={}),
                         "colorsys")

    def test_all_candidates_unimportable_yields_an_empty_string(self):
        # ★ 第 124/125 行 —— 全部失败后 module 仍是 None ⇒ ""（绝不臆造）
        sources = {"p": ("SOMETHING", ("bogus_one_xyz", "bogus_two_xyz"))}
        self.assertEqual(pt.base_template_text("p", sources=sources, cache={}), "")

    def test_a_missing_attribute_yields_an_empty_string(self):
        sources = {"p": ("NO_SUCH_ATTRIBUTE_XYZ", ("json",))}
        self.assertEqual(pt.base_template_text("p", sources=sources, cache={}), "")

    def test_an_empty_attribute_is_not_cached(self):
        # `if text:` 才写缓存 —— 空串不占用缓存位，下次仍会重试 import
        sources = {"p": ("NO_SUCH_ATTRIBUTE_XYZ", ("json",))}
        cache: dict = {}
        pt.base_template_text("p", sources=sources, cache=cache)
        self.assertNotIn("p", cache)

    def test_a_nonempty_attribute_is_cached(self):
        sys.modules.pop("colorsys", None)
        cache: dict = {}
        pt.base_template_text("p", sources={"p": ("__name__", ("colorsys",))},
                              cache=cache)
        self.assertEqual(cache["p"], "colorsys")


# ───────────────────── text_to_modules ─────────────────────
class TextToModulesTests(unittest.TestCase):
    def test_empty_text_yields_no_modules(self):
        self.assertEqual(_t(""), [])

    def test_a_plain_paragraph_becomes_one_module(self):
        mods = _t("只有一段正文")
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0]["content"], "只有一段正文")

    def test_the_source_and_locked_flags_are_recorded(self):
        mods = pt.text_to_modules("正文", "base", True, max_template_chars=MAX)
        self.assertEqual(mods[0]["source"], "base")
        self.assertTrue(mods[0]["locked"])

    def test_a_legacy_module_id_is_random_not_stable(self):
        # ⚠️ 非 base 来源的 id 是**随机 uuid** ⇒ 同输入两次得到不同 id
        #   （`stable_base_module_id` 的 docstring 说明了原因：只有基座模块的内容
        #     由代码决定，故只有它的 id 派生自标题）
        first = _t("A" * 50)[0]["id"]
        self.assertTrue(first.startswith("module-"))
        self.assertNotEqual(first, _t("A" * 50)[0]["id"])

    def test_a_base_module_id_is_deterministic(self):
        def _base():
            return pt.text_to_modules("A" * 50, "base", max_template_chars=MAX)[0]["id"]
        self.assertEqual(_base(), _base())
        self.assertTrue(_base().startswith("module-base-"))


# ───────────────────── align_pipeline_sources ─────────────────────
class AlignPipelineSourcesTests(unittest.TestCase):
    def test_no_base_text_returns_the_modules_untouched(self):
        mods = _t("正文")
        out = pt.align_pipeline_sources(mods, "p", max_template_chars=MAX,
                                        base_text_resolver=lambda _p: "")
        self.assertEqual(out, mods)

    def test_no_modules_returns_them_untouched(self):
        out = pt.align_pipeline_sources([], "p", max_template_chars=MAX,
                                        base_text_resolver=lambda _p: "基座正文")
        self.assertEqual(out, [])

    def test_a_verbatim_match_is_retagged_as_base(self):
        base = "基座正文内容"
        mods = pt.text_to_modules(base, "legacy", max_template_chars=MAX)
        out = pt.align_pipeline_sources(mods, "p", max_template_chars=MAX,
                                        base_text_resolver=lambda _p: base)
        self.assertEqual(out[0]["source"], "base")
        self.assertEqual(out[0]["id"], pt.text_to_modules(base, "base",
                         max_template_chars=MAX)[0]["id"], "换成基座的确定性 id")

    def test_a_base_match_is_never_marked_locked(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：`align_pipeline_sources` 内部用
        #   `text_to_modules(base_text, "base", ...)` 重建候选，而该调用**没传**
        #   `locked=True` ⇒ 认亲成功的模块 `source="base"` 但 `locked=False`。
        #   `locked` 实际只可能由 `_inherit_module_tags` 从**已存**模块继承而来。
        base = "基座正文内容"
        out = pt.align_pipeline_sources(
            pt.text_to_modules(base, "legacy", max_template_chars=MAX), "p",
            max_template_chars=MAX, base_text_resolver=lambda _p: base)
        self.assertEqual(out[0]["source"], "base")
        self.assertFalse(out[0]["locked"])

    def test_the_enabled_flag_from_the_submission_is_preserved(self):
        base = "基座正文内容"
        mods = pt.text_to_modules(base, "legacy", max_template_chars=MAX)
        mods[0]["enabled"] = False
        out = pt.align_pipeline_sources(mods, "p", max_template_chars=MAX,
                                        base_text_resolver=lambda _p: base)
        self.assertFalse(out[0]["enabled"])

    def test_a_non_matching_module_keeps_its_own_source(self):
        # 只按**逐字相同**判定 ⇒ 差一个字就不认亲
        base = "基座正文内容"
        mods = pt.text_to_modules(base + "改", "legacy", max_template_chars=MAX)
        out = pt.align_pipeline_sources(mods, "p", max_template_chars=MAX,
                                        base_text_resolver=lambda _p: base)
        self.assertEqual(out[0]["source"], "legacy")


# ───────────────────── _inherit_module_tags ─────────────────────
class InheritModuleTagsTests(unittest.TestCase):
    def test_a_stored_list_that_is_empty_returns_the_submission(self):
        submitted = [{"id": "m1"}]
        self.assertEqual(pt._inherit_module_tags(submitted, []), submitted)
        self.assertEqual(pt._inherit_module_tags(submitted, None), submitted)
        self.assertEqual(pt._inherit_module_tags(submitted, "junk"), submitted)

    def test_tags_are_inherited_by_id(self):
        merged = pt._inherit_module_tags(
            [{"id": "m1", "content": "x"}],
            [{"id": "m1", "source": "base", "locked": True}])
        self.assertEqual(merged[0]["source"], "base")
        self.assertTrue(merged[0]["locked"])

    def test_tags_are_inherited_by_title_when_the_id_is_new(self):
        merged = pt._inherit_module_tags(
            [{"id": "new", "title": "同一个标题", "content": "x"}],
            [{"id": "old", "title": "同一个标题", "source": "base", "locked": True}])
        self.assertEqual(merged[0]["source"], "base")

    def test_an_explicit_value_always_wins(self):
        # 「显式传入的值永远优先」
        merged = pt._inherit_module_tags(
            [{"id": "m1", "source": "custom", "locked": False}],
            [{"id": "m1", "source": "base", "locked": True}])
        self.assertEqual(merged[0]["source"], "custom")
        self.assertFalse(merged[0]["locked"])

    def test_a_non_dict_submission_item_is_passed_through(self):
        # ★ 第 162–164 行
        merged = pt._inherit_module_tags(["junk", 42],
                                         [{"id": "m1", "source": "base"}])
        self.assertEqual(merged, ["junk", 42])

    def test_an_unmatched_item_is_passed_through_unchanged(self):
        merged = pt._inherit_module_tags([{"id": "m9", "content": "x"}],
                                         [{"id": "m1", "source": "base"}])
        self.assertNotIn("source", merged[0])

    def test_the_original_submission_is_not_mutated(self):
        item = {"id": "m1", "content": "x"}
        pt._inherit_module_tags([item], [{"id": "m1", "source": "base"}])
        self.assertNotIn("source", item)

    def test_a_stored_entry_without_an_id_or_title_is_not_indexed(self):
        merged = pt._inherit_module_tags([{"id": "m1", "content": "x"}],
                                         [{}, "junk", {"id": "m1", "source": "base"}])
        self.assertEqual(merged[0]["source"], "base")


# ───────────────────── pipeline_view ─────────────────────
class PipelineViewTests(unittest.TestCase):
    def _resolver(self, text):
        return lambda _p: text

    def test_the_stored_pipeline_wins_and_is_unlocked_for_the_editor(self):
        profile = {"pipelines": {"trading_system": [
            {"id": "m1", "content": "用户改过的", "source": "base", "locked": True}]}}
        view = pt.pipeline_view("", profile, "trading_system", max_template_chars=MAX,
                                base_text_resolver=self._resolver(""))
        self.assertEqual(view[0]["content"], "用户改过的")
        self.assertFalse(view[0]["locked"], "编辑器视图必须解锁")

    def test_the_stored_pipeline_is_deep_copied(self):
        profile = {"pipelines": {"trading_system": [{"id": "m1", "content": "原始"}]}}
        view = pt.pipeline_view("", profile, "trading_system", max_template_chars=MAX,
                                base_text_resolver=self._resolver(""))
        view[0]["content"] = "MUTATED"
        self.assertEqual(profile["pipelines"]["trading_system"][0]["content"], "原始")

    def test_an_empty_stored_pipeline_falls_back_to_the_base_plus_flat_text(self):
        # ⚠️ 基座模块来自**第一个实参 `base`**，不是 resolver：
        #   `base_template_modules` 的 `base_text_resolver` 参数**从未被使用**
        #   （见 `test_the_resolver_argument_is_never_used`）。
        profile = {"pipelines": {"trading_system": []}, "trading_system": "扁平正文"}
        view = pt.pipeline_view("基座", profile, "trading_system",
                                max_template_chars=MAX,
                                base_text_resolver=self._resolver("不该被用到"))
        contents = " ".join(m["content"] for m in view)
        self.assertIn("基座", contents)
        self.assertIn("扁平正文", contents)

    def test_the_resolver_argument_is_never_used(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：`base_template_modules` 收下
        #   `base_text_resolver` 却**一次都没调用**它（第 97–104 行的实现里没有引用）。
        #   第 100 行已传 `locked=False`，第 102/103 行再逐条赋 `False` 是**纯冗余**。
        calls = []

        def _spy(pipeline):
            calls.append(pipeline)
            return "基座正文"
        mods = pt.base_template_modules("另一段文本", "trading_system",
                                        max_template_chars=MAX,
                                        base_text_resolver=_spy)
        self.assertEqual(calls, [], "resolver 被调用过就说明该参数不再是死参数")
        self.assertEqual(mods[0]["content"], "另一段文本")

    def test_a_non_dict_pipelines_value_falls_back_to_flat_text(self):
        profile = {"pipelines": "junk", "trading_system": "扁平正文"}
        view = pt.pipeline_view("", profile, "trading_system", max_template_chars=MAX,
                                base_text_resolver=self._resolver(""))
        self.assertIn("扁平正文", " ".join(m["content"] for m in view))

    def test_a_missing_pipeline_and_flat_text_yields_an_empty_view(self):
        view = pt.pipeline_view("", {}, "trading_system", max_template_chars=MAX,
                                base_text_resolver=self._resolver(""))
        self.assertEqual(view, [])


if __name__ == "__main__":
    unittest.main()

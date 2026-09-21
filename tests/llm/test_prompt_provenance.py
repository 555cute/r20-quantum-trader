"""提示词方案库的**来源判定**回归（审计批5 新发现）。

背景（实测事故链）：`update_profile` 走扁平文本路径时无条件
`text_to_modules(text, "legacy")`——即使提交的文本与代码基座**逐字相同**，八个模块也会
全部变成 `legacy`；下一轮 `apply_module_layout` 判定「无 base 模块」就把基座整段前置，
于是线上交易提示词从 6454 字符变成 12910 字符（×2.00，军规/JSON 契约各出现两遍）。
这条链与 P1-2（只提交一条管线把其他管线降级）同族，只是触发路径在扁平文本上。

修复：`scripts/prompt_library.py` 增加「管线 → 代码基座文本」注册表 +
`align_pipeline_sources()`，只按**逐字相同**恢复 `source="base"`（连同基座 id/locked），
真正被改写的模块才留在 legacy；注册表取不到基座时退化为"不改来源"，绝不臆造。
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.prompt_library as pl

# 线上真实方案库路径：测试若指向它，说明沙箱没生效，必须立刻失败
REAL_LIBRARY = (Path(__file__).resolve().parents[2] / "data" / "prompt_library.json").resolve()
BASE_TITLE = "系统角色定位与核心使命"


_READ_SCOPE = None


def setUpModule():
    """显式声明生产读（第二百三十六刀）：
    本文件把**线上**提示词库快照抄进沙箱后断言其 provenance 属性 —— 它的存在目的
    就是「线上那份库是否健康」（含绝对金额/来源标注等），属**有意的线上守卫**。

    只读、不改；声明在此是为了把「依赖线上配置内容」从**静默**变成**可审计**
    （守卫见 `tests/__init__.py`；`R20_TESTS_STRICT_READS=1` 下未声明的读会报错）。
    """
    global _READ_SCOPE
    from tests import allow_real_data_reads
    _READ_SCOPE = allow_real_data_reads()
    _READ_SCOPE.__enter__()


def tearDownModule():
    global _READ_SCOPE
    if _READ_SCOPE is not None:
        _READ_SCOPE.__exit__(None, None, None)
        _READ_SCOPE = None


class _PromptLibraryCase(unittest.TestCase):
    """每个用例都把方案库指到临时副本。

    注意：全局沙箱（tests.config_sandbox.isolate_config）只重定向**导入时已存在**的模块，
    而本文件是懒加载 `prompt_library` 的——所以这里必须显式 patch；万一漏了，
    `tests/__init__.py` 的运维配置写保护也会把写 `data/prompt_library.json` 拦下来。
    """

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="r20-promptlib-"))
        self.addCleanup(shutil.rmtree, self._tmp, True)
        target = self._tmp / "prompt_library.json"
        target.write_text(REAL_LIBRARY.read_text(encoding="utf-8"), encoding="utf-8")
        patcher = mock.patch.object(pl, "LIBRARY_FILE", target)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertNotEqual(Path(pl.LIBRARY_FILE).resolve(), REAL_LIBRARY,
                            "提示词方案库没有被沙箱化——测试会改写生产配置")

    def _profile(self, pid: str = "stable") -> dict:
        return pl.load_library()["profiles"][pid]


class BaseTemplateRegistryTests(unittest.TestCase):
    def test_every_pipeline_has_a_resolvable_base(self):
        missing = [key for key in pl.TEMPLATE_KEYS if not pl.base_template_text(key).strip()]
        self.assertEqual(missing, [], f"这些管线没有可解析的代码基座文本（来源判定会退化）: {missing}")

    def test_base_text_matches_the_code_prompt_it_claims(self):
        import scripts.ai_brain_trader as abt
        self.assertEqual(pl.base_template_text("trading_system"), abt.SYSTEM_PROMPT)

    def test_unknown_pipeline_is_fail_safe(self):
        # 取不到基座 → 原样返回，绝不把模块误标成 base
        modules = pl.text_to_modules("==== 【未知节】 ====\n内容", "legacy")
        self.assertEqual(pl.base_template_text("not_a_pipeline"), "")
        self.assertEqual(pl.align_pipeline_sources(modules, "not_a_pipeline"), modules)


class SourceAlignmentTests(unittest.TestCase):
    def test_verbatim_base_text_stays_base(self):
        text = pl.base_template_text("trading_system")
        aligned = pl.align_pipeline_sources(pl.text_to_modules(text, "legacy"), "trading_system")
        self.assertEqual({m["source"] for m in aligned}, {"base"})
        # id 必须回到基座 id，否则布局匹配又会退化成"整段前置"
        base_ids = {m["id"] for m in pl.text_to_modules(text, "base")}
        self.assertEqual({m["id"] for m in aligned}, base_ids)

    def test_only_edited_modules_become_legacy(self):
        text = pl.base_template_text("trading_system")
        modules = pl.text_to_modules(text, "legacy")
        modules[1]["content"] = modules[1]["content"] + "\n- 临时补充规则"
        aligned = pl.align_pipeline_sources(modules, "trading_system")
        self.assertEqual([m["source"] for m in aligned],
                         ["base", "legacy"] + ["base"] * (len(aligned) - 2))

    def test_same_title_but_different_content_is_not_adopted(self):
        text = pl.base_template_text("trading_system")
        modules = pl.text_to_modules(text, "legacy")
        modules[0]["content"] = "==== 【系统角色定位与核心使命】 ====\n完全改写的正文"
        aligned = pl.align_pipeline_sources(modules, "trading_system")
        self.assertEqual(aligned[0]["source"], "legacy", "同标题但内容不同不得认亲为 base")


class FlatUpdateProvenanceTests(_PromptLibraryCase):
    def test_flat_update_with_verbatim_base_keeps_single_copy(self):
        """回归：把基座原文存回去，线上渲染不得翻倍。"""
        base = pl.base_template_text("trading_system")
        pl.update_profile("stable", {"trading_system": base}, note="回归：逐字回存基座")
        profile = self._profile()
        self.assertEqual({m["source"] for m in profile["pipelines"]["trading_system"]}, {"base"})
        import scripts.ai_brain_trader as abt
        rendered = pl.apply_module_layout(
            abt.SYSTEM_PROMPT, profile, "trading_system", "交易 System")  # 返回渲染后的文本
        self.assertEqual(rendered.count("==== 【系统角色定位与核心使命】 ===="), 1,
                         "基座被前置了两次——提示词翻倍回归")
        self.assertLessEqual(len(rendered), len(abt.SYSTEM_PROMPT) + 16)

    def test_flat_update_with_one_edit_keeps_other_modules_base(self):
        base = pl.base_template_text("trading_system")
        edited = base.replace("5. 反磨损意识", "5. 反磨损意识（自定义：手续费敏感度加倍）")
        self.assertNotEqual(edited, base)
        pl.update_profile("stable", {"trading_system": edited}, note="回归：只改一节")
        profile = self._profile()
        sources = [m["source"] for m in profile["pipelines"]["trading_system"]]
        self.assertIn("base", sources, "未改动的模块不该丢掉 base 来源")
        self.assertEqual(sources.count("legacy"), 1, f"只应有一个模块变 legacy: {sources}")

    def test_flat_update_does_not_touch_other_pipelines(self):
        before = {key: pl.compile_modules(pl.load_library()["profiles"]["stable"]["pipelines"][key])
                  for key in pl.TEMPLATE_KEYS if key != "trading_system"}
        base = pl.base_template_text("trading_system")
        pl.update_profile("stable", {"trading_system": base + "\n- 尾部补充"}, note="回归：单管线")
        after = {key: pl.compile_modules(self._profile()["pipelines"][key]) for key in before}
        self.assertEqual(before, after, "保存一条管线不得改写其他管线")

    def test_revision_rollback_restores_previous_state(self):
        base = pl.base_template_text("trading_system")
        pl.update_profile("stable", {"trading_system": base + "\n- 会被回滚的一段"}, note="回归：待回滚")
        target = pl.load_library()["revisions"][-1]["snapshot"]["trading_system"]
        pl.update_profile("stable", {"trading_system": target}, note="回归：回滚")
        self.assertIn("会被回滚的一段", pl.load_library()["profiles"]["stable"]["trading_system"])

    def test_clean_pipelines_fallback_also_aligns(self):
        """_clean_pipelines 的兜底重建路径也必须恢复 base 来源。"""
        base = pl.base_template_text("trading_system")
        cleaned = pl._clean_pipelines({}, {"pipelines": {}, "trading_system": base})
        self.assertEqual({m["source"] for m in cleaned["trading_system"]}, {"base"})


if __name__ == "__main__":
    unittest.main()

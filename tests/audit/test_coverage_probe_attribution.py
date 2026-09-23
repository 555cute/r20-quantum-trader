"""覆盖率探针的归因语义（第二百五十九刀）。

`tests/coverage_probe.py` 是本仓"逐行核对"的依据，它自己有两条必须成立的性质：

1. **导入期行要被算作已执行** —— 模块级语句与 `def`/`class` 行在 import 时必然执行，
   而探针只在跑用例期间 `settrace`（模块常已被 `tests/__init__.py` 提前导入）
   ⇒ 不自证就会给每个文件留一个**假象地板**（实测 `binance.py` 因此凭空多 43 行"未命中"）。
2. **没走到的分支不得被豁免** —— 模块级代码里也有真分支（如 `except ImportError:` 兜底），
   静态豁免导入期行会把**真实缺口**一起藏掉。故修法是**真的重新执行导入**
   （`traced_import`），而不是按 AST 类型豁免。

本门测的正是这两条：用临时模块造一个"走到的顶层语句 + 一个**没走到**的 except 兜底"，
检查跟踪结果里前者命中、后者不命中。
"""

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests import coverage_probe


class TracedImportAttributionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="r20_probe_fixture_")
        self.name = "r20_probe_fixture_mod"
        self.path = Path(self.dir, f"{self.name}.py")
        # 第 1-7 行：走到；第 8-12 行：except 兜底（**不会**走到）
        self.path.write_text(textwrap.dedent("""
            import os

            VALUE = 1

            def touched():
                return VALUE

            if os.environ.get("R20_PROBE_FIXTURE_ON") == "1":   # 真分支，但本轮**不走**
                NEVER_EXECUTED = "不应被执行"
        """).lstrip(), encoding="utf-8")
        # ⚠️ 夹具第一版我用的是 `try: from 不存在的模块 import … except ImportError:` —— 那个
        # `except` **真的会执行**（导入失败就进兜底）⇒ 断言"未走到的分支不得命中"必然误报。
        # 换成**条件为假**的真分支，才是"没走到"的干净样本。
        sys.path.insert(0, self.dir)
        self.addCleanup(lambda: sys.path.remove(self.dir))
        self.addCleanup(lambda: sys.modules.pop(self.name, None))

    def _trace_import(self):
        seen = set()
        target = str(self.path)

        def tracer(frame, event, arg):
            if event == "line" and frame.f_code.co_filename == target:
                seen.add(frame.f_lineno)
            return tracer

        sys.settrace(tracer)
        try:
            # ⚠️ 传**相对模块路径**：`traced_import` 由路径推模块名（`/`→`.`）。
            # 绝对路径会推出 `.tmp.xxx.mod` 这种名字 ⇒ 相对导入报错。
            # （真实调用方传的就是 `r20_backend/exchanges/binance.py` 这类仓库相对路径。）
            failures = coverage_probe.traced_import([f"{self.name}.py"])
        finally:
            sys.settrace(None)
        return seen, failures

    def test_import_time_lines_of_taken_code_are_executed(self):
        seen, failures = self._trace_import()
        self.assertEqual(failures, [], "临时模块应当能正常重导")
        self.assertGreater(len(seen), 0, "导入期代码必须被跟踪到（否则假象地板仍在）")

    def test_untaken_module_level_branch_is_not_exempted(self):
        """★ 关键反例：`except ImportError` 兜底**没走到** ⇒ 那些行不得被算作已执行。"""
        seen, _ = self._trace_import()
        src = self.path.read_text(encoding="utf-8").splitlines()
        untaken_body = {i for i, line in enumerate(src, start=1) if "NEVER_EXECUTED" in line}
        taken_guard = {i for i, line in enumerate(src, start=1) if "R20_PROBE_FIXTURE_ON" in line}
        self.assertTrue(untaken_body, "夹具自身要含未走到的分支体")
        self.assertFalse(untaken_body & seen,
                         f"未走到的分支被当成已执行了：{sorted(untaken_body & seen)}")
        self.assertTrue(taken_guard & seen,
                        "分支的**判断行**本身是走到的（否则这说明跟踪根本没生效）")

    def test_executable_lines_counts_definitions_so_they_need_the_reimport(self):
        """`executable_lines` 把 `def` 行算进分母 ⇒ 正因如此才需要 `traced_import` 自证。"""
        lines = coverage_probe.executable_lines(self.path)
        def_line = next(i for i, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(),
                                                    start=1) if line.startswith("def touched"))
        self.assertIn(def_line, lines, "`def` 行在分母里（也就必须有办法把它记成命中）")

    def test_multiline_if_condition_attributes_to_expression_lineno(self):
        """★ 第三百三十八刀：CPython 3.11 编译纪律 —— 当写成 `if (\n    expr` 时，
        字节码 line 事件只在表达式首行（node.test.lineno）产生，
        纯 `if (` 行没有任何指令。分母若记 `if (` 会产生不可被任何运行消除的幽灵缺口。
        """
        temp_file = Path(self.dir, "multiline_if_fixture.py")
        temp_file.write_text(textwrap.dedent("""
            x = 1
            if (
                x == 1
            ):
                y = 2
        """).lstrip(), encoding="utf-8")
        lines = coverage_probe.executable_lines(temp_file)
        self.assertNotIn(2, lines, "纯 `if (` 行无字节码指令，不得计入分母制造幽灵缺口")
        self.assertIn(3, lines, "表达式首行必须计入分母")


if __name__ == "__main__":
    unittest.main()


class StickyTracerTest(unittest.TestCase):
    """★ 第三百三十四刀：外层 tracer **不许**被测试的 `settrace(None)` 拆掉。

    事故：`tests/audit/test_coverage_probe_attribution.py` 自己的 `finally:` 就是一句
    `sys.settrace(None)`。只要探针的范围里包含本文件，它就在测试内部拆掉外层 tracer
    ⇒ 其后所有文件记 0 行，整份基线被**静默截断**。
    症状是"**超集范围报出比子集更少的命中**"—— 逻辑上不可能，见者应怀疑探针本身。
    """

    def setUp(self):
        self._saved = sys.settrace
        self.addCleanup(lambda: setattr(sys, "settrace", self._saved))

    def _outer(self):
        def outer(frame, event, arg):      # pragma: no cover - 由 gettrace 观测
            return outer
        return outer

    def test_a_nested_settrace_none_rearms_the_outer_tracer(self):
        outer = self._outer()
        restore = coverage_probe.arm_sticky_tracer(outer)
        self.addCleanup(restore)
        self.assertIs(sys.gettrace(), outer)
        # ↓ 就是那条例外用例做的事
        sys.settrace(None)
        self.assertIs(sys.gettrace(), outer,
                      "外层 tracer 被拆掉 ⇒ 整份基线会被静默截断")

    def test_a_nested_foreign_tracer_is_passed_through(self):
        outer = self._outer()

        def inner(frame, event, arg):      # pragma: no cover - 由 gettrace 观测
            return inner
        restore = coverage_probe.arm_sticky_tracer(outer)
        self.addCleanup(restore)
        sys.settrace(inner)
        self.assertIs(sys.gettrace(), inner, "调用方自己的 tracer 必须能装上")
        sys.settrace(None)
        self.assertIs(sys.gettrace(), outer, "收起自己的 tracer 后应装回外层")

    def test_restore_puts_the_original_settrace_back(self):
        original = sys.settrace
        restore = coverage_probe.arm_sticky_tracer(self._outer())
        self.assertIsNot(sys.settrace, original)
        restore()
        self.assertIs(sys.settrace, original)
        sys.settrace(None)
        self.assertIsNone(sys.gettrace(), "还原后 `settrace(None)` 必须恢复原语义")

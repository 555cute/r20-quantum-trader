"""探针分母不得包含 `global` / `nonlocal` 声明行（第一百九十四刀）。

实测：`global X` 语句**不产生行事件** ⇒ 若把它算进分母，就永远显示"未命中"，
变成与 `def` 行同类的**假象缺口**。本用例把这条钉进工具。
"""

import ast
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.coverage_probe import executable_lines  # noqa: E402


FIXTURE = '''
COUNTER = 0


def bump(step):
    global COUNTER
    COUNTER = COUNTER + step
    return COUNTER


def outer():
    value = 1

    def inner():
        nonlocal value
        value = value + 1
    inner()
    return value
'''


class GlobalsExcludedTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp()) / "probe_fixture_globals.py"
        tmp.write_text(textwrap.dedent(FIXTURE).lstrip(), encoding="utf-8")
        self.tmp = tmp
        self.lines = executable_lines(tmp)

    def test_global_and_nonlocal_declarations_are_not_counted(self):
        src = self.tmp.read_text(encoding="utf-8").splitlines()
        decl = [i + 1 for i, line in enumerate(src)
                if line.strip().startswith(("global ", "nonlocal "))]
        self.assertEqual(len(decl), 2, "夹具里应有一处 global 与一处 nonlocal")
        for line in decl:
            self.assertNotIn(line, self.lines,
                             f"{line} 行是声明、不产生行事件，不得计入分母")

    def test_real_statements_around_them_are_still_counted(self):
        src = self.tmp.read_text(encoding="utf-8").splitlines()
        idx = next(i for i, line in enumerate(src) if line.strip() == "global COUNTER")
        self.assertIn(idx + 2, self.lines, "紧邻 global 的真实赋值行必须仍在分母里")
        self.assertIn(1, self.lines, "模块级语句（导入期靠 traced_import 补）仍在分母里")

    def test_teeth_fixture_is_actually_parsable_and_covers_both(self):
        tree = ast.parse(self.tmp.read_text(encoding="utf-8"))
        kinds = {type(n).__name__ for n in ast.walk(tree)
                 if isinstance(n, (ast.Global, ast.Nonlocal))}
        self.assertEqual(kinds, {"Global", "Nonlocal"}, "两种声明都要有，否则用例是空的")


if __name__ == "__main__":
    unittest.main()

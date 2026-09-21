"""覆盖面不许"只在有数据时才存在"（第二百零六刀）。

## 这一刀查的三件事（三条假设，**全部被推翻** —— 如实记录）

1. **整文件都可条件跳过**（干净检出下该文件贡献 0 验证）⇒ 实测 **0 个文件**；
2. **断言全在 if/except 里**（条件不成立就零断言通过）⇒ 初版报 432 条，逐类看全是**误报**：
   - `for x in FIXED: assert` —— 循环体**照常执行**断言（我第一版把 For/While 也当条件，已剔）；
   - `for node in ast.walk(tree): if isinstance(node, ast.Import): assertNotIn(...)` ——
     这是"**扫违规**"惯用法：`if` 用来**挑出违规**，「没违规」本身就是通过，不是漏测；
   ⇒ 剔除后剩 41 条，仍未发现真问题；
3. **数据存在性守卫下静默通过**（`if os.path.exists(data): assert…` 且 `if` **无 else**）——
   这才是真正危险的那类（文件不在就零断言通过）⇒ 实测 **0 条**。

⇒ 本门不是"修问题"，而是把**可判定的判据**钉死，并把上面这些**误报类**写进文件头，
免得下一轮又造一条"看着能抓其实全是误报"的规则。

## 判据（两条，都能静态判定）

- **R1**：任何测试文件里，`test_*` 方法**不能全部**含跳过调用（否则干净检出下该文件贡献 0 验证）；
- **R2**：任何 `test_*` 方法的断言**不能全部**落在"数据/环境存在性"守卫（`.exists()`/`getenv`/
  `environ.get`/`is_file()`/`is_dir()`）之下且该 `if` 无 `else`
  （有 `else` 通常意味着"要么断言要么披露"，属合法）。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SKIP_CALLS = {"skipTest", "skip_if_offline_suite", "skipIf", "skipUnless"}
#: "数据/环境存在性"守卫（R2 只认这类守卫；**不**认 `isinstance(node, ast.Import)` 那种"扫违规"守卫）
DATA_GUARD = re.compile(r"\.exists\(\)|os\.path\.exists|is_file\(\)|is_dir\(\)|getenv|environ\.get")

#: 允许"整文件可跳过"的例外（附理由）。当前为空。
ALL_FILE_SKIPPABLE: dict[str, str] = {}


def _parents(tree: ast.AST) -> dict:
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _is_assertion(node: ast.AST) -> bool:
    if isinstance(node, ast.Assert):
        return True
    if isinstance(node, ast.Call):
        name = ast.unparse(node.func).split(".")[-1]
        return name.startswith("assert") or name == "fail"
    return False


def _test_methods(tree: ast.AST) -> list:
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]


def _has_skip(method: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and ast.unparse(n.func).split(".")[-1] in SKIP_CALLS
               for n in ast.walk(method))


def all_tests_skippable(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    methods = _test_methods(tree)
    return bool(methods) and all(_has_skip(m) for m in methods)


def data_guarded_silent_pass(source: str) -> list:
    """断言全在"数据存在性"守卫下、且 `if` 无 else 的用例名。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    parents = _parents(tree)
    flagged = []
    for method in _test_methods(tree):
        assertions = [n for n in ast.walk(method) if _is_assertion(n)]
        if not assertions:
            continue
        unconditional = False
        data_guard_without_else = False
        for assertion in assertions:
            node, guard = assertion, None
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.If, ast.ExceptHandler)):
                    guard = node
                    break
                if isinstance(node, ast.FunctionDef):
                    break
            if guard is None:
                unconditional = True
                break
            if isinstance(guard, ast.If) and DATA_GUARD.search(ast.unparse(guard.test)) \
                    and not guard.orelse:
                data_guard_without_else = True
        if not unconditional and data_guard_without_else:
            flagged.append(method.name)
    return flagged


class CoverageNeverDependsOnDataTest(unittest.TestCase):
    def _files(self):
        for path in sorted((ROOT / "tests").rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path

    def test_no_file_is_entirely_skippable(self):
        bad = []
        for path in self._files():
            rel = str(path.relative_to(ROOT))
            if rel in ALL_FILE_SKIPPABLE:
                continue
            if all_tests_skippable(path.read_text(encoding="utf-8")):
                bad.append(rel)
        self.assertEqual(bad, [], f"这些文件里每条用例都可跳过 ⇒ 干净检出下贡献 0 验证：{bad}")

    def test_no_data_guarded_silent_pass(self):
        bad = {}
        for path in self._files():
            hits = data_guarded_silent_pass(path.read_text(encoding="utf-8"))
            if hits:
                bad[str(path.relative_to(ROOT))] = hits
        self.assertEqual(bad, {}, "断言全在数据存在性守卫下且无 else ⇒ 文件不在就零断言通过："
                                  f"{bad}")

    def test_scan_is_not_vacuous(self):
        files = list(self._files())
        self.assertGreaterEqual(len(files), 200, f"只扫到 {len(files)} 个测试文件")
        methods = skips = 0
        for path in files:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for method in _test_methods(tree):
                methods += 1
                skips += 1 if _has_skip(method) else 0
        self.assertGreaterEqual(methods, 3000, f"只扫到 {methods} 条用例 ⇒ 判据失效")
        self.assertGreaterEqual(skips, 20, f"只发现 {skips} 条含跳过的用例 ⇒ skip 扫描失效")

    def test_teeth_on_both_rules(self):
        skippable = ("class T(unittest.TestCase):\n"
                     "    def test_a(self):\n        self.skipTest('x')\n"
                     "    def test_b(self):\n        self.skipTest('y')\n")
        self.assertTrue(all_tests_skippable(skippable), "整文件可跳过没被抓到")
        partly = ("class T(unittest.TestCase):\n"
                  "    def test_a(self):\n        self.skipTest('x')\n"
                  "    def test_b(self):\n        self.assertEqual(1, 1)\n")
        self.assertFalse(all_tests_skippable(partly), "还有用例照跑的文件被误判")
        guarded = ("class T(unittest.TestCase):\n"
                   "    def test_c(self):\n"
                   "        if os.path.exists('data/x.json'):\n"
                   "            self.assertEqual(1, 1)\n")
        self.assertEqual(data_guarded_silent_pass(guarded), ["test_c"], "数据守卫静默通过没被抓到")

    def test_no_false_positive_on_legitimate_idioms(self):
        """本刀剔掉的三类误报形态（全部钉成"不得误杀"用例）。"""
        loops = ("class T(unittest.TestCase):\n"
                 "    def test_loop(self):\n"
                 "        for x in (1, 2):\n"
                 "            self.assertEqual(x, x)\n")
        self.assertEqual(data_guarded_silent_pass(loops), [], "循环体断言被误判为条件断言")
        violation_scan = ("class T(unittest.TestCase):\n"
                          "    def test_scan(self):\n"
                          "        for node in ast.walk(tree):\n"
                          "            if isinstance(node, ast.Import):\n"
                          "                self.assertNotIn('x', ast.unparse(node))\n")
        self.assertEqual(data_guarded_silent_pass(violation_scan), [],
                         "扫违规惯用法（if 用来挑违规）被误判")
        with_else = ("class T(unittest.TestCase):\n"
                     "    def test_disclosed(self):\n"
                     "        if os.path.exists('data/x.json'):\n"
                     "            self.assertEqual(1, 1)\n"
                     "        else:\n"
                     "            self.skipTest('无数据（已披露）')\n")
        self.assertEqual(data_guarded_silent_pass(with_else), [],
                         "带 else 披露的守卫被误判（有披露就不是静默通过）")

    def test_exemptions_have_reasons(self):
        for rel, reason in ALL_FILE_SKIPPABLE.items():
            self.assertTrue((ROOT / rel).exists(), f"登记表过期：{rel} 不存在")
            self.assertGreaterEqual(len(str(reason).strip()), 15, f"{rel} 的理由太短")


if __name__ == "__main__":
    unittest.main()

"""用例不许"**看着在测、其实永远绿**"（第二百零四刀）。

## 两种"跑了等于没跑"的形态

1. **吞掉异常**：用例里 `try: … except Exception: pass/return/continue` ——
   被 try 包住的断言/调用一旦抛错就被吃掉，用例照样绿。
   （注意：`except` 里**还有别的语句**、或 except 的是具体异常类型并有注释说明，
   属于有意过滤 ⇒ 必须登记理由，不许默默存在。）
2. **整条用例没有任何断言证据**：既没有 `assert*`/`assert`/`fail`，
   也没有调用**本文件里带断言的 helper**（本仓常见的 `self._assert_rows(...)`、`self._check(...)`），
   名字也不属于"必须不抛错"那一类（`never_raises`/`do_not_crash`/`non_fatal`/`kill_switch`/
   `..._import` 等 —— 这类用例的断言**就是"不抛异常"本身**）。

## 本刀实测（结论是**假设被推翻**）

- 吞异常：全仓 **1 处**，且是**有意的**（`test_sync_web_data_single_read.py` 要在被拦下的写盘
  异常之后继续断言"每个数据文件恰好读一次"，注释写明"属预期"）⇒ 登记而非改造；
- 无断言用例：初版判据报 15 条，逐条看后发现全是**误报** —— 断言在 helper 里
  （`_assert_rows`/`_check`）或属"必须不抛错"类。**精化判据后剩 0 条**。

⇒ 本门的作用不是"修一堆问题"，而是**把这两种形态钉死**：将来新写的用例若吞异常或完全没有
断言证据，必须在登记表里写明理由才能过。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: "必须不抛错"型用例名（其断言即"不抛异常"）
MUST_NOT_RAISE = re.compile(
    r"never_raises|do_not_crash|non_fatal|fail_soft|failsoft|still_work|kill_switch"
    r"|_import|_never_|no_raise|does_not_raise|missing_sections|not_crash"
)

#: 逐条登记（附理由）
SWALLOW_ALLOWLIST = {
    "tests/ui/test_sync_web_data_single_read.py:test_generate_reads_each_data_file_exactly_once":
        "有意过滤：本用例要数「每个数据文件被读几次」，被拦下的写盘异常（PermissionError 及其它 "
        "fail-closed）属预期，注释已写明；且 try 之后仍有真实断言（读次数、绝不写生产文件）",
}
NO_ASSERT_ALLOWLIST: dict[str, str] = {}


def _swallows(fn: ast.FunctionDef) -> list:
    """用例里"吃掉异常"的位置（try/except Exception: pass/return/continue）。"""
    hits = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if handler.type is None:
                type_text = ""
            else:
                type_text = ast.unparse(handler.type)
                if not any(w in type_text for w in ("Exception", "BaseException")):
                    continue
            body = [n for n in handler.body
                    if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
            if body and all(isinstance(n, (ast.Pass, ast.Return, ast.Continue)) for n in body):
                hits.append(node.lineno)
    return hits


def _has_assertion(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func).split(".")[-1]
            if name.startswith("assert") or name in ("fail", "skipTest", "raises", "subTest"):
                return True
    return False


def _test_methods(tree: ast.AST) -> list:
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]


def vacuous_shapes(source: str) -> dict:
    """返回 {'swallow': [(用例名, 行号)], 'no_assert': [(用例名, 行号)]}。"""
    out = {"swallow": [], "no_assert": []}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    asserting_helpers = {n.name for n in ast.walk(tree)
                         if isinstance(n, ast.FunctionDef) and _has_assertion(n)}
    for fn in _test_methods(tree):
        for lineno in _swallows(fn):
            out["swallow"].append((fn.name, lineno))
        if _has_assertion(fn):
            continue
        calls_helper = any(isinstance(n, ast.Call)
                           and ast.unparse(n.func).split(".")[-1] in asserting_helpers
                           for n in ast.walk(fn))
        if calls_helper or MUST_NOT_RAISE.search(fn.name):
            continue
        out["no_assert"].append((fn.name, fn.lineno))
    return out


class TestsCannotPassVacuouslyTest(unittest.TestCase):
    def test_no_unregistered_swallowing(self):
        bad = {}
        for path in sorted((ROOT / "tests").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(ROOT))
            for name, lineno in vacuous_shapes(path.read_text(encoding="utf-8"))["swallow"]:
                if f"{rel}:{name}" in SWALLOW_ALLOWLIST:
                    continue
                bad.setdefault(rel, []).append(f"{name} L{lineno}")
        self.assertEqual(bad, {}, "用例里吞掉了异常（断言抛错也不会红；如属有意请登记理由）："
                                  f"{bad}")

    def test_no_unregistered_assertion_free_tests(self):
        bad = {}
        for path in sorted((ROOT / "tests").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(ROOT))
            for name, lineno in vacuous_shapes(path.read_text(encoding="utf-8"))["no_assert"]:
                if f"{rel}:{name}" in NO_ASSERT_ALLOWLIST:
                    continue
                bad.setdefault(rel, []).append(f"{name} L{lineno}")
        self.assertEqual(bad, {}, "用例没有任何断言证据（跑了等于没跑；如属'必须不抛错'型请登记）："
                                  f"{bad}")

    def test_scan_is_not_vacuous(self):
        files = [p for p in (ROOT / "tests").rglob("*.py") if "__pycache__" not in p.parts]
        self.assertGreaterEqual(len(files), 200, f"只扫到 {len(files)} 个测试文件")
        methods = 0
        for path in files:
            try:
                methods += len(_test_methods(ast.parse(path.read_text(encoding="utf-8"))))
            except SyntaxError:
                continue
        self.assertGreaterEqual(methods, 3000, f"只扫到 {methods} 条用例 ⇒ 判据失效")

    def test_teeth_on_both_shapes(self):
        swallow = '''
class T(unittest.TestCase):
    def test_x(self):
        try:
            self.assertEqual(f(), 1)
        except Exception:
            pass
'''
        self.assertEqual(len(vacuous_shapes(swallow)["swallow"]), 1, "吞异常没被抓到")
        no_assert = '''
class T(unittest.TestCase):
    def test_y(self):
        do_something()
'''
        self.assertEqual(len(vacuous_shapes(no_assert)["no_assert"]), 1, "无断言用例没被抓到")

    def test_no_false_positive_on_legitimate_shapes(self):
        """合法形态不得误杀（本刀三次判据精化的成果，全部钉成用例）。"""
        ok = '''
class T(unittest.TestCase):
    def _check(self, row):
        self.assertEqual(row["a"], 1)

    def test_delegates_to_helper(self):
        self._check(f())

    def test_never_raises_on_garbage(self):
        parse("garbage")

    def test_imports(self):
        import_live_entry()

    def test_filters_expected_error(self):
        try:
            self.assertEqual(f(), 1)
        except PermissionError:
            pass
'''
        shapes = vacuous_shapes(ok)
        self.assertEqual(shapes["swallow"], [], "具体异常类型的有意过滤被误判为吞异常")
        self.assertEqual(shapes["no_assert"], [],
                         f"helper 委派/必须不抛错型被误判为无断言：{shapes['no_assert']}")

    def test_allowlist_entries_have_reasons(self):
        for key, reason in list(SWALLOW_ALLOWLIST.items()) + list(NO_ASSERT_ALLOWLIST.items()):
            path, _, name = key.rpartition(":")
            self.assertTrue((ROOT / path).exists(), f"登记表过期：{path} 不存在")
            self.assertGreaterEqual(len(str(reason).strip()), 20, f"{key} 的理由太短")


if __name__ == "__main__":
    unittest.main()

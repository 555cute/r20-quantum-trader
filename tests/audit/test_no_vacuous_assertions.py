"""不许有**恒真断言**（第二百零三刀）。

## 这一刀查的是什么

上一刀我自己写出了一条假牙齿：
`self.assertIn("other_place", " ".join(offenders) + " other_place")` —— 把要断言的关键字
**自己拼进了被检查的字符串** ⇒ 恒真。假断言比没有断言更坏：它看着像护栏，实际什么都不拦。
本轮把它当**一类**问题全仓扫，实测抓到 4 处真恒真（3 个文件）+ 1 处误报（见下）。

## 三种判据（都是"看着像断言、实际恒真"的形态）

1. **两侧同一个纯表达式**：`assertEqual(TODAY, TODAY)`、`assertIn(x, x)` …
   —— 只在两侧**都没有函数调用**时才算恒真（`assertEqual(f(X), f(X))` 是**纯度/确定性**检查，
   是合法的，不能误杀）。
2. **`assertTrue(True)` / `assertFalse(False)`**（含 `assertTrue("非空串")` 这类常量真值）。
3. **字面量自证**：`assertIn("A", "…A…")` —— 被检查的表达式里**非调用部分**的字符串常量
   已经包含了要断言的子串 ⇒ 无论被测代码怎样都成立。

## 已知的误报面（如实登记）

判据 3 是**启发式**：若字面量只出现在**下标键名**里（`assertNotIn("entry", golden["plan_entry_guard"])`），
断言本身是在真值上判的，属误报。这类必须进 `ALLOWLIST` 并写理由，不许默默放过。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 双值断言（这些方法的第二个参数是**值**；assertTrue/False 的第二个参数是 msg，不能按值比）
TWO_VALUE_ASSERTS = {
    "assertEqual", "assertNotEqual", "assertIn", "assertNotIn", "assertIs", "assertIsNot",
    "assertGreater", "assertGreaterEqual", "assertLess", "assertLessEqual",
    "assertAlmostEqual", "assertNotAlmostEqual",
}

#: 逐条登记已知误报（附理由）
ALLOWLIST = {
    "tests/ui/test_frontend_chart_overlays.py:313":
        "判据 3 误报：`assertNotIn(\"entry\", self.golden[\"plan_entry_guard\"])` 里的 \"entry\" "
        "只出现在**下标键名** `plan_entry_guard` 中，断言是在真 golden 值（字符串）上判的",
}


def _has_call(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) for n in ast.walk(node))


def _strings_outside_calls(node: ast.AST) -> list:
    """表达式里**不在任何调用之内**的字符串常量（自证的来源）。"""
    out = []
    calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)]
    for n in ast.walk(node):
        if not (isinstance(n, ast.Constant) and isinstance(n.value, str)):
            continue
        if any(any(x is n for x in ast.walk(c)) for c in calls):
            continue
        out.append(n.value)
    return out


def vacuous_assertions(source: str) -> list:
    """返回 [(行号, 说明)] —— 该源码里的恒真断言。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func).split(".")[-1]
        args = node.args
        if name in TWO_VALUE_ASSERTS and len(args) >= 2 \
                and not _has_call(args[0]) and not _has_call(args[1]) \
                and ast.unparse(args[0]) == ast.unparse(args[1]):
            found.append((node.lineno, f"{name} 两侧是同一个纯表达式 ⇒ 恒真："
                                       f"{ast.unparse(args[0])[:60]}"))
        if name in ("assertTrue", "assertFalse") and args \
                and isinstance(args[0], ast.Constant) \
                and bool(args[0].value) == (name == "assertTrue"):
            found.append((node.lineno, f"{name}({args[0].value!r}) ⇒ 恒真"))
        if name in ("assertIn", "assertNotIn") and len(args) >= 2 \
                and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str) \
                and args[0].value:
            if any(args[0].value in s for s in _strings_outside_calls(args[1])):
                found.append((node.lineno, f"{name}({args[0].value!r}, …被检查表达式的**非调用部分**"
                                           f"已含该字面量…) ⇒ 恒真"))
    return found


class NoVacuousAssertionsTest(unittest.TestCase):
    def test_no_vacuous_assertion_in_the_suite(self):
        offenders = {}
        for path in sorted((ROOT / "tests").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(ROOT))
            for lineno, why in vacuous_assertions(path.read_text(encoding="utf-8")):
                if f"{rel}:{lineno}" in ALLOWLIST:
                    continue
                offenders.setdefault(rel, []).append(f"L{lineno}: {why}")
        self.assertEqual(offenders, {}, "恒真断言（看着像护栏、其实什么都不拦）："
                                        f"{offenders}\n（确属误报请登记 ALLOWLIST 并写理由）")

    def test_scan_is_not_vacuous(self):
        files = [p for p in (ROOT / "tests").rglob("*.py") if "__pycache__" not in p.parts]
        self.assertGreaterEqual(len(files), 200, f"只扫到 {len(files)} 个测试文件 ⇒ 扫描失效")
        # 判据必须真的会在合成样本上翻红（下一条用例逐一验证）；这里只查基本规模
        self.assertGreaterEqual(len(_strings_outside_calls(ast.parse('f("A") + "B"').body[0].value)), 1,
                                "非调用部分字符串的提取失效")

    def test_teeth_on_each_pattern(self):
        """三种判据各自都要能咬。"""
        cases = {
            "两侧同表达式": 'self.assertEqual(TODAY, TODAY)',
            "常量真值": 'self.assertTrue(True)',
            "字面量自证": 'self.assertIn("other_place", " ".join(x) + " other_place")',
        }
        for label, src in cases.items():
            self.assertTrue(vacuous_assertions(src), f"判据「{label}」没有咬到合成样本：{src}")

    def test_no_false_positive_on_legitimate_checks(self):
        """合法形态不得误杀：纯度检查、真值断言、被测函数参数里出现同一字面量。"""
        ok = [
            'self.assertEqual(f(X), f(X))',                       # 确定性/纯度检查
            'self.assertIn("{{account_balance}}", render("BASE", "{{account_balance}}"))',
            'self.assertTrue(res["ok"], res.get("detail"))',
            'self.assertEqual(ast.unparse(a), ast.unparse(b))',
        ]
        for src in ok:
            self.assertEqual(vacuous_assertions(src), [], f"误杀合法断言：{src}")

    def test_allowlist_entries_have_reasons(self):
        for key, reason in ALLOWLIST.items():
            path, _, lineno = key.rpartition(":")
            self.assertTrue((ROOT / path).exists(), f"登记表过期：{path} 不存在")
            self.assertGreaterEqual(len(str(reason).strip()), 15, f"{key} 的理由太短")


if __name__ == "__main__":
    unittest.main()

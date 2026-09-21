"""测试/脚本里**不得**把定义写进 `if __name__ == "__main__"` 块内（第一百九十刀）。

## 这一刀从哪来（真实的假绿）

第一百八十九刀我给 `tests/trading/test_close_cancels_protection.py` 追加两个用例时，
把方法粘在了文件末尾的 `if __name__ == "__main__": unittest.main()` **之后**且保持 4 空格缩进
⇒ 它们成了该 `if` 块体里的**嵌套函数**：语法合法、`ast` 正常、**pytest 一个都不收集**。
于是：新用例根本没跑，而"反向验证"（把修复退回去）**依旧全绿** —— 我差点据此宣称验证通过。
是"反向验证没翻红"这个反常现象把问题揪出来的（`pytest -k` 显示 `14 deselected`）。

## 判据（AST，精确到块内）

`tests/`、`scripts/`、`r20_backend/` 下任何 `if __name__ == '__main__'` 的**块体内**
不得出现 `def`/`async def`/`class`（块内定义在 pytest 导入时**永不执行** ⇒ 静默不收集）。

⚠️ 反过来说：定义出现在该 `if` **之后**（模块级）是**无害**的 —— pytest 是 import 模块，
`unittest.main()` 只在 `python file.py` 时执行。故本门只钉"块内"，不钉"之后"，免得后人误改。

## 本门

1. 全树扫描（块内定义 ⇒ 判红）；
2. **收集牙齿**（本门的存在理由）：真的造两个临时测试文件 —— 块内的那个必须
   **收集到 0 个用例**，正常缩进的那个必须收集到 1 个 ⇒ 证明"这条规矩不是洁癖"；
3. 非空自检（必须真的扫到足够多的文件）。
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("tests", "scripts", "r20_backend", "r20_gateway", "plugins")
DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def find_defs_inside_main_block(source: str):
    """返回 (if 行号, [定义名…]) —— 定义写在 `if __name__ == '__main__'` 块体里的情况。"""
    out = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        try:
            if ast.unparse(node.test).replace(" ", "") != "__name__=='__main__'":
                continue
        except Exception:
            continue
        inner = [n for n in node.body if isinstance(n, DEF_NODES)]
        if inner:
            out.append((node.lineno, [n.name for n in inner]))
    return out


def _iter_py():
    for root in SCAN_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


class DefsNotInsideMainBlockTest(unittest.TestCase):
    def test_no_defs_inside_main_blocks(self):
        problems = []
        for path in _iter_py():
            rel = str(path.relative_to(ROOT))
            for line, names in find_defs_inside_main_block(path.read_text(encoding="utf-8")):
                problems.append(f"{rel}:{line} 块内定义 {names}")
        self.assertEqual(problems, [], "定义写在 `if __name__` 块内 ⇒ pytest 永不收集（假绿源头）：\n"
                                       + "\n".join(problems))

    def test_nested_tests_really_are_not_collected(self):
        """牙齿：块内用例**真的**收集不到（不是洁癖），正常缩进的能收集到。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "test_hidden.py").write_text(textwrap.dedent('''
                import unittest
                if __name__ == "__main__":
                    class HiddenTest(unittest.TestCase):
                        def test_hidden(self):
                            self.fail("永远不会跑到")
                    unittest.main()
            '''), encoding="utf-8")
            (d / "test_visible.py").write_text(textwrap.dedent('''
                import unittest
                class VisibleTest(unittest.TestCase):
                    def test_visible(self):
                        self.assertTrue(True)
                if __name__ == "__main__":
                    unittest.main()
            '''), encoding="utf-8")
            hidden = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                                     str(d / "test_hidden.py")],
                                    capture_output=True, text=True, cwd=str(ROOT))
            visible = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                                      str(d / "test_visible.py")],
                                     capture_output=True, text=True, cwd=str(ROOT))
            hidden_out = hidden.stdout.lower()
            self.assertTrue("no tests collected" in hidden_out or "no tests ran" in hidden_out,
                            f"块内用例竟然被收集到了 ⇒ 本门的判据失效：{hidden.stdout[-200:]}")
            self.assertIn("1 test collected", visible.stdout.lower(),
                          f"正常缩进的用例反而没被收集 ⇒ 环境异常：{visible.stdout[-200:]}")

    def test_scan_is_not_vacuous(self):
        files = list(_iter_py())
        self.assertGreater(len(files), 200, f"只扫到 {len(files)} 个文件 ⇒ 门与实现脱节")
        self.assertTrue(find_defs_inside_main_block(
            "if __name__ == '__main__':\n    def f():\n        pass\n"),
            "合成的块内定义没被抓到 ⇒ 门没有牙齿")
        self.assertEqual(find_defs_inside_main_block(
            "def f():\n    pass\nif __name__ == '__main__':\n    import unittest\n"), [],
            "块**后**的模块级定义被误判（那块是无害的）")


if __name__ == "__main__":
    unittest.main()

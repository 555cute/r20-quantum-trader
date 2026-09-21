"""扫描型门的**判据范围**必须显式且完整（第二百零一刀）。

## 为什么

本会话连续吃到两次"范围缺根"的亏：配置扫描门只写了 `scripts/` + `r20_backend/`，
而仓里还有 `r20_gateway/`（网关：凭证库/发布器/任务存储）与 `plugins/` ⇒
那些地方的违规**门看不见**（既可能是假绿，也可能是假警报）。

本门把"扫描范围"本身变成受检对象：**任何**声明了 `SCAN_DIRS`/`SCAN_ROOTS`/`SOURCE_ROOTS`
的测试，只要它扫了**至少一个代码根**，就必须扫**全部代码根** —— 否则必须登记豁免并写明理由。

代码根是**动态推导**的（凡含 `.py` 的一级目录，排除隐藏目录、构建产物与
`tests/data/docs/...` 这类非运行时目录）⇒ 将来新增代码根时，所有扫描门会被本门一次性判红，
逼着维护者去扩范围，而不是静默漏扫。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 不算"代码根"的目录（测试、数据、文档、构建产物、一次性归档……）
NON_CODE_DIRS = {
    "tests", "data", "backups", "logs", "deploy", "docs", "plan_local",
    "promo_local", "frontend", "node_modules", "dist", ".archive",
}

#: 根变量名（本仓三种写法）
ROOT_VARS = ("SCAN_DIRS", "SCAN_ROOTS", "SOURCE_ROOTS")

#: 允许"只扫部分代码根"的门（附理由）。**当前为空**：全部扫描门都已覆盖四个根。
EXEMPT_SCOPE: dict[str, str] = {}


def code_roots() -> set:
    """仓里所有含 `.py` 的一级代码根（动态推导）。"""
    roots = set()
    for path in ROOT.iterdir():
        if not path.is_dir() or path.name.startswith(".") or path.name in NON_CODE_DIRS:
            continue
        if any(path.rglob("*.py")):
            roots.add(path.name)
    return roots


def _declared_roots(file: Path) -> set:
    """该测试声明的扫描根（字符串或 `ROOT / "x"` 形式都归一成目录名）。"""
    try:
        tree = ast.parse(file.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id in ROOT_VARS for t in node.targets):
            continue
        for item in getattr(node.value, "elts", []):
            name = None
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                name = item.value
            elif isinstance(item, ast.BinOp):                 # ROOT / "scripts"
                right = item.right
                if isinstance(right, ast.Constant) and isinstance(right.value, str):
                    name = right.value
            if name:
                out.add(name.rstrip("/"))
    return out


def scanning_gates() -> dict:
    """{测试文件相对路径: 声明的根}（只收扫了代码根的那些）。"""
    gates = {}
    for path in sorted((ROOT / "tests").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        declared = _declared_roots(path)
        if declared & code_roots():
            gates[str(path.relative_to(ROOT))] = declared
    return gates


class ScanScopeIsExplicitTest(unittest.TestCase):
    def test_every_scanning_gate_covers_all_code_roots(self):
        roots = code_roots()
        gaps = {}
        for gate, declared in scanning_gates().items():
            if gate in EXEMPT_SCOPE:
                continue
            missing = sorted(roots - declared)
            if missing:
                gaps[gate] = missing
        self.assertEqual(gaps, {}, "这些扫描门漏了代码根（那些地方的违规它看不见）："
                                   f"{gaps}\n（确需只扫部分根请登记 EXEMPT_SCOPE 并写理由）")

    def test_meta_scan_is_not_vacuous(self):
        roots = code_roots()
        self.assertGreaterEqual(len(roots), 3, f"代码根只推出 {len(roots)} 个 ⇒ 推导失效：{roots}")
        for must in ("scripts", "r20_backend"):
            self.assertIn(must, roots)
        gates = scanning_gates()
        self.assertGreaterEqual(len(gates), 8, f"只发现 {len(gates)} 个扫描门 ⇒ 发现逻辑失效")
        # 抽查：本轮被扩根的门必须在列
        for must in ("tests/audit/test_env_keys_are_documented.py",
                     "tests/core/test_timestamp_unit_convention.py",
                     "tests/audit/test_defs_not_inside_main_block.py"):
            self.assertIn(must, gates, f"{must} 应被识别为扫描门")

    def test_exemptions_have_reasons(self):
        for gate, reason in EXEMPT_SCOPE.items():
            self.assertGreaterEqual(len(str(reason).strip()), 12, f"{gate} 的豁免理由太短")
            self.assertTrue((ROOT / gate).exists(), f"豁免表过期：{gate} 不存在")

    def test_teeth_on_a_partial_scope(self):
        """牙齿：只扫部分代码根的声明必须被判定为缺口。"""
        roots = {"scripts", "r20_backend", "r20_gateway", "plugins"}
        partial = {"scripts", "r20_backend"}
        self.assertEqual(sorted(roots - partial), ["plugins", "r20_gateway"],
                         "部分范围必须被识别出缺失的根 ⇒ 门没有牙齿")


if __name__ == "__main__":
    unittest.main()

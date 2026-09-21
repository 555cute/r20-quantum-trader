"""`posSide` 比较必须**净持仓容错**（第一百八十六刀）。

## 为什么

OKX 两种持仓模式给出的 `posSide` 不同：

- **long/short（对冲）模式**：`posSide` 是 `"long"` / `"short"`（**本机真单核对**：线上就是
  `{"instId": "ADA-USDT-SWAP", "posSide": "long"}`）；
- **净持仓（one-way）模式**：`posSide` 是 `"net"`。

本仓既有的正确 convention 是 `str(o.get("posSide", "net")).lower() in {pos_side, "net"}`
（`scale_out.py`、`cloud_protection.py:105` 等）。但真机扫描发现**五处**写成精确相等，
其中三处的后果是"假阴性 + 危险动作"：

| 位置 | 精确相等的后果 |
|---|---|
| `cloud_protection.py`（收紧云端止损）| 找不到活止损单 ⇒ 返回 False ⇒ **止损上移静默失效**（同一文件另一处却是 net 容错）|
| `position_mgmt.py`（云端止损上移）| 同样找不到 ⇒ 只打印"未找到真实云端止损单" ⇒ 静默不生效 |
| `venue_query.py`（平仓核验）| 匹配不上 ⇒ `remaining` 保持 0 ⇒ **仓位还开着却宣称"已平仓"**（调用方以为已空仓）|

另两处（`okx_history`、`okx_trade_service._position_match`）是**自洽**的（两边都来自 OKX 自身，
net↔net / long↔long 都能配上），故列入带理由的允许清单。

## 本门

AST 扫描 `scripts/` 与 `r20_backend/`：任何提到 `posSide` 的比较，若**不含** `"net"` 容错，
必须出现在允许清单里（附理由），否则判红。允许清单亦有"防腐"检查：条目所指函数必须仍存在
且仍含 `posSide` 比较。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 允许的精确比较（自洽：两侧 `posSide` 都源自 OKX 自身）
ALLOWLIST = {
    ("scripts/ledger/okx_history.py", "build_okx_trade"):
        "两侧都取自 OKX 自身流水（net↔net / long↔long 都能配上）",
    ("r20_backend/okx_trade_service.py", "_position_match"):
        "intent 的 posSide 取自同一份持仓快照（net↔net）",
    ("r20_backend/okx_trade_service.py", "fast_close_confirmed"):
        "那是**判断侧向是否显式**（`in {long, short}`）的分支，不是与交易所 posSide 的兼容比较",
}

SCAN_DIRS = ("scripts", "r20_backend")


def _enclosing(tree: ast.AST, lineno: int):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                if best is None or node.lineno >= best.lineno:
                    best = node
    return best


def find_intolerant_posside_compares(source: str, rel_path: str):
    """返回 (函数名, 行号, 表达式) 列表：提到 `posSide` 但没做 net 容错的比较。"""
    problems = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return problems
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        text = ast.unparse(node)
        if "posSide" not in text:
            continue
        if '"net"' in text or "'net'" in text:
            continue
        fn = _enclosing(tree, node.lineno)
        fname = fn.name if fn else "<module>"
        if (rel_path, fname) in ALLOWLIST:
            continue
        problems.append((fname, node.lineno, text[:100]))
    return problems


def iter_py_files():
    for d in SCAN_DIRS:
        for path in sorted((ROOT / d).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


class PosSideNetConventionTest(unittest.TestCase):
    def test_every_posside_compare_is_net_tolerant_or_allowlisted(self):
        problems = []
        for path in iter_py_files():
            rel = str(path.relative_to(ROOT))
            problems += [(rel, fn, ln, expr)
                         for fn, ln, expr in find_intolerant_posside_compares(
                             path.read_text(encoding="utf-8"), rel)]
        self.assertEqual(problems, [], "存在非 net 容错的 posSide 比较（净持仓模式下会假阴性）：\n"
                                       + "\n".join(f"{r}:{ln} ({fn}) {expr}" for r, fn, ln, expr in problems))

    def test_the_three_fixed_sites_are_now_tolerant(self):
        """回归：三处危险点必须含 `"net"` 容错（防止有人再手滑写回精确相等）。"""
        for rel, needle in (("scripts/trader/cloud_protection.py", 'in {pos_side, "net"}'),
                            ("scripts/trader/position_mgmt.py", 'in {pos_side, "net"}'),
                            ("scripts/trader/venue_query.py", 'in {pos_side, "net"}')):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(needle, src, f"{rel} 的 posSide 比较退回了精确相等")

    def test_scan_is_not_vacuous(self):
        seen = 0
        for path in iter_py_files():
            rel = str(path.relative_to(ROOT))
            src = path.read_text(encoding="utf-8")
            seen += sum(1 for n in ast.walk(ast.parse(src))
                        if isinstance(n, ast.Compare) and "posSide" in ast.unparse(n))
        self.assertGreaterEqual(seen, 6, f"只扫到 {seen} 处 posSide 比较 ⇒ 门可能已与实现脱节")

    def test_allowlist_entries_still_exist(self):
        """防腐：允许清单指向的函数必须仍在、且仍含 `posSide` 比较。"""
        for (rel, fname), reason in ALLOWLIST.items():
            src = (ROOT / rel).read_text(encoding="utf-8")
            tree = ast.parse(src)
            fn = next((n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fname), None)
            self.assertIsNotNone(fn, f"允许清单过期：{rel} 里没有 {fname}（理由：{reason}）")
            body = ast.unparse(fn)
            self.assertIn("posSide", body, f"允许清单过期：{rel}::{fname} 已不再比较 posSide")

    def test_gate_has_teeth(self):
        bad = (
            "def f(o, pos_side):\n"
            "    return o.get('posSide') == pos_side\n"
        )
        self.assertTrue(find_intolerant_posside_compares(bad, "scripts/x.py"),
                        "精确相等的 posSide 比较没被抓出来 ⇒ 门没有牙齿")
        good = (
            "def f(o, pos_side):\n"
            "    return str(o.get('posSide', 'net')).lower() in {pos_side, 'net'}\n"
        )
        self.assertEqual(find_intolerant_posside_compares(good, "scripts/x.py"), [])
        allowed = (
            "def _position_match(o, pos_side):\n"
            "    return o.get('posSide') == pos_side\n"
        )
        self.assertEqual(find_intolerant_posside_compares(allowed, "r20_backend/okx_trade_service.py"), [],
                         "允许清单里的自洽站点被误判")


if __name__ == "__main__":
    unittest.main(verbosity=2)

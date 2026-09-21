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

SCAN_DIRS = ("scripts", "r20_backend", "r20_gateway", "plugins")


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



def find_posside_defaults(source: str):
    """返回 `get("posSide", <默认值>)` 的 (行号, 默认值) 列表。"""
    out = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
            continue
        if len(node.args) < 2:
            continue
        key = node.args[0]
        if not (isinstance(key, ast.Constant) and key.value == "posSide"):
            continue
        default = node.args[1]
        out.append((node.lineno, ast.unparse(default)))
    return out


#: 允许的非 `"net"` 默认值：**回退链的末端**或**展示文案**，都不是"模式兼容"判断
DEFAULT_ALLOWLIST = {
    ("scripts/ai_brain_trader.py", "execute_batch_ai_brain_cycle"):
        "回退链 `p.get(\"side\", p.get(\"posSide\", \"\"))` 的末端（posSide 只是备选）",
    ("scripts/brain/account_text.py", "build_position_lines"):
        "提示词展示默认（`side or posSide`，兜底 \"long\" 供文案）",
    ("scripts/sync_full_ledger.py", "_holding_row"):
        "回退链 `posSide → side → \"\"`（台账侧字段归一）",
    ("scripts/trader/factors.py", "fetch_single_instrument_data"):
        "回退链 `posSide → side → \"\"`（因子侧字段归一）",
    ("scripts/trader/cycle_stages.py", "fetch_positions_and_reconcile"):
        "**未计数**而非误判：净持仓模式下新开仓已被上游持仓模式闸拦住"
        "（`execution_router` 的 `entry_ready_position_modes`），故此处不可达",
    ("r20_backend/exchanges/okx.py", "open_orders"):
        "归一化透传字段（消费方是 sandbox 假适配器；真机 OKX 恒返回 posSide）",
    ("r20_backend/routers/strategy/council.py", "admin_test_council_debate"):
        "面板/辩论文案的展示默认（\"—\"）",
}


class PosSideDefaultConsistencyTest(unittest.TestCase):
    """第一百八十七刀：缺失 `posSide` 的默认值必须**全仓一致**（`"net"`）。

    缺字段时用 `""` 会与 `"net"` 配不上 ⇒ 明明有仓位却"匹配不到"（净持仓模式更易触发）。
    """

    def test_every_default_is_net_or_a_documented_exception(self):
        problems = []
        for path in iter_py_files():
            rel = str(path.relative_to(ROOT))
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            for line, default in find_posside_defaults(src):
                if default in ('"net"', "'net'"):
                    continue
                fn = _enclosing(tree, line)
                fname = fn.name if fn else "<module>"
                if (rel, fname) in DEFAULT_ALLOWLIST:
                    continue
                problems.append(f"{rel}:{line} ({fname}) 默认值={default}")
        self.assertEqual(problems, [], "缺失 posSide 的默认值不一致且未登记理由：\n"
                                       + "\n".join(problems))

    def test_default_allowlist_has_reasons(self):
        for key, reason in DEFAULT_ALLOWLIST.items():
            self.assertTrue(reason.strip(), f"{key} 的放行理由不能为空")

    def test_the_scan_is_not_vacuous(self):
        seen = 0
        for path in iter_py_files():
            seen += len(find_posside_defaults(path.read_text(encoding="utf-8")))
        self.assertGreaterEqual(seen, 8, f"只扫到 {seen} 处 posSide 默认值 ⇒ 门已与实现脱节")

    def test_teeth_on_inconsistent_default(self):
        bad = "def f(p):\n    return str(p.get('posSide', '')).lower()\n"
        self.assertTrue(find_posside_defaults(bad), "连默认值都没扫到 ⇒ 门是空的")
        self.assertNotEqual(find_posside_defaults(bad)[0][1], '"net"')
        good = "def f(p):\n    return str(p.get('posSide', 'net')).lower()\n"
        self.assertEqual(find_posside_defaults(good)[0][1], "'net'")


class OkxEndpointFieldNamesTest(unittest.TestCase):
    """第一百八十七刀：**刻意**的端点字段差异不得被"顺手统一"。

    OKX 的 `/trade/close-position` 用 `mgnMode`，而 `/trade/order`、`/trade/order-algo`
    用 `tdMode` —— 这是交易所契约差异，统一成一个反而会 400（本门把它钉住）。
    """

    def test_close_position_uses_mgn_mode(self):
        src = (ROOT / "scripts" / "okx_rest.py").read_text(encoding="utf-8")
        start = src.index("def close_position(")
        body = src[start:start + 700]
        self.assertIn('"mgnMode": td_mode', body, "close-position 的保证金字段必须是 mgnMode")
        self.assertNotIn('"tdMode": td_mode', body, "close-position 不该出现 tdMode（OKX 契约是 mgnMode）")

    def test_order_payloads_use_td_mode(self):
        src = (ROOT / "scripts" / "okx_rest.py").read_text(encoding="utf-8")
        self.assertIn('"tdMode": td_mode', src, "下单/算法单载荷必须用 tdMode")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""孪生对拍门：熔断判定的**两份实现**不得静默漂移（第一百四十七刀）。

## 为什么需要它

`is_circuit_breaker_active` 在仓内有**两份活实现**（文本形式刻意不同：一份注入门面全局、
一份直接读模块常量）：

| 实现 | 角色 |
|---|---|
| `r20_backend/execution/circuit_breaker.py` | 后端风控面 + **助手单一事实源**（`ledger_daily_closed_pnl` 等） |
| `scripts/trader/circuit_guard.py` | **交易活路径**（trader 每轮经门面调到它） |

本仓已经吃过一次这个亏并有案可查：`circuit_guard.py` 里的注释写着「上轮 A2 的
『同步失败所→禁开仓』加固**只进了模块版**，而活路径走本函数（孪生漂移），等于闸装了死副本」。
也就是说：**加固只改一边 ⇒ 另一边当没改**，而两边的单测都可能照常全绿。

全文字面比较做不到（两份形式不同是有意的），所以本门对拍的是**决策序列**：
把每个实现抽成"有序的判定事件"（哪个条件 ⇒ 返回 True/False，哪些 except ⇒ fail-closed
还是吞掉），映射掉纯形式差异（`CIRCUIT_BREAKER_FILE.exists()` vs
`os.path.exists(circuit_breaker_file)`、注入名 vs 模块名）后要求**逐项相等**。

## 判据与自检

- **不做**：两份实现的决策序列必须完全一致；
- **自检 1（非空）**：事件数 ≥ 8 且必须包含 旁车不可判 / 失败所 / 熔断文件活跃 三类事件，
  否则说明抽取器坏了（抽取器一坏，本门会"永远绿"）；
- **自检 2（有牙齿）**：把"旁车不可判 ⇒ 禁开仓"这一支从一份实现里删掉，对拍**必须**报不等
  —— 这正是历史漂移的形状（一边加了闸，另一边没有）。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "r20_backend" / "execution" / "circuit_breaker.py"
TRADER = ROOT / "scripts" / "trader" / "circuit_guard.py"
FN = "is_circuit_breaker_active"

#: 纯形式差异 → 规范事件名。**只映射语义等价的写法**，不映射"差不多"。
_NORMALIZERS: "list[tuple[re.Pattern, str]]" = [
    (re.compile(r"(LEDGER_JSON_FILE|ledger_json_file)"), "LEDGER_FILE"),
    (re.compile(r"(CIRCUIT_BREAKER_FILE|circuit_breaker_file)"), "CB_FILE"),
    (re.compile(r"os\.path\.exists\(\s*LEDGER_FILE\s*\)|LEDGER_FILE\.exists\(\)"), "EXISTS(LEDGER)"),
    (re.compile(r"os\.path\.exists\(\s*CB_FILE\s*\)|CB_FILE\.exists\(\)"), "EXISTS(CB)"),
    (re.compile(r"_sidecar_unknown"), "SIDECAR_UNKNOWN"),
    (re.compile(r"(_?failed_venues)"), "FAILED_VENUES"),
    (re.compile(r"bs_active"), "SENTINEL_ACTIVE"),
    (re.compile(r"today_pnl\s*<\s*-_?loss_cap"), "PNL_OVER_CAP"),
    (re.compile(r"active\s+and\s+\(expires_at.*"), "CB_ACTIVE_AND_UNEXPIRED"),
]


def _norm(text: str) -> str:
    for pattern, name in _NORMALIZERS:
        if pattern.search(text):
            text = pattern.sub(name, text)
    return re.sub(r"\s+", " ", text).strip()


def _ret_kind(value: "ast.expr | None") -> str:
    """把返回值归到"放行/拦截"两类：`(True, …)` ⇒ TRUE（拦截），其余 ⇒ FALSE。"""
    if isinstance(value, ast.Tuple) and value.elts and isinstance(value.elts[0], ast.Constant):
        return "TRUE" if value.elts[0].value is True else "FALSE"
    if isinstance(value, ast.Constant) and value.value is True:
        return "TRUE"
    return "FALSE"


def decision_events(source: str, fn_name: str = FN) -> "list[str]":
    """抽取判定事件序列（有序、形式无关）。"""
    tree = ast.parse(source)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            target = node
            break
    if target is None:
        raise AssertionError(f"没找到 {fn_name}（门自身失效）")

    events: "list[str]" = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If):
                rets = [s for s in child.body if isinstance(s, ast.Return)]
                if rets:
                    events.append(f"IF {_norm(ast.unparse(child.test))} -> {_ret_kind(rets[0].value)}")
                else:
                    events.append(f"IF {_norm(ast.unparse(child.test))}")
                visit(child)
            elif isinstance(child, ast.ExceptHandler):
                rets = [s for s in ast.walk(child) if isinstance(s, ast.Return)]
                events.append(f"EXCEPT -> {_ret_kind(rets[0].value) if rets else 'SWALLOW'}")
                visit(child)
            elif isinstance(child, ast.Return):
                events.append(f"RET {_ret_kind(child.value)}")
            else:
                visit(child)

    visit(target)
    return events


class TwinParityTest(unittest.TestCase):
    def setUp(self):
        self.backend = decision_events(BACKEND.read_text(encoding="utf-8"))
        self.trader = decision_events(TRADER.read_text(encoding="utf-8"))

    def test_extractor_is_not_vacuous(self):
        self.assertGreaterEqual(len(self.backend), 8, "抽取器坏了？事件太少，本门会永远绿")
        self.assertGreaterEqual(len(self.trader), 8)
        for label, events in (("backend", self.backend), ("trader", self.trader)):
            joined = "\n".join(events)
            for need in ("SIDECAR_UNKNOWN", "FAILED_VENUES", "CB_ACTIVE"):
                with self.subTest(impl=label, need=need):
                    self.assertIn(need, joined, "关键判定事件缺席（抽取或实现真缺）")

    def test_both_implementations_decide_identically(self):
        """核心判据：决策序列逐项相等（加固只改一边 ⇒ 这里翻红）。"""
        self.assertEqual(self.backend, self.trader,
                         "熔断判定的两份实现已漂移 —— 要么同步两边，要么写清为何允许不同")

    def test_gate_has_teeth(self):
        """有牙齿自检：删掉一份实现里的"旁车不可判 ⇒ 禁开仓"分支，对拍必须报不等。"""
        src = TRADER.read_text(encoding="utf-8")
        mutated = re.sub(r"\n\s*if _sidecar_unknown:\n(?:\s+.*\n)+?(?=\s*if _failed_venues:)",
                         "\n", src)
        self.assertNotEqual(mutated, src, "变异没生效 ⇒ 这条自检本身失效")
        self.assertNotEqual(decision_events(mutated), self.backend,
                            "删掉一边的闸之后对拍仍是绿的 ⇒ 本门没有牙齿")


if __name__ == "__main__":
    unittest.main(verbosity=2)

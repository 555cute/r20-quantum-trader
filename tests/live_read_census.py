"""普查：测试里对**生产数据**的读取（工具，不是闸）。

用法：

    .venv/bin/python -m tests.live_read_census           # 全部命中
    .venv/bin/python -m tests.live_read_census --json

## 为什么是工具而不是闸（第二百三十一刀实测后的决定）

起因：`tests/venues/test_own_position_records.py` 里有一条用例断言
「**线上** `data/trading_ledger.json` 里必须存在 UNI/binance 的 holding 行」——
仓一平/台账一更新，本门就红（而代码一行没改）。**断言依赖生产数据的内容＝定时炸弹**，
与本仓「测试不触生产文件」的纪律（只读也不该**断言其内容**）冲突。那条已按"确定性数据 +
线上只查结构"修好。

于是想做个闸把这类扫出来，先普查再说：

| 判据 | 命中 | 其中真问题 |
|---|---|---|
| 测试函数里出现 `data/xxx.json` 字样，或调用零参读取器（`load_trackers()` 等） | **41** | 1（已修）|

**41 处里绝大多数是假阳性**：docstring 里提到 `data/…`、临时目录里恰好有个 `data/hello.txt`、
以及**已被 `patch` 到临时路径**的读取（`tests/trading/test_silent_degradation_telemetry.py`
那一批就是 setUp 里 patch 了 `POSITION_TRACKER_FILE`）。

⇒ 静态判**不可靠**（区分不了"读生产"与"读被 patch 的临时路径"）。按本仓纪律
「白名单必须少且写明理由」，硬做闸就要背一堆噪声 ⇒ **不做成闸**，把普查留成工具。

## 正解（下一刀，需先给允许清单）

运行时隔离才是可靠做法：`tests/__init__.py` 已经**硬阻断**测试进程连生产 `data/*.db`、
并对其它 `data/` **写**强制沙箱化、对**读**只打印提示。把「生产 `data/*.json` 的**读**」
也纳入同一守卫（默认拒绝 + 显式允许清单），一次就能根治这一整类。
本文件是给它做基线普查用的。
"""

import ast
import json
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

DATA_LITERAL = re.compile(r"data/[A-Za-z0-9_.\-]+\.(json|db|sqlite|txt)")
ZERO_ARG_READERS = {"load_ledger", "load_trackers", "load_open_intents", "load_watchdog_state",
                    "read_cycle_health", "pool_state"}

Hit = Tuple[str, str, int, str]


def _docstring_nodes(tree: ast.AST) -> set:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(n, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def hits_in_source(src: str, *, label: str = "sample.py") -> List[Hit]:
    """一个文件里的命中（供工具与自检共用）。**跳过 docstring**（提到 `data/…` 不算读）。"""
    tree = ast.parse(src)
    docstrings = _docstring_nodes(tree)
    out: List[Hit] = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]:
        for n in ast.walk(fn):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                    and id(n) not in docstrings and DATA_LITERAL.search(n.value):
                out.append((label, fn.name, n.lineno, f"字面量 {n.value[:40]}"))
            if isinstance(n, ast.Call):
                f = n.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if name in ZERO_ARG_READERS and not n.args and not n.keywords:
                    out.append((label, fn.name, n.lineno, f"零参读取 {name}"))
    return out


def census() -> List[Hit]:
    out: List[Hit] = []
    for p in sorted((ROOT / "tests").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            out.extend(hits_in_source(p.read_text(encoding="utf-8"),
                                      label=str(p.relative_to(ROOT))))
        except SyntaxError:      # pragma: no cover
            continue
    return out


def format_report(rows: List[Hit]) -> str:
    lines = [f"{f}::{fn}:{ln}  {why}" for f, fn, ln, why in rows]
    lines.append(f"=== 共 {len(rows)} 处（含大量假阳性，见模块 docstring）===")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rows = census()
    if "--json" in argv:
        print(json.dumps([{"file": f, "test": t, "line": ln, "why": w} for f, t, ln, w in rows],
                         ensure_ascii=False, indent=2))
    else:
        print(format_report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

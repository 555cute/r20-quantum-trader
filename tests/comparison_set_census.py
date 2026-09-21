"""比较口径普查（**工具，不是闸**）：同一"主语"在同一文件里被比出不同的取值集合。

用法：

    .venv/bin/python -m tests.comparison_set_census            # 全部组
    .venv/bin/python -m tests.comparison_set_census --risky    # 只看侧/状态类主语
    .venv/bin/python -m tests.comparison_set_census --net-drift # 只看"net 容错不一致"

## 它为什么不是闸（第二百二十二刀实测后的决定）

本会话修过一处"同文件同一语义两种写法"（第一百八十六刀：`posSide` 在该处用
`in {pos_side, "net"}` 容错、在另一处只认精确相等 ⇒ 云端止损收紧静默失效）。于是想给它
做个全仓闸，先做了普查并且**逐组人工判读**：

| 口径 | 组数 | 判读 |
|---|---|---|
| 全部同主语多口径 | **105** | 绝大多数是**同一处按分支派发**（`venue == "okx"` / `== "binance"` / `== "gate"`）与**嵌套校验**（先查合法性、再判具体值），不是漂移 |
| 只看侧/状态类主语 | **25** | 同上：`long` 与 `short` 各自分支、`posSide` 合法性校验 vs 语义判定，逐组看过，**无一处是真漂移** |
| 只看"net 容错不一致" | **0** | 当前仓库没有这类字面量漂移 |

⇒ 全仓闸会有 25-105 条白名单噪声，违背"白名单必须少且写明理由"的既有纪律，**故不做成闸**；
把普查留成工具（可重复跑），另在 `tests/audit/test_comparison_set_drift.py` 只钉**尖锐子集**
（字面量层面的 net 容错不一致）。

## 已知盲区（如实）

历史那处的比较对象是**变量**（`== pos_side`）而非字面量 ⇒ 本工具的字面量口径扫不到它。
它抓的是"两边都写字面量、但一边含 `net` 一边不含"这一类。
"""

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ("scripts", "r20_backend", "plugins")

RISKY = re.compile(r"(posside|positionside|_side|side|state|status|reduce_?only)", re.I)

Subject = str
Group = Tuple[str, Subject, List[Tuple[int, List[str]]]]


def norm(node: ast.AST) -> Optional[str]:
    """主语归一化：去空白（`str(o.get("posSide","net")).lower()` 与带空格写法视为同一主语）。"""
    try:
        return re.sub(r"\s+", "", ast.unparse(node))
    except Exception:      # pragma: no cover - ast.unparse 对合法 AST 不会失败
        return None


def lits(node: ast.AST) -> Optional[set]:
    """比较对象是字面量集合时返回它，否则 None（变量/表达式一律不猜）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        out = set()
        for e in node.elts:
            if isinstance(e, ast.Constant) and isinstance(e.value, str):
                out.add(e.value)
            else:
                return None
        return out or None
    return None


def groups_in_source(src: str) -> List[Group]:
    """一个源文件里的「同主语多口径」组（供工具与用例共用）。"""
    tree = ast.parse(src)
    seen: Dict[Subject, List[Tuple[int, List[str]]]] = defaultdict(list)
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Compare) and len(n.ops) == 1):
            continue
        if not isinstance(n.left, (ast.Name, ast.Call, ast.Attribute, ast.Subscript)):
            continue
        if not isinstance(n.ops[0], (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
            continue
        vals = lits(n.comparators[0])
        subj = norm(n.left)
        if subj and vals:
            seen[subj].append((n.lineno, sorted(vals)))
    return [(subj, subj, sorted(locs)) for subj, locs in seen.items() if len({frozenset(v) for _, v in locs}) > 1]


def net_tolerance_drift(src: str) -> List[Group]:
    """尖锐子集：同一主语的字面量口径里，有的含 `net`、有的不含。"""
    out = []
    for _, subj, locs in groups_in_source(src):
        lowered = [(ln, {v.lower() for v in vals}) for ln, vals in locs]
        has = [ln for ln, v in lowered if "net" in v]
        without = [ln for ln, v in lowered if "net" not in v]
        if has and without:
            out.append((subj, subj, locs))
    return out


def files() -> List[Path]:
    out = []
    for root in ROOTS:
        out.extend(p for p in (ROOT / root).rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(out)


def census(*, only_risky: bool = False, only_net_drift: bool = False) -> List[Group]:
    """扫全仓，返回 `[(相对路径, 主语, [(行号, 取值)])]`。"""
    out: List[Group] = []
    for p in files():
        try:
            src = p.read_text(encoding="utf-8")
            found = net_tolerance_drift(src) if only_net_drift else groups_in_source(src)
        except SyntaxError:      # pragma: no cover
            continue
        for _, subj, locs in found:
            if only_risky and not RISKY.search(subj):
                continue
            out.append((str(p.relative_to(ROOT)), subj, locs))
    return out


def format_report(rows: List[Group]) -> str:
    lines = []
    for path, subj, locs in rows:
        lines.append(f"{path}  「{subj}」 {len({frozenset(v) for _, v in locs})} 种口径")
        for ln, vals in locs:
            lines.append(f"    L{ln}: {vals}")
    lines.append(f"=== 共 {len(rows)} 组 ===")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rows = census(only_risky="--risky" in argv, only_net_drift="--net-drift" in argv)
    if "--json" in argv:
        print(json.dumps([{"file": f, "subject": s, "checks": l} for f, s, l in rows],
                         ensure_ascii=False, indent=2))
    else:
        print(format_report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

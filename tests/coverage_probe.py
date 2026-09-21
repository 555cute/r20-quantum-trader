"""本地**运行时覆盖率探针**（stdlib 实现，不装 `coverage`）。

## 为什么自己写

`coverage`/`pytest-cov` 未安装，也不为一个开发期工具上网装依赖。用 `sys.settrace`
只对**目标文件**返回 tracer（其它文件的 line 事件一律不接），跑指定的 pytest 选择，
再把命中行号与 `ast` 推出的"可执行语句行"相减 ⇒ 得到未命中行。

## 用法（**仅供开发期自查**，不在门里跑）

    .venv/bin/python -m tests.coverage_probe \\
        scripts/trader/venue_protection.py r20_backend/execution_router.py \\
        -- tests/trading tests/venues -q

注意两点，否则数字会骗人：

1. **范围决定数字**：只跑一部分测试目录 ⇒ 结果是**下界**（别的目录也会覆盖同一文件）；
2. **`ast` 近似**：多行表达式/`elif` 分支等会让"可执行行"估计偏大，未命中行要用眼睛看一遍，
   不要拿百分比当 KPI。

## 本会话用它抓到过什么

第一百零七/一百零八刀：`venue_protection` 的撤销与护栏分支（仍有活动持仓 ⇒ 整合约跳过、
腿无 id ⇒ 跳过、读腿失败 ⇒ 进 errors、tag 孤儿腿才可撤）**从未被执行** ⇒ 补 5 条用例后
94.1% → 94.7%。
"""

from __future__ import annotations

import ast
import json
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def executable_lines(path: Path) -> set:
    """文件里"可执行语句"的行号（去掉函数/类定义、导入、纯 docstring 表达式）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            lines.add(node.lineno)
        elif isinstance(node, ast.stmt) and not isinstance(
                node, (ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom, ast.Expr)):
            lines.add(node.lineno)
    return lines


def run(targets: list, pytest_args: list) -> dict:
    """跑 pytest 并返回 {目标: {executable, hit, missing}}。"""
    hits: dict = {}

    def tracer(frame, event, arg):
        filename = frame.f_code.co_filename
        if any(filename.endswith(t) for t in targets):
            if event == "line":
                hits.setdefault(filename, set()).add(frame.f_lineno)
            return tracer
        return None

    argv = sys.argv
    sys.argv = ["pytest"] + list(pytest_args)
    sys.settrace(tracer)
    try:
        runpy.run_module("pytest", run_name="__main__")
    except SystemExit:
        pass
    finally:
        sys.settrace(None)
        sys.argv = argv

    report = {}
    for target in targets:
        path = ROOT / target
        if not path.exists():
            continue
        lines = executable_lines(path)
        got = {ln for filename, seen in hits.items() if filename.endswith(target) for ln in seen}
        report[target] = {"executable": len(lines), "hit": len(lines & got),
                          "missing": sorted(lines - got)}
    return report


def format_report(report: dict) -> str:
    out = []
    for target, data in report.items():
        pct = 100.0 * data["hit"] / data["executable"] if data["executable"] else 0.0
        out.append(f"{target}: {data['hit']}/{data['executable']} = {pct:.1f}%  "
                   f"未命中 {len(data['missing'])}: {data['missing'][:30]}")
    return "\n".join(out)


def main() -> int:
    argv = sys.argv[1:]
    if "--" not in argv or not argv:
        print(__doc__)
        return 2
    split = argv.index("--")
    targets = argv[:split] or ["scripts/trader/venue_protection.py"]
    report = run(targets, argv[split + 1:])
    print("\n=== 运行时覆盖率（目标文件；范围= 你给的 pytest 选择 ⇒ 下界）===")
    print(format_report(report))
    Path("/tmp/r20_coverage_probe.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

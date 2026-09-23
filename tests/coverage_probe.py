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
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def executable_lines(path: Path) -> set:
    """文件里"可执行语句"的行号。

    排除项分两类：
    ① **不可能被计到**的：导入、纯 docstring 表达式、`global`/`nonlocal` 声明
       —— 它们要么在探针启用前就执行完，要么根本不产生行事件（实测：`global X` 的
       行号永远不进命中集，与 `def` 行同类，留着只会制造假象缺口）；
    ② 语义上不属于"这一行的逻辑"的：函数/类定义行（**但 `def` 行仍计入分母** ——
       导入期执行靠 `traced_import` 补，不能靠排除来掩盖）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            lines.add(node.lineno)
        elif isinstance(node, (ast.If, ast.While)):
            lines.add(node.test.lineno)
        elif isinstance(node, ast.stmt) and not isinstance(
                node, (ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom,
                       ast.Expr, ast.Global, ast.Nonlocal)):
            lines.add(node.lineno)
    return lines


def traced_import(targets: list) -> list:
    """在**探针启用之后**重新执行目标模块的导入期代码，返回失败清单。

    ## 为什么必须有这一步（否则每个文件都带一个固定「假象地板」）

    `executable_lines` 把模块级语句与 `def`/`class` 行也算作"可执行"——它们确实可执行，
    但**执行时机是导入期**。而本探针只在 pytest 跑用例期间 `settrace`：若目标模块在探针
    启用前就已被 `tests/__init__.py` 等导入过，这些行永远不可能被记到
    ⇒ 表现为"未命中"，与有没有测试**无关**（实测：`binance.py` 的 `def` 行 34/68/124
    与模块级/类体行 75/83-85/89 一直显示未命中，而其函数体行全部命中）。

    ## 为什么是"真的重跑导入"而不是"静态豁免导入期行"

    模块级代码里可能有**没走到**的分支（如 `except ImportError:` 兜底常量）。把它们一律
    静态豁免，会把真实缺口一起藏掉（本仓恰好为这类兜底写过用例）。故这里**真的重新执行**
    导入：走到的分支记命中、没走到的分支照旧显示未命中 —— 语义与用例覆盖一致。
    """
    import importlib
    failed = []
    for target in targets:
        mod_name = str(target)[:-3].replace("/", ".") if str(target).endswith(".py") else str(target)
        try:
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
            else:
                importlib.import_module(mod_name)
        except Exception as exc:                      # 导入失败不拦探针：真失败由用例暴露
            failed.append(f"{mod_name}: {type(exc).__name__}: {exc}")
    return failed


def arm_sticky_tracer(outer):
    """把 `sys.settrace` 换成**粘性**版本；返回 `restore()`。

    ## 为什么必须有（第三百三十四刀，实测事故）

    `tests/audit/test_coverage_probe_attribution.py` 在自己的 `finally:` 里调
    `sys.settrace(None)`（对普通 pytest 运行是**正确**行为）。但只要探针的范围里包含
    那个文件，它在测试内部就**拆掉了外层 tracer** ⇒ 其后所有文件一律记 0 行，
    整份基线被**静默截断**。

    症状极具误导性：同一份数据下，"超集范围"能报出比"子集范围"**更少**的命中
    （实测 `r20_backend/exchanges/gate.py`：全量 `0/347`，只跑 `tests/venues` `305/347`）。
    **超集不可能更少** —— 见者即应怀疑探针本身，而不是去补测试。

    语义：`settrace(None)` ⇒ 重新装回 `outer`；`settrace(别的 tracer)` 照常放行
    （调用方多半只在自己的块内用，随后那句 `settrace(None)` 会装回 `outer`）。
    """
    original = sys.settrace

    def sticky(fn):
        if fn is None:
            return original(outer)
        return original(fn)
    original(outer)                      # 自装一次：调用方不必再单独 settrace
    sys.settrace = sticky
    return lambda: setattr(sys, "settrace", original)


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
    # ⚠️ `sys.settrace` **只对调用它的那个线程**生效。默认的 tracer 不会被新线程继承
    # （CPython 里线程的 tracing 由 `threading.settrace` 单独设置）。而本仓有大量代码跑在
    # `ThreadPoolExecutor` 的工作线程里（如 `factors/smart_money.py` 的 `fetch_smart_money_pool`、
    # 看板的并发抓取）—— 不装这一条，那些行会被**静默误报成"未命中"**，
    # 而错误方向是"看起来还有缺口"，于是白写一堆测试（`smart_money.py` 实测差 4 行即此因）。
    threading.settrace(tracer)
    # ★★ 追踪必须"粘性"，否则数字会被**静默截断**（第三百三十四刀）。
    #
    # 根因：`tests/audit/test_coverage_probe_attribution.py` 在自己的 `finally:` 里调
    # `sys.settrace(None)`（对普通 pytest 运行是**正确**行为）。但当探针的范围里包含
    # 那个文件时，它在测试内部**拆掉外层 tracer** ⇒ 其后所有文件一律记 0 行。
    #
    # 症状极具误导性：同一份数据下，"超集范围"能比"子集范围"报出**更少**的命中
    # （实测 `r20_backend/exchanges/gate.py`：全量 0/347，只跑 tests/venues 305/347）。
    # **超集不可能更少** —— 见者即应怀疑探针本身，而不是去补测试。
    #
    # 修法：把 `settrace(None)` 改写为"重新装回我们的 tracer"；测试自己的
    # `settrace(它自己的 tracer)` 照常放行（它只在那个块内有效）。
    restore_settrace = arm_sticky_tracer(tracer)
    try:
        _failed = traced_import(targets)
        if _failed:
            print("[coverage_probe] 目标模块重导失败（其导入期行仍会被记为未命中）:")
            for item in _failed:
                print(f"  - {item}")
        runpy.run_module("pytest", run_name="__main__")
    except SystemExit:
        pass
    finally:
        restore_settrace()
        threading.settrace(None)
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

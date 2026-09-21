"""日切键（`%Y-%m-%d`）必须**可证明**基于北京时间（第一百五十一刀）。

## 为什么

"今天是哪一天"直接决定三件钱相关的事：**当日亏损求和**、**止损冷却是否跨日**、
**日切统计/备份归档**。本仓到处都在算日切键，而这类漂移**不报错**：
把 `datetime.now(tz_bj)` 写成 `datetime.utcnow()` 或 `datetime.now()`（本地时区），
在 UTC+8 的机器上大部分时间"看起来是对的"，只在每天 **00:00–08:00**（北京时间）
悄悄把交易算到前一天 —— 单测若不用冻结时钟也照常全绿。

## 本次审计结果（先查后钉）

全仓 `strftime("%Y-%m-%d")` 共 **6 处**（`scripts/trader/circuit_guard.py`、
`scripts/sync_web_data.py`×2、`scripts/daily_summary_and_backup.py`、
`r20_backend/dashboard_cache.py`、`r20_backend/execution/circuit_breaker.py`），
逐处读码：**全部**基于显式 `timezone(timedelta(hours=8))`（本模块相关函数里的
`tz_bj`/`tz_beijing`）⇒ **今天没有漂移**。既然此刻是对的，就把它钉住，
而不是等它哪天被改坏。

## 判据（两条，AST 级）

1. **基座必须带时区**：`X.strftime("%Y-%m-%d")` 的 `X` 不得来自**无参** `datetime.now()` /
   `datetime.utcnow()`（那是"本机时区"或"UTC"，都不是日切键该用的）；
2. **时区必须可证明是 +08:00**：基座用的 tz 变量要么在本函数内由含 `hours=8` 的表达式绑定，
   要么是模块级常量且其赋值含 `hours=8` —— 于是"改用 `timezone.utc`"会翻红。

自检：把某一处的时区改成 `timezone.utc`、或把带时区的 `now(tz)` 改成无参 `now()`，
检查器必须报出该处（否则本门只是装饰）。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (ROOT / "scripts", ROOT / "r20_backend", ROOT / "r20_gateway", ROOT / "plugins")
DAY_KEY = "%Y-%m-%d"


def _module_bj_constants(tree: ast.Module) -> "set[str]":
    """模块级常量名 → 其赋值里含 `hours=8`（即 +08:00 可证明）。"""
    names: "set[str]" = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            text = ast.unparse(node.value)
            if "hours=8" in text or "hours = 8" in text:
                for t in targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
    return names


def _local_bj_names(fn: ast.AST) -> "set[str]":
    """函数内绑定为 +08:00 的局部名。"""
    names: "set[str]" = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            text = ast.unparse(node.value)
            if "hours=8" in text or "hours = 8" in text:
                for t in targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
    return names


def _tz_arg_bj(call: ast.Call, fn: ast.AST, module_bj: "set[str]") -> bool:
    """调用里的 `tz=` 实参可证明是 +08:00？"""
    args = [a for a in call.args] + [k.value for k in call.keywords if k.arg == "tz"]
    local = _local_bj_names(fn)
    for a in args:
        text = ast.unparse(a)
        if "hours=8" in text or "hours = 8" in text:
            return True
        if isinstance(a, ast.Name) and (a.id in local or a.id in module_bj):
            return True
    return False


def day_key_offenders(sources: "dict[str, str]") -> "list[str]":
    """返回违规点（文件:行:原因）。空 ⇒ 全部可证明基于北京时间。"""
    problems: "list[str]" = []
    for name, src in sorted(sources.items()):
        try:
            tree = _parse_quiet(src)
        except SyntaxError:
            continue
        module_bj = _module_bj_constants(tree)
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for call in ast.walk(fn):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "strftime"):
                    continue
                if not (call.args and isinstance(call.args[0], ast.Constant)
                        and call.args[0].value == DAY_KEY):
                    continue
                base = call.func.value
                # 基座：`datetime.now(...)` / `datetime.utcnow()` / 变量
                if isinstance(base, ast.Call):
                    fname = ast.unparse(base.func)
                    if fname.endswith("utcnow"):
                        problems.append(f"{name}:{call.lineno}: 日切键用 utcnow()（不是北京时间）")
                        continue
                    if fname.endswith("now"):
                        if not _tz_arg_bj(base, fn, module_bj):
                            problems.append(
                                f"{name}:{call.lineno}: 日切键的 now() 无 +08:00 时区（本机时区/U譅C 不可接受）")
                        continue
                    if not _tz_arg_bj(base, fn, module_bj):
                        problems.append(
                            f"{name}:{call.lineno}: 日切键基座 {fname}(...) 无法证明是 +08:00")
                    continue
                if isinstance(base, ast.Name):
                    # 变量：查它的绑定来源是否为带 +08:00 时区的 now/fromtimestamp
                    bound_ok = False
                    bound_needs_tz = False
                    for node in ast.walk(fn):
                        if not isinstance(node, ast.Assign):
                            continue
                        if not any(isinstance(t, ast.Name) and t.id == base.id for t in node.targets):
                            continue
                        v = node.value
                        if isinstance(v, ast.Call):
                            vf = ast.unparse(v.func)
                            if vf.endswith("utcnow"):
                                bound_needs_tz = True
                            elif vf.endswith("now") or vf.endswith("fromtimestamp"):
                                bound_ok = _tz_arg_bj(v, fn, module_bj)
                                bound_needs_tz = not bound_ok
                    if bound_needs_tz or not bound_ok:
                        problems.append(
                            f"{name}:{call.lineno}: 日切键基座变量 {base.id} 无法证明来自 +08:00")
                    continue
                problems.append(f"{name}:{call.lineno}: 日签键基座形态未知（{ast.unparse(base)[:40]}）")
    return problems


def _sources() -> "dict[str, str]":
    out: "dict[str, str]" = {}
    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            out[str(path.relative_to(ROOT))] = path.read_text(encoding="utf-8")
    return out


def _parse_quiet(src: str):
    """解析源码，但**抑制**`ast.parse` 顺带抛出的既有转义告警。

    仓内有 2 处 docstring 写了 LaTeX（`\int` / `\Pr`，`scripts/calculus/calculate.py`），
    解析时会报 `DeprecationWarning: invalid escape sequence`。那是**既有残留**且属文档字符串，
    但该文件的抽取门要求被搬运函数**逐字节一致**，故本刀不动它（见 ledger 记录的取舍）——
    这里只做噪音抑制，**不是**掩盖：本门要抓的是日切键时区，不是转义写法。
    """
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return ast.parse(src)


class DayKeyUsesBeijingTzTest(unittest.TestCase):
    def test_scan_is_not_vacuous(self):
        """抽取器自检：必须真的扫到日切键（否则本门会永远绿）。"""
        found = 0
        for src in _sources().values():
            tree = _parse_quiet(src)
            for call in ast.walk(tree):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "strftime" and call.args
                        and isinstance(call.args[0], ast.Constant)
                        and call.args[0].value == DAY_KEY):
                    found += 1
        self.assertGreaterEqual(found, 4, f"只扫到 {found} 处日切键，扫描逻辑可能失效")

    def test_every_day_key_is_provably_beijing(self):
        problems = day_key_offenders(_sources())
        self.assertEqual(problems, [], "日切键未基于北京时间：\n" + "\n".join(problems))

    def test_gate_has_teeth(self):
        """有牙齿自检：把带时区的 now 改成无参 now() / utcnow()，必须被抓。"""
        good = (
            "import datetime\n"
            "def f():\n"
            "    tz_bj = datetime.timezone(datetime.timedelta(hours=8))\n"
            "    now_bj = datetime.datetime.now(tz_bj)\n"
            "    return now_bj.strftime('%Y-%m-%d')\n"
        )
        self.assertEqual(day_key_offenders({"good.py": good}), [])
        naive = good.replace("datetime.datetime.now(tz_bj)", "datetime.datetime.now()")
        self.assertTrue(day_key_offenders({"naive.py": naive}), "无参 now() 必须被抓")
        utc = good.replace("hours=8", "hours=0")
        self.assertTrue(day_key_offenders({"utc.py": utc}), "改成 UTC 必须被抓")


if __name__ == "__main__":
    unittest.main(verbosity=2)

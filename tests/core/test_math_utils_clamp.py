"""`r20_backend/math_utils.py::clamp` 单一事实源（阶段 4·B3 第五十一刀）。

## 修了什么

跨文件重复扫描发现 `clamp` 在**两处逐字相同**（5 行，只差一行 docstring）：

| 位置 | 用途 |
|---|---|
| `scripts/trader/signals.py` L32 | 策略权重夹取 `clamp(w, 0.7, 1.3, 1.0)` |
| `scripts/self_improvement_engine.py` L82 | 资产倍数夹取 `clamp(m, 0.5, 1.5, 1.0)` |

两者都在**风控/权重计算路径**上。两份拷贝若漂移（一处改成
`except Exception`、或去掉 `float()` 转换），会出现
**信号评分与自进化引擎对同一个配置值给出不同夹取结果**的不一致。

## ⚠️ 本文件同样钉住"**不**该合并的兄弟函数"

本仓还有三个名字带 `clamp` 的函数，语义**各不相同**：

| 函数 | 位置 |
|---|---|
| `clamp_leverage` | `r20_backend/execution/risk_gates.py` |
| `clamp_margin` | 同上 |
| `clamp_council_timeout` | `r20_backend/council/roster.py` |

它们不是"同一函数的拷贝"，而是**同一动词的不同业务规则**
（取三道上限的最小值 / `0` 退化为不夹 / 处理 `NaN`·`±inf` / 夹动时打印运维文案）。
`NotToBeMergedTest` 明确断言它们**与本模块语义不同** ——
防止后来者"顺手统一"而改变风控行为（本仓红线）。
"""

from __future__ import annotations

import ast
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

MODULE = ROOT / "r20_backend" / "math_utils.py"
SIGNALS = ROOT / "scripts" / "trader" / "signals.py"
ENGINE = ROOT / "scripts" / "self_improvement_engine.py"

from r20_backend.math_utils import clamp  # noqa: E402


class ClampSemanticsTest(unittest.TestCase):
    def test_within_range_is_unchanged(self):
        self.assertEqual(clamp(1.0, 0.7, 1.3, 1.0), 1.0)

    def test_clamps_to_lower_and_upper(self):
        self.assertEqual(clamp(0.1, 0.7, 1.3, 1.0), 0.7)
        self.assertEqual(clamp(9.9, 0.7, 1.3, 1.0), 1.3)

    def test_bounds_are_inclusive(self):
        self.assertEqual(clamp(0.7, 0.7, 1.3, 1.0), 0.7)
        self.assertEqual(clamp(1.3, 0.7, 1.3, 1.0), 1.3)

    def test_string_number_is_converted(self):
        """⚠️ `float(value)` 转换：配置来自 JSON/env 时是**字符串**。"""
        self.assertEqual(clamp("1.4", 0.7, 1.3, 1.0), 1.3)
        self.assertEqual(clamp("1.0", 0.7, 1.3, 1.0), 1.0)

    def test_none_returns_default(self):
        """⚠️ `None` 走 `TypeError` 分支 → 返回 `default`（不是 0、不是边界值）。"""
        self.assertEqual(clamp(None, 0.7, 1.3, 1.0), 1.0)

    def test_non_numeric_string_returns_default(self):
        """⚠️ `"abc"` 走 `ValueError` 分支 → 返回 `default`。"""
        self.assertEqual(clamp("abc", 0.7, 1.3, 1.0), 1.0)

    def test_list_returns_default(self):
        self.assertEqual(clamp([1], 0.7, 1.3, 1.0), 1.0)

    def test_default_may_be_any_type(self):
        """`default` 不被转换（原样返回）—— 既有语义。"""
        sentinel = object()
        self.assertIs(clamp("abc", 0, 1, sentinel), sentinel)

    def test_lower_greater_than_upper_yields_lower(self):
        """⚠️ 边界顺序 `max(lower, min(upper, x))` 的后果：`lower > upper` 时
        结果恒为 `lower`（先夹上界再抬下界，**下界胜出**）。

        这是既有行为，保留 —— 不是 bug，但很容易被"顺手修正"。
        """
        self.assertEqual(clamp(5, 2, 1, 9), 2)
        self.assertEqual(clamp(-5, 2, 1, 9), 2)

    def test_returns_float_not_string(self):
        self.assertIsInstance(clamp("1.0", 0.0, 2.0, 1.0), float)

    def test_nan_collapses_to_upper(self):
        """⚠️⚠️ `NaN` **会落到 `upper`**，不是"原样穿过"、也不是 `default`。

        ⚠️ 我第一版把这条写成了"NaN 原样穿过"——**猜的，不是测的**。
        实测（Python `min`/`max` 在比较为 False 时返回**第一个参数**）：

            min(upper, nan) → upper        # upper 在前
            max(lower, upper) → upper      # upper > lower

        故 NaN 最终**恒等于 upper**（当 `lower <= upper`）。

        这是既有行为，本刀**不修改**（改它会动风控/权重语义，属红线）。
        但它值得被显式记住：NaN 一旦流进 `clamp`，会静默变成**上界**，
        而不是报错、也不是默认值 —— 上游必须自己拦（例如
        `risk_gates.clamp_margin` 就做了 `math.isfinite` 过滤）。
        """
        nan = float("nan")
        for lo, hi, d in ((0.0, 1.0, 0.5), (5, 9, 0.5), (-3, -1, 7), (1.0, 3.0, 2.0)):
            out = clamp(nan, lo, hi, d)
            self.assertEqual(out, hi, f"clamp(nan, {lo}, {hi}, {d}) 应等于上界 {hi}")
        # `lower > upper` 时下界胜出（见边界顺序用例）
        self.assertEqual(clamp(nan, 2, 1, 3), 2)

    def test_nan_does_not_return_default(self):
        """反证：NaN 走的是 `float()` 成功路径，**不进** `except` 分支。"""
        self.assertNotEqual(clamp(float("nan"), 0.0, 1.0, 0.5), 0.5)

    def test_infinity_clamps_to_bound(self):
        self.assertEqual(clamp(float("inf"), 0.0, 1.0, 0.5), 1.0)
        self.assertEqual(clamp(float("-inf"), 0.0, 1.0, 0.5), 0.0)


class ConsumerParityTest(unittest.TestCase):
    """两个消费方必须与新实现完全一致。"""

    def test_all_three_agree_over_a_grid(self):
        from scripts.trader.signals import clamp as signals_clamp
        import self_improvement_engine as engine
        cases = [
            (1.4, 0.7, 1.3, 1.0), ("1.4", 0.7, 1.3, 1.0), ("abc", 0.7, 1.3, 1.0),
            (None, 0.7, 1.3, 1.0), (0.5, 0.7, 1.3, 1.0), (2.0, 0.7, 1.3, 1.0),
            (0.7, 0.7, 1.3, 1.0), (1.3, 0.7, 1.3, 1.0), (5, 2, 1, 9),
            (float("nan"), 0.7, 1.3, 1.0), (float("inf"), 0.7, 1.3, 1.0),
            ("", "x", "y", "d"), (0, 0.0, 0.0, 9.0), ([1], 0.0, 1.0, 7.0),
        ]
        for c in cases:
            got = (repr(clamp(*c)), repr(signals_clamp(*c)), repr(engine.clamp(*c)))
            self.assertEqual(len(set(got)), 1, f"{c} → {got}")

    def test_shells_are_distinct_functions_not_aliases(self):
        """⚠️ 薄壳必须是**定义**：`clamp = _clamp` 这类别名会让
        `patch.object(模块, "clamp")` 之类的门面接缝失效。"""
        from scripts.trader.signals import clamp as signals_clamp
        import self_improvement_engine as engine
        self.assertIsNot(clamp, signals_clamp)
        self.assertIsNot(clamp, engine.clamp)
        self.assertIsNot(signals_clamp, engine.clamp)

    def test_shells_delegate_to_the_shared_implementation(self):
        for path in (SIGNALS, ENGINE):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for n in tree.body:
                if isinstance(n, ast.FunctionDef) and n.name == "clamp":
                    body = [s for s in n.body
                            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
                    self.assertEqual(len(body), 1, f"{path.name}::clamp 壳不止一条语句")
                    self.assertIn("_clamp", ast.unparse(body[0]))

    def test_no_inline_implementation_left(self):
        """全仓（非测试/归档）不应再有内联的 `clamp` 实现。"""
        offenders = []
        for p in ROOT.rglob("*.py"):
            if any(x in p.parts for x in (".venv", "plan_local", "__pycache__",
                                          "node_modules", ".git", ".archive", "tests")):
                continue
            if p == MODULE:
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for n in tree.body:
                if isinstance(n, ast.FunctionDef) and n.name == "clamp":
                    body = [s for s in n.body
                            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
                    code = ast.unparse(ast.Module(body=body, type_ignores=[]))
                    if "float(value)" in code:
                        offenders.append(f"{p}::{n.name}")
        self.assertEqual(offenders, [], f"仍有内联实现: {offenders}")

    def test_shared_module_depends_only_on_stdlib(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imported |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                imported.add((n.module or "").split(".")[0])
        self.assertEqual(imported - {"__future__"}, set(),
                         f"math_utils 不该有任何运行时依赖，实际: {imported}")


class NotToBeMergedTest(unittest.TestCase):
    """⚠️ 名字带 `clamp` 的兄弟函数**语义不同**，不得用本模块替换。

    本用例通过"同一输入给出不同结果"来**证明**它们不是同一函数 ——
    比注释更难绕过。
    """

    def test_clamp_council_timeout_rejects_nan(self):
        """`clamp_council_timeout` 会把 `NaN`/`±inf` 换成默认值；`clamp` 不会。"""
        from r20_backend.council.roster import clamp_council_timeout, DEFAULT_COUNCIL_TIMEOUT
        self.assertEqual(clamp_council_timeout(float("nan")), DEFAULT_COUNCIL_TIMEOUT)
        self.assertEqual(clamp_council_timeout(float("inf")), DEFAULT_COUNCIL_TIMEOUT)
        # 反证：本模块的 clamp 对 NaN 不做这层过滤
        self.assertFalse(clamp(float("nan"), 0.0, 1.0, 0.5) == DEFAULT_COUNCIL_TIMEOUT
                         and DEFAULT_COUNCIL_TIMEOUT == 0.5)

    def test_clamp_margin_only_accepts_positive_finite_caps(self):
        """`clamp_margin` 的 `0` 表示"该上限不可用"，而 `clamp` 的 `0` 是真实边界。"""
        src = (ROOT / "r20_backend" / "execution" / "risk_gates.py").read_text(encoding="utf-8")
        self.assertIn("math.isfinite(c) and c > 0", src,
                      "clamp_margin 的 `>0 且有限` 过滤是它的核心语义")

    def test_clamp_leverage_zero_means_no_clamping(self):
        """`clamp_leverage` 的 `0/None` 退化为**不夹**（用原值），
        而 `clamp` 的 `0` 会把值夹成 0 —— 语义相反。"""
        src = (ROOT / "r20_backend" / "execution" / "risk_gates.py").read_text(encoding="utf-8")
        self.assertIn("MAX_LEVERAGE or leverage", src)
        # 反证：本模块的 clamp 遇 0 上界会把值夹成 0
        self.assertEqual(clamp(5, 0, 0, 1), 0)

    #: 允许住在 `math_utils` 的**数值边界工具**（白名单：新增一项必须是有意识的编辑）。
    ALLOWED_IN_MATH_UTILS = ("clamp", "safe_float")
    #: 名字带 clamp 的兄弟函数**语义不同**，永远不得进本模块（风控红线）。
    FORBIDDEN_IN_MATH_UTILS = ("clamp_council_timeout", "clamp_margin", "clamp_leverage")

    def test_the_three_siblings_are_not_defined_in_math_utils(self):
        """本模块只能放**白名单内**的数值边界工具，且三个 `clamp_*` 兄弟永不得进来。

        第一百五十刀说明：本用例原先断言"只能有 `clamp` 一个名字"。那条**字面**约束
        把"数值边界工具"与"名字带 clamp 的语义不同的兄弟"混为一谈 —— 前者（如 `safe_float`）
        收敛进来是**减少**漂移，后者进来才是改风控行为。故改为：
        ①三个兄弟名**显式**禁止（比原来更直白）；②其余函数必须逐个列入白名单
        （所以"顺手再塞一个"仍会翻红，只是现在要显式改白名单）。
        """
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        funcs = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for bad in self.FORBIDDEN_IN_MATH_UTILS:
            with self.subTest(forbidden=bad):
                self.assertNotIn(bad, funcs,
                                 f"{bad} 与 clamp 语义不同，塞进本模块会改变风控行为（红线）")
        self.assertIn("clamp", funcs, "clamp 必须仍在（单一事实源）")
        extra = [f for f in funcs if f not in self.ALLOWED_IN_MATH_UTILS]
        self.assertEqual(extra, [], f"math_utils 出现了未列入白名单的函数: {extra}")


if __name__ == "__main__":
    unittest.main()

"""数值强转 `safe_float` 的**单一事实源**门（第一百五十刀）。

## 为什么

同一语义在仓内有**三份**实现（`scripts/factor_library.py`、`scripts/ai_brain_trader.py`
的公开同名函数 + `scripts/calculus/regime.py` 的私有 `_safe_float`），逐条等价但**重复三次**。

这类重复比"读取失败"更隐蔽：**不报错、没日志、结果看起来还很合理**。若有人"顺手优化"
其中一份（例如把 `bool` 也当非法、或改成 `math.isfinite` 之外的判断），因子与风控就会
**静默算出不同的数** —— 这类差异不会让任何测试翻红，除非有人专门对拍。

注：第三份藏在**私有名** `_safe_float` 下，按"同名函数"扫描是扫不到的（重名清单只覆盖
公开名）—— 这也是本门覆盖三处而不是两处的原因。

## 判据

1. **薄壳**：三个入口的函数体（去 docstring）必须只有一条 `return _shared_safe_float(...)`；
2. **行为对拍**：三者在 14 组边界输入上必须**同判**（`nan`/`±inf`/字符串形式/`None`/空串/
   列表/带空格字符串/bool…）；
3. **语义钉**：单一实现的既有怪癖逐条钉住（`nan`/`inf` ⇒ `default`；`True → 1.0`）——
   将来要改就是**有意识的**改动，而不是"顺手"。
"""

from __future__ import annotations

import ast
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINTS = (
    (ROOT / "scripts" / "factor_library.py", "safe_float"),
    (ROOT / "scripts" / "ai_brain_trader.py", "safe_float"),
    (ROOT / "scripts" / "calculus" / "regime.py", "_safe_float"),
)

#: 边界输入：三份实现曾经"看起来不同"但必须同判的地方。
BATTERY = (
    float("nan"), float("inf"), float("-inf"),
    "nan", "inf", "-inf", "abc", "", " 2 ", "1.5",
    None, 3, 0, -0.0, True, False, [1], {"a": 1}, 1e308 * 10,
)


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
    return a == b and type(a) is type(b) or (isinstance(a, float) and isinstance(b, float) and a == b)


def _load_wrapper(path: Path, name: str):
    """只加载该薄壳函数本体，并**注入**它依赖的单一事实源（隔离其余模块副作用）。"""
    from r20_backend.math_utils import safe_float as shared
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == name)
    ns: dict = {"_shared_safe_float": shared}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), ns)  # noqa: S102
    return ns[name]


class SafeFloatSingleSourceTest(unittest.TestCase):
    def test_all_three_entrypoints_are_shells(self):
        for path, name in ENTRYPOINTS:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            fn = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == name)
            body = [n for n in fn.body
                    if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                            and isinstance(n.value.value, str))]
            with self.subTest(path=path.name, fn=name):
                self.assertEqual(len(body), 1,
                                 "数值强转规则又长回函数体里了（应转调单一事实源）")
                call = body[0].value
                self.assertIsInstance(call, ast.Call)
                self.assertEqual(getattr(call.func, "id", ""), "_shared_safe_float",
                                 "薄壳必须转调 r20_backend.math_utils.safe_float")

    def test_battery_equivalence_across_entrypoints(self):
        from r20_backend.math_utils import safe_float as shared
        for path, name in ENTRYPOINTS:
            wrapper = _load_wrapper(path, name)
            for value in BATTERY:
                with self.subTest(entry=path.name, value=repr(value)):
                    expected = shared(value)
                    got = wrapper(value)
                    self.assertTrue(_same(expected, got),
                                    f"{path.name} 与单一事实源不同判：{value!r} → {got!r} vs {expected!r}")
            # 非默认 default 也必须同样透传
            with self.subTest(entry=path.name, default=-1.0):
                self.assertEqual(wrapper(float("nan"), -1.0), -1.0)

    def test_documented_semantics_of_the_single_source(self):
        from r20_backend.math_utils import safe_float
        for bad in (float("nan"), float("inf"), float("-inf"), "nan", "inf", "-inf",
                    "abc", None, "", [1], {"a": 1}):
            with self.subTest(bad=repr(bad)):
                self.assertEqual(safe_float(bad, 7.0), 7.0, f"{bad!r} 应回落 default")
        self.assertEqual(safe_float(" 2 "), 2.0)
        self.assertEqual(safe_float("1.5"), 1.5)
        self.assertEqual(safe_float(3), 3.0)
        # 既有怪癖：bool 走 float()，不是"非法值"
        self.assertEqual(safe_float(True), 1.0)
        self.assertEqual(safe_float(False), 0.0)
        # 默认 default
        self.assertEqual(safe_float("abc"), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

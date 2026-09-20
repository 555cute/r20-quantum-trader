"""通用数值工具（结构优化阶段 4·B3 第五十一刀）。

## 为什么新增这个模块

跨文件重复扫描发现 `clamp` 在**两处逐字相同**（5 行，只差一行 docstring）：

| 位置 | 用途 |
|---|---|
| `scripts/trader/signals.py` L32 | 策略权重夹取（`clamp(w, 0.7, 1.3, 1.0)`） |
| `scripts/self_improvement_engine.py` L82 | 资产倍数夹取（`clamp(m, 0.5, 1.5, 1.0)`） |

两者都在**风控/权重计算路径**上。两份拷贝若漂移（例如一处改成
`except Exception`、或去掉 `float()` 转换），会出现
**信号评分与自进化引擎对同一个配置值给出不同夹取结果**的不一致。

`clamp` 的语义有三条容易写错的细节，本模块把它们钉在一处：

1. **`float(value)` 转换**：字符串 `"1.4"` 要被接受（配置来自 JSON/env 时是字符串）；
2. **不可比较 → 返回 `default`**（而不是抛异常，也不是返回边界值）；
   捕获 `(TypeError, ValueError)` 两个 —— `None` 走 `TypeError`，
   `"abc"` 走 `ValueError`；
3. **边界顺序**：`max(lower, min(upper, x))`。`lower > upper` 时结果恒为 `lower`
   （先夹上界再抬下界，下界胜出）—— 这是既有行为，保留。

## ⚠️⚠️ `NaN` 会**静默变成上界**（既有行为，本刀不改）

`float("nan")` 转换**成功**，故不进 `except`；而 Python 的 `min`/`max`
在比较为 False 时返回**第一个参数**：

    min(upper, nan)  → upper      # upper 在前
    max(lower, upper) → upper     # upper > lower

**故 `clamp(nan, lo, hi, d)` 恒等于 `hi`**（`lo <= hi` 时）——
既不报错、也不返回 `default`，而是悄悄取**最宽的边界**。

这意味着 `NaN` 一旦流进 `clamp`（例如上游 JSON 里出现 `NaN`、
或除法产生了 NaN），会变成"顶格"的权重/倍数，**不会**被默认值兜住。
上游必须自己拦 —— 例如 `risk_gates.clamp_margin` 就用
`math.isfinite(c) and c > 0` 过滤掉非有限上限。

本刀**不修改**该行为：它同时影响信号评分与自进化引擎的既有取值，
改动属于业务语义变更（本仓红线）。此处只**记录**并**钉住**。

## ⚠️ 明确**不**收敛的兄弟函数（勿"顺手"合并）

本仓还有三个名字带 `clamp` 的函数，语义**各不相同**，**不得**用本模块替换：

| 函数 | 位置 | 为什么不能合并 |
|---|---|---|
| `clamp_leverage` | `r20_backend/execution/risk_gates.py` | 取 `min(全局上限, 池内 tier 派生上限)`、`0/None` 退化为**不夹**、夹动时 `print` 运维文案 |
| `clamp_margin` | 同上 | 取三道上限的**最小值**、只采纳 `>0 且有限` 的上限、夹动时 `print` |
| `clamp_council_timeout` | `r20_backend/council/roster.py` | 额外处理 `NaN`/`±inf`（返回默认值）、`round(num, 1)` |

它们不是"同一函数的拷贝"，而是**同一动词的不同业务规则**。
把它们强行统一会改变风控行为 —— 本仓红线。

## ⚠️ 依赖方向

本模块只依赖标准库内置，**不** import `scripts` 或任何门面，
故两个消费方都能安全依赖它、不引入新的模块耦合。

两个消费方各自保留**同名薄壳**（`clamp = ...` 别名赋值会让
`patch.object(模块, "clamp")` 这类接缝失效）。
"""

from __future__ import annotations

__all__ = ["clamp", "safe_float"]


def clamp(value, lower, upper, default):
    """把 `value` 夹到 `[lower, upper]`；不可比较时返回 `default`。

    语义细节见模块文档（`float()` 转换、捕获哪两种异常、边界顺序）。
    """
    try:
        return max(lower, min(upper, float(value)))
    except (TypeError, ValueError):
        return default


def safe_float(val, default: float = 0.0) -> float:
    """把 `val` 转成有限浮点；**转不出有限值就返回 `default`**（数值强转的单一事实源）。

    第一百五十刀：本语义在仓内有**三份**逐条等价的实现
    （`scripts/factor_library.py`、`scripts/ai_brain_trader.py` 的公开同名函数，以及
    `scripts/calculus/regime.py` 的私有 `_safe_float`）。它们恰好一致（我按 14 组输入做过
    行为对拍：`nan`/`±inf`/`'nan'`/`'inf'`/`None`/`''`/`[1]`/`' 2 '`/`True` …全部同判），
    但**逐字重复三次**意味着：任何一次"顺手优化"都会让因子与风控算出的数**静默不同**
    —— 比读取失败更隐蔽，因为不报错、没日志、结果看起来还很合理。

    语义（**逐条钉住，勿"顺手"改**）：

    - `nan` / `inf` / `-inf`（含字符串形式）⇒ `default`；
    - `None` / `''` / 不可转类型（list 等）⇒ `default`；
    - 可转的字符串（含前后空格）⇒ 其数值；
    - `bool` 走 `float()` ⇒ `True→1.0` / `False→0.0`（这是既有怪癖，**不是**要改成 `default`）。
    """
    if val is None:
        return default
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    return f if f == f and abs(f) != float("inf") else default

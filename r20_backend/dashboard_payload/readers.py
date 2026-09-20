"""仪表盘载荷装配 —— 通用读取器。

本包是结构性重构（plan_local/R20_STRUCTURE_OPTIMIZATION_20260914.md 阶段 2）的落点：
`r20_backend/dashboard_cache.py` 里那个 1021 行的 `update_cache_cycle` 要按数据域拆出来，
这里先承载最基础、最无副作用的一层 —— 本地文件读取。

语义约定（与拆分前的内联写法**逐条等价**，这是本次重构的安全底线）：
- 文件不存在 / 读不动 / JSON 解析失败 → 一律回退到 `default`，绝不抛错；
  `r20_backend/dashboard_cache.py` 原来的写法是 `if os.path.exists(...): try: ... except: pass`，
  这里直接 `try: open(...) except OSError` —— 结果相同，且没有 exists→open 的 TOCTOU 窗口。
- 调用方若要区分「读不到」与「读到空」，不要用这些函数（它们刻意把两者合并为 default）。

本模块只依赖标准库；**不得**反向 import `r20_backend.dashboard_cache`（会与
routers/dashboard.py 的 `import r20_backend.dashboard_cache` 构成环）。
"""
from __future__ import annotations

import json
import os
from typing import Any

__all__ = ["read_json", "read_text", "read_text_lines"]


def read_json(path: str | os.PathLike[str], default: Any) -> Any:
    """读 JSON；任何失败都返回 default。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        return default


def read_text(path: str | os.PathLike[str], default: str = "") -> str:
    """读文本全文；任何失败都返回 default。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return default


def read_text_lines(
    path: str | os.PathLike[str], limit: int, strip: bool = True
) -> list[str]:
    """读文本末尾 limit 行；任何失败都返回 []。

    strip=True 时丢弃空行（对齐 r20_backend/dashboard_cache.py 原日志读取语义：
    `[l.strip() for l in lines[-60:] if l.strip()]`）。
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except (OSError, UnicodeDecodeError):
        return []
    tail = lines[-limit:] if limit > 0 else []
    if strip:
        return [line.strip() for line in tail if line.strip()]
    return [line.rstrip("\n") for line in tail]

def load_json_dict_disclosed(path: str | os.PathLike[str]) -> tuple[dict, str]:
    """读 JSON dict，返回 `(数据, 错误文本)`（第 52 刀）。

    为什么要有它：面板侧同一个读取语义此前**存在两份实现**（`factors` 与
    `ledger_view`），且两份都是 `except Exception: return {}` ——
    这正是本仓反复吃过的两个坑叠在一起：

    1. **同一语义两处写 ⇒ 必然漂移**（两份签名还不一样：一份吃文件路径、一份吃目录）；
    2. **读不到被渲染成"没有"**：追踪器读失败 ⇒ 面板给仓位补的风控字段静默缺失，
       读者看不出"是没数据还是没读到"。

    约定（对齐 `docs/FAILURE_SEMANTICS.md`）：

    - 文件**不存在** ⇒ `({}, "")`（合法空态，不吵）；
    - **读不出来/不是 dict** ⇒ `({}, 原因)` 并打印一行 warn（**返回空值不变**，
      只补披露；调用方签名与行为保持兼容）。
    """
    text = os.fspath(path)
    if not os.path.exists(text):
        return {}, ""
    try:
        with open(text, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:      # noqa: BLE001 - 面板侧不得因一个文件炸掉整个载荷
        reason = f"追踪/状态文件读不出来: {exc!r}"
        print(f"[面板] warn {os.path.basename(text)} {reason}（相关字段将显示为空，"
              "请勿据此判断\"没有数据\"）")
        return {}, reason
    if not isinstance(data, dict):
        reason = f"顶层应为 dict，实为 {type(data).__name__}"
        print(f"[面板] warn {os.path.basename(text)} {reason}（形状不对 ⇒ 字段显示为空）")
        return {}, reason
    return data, ""

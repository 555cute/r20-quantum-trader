"""止损冷却的**单一事实源**（结构优化阶段 4·B3 第五十刀）。

## 为什么新增这个模块

`scripts/ai_factor_trader.py`（活交易路径）与
`r20_backend/execution/circuit_breaker.py`（后端风控面）**各自内联**了同一套
止损冷却读写。两个文件同名的顶层函数有 7 个，其中 3 个是**逐字/等价重复**：

| 函数 | 状态 |
|---|---|
| `is_in_stop_cooldown` | **逐字节相同**（10 行 × 2） |
| `_read_stop_cooldowns_state` | 等价（14 行 / 12 行，差在 `os.path.exists` vs `Path.exists` 与类型注解） |
| `load_stop_cooldowns` | 等价（均为 `_read_stop_cooldowns_state()[0]`） |

**此刻不是 bug**，但它是「孪生漂移」的典型现场：本仓
`r20_backend/execution/sizing.py` 的注释已经记录过同源问题并用同样办法修过 ——

> 审计 P1-1(2026-09-13)：这两条 `min()` 口径曾在本文件与 `ai_factor_trader`
> 各存一份拷贝……

冷却判定直接决定「硬止损后能否**立即同向重进**」。两份拷贝若漂移
（例如一侧改成读取失败按"无冷却"处理），就会出现
**交易侧认为可重进、风控面认为仍在冷却**（或反之）的不一致 ——
这正是审计③(2026-09-13) 修的那个 fail-closed 语义：
`corrupt=True` 必须按「仍在冷却」处理，否则损坏的冷却文件等价于无冷却。

## ⚠️ 为什么参数是**显式传入**的，而不是模块内自己读常量

两个调用方各自持有**可被 patch 的**模块级全局：

- `scripts/ai_factor_trader.py`：`STOP_COOLDOWN_FILE`（**str**）
- `r20_backend/execution/circuit_breaker.py`：`STOP_COOLDOWN_FILE`（**Path**）

测试**同时** patch 两边（见
`tests/audit/test_audit_batch3_persistence_atomic.py` 里
`patch.object(aft, "STOP_COOLDOWN_FILE", f)` 与
`patch.object(cb, "STOP_COOLDOWN_FILE", Path(f))` 并列出现）。

若本模块自己 `import` 任一方的常量，就会在 import 期烘焙副本 →
**patch 静默失效**（读真实 `data/stop_cooldown.json`），
属于本仓已实证的"测试写进生产 data/"事故类型。
故本模块**不读任何模块级路径常量**，冷却文件与冷却时长一律由调用方
在**调用时**解析自己的全局后传入。

⚠️ 因此本模块只依赖标准库，且**不** import `scripts` 或任何门面。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def read_stop_cooldowns_state(cooldown_file) -> Tuple[Dict[str, Any], bool]:
    """读冷却文件，返回 `(data, corrupt)`。

    ⚠️ **损坏与缺失不同权**（审计③ 2026-09-13 的核心语义）：

    - 文件**不存在** → `({}, False)`：真的没有冷却记录；
    - 文件存在但**解析失败 / 不是 dict** → `({}, True)`：
      不可判定，调用方必须按「仍在冷却」fail-closed。

    旧实现损坏时返回 `{}`，等价于「无冷却」→ **硬止损后可立即同向重进**。
    """
    path = Path(cooldown_file)
    if not os.path.exists(path):
        return {}, False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}, True
        return data, False
    except Exception:
        return {}, True


def is_in_stop_cooldown(inst_id: str, side: str, cooldown_file,
                        cooldown_seconds: int) -> bool:
    """`inst_id` 的 `side` 方向是否仍在止损冷却期内。

    ⚠️ fail-closed：读取状态 corrupt 时返回 `True`（不放松）。
    ⚠️ `cooldowns[key].get("ts", 0)` 保留原语义：缺 `ts` 的条目按 epoch 0 计算，
    即**视为早已过期**。（这是既有的怪癖行为，勿"顺手"改成 fail-closed。）
    """
    cooldowns, corrupt = read_stop_cooldowns_state(cooldown_file)
    if corrupt:
        return True  # 不可判定=不放松：损坏按仍在冷却处理
    key = f"{inst_id}_{side}"
    if key in cooldowns:
        rem_sec = cooldown_seconds - (int(time.time()) - cooldowns[key].get("ts", 0))
        if rem_sec > 0:
            return True
    return False


def load_stop_cooldowns(cooldown_file) -> Dict[str, Any]:
    """兼容旧契约的**只读展示面**：只返回数据，丢弃 corrupt 标记。

    ⚠️ 风控判断路径一律走 `is_in_stop_cooldown` / `read_stop_cooldowns_state`，
    不要用本函数做放开判定（它拿不到 corrupt 信号）。
    """
    return read_stop_cooldowns_state(cooldown_file)[0]

def add_stop_cooldown(inst_id: str, side: str, cooldown_file, *, reason: str = "止损冷却",
                      atomic_write_json, log=print) -> None:
    """登记一笔止损冷却（**写入路径的单一事实源**，第一百四十八刀）。

    收敛历史：读取路径在结构优化阶段 4·B3 第五十刀已收敛到本模块，但**写入**一直有
    两份等价实现（`r20_backend/execution/circuit_breaker.py` 与
    `scripts/ai_factor_trader.py`），只差一句提示文案 —— 正是本仓反复吃过的
    "同一语义两处写 ⇒ 必然漂移"（此处漂移的代价是：冷却登记规则一变，两个进程
    可能一个记一个不记，而"止损后能否立刻反手"直接取决于它）。

    两条规则（与既有实现逐条等价，勿"顺手"改）：

    1. **状态损坏 ⇒ 拒绝合并写回**（保全现场，宁可这笔冷却不登记，也不覆盖掉现场：
       读取侧对损坏按"仍在冷却"fail-closed，故保守方向一致）；
    2. 落盘失败 ⇒ 只告警（本笔冷却丢失，依赖云端 SL 兜底）。
    """
    cooldowns, corrupt = read_stop_cooldowns_state(cooldown_file)
    if corrupt:
        log(f"[止损冷却] CRITICAL 状态文件损坏，拒绝合并写回以保全现场"
            f"（期间所有标的按『仍在冷却』fail-closed）: {cooldown_file}")
        return
    key = f"{inst_id}_{side}"
    cooldowns[key] = {
        "instId": inst_id,
        "side": side,
        "ts": int(time.time()),
        "reason": reason,
    }
    try:
        atomic_write_json(cooldown_file, cooldowns)
    except Exception as e:      # noqa: BLE001 - 冷却丢失只告警，绝不打断平仓流程
        log(f"[止损冷却] warn 落盘失败（本笔冷却丢失，依赖云端SL兜底）: {e}")

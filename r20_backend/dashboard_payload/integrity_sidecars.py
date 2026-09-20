"""数据完整性旁车 → `source_errors`（结构优化阶段 4·B3 第二十七刀）。

原样搬自 `r20_backend/dashboard_cache.py::update_cache_cycle` 的两段「旁车并入」块（共 37 行）。

## 这两段在做什么

仪表盘载荷里 `data_health.status` 只有 `LIVE` / `PARTIAL` 两态，判据是
**`source_errors` 是否为空**。于是"数据不全"必须**主动变成一条 source_error**，
否则面板会以「完整」示人 —— 这正是历史上两次真实故障的形态：

| 旁车 | 曾经的故障形态 |
|---|---|
| `ledger_sync_status.json` | binance/gate 拉取失败时数据仍显示「完整」；分页化后「历史未取尽」也不可见 |
| `ai_health.json` | AI 批次决策连续失败（04:45 起 14 轮停摆），巡检却「全绿」 |

两段都是**尽力而为**：文件缺失 / 解析失败 / 结构不符一律**静默跳过**
（外层 `except Exception: pass`），绝不让"读旁车"本身拖垮整个仪表盘载荷。

## 四处易错点（均原样保留）

1. **新鲜度窗口是 2700 秒（45 分钟）**，且按 `generated_at` **自带时区**比较
   （`datetime.datetime.now(_gen.tzinfo) - _gen`）—— 不是按本地时间裸减。
   解析失败 → `_fresh = False` → **整段跳过**（过旧由台账文件新鲜度通道兜底 STALE）。
2. **`failed` 与 `truncated` 是 `elif` 关系**：同一所要么报"同步失败"要么报
   "分页未取尽"，不会两条都报。
3. **原因串截断到 120 字符**（`[:120]`）—— 防止把整段异常正文灌进载荷。

   ⚠️ 这里有个**负向验证发现的冗余**：`os.path.exists()` 守卫其实**不承重**。
   去掉它之后 `open()` 会抛 `FileNotFoundError`，被同一层 `except Exception`
   吞掉，**行为完全一致**（缺文件仍然静默）。保留它无害（少一次异常往返），
   但我不假装它被测试覆盖 —— 与 §29.4 那个"第二个 `replace` 永不生效"同类。
4. **`ai_health` 的阈值是 `>= 2`**，不是 `>= 1`：单次失败不降 PARTIAL
   （瞬时抖动），**连续 2 轮**才报。

## 与门面的分工

`source_errors` 由调用方传入并**原地 append**（与 `algo_protection` / `position_view`
的"入参原地改"约定一致）。`data_dir` 与 `datetime` 由调用方注入 ——
门面同名名字会被测试重定向，import 期绑定会让补丁失效。
"""
from __future__ import annotations

import json
import os
from typing import Any, List

__all__ = ["merge_ledger_sync_status", "merge_ai_health_failures",
           "LEDGER_STALE_SECONDS", "AI_CONSECUTIVE_FAILURE_THRESHOLD",
           "REASON_TRUNCATE"]

#: 台账同步状态旁车的新鲜度窗口：45 分钟。过旧则整段跳过。
LEDGER_STALE_SECONDS = 2700
#: AI 连续失败阈值：**≥2** 才降 PARTIAL（单次是瞬时抖动）
AI_CONSECUTIVE_FAILURE_THRESHOLD = 2
#: 失败原因串截断长度，防止整段异常正文灌进载荷
REASON_TRUNCATE = 120


def merge_ledger_sync_status(source_errors: List[str], data_dir, *, datetime) -> None:
    """把 `ledger_sync_status.json` 的失败/截断状态并进 `source_errors`（原地）。

    - 文件不存在 / 解析失败 / 结构不符 → 静默跳过；
    - `generated_at` 解析失败或超过 2700 秒 → 静默跳过；
    - 某所 `status == "failed"` → 追加一条；否则若 `truncated`/`truncated_at`
      存在 → 追加"分页未取尽"（两者是 **elif**，不重复报）。
    """
    try:
        _lss = os.path.join(data_dir, "ledger_sync_status.json")
        if os.path.exists(_lss):
            with open(_lss, "r", encoding="utf-8") as _f:
                _ls = json.load(_f)
            _fresh = True
            try:
                _gen = datetime.datetime.fromisoformat(str(_ls.get("generated_at") or ""))
                _fresh = (datetime.datetime.now(_gen.tzinfo) - _gen).total_seconds() <= LEDGER_STALE_SECONDS
            except Exception:
                _fresh = False
            if _fresh:
                for _v, _d in (_ls.get("venues") or {}).items():
                    if isinstance(_d, dict) and _d.get("status") == "failed":
                        source_errors.append(f"ledger-{_v}: 台账同步失败({str(_d.get('reason') or '')[:REASON_TRUNCATE]})，所盈亏/日亏数据不全")
                    elif isinstance(_d, dict) and (_d.get("truncated") or _d.get("truncated_at")):
                        # 批C：分页化后该标记仅在「历史分页未取尽且仍停在基线窗口之内」时出现
                        # （早期版本按单页 len>=100 反推，会把「已覆盖在册窗口」误报成截断）。
                        source_errors.append(f"ledger-{_v}: 历史分页未取尽（仍在基线窗口内），可能存在截断")
                # 无主活动持仓（2026-09-20 实测 ARB/binance -2416.7）：交易所有仓、
                # 却不在准入清单 ⇒ **没进台账** ⇒ 风险界面看不到，而开仓侧又把它当
                # "外部仓"永久拒开。这条必须显式上报：它意味着**可能无人管理的敞口**。
                _unm = _ls.get("unmanaged_positions")
                if isinstance(_unm, dict) and int(_unm.get("count") or 0) > 0:
                    _items = [i for i in (_unm.get("items") or []) if isinstance(i, dict)]
                    _desc = ", ".join(
                        f"{i.get('instId')} {i.get('size')}" if i.get("size") is not None
                        else str(i.get("instId") or "?") for i in _items[:3])
                    _more = (f" 等 {int(_unm.get('count'))} 笔"
                             if int(_unm.get("count")) > len(_items[:3]) else "")
                    source_errors.append(
                        f"ledger: {int(_unm.get('count'))} 个活动持仓不在准入清单、未进台账"
                        f"（风险界面看不到，可能无人管理）: {_desc}{_more}")
    except Exception:
        pass


def merge_ai_health_failures(source_errors: List[str], data_dir) -> None:
    """把 `ai_health.json` 的连续失败并进 `source_errors`（原地）。

    阈值 `>= 2`：单次失败不降 PARTIAL（瞬时抖动），连续 2 轮才报。
    """
    try:
        _ah_path = os.path.join(data_dir, "ai_health.json")
        if os.path.exists(_ah_path):
            with open(_ah_path, "r", encoding="utf-8") as _f:
                _ah = json.load(_f)
            _cf = int(_ah.get("consecutive_failures", 0) or 0)
            if _cf >= AI_CONSECUTIVE_FAILURE_THRESHOLD:
                source_errors.append(f"ai-inference: AI决策链连续{_cf}轮失败({str(_ah.get('last_error') or '')[:REASON_TRUNCATE]})，本轮无新指令")
    except Exception:
        pass


def merge_all_integrity_sidecars(source_errors: List[str], data_dir, *, datetime) -> None:
    """两段一起跑（门面按原顺序调用，保持 `source_errors` 里的条目顺序）。"""
    merge_ledger_sync_status(source_errors, data_dir, datetime=datetime)
    merge_ai_health_failures(source_errors, data_dir)

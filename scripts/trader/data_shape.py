"""生产数据产物的**形状校验**（第 49 刀从测试搬进运行时）。

出处与用法见 `docs/FAILURE_SEMANTICS.md`：本模块负责手册里"**读到了、但形状不对**"
那一半 —— 形状不合规会让下游做错决策，或在**周期中途**抛异常（其后相位整段跳过）。

分两层：

| 层 | 判据 | 后果 |
|---|---|---|
| 测试层（`tests/core/test_live_artifact_shape.py`） | 活体文件形状 + 永久负例 | 提交前拦住 |
| 运行时层（`cycle_stages.data_shape_preflight_stage`） | 周期开跑前**只读预检**并打印 | 运行时**尽早、指名**暴露 |

⚠️ 运行时层刻意**只警告、不阻断**：加载侧的 fail-closed 已经负责行为（意图读不出来 ⇒
不撤单 + 禁新开仓；追踪器读不出来 ⇒ 拒绝覆盖），预检负责的是**让人看见**那些
"读得到但会被静默忽略"的形状问题（典型：追踪器键名拼写不合约定 ⇒ 水位静默丢失）。

判据只钉**代码自己保证的结构不变量**（类型/键名/单调性），不钉业务取值；
每条违规都带**下游后果**，便于直接定位影响面。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

#: 追踪器键约定：`f"{instId}_{side}"`（见 factors/position_universe）
TRACKER_KEY_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-]*_(long|short)$")


def validate_intents(raw):
    """校验开仓意图文件形状，返回 (违规列表, 校验条数)。"""
    bad = []
    if not isinstance(raw, list):
        return ([f"顶层应为 list，实为 {type(raw).__name__}："
                 "`load_open_intents` 会判为不可读 ⇒ 撤单与开仓双双 fail-closed（周期停摆）"], 0)
    now_ms = time.time() * 1000
    seen = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            bad.append(f"[{i}] 不是 dict（{type(item).__name__}）⇒ 归属判定会跳过该条")
            continue
        inst, side, ts = item.get("instId"), item.get("side"), item.get("ts")
        if not isinstance(inst, str) or not inst.strip():
            bad.append(f"[{i}] instId 缺失/非字符串（{inst!r}）⇒ 该意图无法归属任何挂单")
        if not isinstance(side, str) or not side.strip():
            bad.append(f"[{i}] side 缺失/非字符串（{side!r}）⇒ 同标的反向挂单会被误判为孤儿")
        if not isinstance(ts, int) or isinstance(ts, bool):
            bad.append(f"[{i}] ts 应为毫秒整数（{ts!r}）⇒ TTL 过期判定会异常")
        elif ts > now_ms + 60_000:
            bad.append(f"[{i}] ts 在未来（{ts}）⇒ TTL 判定会把活意图当过期")
        if isinstance(inst, str) and isinstance(side, str):
            key = (inst, side)
            if key in seen:
                bad.append(f"[{i}] 与 [{seen[key]}] 重复 (instId, side) ⇒ "
                           "对账取最新一条，重复会让归属结果不确定")
            seen[key] = i
    return bad, len(raw)


def validate_trackers(raw):
    """校验持仓追踪文件形状，返回 (违规列表, 校验条数)。"""
    bad = []
    if not isinstance(raw, dict):
        return ([f"顶层应为 dict，实为 {type(raw).__name__}："
                 "`load_trackers` 会判为不可读 ⇒ 加仓上限 fail-closed、拒绝覆盖（含水位丢失）"], 0)
    for key, t in raw.items():
        if not TRACKER_KEY_RE.match(str(key)):
            bad.append(f"键 {key!r} 不符合 `<instId>_<long|short>` 约定 ⇒ 归属/水位查找会落空")
        if not isinstance(t, dict):
            bad.append(f"{key}: 值应为 dict（实为 {type(t).__name__}）⇒ 下游 `.get` 链会异常")
            continue
        if "scale_count" in t:
            sc = t["scale_count"]
            if not isinstance(sc, int) or isinstance(sc, bool) or sc < 0:
                bad.append(f"{key}: scale_count={sc!r} 非非负整数 ⇒ 入场循环 "
                           "`int(tracker.get(\"scale_count\", 0))` 会 TypeError（周期中途中断）")
        for num_key in ("trailingStopPx", "highWaterMark", "lowWaterMark",
                        "takeProfitPx", "entryTs"):
            if num_key in t and t[num_key] is not None:
                v = t[num_key]
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    bad.append(f"{key}: {num_key}={v!r} 非数值 ⇒ 移动止损比较会 TypeError")
    return bad, len(raw)


def read_json_safe(path) -> tuple:
    """只读读取 JSON：返回 `(对象, 错误文本)`（读不到时对象为 None，错误文本非空）。

    刻意不抛异常：预检是"报告器"，不是"判定器"（判定在加载侧）。
    """
    p = Path(path)
    if not p.exists():
        return None, ""          # 文件不存在 = 合法空态，不算违规
    try:
        return json.loads(p.read_text(encoding="utf-8")), ""
    except Exception as exc:      # noqa: BLE001 - 预检要报告一切读取失败
        return None, f"{p.name} 读不出来：{exc!r}（加载侧会 fail-closed；请检查文件）"


def validate_intents_file(path) -> list:
    """读并校验意图文件，返回违规行列表（读不出来 ⇒ 一条说明性违规）。"""
    raw, err = read_json_safe(path)
    if err:
        return [err]
    if raw is None:
        return []
    bad, _checked = validate_intents(raw)
    return bad


def validate_trackers_file(path) -> list:
    """读并校验追踪文件，返回违规行列表（读不出来 ⇒ 一条说明性违规）。"""
    raw, err = read_json_safe(path)
    if err:
        return [err]
    if raw is None:
        return []
    bad, _checked = validate_trackers(raw)
    return bad

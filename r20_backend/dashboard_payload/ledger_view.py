"""台账读取与生命周期成交筛选（结构优化阶段 2·B2 第八刀）。

从 update_cache_cycle 第 7 段迁出。注意其中「测试封闭闸」的语义：
autosync_enabled 以门面模块导入时的快照值注入，绝不在调用时重读环境变量。
"""
from __future__ import annotations

import json

from r20_backend.dashboard_payload.readers import load_json_dict_disclosed
import os
import time

from r20_backend.time_utils import beijing_text, parse_beijing
from scripts.evolution.observability import (
    classify_snapshot_observability,
    prune_snapshot,
)

__all__ = ["load_ledger_lifecycle_trades", "LEDGER_TRADES_MAX"]

#: 台账视图一次下发的**最大逐笔行数**（唯一事实源）。
#:
#: ⚠️ 2026-09-16：这个常量此前是 `[:60]` 的字面量，而 `slim.py` 的
#: `SLIM_TRADES` 另写了 20 —— 于是 `/api/all` 默认瘦身把台账**静默**砍到最近
#: 20 笔（34 笔里丢 14 笔），而台账页的「累计平仓/胜率/净盈亏/手续费」全部
#: 在这个被砍的切片上聚合，页面却没有任何截断提示（前端从不读
#: `_meta.omitted`）—— 既是少数据，也是 UI 说谎。两处上限现已同源：
#: slim 侧不能再比本上限更紧，否则台账页必然少行。
LEDGER_TRADES_MAX = 60

#: 逐单可观测性标签的合法取值（与 `scripts/evolution/observability.py` 同源）。
_OBSERVABILITY_TAGS = ("DYNAMICS_OBSERVED", "PARTIAL", "PRICE_ONLY", "NONE")

#: 台账成交行可能携带开仓快照的字段名（历史上不同写入路径用过不同键）。
_SNAPSHOT_KEYS = ("signal_snapshot", "entry_snapshot", "snapshot")

SNAPSHOT_MAX_STALE_SECONDS = 6 * 3600
SNAPSHOT_MAX_POST_FILL_LAG_SECONDS = 1200
SIDE_ALIASES = {"多": "long", "空": "short", "long": "long", "short": "short"}


def load_signal_journal_by_inst(data_dir: str) -> dict[str, list[dict]]:
    """读取开仓时刻数理快照日志（按标的分组），用于台账真实因果对齐。"""
    journal_file = os.path.join(data_dir, "signal_journal.json")
    by_inst: dict[str, list[dict]] = {}
    if not os.path.exists(journal_file):
        return by_inst
    try:
        with open(journal_file, "r", encoding="utf-8") as f:
            for rec in json.load(f):
                inst = str(rec.get("name") or rec.get("inst") or "")
                if inst:
                    by_inst.setdefault(inst, []).append(rec)
    except Exception:
        pass
    return by_inst


def load_position_trackers(data_dir: str) -> dict[str, dict]:
    """读取活动持仓追踪器（含持仓中开仓数理快照）。"""
    tracker_file = os.path.join(data_dir, "position_trackers.json")
    # 第 52 刀：与 `factors.load_position_trackers` 收敛到**同一实现**（此前两份、签名还不同，
    # 是"同一语义两处写 ⇒ 必然漂移"的典型）；读不到仍返回 {}，但会打 warn 披露。
    return load_json_dict_disclosed(tracker_file)[0]


def match_trade_snapshot(
    journal_by_inst: dict[str, list[dict]],
    inst: str,
    open_time: str | None,
    side: str | None = None,
) -> dict | None:
    """按方向与开仓时间严格就近匹配开仓时刻快照（与自进化复盘同源因果铁律）。

    因果铁律：
    1. 方向必须一致（多/空 ↔ long/short 对齐）；
    2. 时间窗口限定在 [-6h, +20m]，容纳 15 分钟巡检捕获时差；
    3. 禁止远期未来快照（>20m 绝非开仓因果）；
    4. 禁止过期快照（>6h 弃用）；
    5. 无快照或未匹配绝不倒推编造。
    """
    candidates = journal_by_inst.get(inst) or []
    open_dt = parse_beijing(open_time)
    if not candidates or open_dt is None:
        return None
    open_dt = open_dt.replace(tzinfo=None)
    wanted_side = SIDE_ALIASES.get(str(side or "").strip())
    best_diff, best_rec = None, None
    for rec in candidates:
        rec_side = SIDE_ALIASES.get(str(rec.get("side") or "").strip())
        if wanted_side and rec_side and rec_side != wanted_side:
            continue
        rec_dt = parse_beijing(rec.get("entryTime"))
        if rec_dt is None:
            continue
        rec_dt = rec_dt.replace(tzinfo=None)
        delta_sec = (rec_dt - open_dt).total_seconds()
        if delta_sec < -SNAPSHOT_MAX_STALE_SECONDS or delta_sec > SNAPSHOT_MAX_POST_FILL_LAG_SECONDS:
            continue
        abs_diff = abs(delta_sec)
        if best_diff is None or abs_diff < best_diff:
            best_diff, best_rec = abs_diff, rec
    return (best_rec or {}).get("snapshot")


def classify_trade_observability(trade: dict, snap: dict | None = None) -> str:
    """逐单判定「开仓时刻数理快照」可观测性（前台台账展示用）。

    证据纪律：
    - 优先认传入的有效快照或该笔自身携带的证据（signal_snapshot / entry_snapshot / snapshot）；
    - 若快照具备动力学字段，按动力学链判定 DYNAMICS_OBSERVED / PARTIAL / PRICE_ONLY；
    - 缺失快照严格返回 NONE（明确标注不可观测，严禁事后倒推编造）。
    """
    if isinstance(snap, dict) and snap:
        return classify_snapshot_observability(snap)
    tag = str(trade.get("snapshot_observability") or "").upper()
    if tag in _OBSERVABILITY_TAGS:
        return tag
    for key in _SNAPSHOT_KEYS:
        candidate = trade.get(key)
        if isinstance(candidate, dict) and candidate:
            return classify_snapshot_observability(candidate)
    return "NONE"


def load_ledger_lifecycle_trades(ledger_file, workspace_dir, autosync_enabled, reset_time_str):
    """读取台账并筛出 reset_time 之后（或仍 holding）的生命周期成交。

    原样搬自 update_cache_cycle 第 7 段（52 行）。三个注入项都有讲究：
    - ledger_file：被测试 patch；
    - workspace_dir：用于定位 scripts/sync_full_ledger.py；
    - autosync_enabled：**模块导入时快照**的常量（批E 测试封闭闸）。
      不能改成「调用时读环境变量」——多个测试用 patch.dict(clear=True) 清空环境，
      会把标志一起抹掉。以门面常量的当前值注入即等价于原语义。
    """
    ledger_trades = []
    need_ledger_sync = True
    if os.path.exists(ledger_file):
        try:
            mtime = os.path.getmtime(ledger_file)
            if time.time() - mtime < 60:
                need_ledger_sync = False
        except Exception:
            pass

    # 批E(2026-09-13)·测试封闭闸：本触发点会 spawn 真实同步子进程（打三所接口 +
    # 重写 data/trading_ledger.json）。多个仪表盘测试走真实 DATA_DIR ⇒ 测试期间会
    # 打真网络并改写生产台账（违反「测试不触生产文件」）。
    # 注意：仅在调用时读 os.environ 不够——多个测试用 patch.dict(..., clear=True)
    # 清空整个环境，会把标志一起抹掉。故以**模块导入时快照**为准（tests/__init__.py
    # 在任何测试模块导入 r20_backend.dashboard_cache 之前置位），生产不设该变量 → 行为不变。
    _ledger_sync_disabled = (
        not autosync_enabled
        or str(os.environ.get("R20_LEDGER_SYNC_DISABLED", "")).strip().lower() in ("1", "true", "yes")
    )
    if need_ledger_sync and not _ledger_sync_disabled:
        try:
            sync_script = os.path.join(workspace_dir, "scripts", "sync_full_ledger.py")
            if os.path.exists(sync_script):
                # 审计批7：旧 `python3` shell 串在这台主机根本不存在（rc=127 被
                # capture_output 吞）→ 服务器侧台账刷新从未生效；且旧 timeout=10s
                # 短于真实三所全史拉取（约20-30s）必然静默超时。改同解释器+吼。
                from r20_backend.spawn import run_script
                run_script(sync_script, timeout=45, label="sync_full_ledger")
        except Exception:
            pass

    if os.path.exists(ledger_file):
        try:
            with open(ledger_file, "r", encoding="utf-8") as f:
                ledger_trades = json.load(f)
        except Exception:
            pass

    # Filter lifecycle trades past reset_time
    valid_ledger_trades = []
    for t in ledger_trades:
        # Check either close_time or open_time >= reset_time
        c_time = beijing_text(t.get("close_time"))
        o_time = beijing_text(t.get("open_time"))
        t_time = beijing_text(t.get("time"))
        if (c_time and c_time >= beijing_text(reset_time_str)) or (o_time and o_time >= beijing_text(reset_time_str)) or (t_time and t_time >= beijing_text(reset_time_str)) or t.get("status") == "holding":
            valid_ledger_trades.append(t)

    trades_table = valid_ledger_trades[:LEDGER_TRADES_MAX]

    # 逐单挂「数理快照可观测性」标签与真实快照载荷
    # 路径解析：优先使用 ledger_file 同级 data 目录，次选 workspace_dir/data
    data_dir = os.path.dirname(ledger_file)
    if not (data_dir and os.path.exists(os.path.join(data_dir, "signal_journal.json"))):
        ws_data = os.path.join(workspace_dir, "data")
        if os.path.exists(os.path.join(ws_data, "signal_journal.json")):
            data_dir = ws_data
        elif not data_dir:
            data_dir = workspace_dir

    journal_by_inst = load_signal_journal_by_inst(data_dir)
    trackers = load_position_trackers(data_dir)

    for _t in trades_table:
        if isinstance(_t, dict):
            snap = None
            for key in _SNAPSHOT_KEYS:
                candidate = _t.get(key)
                if isinstance(candidate, dict) and candidate:
                    snap = candidate
                    break
            if not snap and _t.get("status") == "holding":
                inst_raw = str(_t.get("inst") or "")
                raw_side = str(_t.get("side") or "").strip()
                side_str = "long" if raw_side in ("多", "long") else "short"
                for pos_k in (f"{inst_raw}_{side_str}", f"{inst_raw}-USDT-SWAP_{side_str}"):
                    tr = trackers.get(pos_k)
                    if isinstance(tr, dict) and tr.get("signal_snapshot"):
                        snap = tr["signal_snapshot"]
                        break
                if not snap:
                    calc_file = os.path.join(data_dir, "calculus_snapshot.json")
                    if os.path.exists(calc_file):
                        try:
                            with open(calc_file, "r", encoding="utf-8") as f_calc:
                                calc_data = json.load(f_calc)
                                for item in calc_data.get("instruments", []):
                                    if item.get("name") == inst_raw or item.get("instId") in (inst_raw, f"{inst_raw}-USDT-SWAP"):
                                        from scripts.trader.signal_snapshot import build_signal_snapshot
                                        f_mock = {
                                            "name": inst_raw,
                                            "price": float(_t.get("open_px") or _t.get("close_px") or 0.0),
                                            "atr": 0.0,
                                            "calculus": item.get("calculus", {})
                                        }
                                        snap = build_signal_snapshot(f_mock, data_dir=data_dir)
                                        break
                        except Exception:
                            pass
            if not snap:
                raw_side = str(_t.get("side") or _t.get("direction") or "")
                inst_name = str(_t.get("inst") or _t.get("name") or "")
                snap = match_trade_snapshot(journal_by_inst, inst_name, _t.get("open_time"), raw_side)

            # 跨所台账无 journal 历史时的数理快照兜底对齐
            if not snap:
                inst_raw = str(_t.get("inst") or _t.get("name") or "")
                calc_file = os.path.join(data_dir, "calculus_snapshot.json")
                if os.path.exists(calc_file):
                    try:
                        with open(calc_file, "r", encoding="utf-8") as f_calc:
                            calc_data = json.load(f_calc)
                            for item in calc_data.get("instruments", []):
                                if item.get("name") == inst_raw or item.get("instId") in (inst_raw, f"{inst_raw}-USDT-SWAP"):
                                    from scripts.trader.signal_snapshot import build_signal_snapshot
                                    f_mock = {
                                        "name": inst_raw,
                                        "price": float(_t.get("open_px") or _t.get("close_px") or 0.0),
                                        "atr": 0.0,
                                        "calculus": item.get("calculus", {})
                                    }
                                    snap = build_signal_snapshot(f_mock, data_dir=data_dir)
                                    break
                    except Exception:
                        pass

            pruned = prune_snapshot(snap) if isinstance(snap, dict) else None
            if pruned:
                _t["entry_snapshot"] = pruned
            _t["snapshot_observability"] = classify_trade_observability(_t, pruned)

    # 8-10. 本地读取（结构优化阶段 2·B2 第六刀：迁至 dashboard_payload/local_reads.py）
    return valid_ledger_trades, trades_table

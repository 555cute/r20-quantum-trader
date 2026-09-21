"""在途持仓 / 挂单的提示词文本渲染（结构优化阶段 4·B3 第三十刀）。

原样搬自 `scripts/brain/prompt.py::construct_full_market_prompt` 的两段文本装配
（`pos_lines` 28 行 + `pending_lines` 33 行）。

## 这两段在做什么

把账户的**在途持仓**与**在途未成交挂单**渲染成给模型看的文本块。
两段都有一条**"缺失 vs 空"的三态语义**，这是本刀最要紧的不变量：

| 入参 | 渲染结果 |
|---|---|
| `None` | `[MISSING_CONTEXT:...]` —— **上下文没给**（调用方漏传） |
| `[]`（空列表） | 「当前无任何在途持仓敞口 (100% 现金空仓状态)」—— **确实是空仓** |
| 非空 | 逐条列举 |

**两者绝不能合并**：`None` 是"我不知道"，`[]` 是"确定没有"。
把它们渲染成同一句话，模型就会把"上下文缺失"误读成"空仓"，进而放大仓位。

## 四处易错点（均原样保留）

1. **持仓的利润描述是"正反两个分支"**：
   多头看 `hwm`（历史最高）、空头看 `lwm`（历史最低），
   且**各有三重合条件**（方向、极值相对开仓价的方位、开仓价 > 0）。
   两个分支的"回撤"分母**不同**：多头是 `(hwm - entry_px)`，
   空头是 `(entry_px - lwm)`。
2. **挂单方向串有 `reduce_only` × 买卖 × 限价/市价 共 8 种组合**，
   且**非 reduce_only 分支的"买"判据不含 ordType**（`side_raw == "buy"` 即算买多），
   而"卖"判据含（`ord_type != "market"` 才是限价卖空）。
   这正是容易写错的地方：`reduce_only` 与普通单的**判定不对称**。
3. **价格展示有"0 视同未填"**：`px` 为空串**或**字面 `"0"` → 市价单显示「市价」，
   否则 `--`。
4. **`int(o.get("cTime", 0) or 0) / 1000.0`**：毫秒转秒，且**先 `or 0`**
   （`None` 与 `""` 都要能兜住），`<= 0` 时显示 `--`。

## 与门面的分工

`build_position_lines` 需要 `safe_float`（浮点兜底）与 `tz_bj`（挂单时间格式化），
两者**由调用方传入** —— 不 import 期绑定，见 `r20_backend/README.md` §5。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["build_position_lines", "build_pending_order_lines"]


def _orphan_legs_text(p: Dict[str, Any]) -> str:
    """该所**孤儿保护腿**的提示词片段（第一百七十六刀）。

    为什么值得告诉模型：孤儿腿是 `reduceOnly` 条件单 —— 同币**再开仓**后，价格触及
    **旧触发价**时会真的减掉新仓。模型据此可以避开"在有旧触发价的币上开新仓"这类坑。

    口径（与面板/指标一致）：
    - **只报告**：可归因候选（证据=本方标签或台账同向同量已平）与"归属不可判定"分开说；
    - 读腿失败 ⇒ 说"不可判定"（**不得**含糊成"没有孤儿腿"）；
    - 不提"撤销"动作 —— 系统绝不自动撤，那是运营的事。
    """
    info = p.get("protectionOrphans")
    if not isinstance(info, dict):
        return ""
    if info.get("readable") is False:
        return " | 该所孤儿腿: **不可判定**（保护腿读取失败）"
    attributed = info.get("attributed") if isinstance(info.get("attributed"), list) else []
    unattributed = info.get("unattributed") if isinstance(info.get("unattributed"), list) else []
    side_mism = info.get("sideMismatch") if isinstance(info.get("sideMismatch"), list) else []
    size_mism = info.get("sizeMismatch") if isinstance(info.get("sizeMismatch"), list) else []
    parts = []
    if attributed:
        syms = sorted({str(l.get("symbol") or "?") for l in attributed if isinstance(l, dict)})
        parts.append(f"可归因 {len(attributed)} 条（{','.join(syms[:4])}）"
                     "：同币再开仓时**可能按旧触发价减仓**，须人工核对")
    if unattributed:
        parts.append(f"归属不可判定 {len(unattributed)} 条（一律不碰）")
    # 第一百八十一刀：与面板/指标同口径 —— 两种 mismatch 的**语义不同**，必须分开说：
    # 反向腿保护不了本仓（不计覆盖），量不符的腿却**正在充当覆盖**（归属存疑）。
    if side_mism:
        parts.append(f"方向与本仓不符 {len(side_mism)} 条（**不计入覆盖**；可能是旧仓或手单）")
    if size_mism:
        parts.append(f"量与任何持仓都不符 {len(size_mism)} 条"
                     "（**仍被计入覆盖**，归属存疑，须人工核对）")
    foreign = info.get("foreignCount")
    if isinstance(foreign, int) and foreign > 0:
        parts.append(f"认不出类型 {foreign} 条（**不计入覆盖** ⇒ 若其实是保护腿，覆盖被低估）")
    unparsed = info.get("unparsedCount")
    if isinstance(unparsed, int) and unparsed > 0:
        parts.append(f"行解析不了 {unparsed} 条（**不计入覆盖**，属取数/形状问题）")
    if not parts:
        return ""
    ledger = "" if info.get("ledgerRows") == "ok" else "（台账未读到 ⇒ 可归因数可能偏少）"
    return " | 该所孤儿腿: " + "；".join(parts) + ledger


def build_position_lines(active_positions_detail: Optional[List[Dict[str, Any]]],
                         *, safe_float) -> str:
    """在途持仓文本块。

    `None` → `[MISSING_CONTEXT:account_positions]`；
    `[]` → 「当前无任何在途持仓敞口 (100% 现金空仓状态)」。
    """
    pos_lines = []
    #: 孤儿腿是**场所级**事实（挂在每行上）⇒ 每个所只提示一次，避免同一句刷 N 遍
    _orphan_seen: set = set()
    if active_positions_detail and len(active_positions_detail) > 0:
        for p in active_positions_detail:
            inst_name = p.get('name') or p.get('instId')
            side = p.get('side') or p.get('posSide', 'long')
            is_long = "long" in str(side).lower()
            entry_px = safe_float(p.get('avgPx', 0))
            cur_px = safe_float(p.get('markPx') or p.get('lastPx') or entry_px)
            hwm = safe_float(p.get('highWaterMark', 0))
            lwm = safe_float(p.get('lowWaterMark', 0))
            # ⚠️ 第一百一十九刀：原写法是 `p.get(k, 默认)` —— **键存在但值为 None 时
            # 回退不生效**，于是把字面量 `None` / 假的 `--` 喂给模型。
            # 真机实测（`data/dashboard_last_good.json` 里的 binance UNI 行）：
            #   `trailingStopPx: None` 而 `trailingSl: 9.025`、`exchangeTp: 8.365`、
            #   `stage_desc: None` 而 `stageDesc: '云端双腿防护中'`
            # ⇒ 模型被告知"动态止损线: -- / 目标止盈: -- / 状态: None"，
            # 而交易所那笔空仓**确实挂着** SL 9.025 + TP 8.365（云端双腿）。
            # 一律改成 `or` 链：空值继续往真实来源回退，最后才给 `--`。
            tp_px = p.get('takeProfitPx') or p.get('exchangeTp') or '--'
            stage_desc = p.get('stage_desc') or p.get('stageDesc') or '持有监控中'

            profit_desc = ""
            if is_long and hwm > entry_px and entry_px > 0:
                peak_gain_pct = round((hwm - entry_px) / entry_px * 100, 2)
                dd_from_peak = round((hwm - cur_px) / (hwm - entry_px) * 100, 1) if hwm > entry_px else 0.0
                profit_desc = f" | 曾最高到: {hwm} (极值浮盈 +{peak_gain_pct}%, 现已从极值回撤 {dd_from_peak}%)"
            elif not is_long and lwm > 0 and lwm < entry_px and entry_px > 0:
                peak_gain_pct = round((entry_px - lwm) / entry_px * 100, 2)
                dd_from_peak = round((cur_px - lwm) / (entry_px - lwm) * 100, 1) if lwm < entry_px else 0.0
                profit_desc = f" | 曾最低到: {lwm} (极值浮盈 +{peak_gain_pct}%, 现已从极值回撤 {dd_from_peak}%)"

            v_badge = f"[{str(p.get('venue', 'OKX')).upper()}] "
            # 止损线同样走 `or` 链（trailingStopPx → trailingSl → exchangeSl → `--`）
            sl_px = (p.get('trailingStopPx') or p.get('trailingSl')
                     or p.get('exchangeSl') or '--')
            # 保护判据（第一百一十八刀起面板/外所持仓都带）：让模型的态势认知
            # 与交易所事实一致 —— 缺口要显式说出来，不可判定**不得**含糊成"已保护"。
            _prot_txt = _protection_text(p)
            _venue_key = str(p.get("venue") or "OKX").upper()
            if _venue_key not in _orphan_seen:
                _orphan_seen.add(_venue_key)
                _prot_txt += _orphan_legs_text(p)
            pos_lines.append(
                f"- {v_badge}标的: {inst_name} | 方向: {side} {p.get('lever', p.get('leverage', '3'))}x | 开仓均价: {p.get('avgPx')} | 当前价: {cur_px} | 浮盈: {p.get('upl')} U (ROI: {round(safe_float(p.get('uplRatio')) * 100, 2)}%){profit_desc} | 动态止损线: {sl_px} | 目标止盈: {tp_px} | 状态: {stage_desc}{_prot_txt}"
            )
    else:
        pos_lines.append("[MISSING_CONTEXT:account_positions]" if active_positions_detail is None else "当前无任何在途持仓敞口 (100% 现金空仓状态)")

    return "\n".join(pos_lines)


#: 触发价类型 → 中文（第一百六十七刀）。交易所上报什么就说什么，不做二次推断。
_TRIGGER_TYPE_TEXT = {"mark": "标记价", "last": "最新成交价", "index": "指数价"}

_PROTECTION_TEXT = {
    "fully_protected": "完全保护",
    #: 没有覆盖率数字时**不宣称"覆盖不足"**（那是没有证据的结论）；有数字时由
    #: `_protection_text` 覆盖成"止损满量但缺止盈腿 / 止损仅覆盖 X%"。
    "partially_protected": "部分保护（覆盖量未知）",
    "unprotected": "⚠️ 无活止损腿",
    "unknown": "保护状态不可判定",
}


def _protection_text(p: Dict[str, Any]) -> str:
    """把保护判据渲染成提示词里的一段（没有判据就**不写**，不假装）。

    判据由面板侧算出（`dashboard_payload.multi_venue` / `algo_protection`）：
    `protectionStatus` + `cloud_oco_verified` + `protectionCoveragePct` +
    `protectionExpiry`。模型据此知道"这笔到底有没有活止损"——
    在此之前它只能看到一个可能说谎的止损价。
    """
    status = str(p.get("protectionStatus") or "").strip()
    if not status:
        return ""
    txt = _PROTECTION_TEXT.get(status, status)
    pct = p.get("protectionCoveragePct")
    _has_pct = isinstance(pct, (int, float))
    if status == "partially_protected":
        # ⚠️ 第一百二十一刀：修我自己上一刀造出的**自相矛盾文案**。
        # OKX 的判据里 `partially_protected` 的含义是"有匹配腿，但不是满量 SL+TP 双腿"
        # （`algo_protection`：`fully_protected` 要求同一腿同时有 SL 与 TP）——
        # 于是"只有满量止损、没有止盈"这种**下行已全覆盖**的仓也会落到这一档，
        # 而覆盖率是 100% ⇒ 旧文案渲染成「部分保护（覆盖不足） 100%」，自相矛盾。
        # 现在按覆盖率分开说：满量只缺止盈腿 / 止损只覆盖 X%。
        if _has_pct and float(pct) >= 99.9:
            txt = "部分保护（止损满量但缺止盈腿）"
        elif _has_pct:
            txt = f"部分保护（止损仅覆盖 {float(pct):g}%）"
    elif _has_pct and status != "unprotected":
        txt += f" {float(pct):g}%"
    # 第一百六十七刀：**按什么价触发**（OKX `slTriggerPxType`）。
    # 与触发价本身同等重要：按最新成交价触发的止损，一根插针就能打掉；
    # 读不到就说"未上报"（**不填**我们期望的类型，也不假装知道）。
    _sl_type = str(p.get("protectionSlTriggerPxType") or "").strip().lower()
    if _sl_type in _TRIGGER_TYPE_TEXT:
        txt += f"（止损按{_TRIGGER_TYPE_TEXT[_sl_type]}触发）"
    elif _sl_type == "unknown" and status != "unprotected":
        txt += "（止损触发价类型未上报）"
    expiry = str(p.get("protectionExpiry") or "").strip()
    if expiry == "expired":
        # "另有"是刻意的：`unprotected` 档下过期腿正是缺口本身，而 `partially_*` 档下
        # 说明**还有别的腿**过期 —— 两种情形都如实，不夸大也不含糊。
        txt += "（另有腿已过期）" if status in ("fully_protected", "partially_protected") else "（腿已过期）"
    elif expiry == "expiring":
        txt += "（腿临期）"
    elif expiry == "unknown" and status != "unprotected":
        txt += "（到期未知）"
    return f" | 保护: {txt}"


def build_pending_order_lines(pending_orders_detail: Optional[List[Dict[str, Any]]],
                              *, tz_bj, datetime) -> str:
    """在途未成交挂单文本块。

    `None` → `[MISSING_CONTEXT:pending_orders]`；
    `[]` → 「当前无任何在途未成交限价挂单 (挂单池为空)」。
    """
    pending_lines = []
    if pending_orders_detail and len(pending_orders_detail) > 0:
        for o in pending_orders_detail:
            c_ts = int(o.get("cTime", 0) or 0) / 1000.0
            c_time_str = datetime.datetime.fromtimestamp(c_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_ts > 0 else "--"
            inst_id = o.get("instId", "")
            side_raw = str(o.get("side", "")).lower()
            # 审计D(2026-09-13)：死局部清除——pos_side 从未参与下方展示/判定
            reduce_only = str(o.get("reduceOnly", "false")).lower() == "true"
            ord_type = str(o.get("ordType", "limit")).lower()

            if reduce_only:
                side_str = "市价平多" if (side_raw == "sell" and ord_type == "market") else ("限价平多" if side_raw == "sell" else ("市价平空" if ord_type == "market" else "限价平空"))
            else:
                side_str = "限价买多" if (side_raw == "buy" and ord_type != "market") else ("市价买多" if side_raw == "buy" else ("限价卖空" if ord_type != "market" else "市价卖空"))

            raw_px = str(o.get("px") or "").strip()
            px_val = raw_px if raw_px and raw_px != "0" else ("市价" if ord_type == "market" else "--")
            raw_sz = o.get("sz")
            try:
                sz_float = float(raw_sz or 0)
                sz_val = f"{abs(sz_float):g}" if sz_float != 0 else str(raw_sz if raw_sz is not None else "--")
            except (TypeError, ValueError):
                sz_val = str(raw_sz if raw_sz is not None else "--")
            ord_id = str(o.get("ordId", ""))

            attach_list = o.get("attachAlgoOrds", [])
            tp_sl_info = ""
            if attach_list and len(attach_list) > 0:
                att = attach_list[0]
                tp_p = att.get("tpTriggerPx", "--")
                sl_p = att.get("slTriggerPx", "--")
                tp_sl_info = f" | 附带云端止盈: {tp_p} / 止损: {sl_p}"

            pending_lines.append(
                f"- [挂单ID: {ord_id}] {inst_id} | {side_str} {sz_val}张 @ {px_val} | 挂单时间: {c_time_str}{tp_sl_info}"
            )
    else:
        pending_lines.append("[MISSING_CONTEXT:pending_orders]" if pending_orders_detail is None else "当前无任何在途未成交限价挂单 (挂单池为空)")

    return "\n".join(pending_lines)

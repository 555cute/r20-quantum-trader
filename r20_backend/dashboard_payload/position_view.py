"""OKX 持仓 → 仪表盘持仓行（结构优化阶段 4·B3 第二十二刀）。

原样搬自 `r20_backend/dashboard_cache.py::update_cache_cycle` 的「持仓遍历」段（71 行）。

## 这段在做什么

把 OKX `account/positions` 的一行原始持仓，规范化成前端直接消费的持仓行：
补齐 `notional_usdt`（名义价值）、`margin_usdt`（保证金）、`roi_pct`、
`price_change_pct`（价格变动）等**OKX 不直接给、需要推算**的字段，
并挂上持仓跟踪器（`position_trackers.json`）里的移动止损与阶段描述。

## 四处易错点（均原样保留）

1. **`pos == 0` 跳过**。OKX 会返回「已平但未消解」的零仓行；不跳过会在
   持仓列表里留下幽灵条目。
2. **`notional_usdt` 优先用交易所给的 `notionalUsd`**，只有它 ≤ 0 时才回退
   `张数 × 面值 × 标记价(或开仓价)`。回退公式里 `mark_px if mark_px > 0 else avg_px`
   这个二级兜底不能省 —— 标记价缺失时用开仓价，否则名义价值会算成 0。
3. **保证金优先用交易所给的 `imr`**，否则 `名义价值 / 杠杆`。同时输出
   `marginSource` 说明**这一行到底用了哪个口径** —— 这是前端"这个数字可不可信"
   的唯一线索，不能省。
4. **`uplRatio` 直接 ×100 当 ROI**，不用 `upl / margin` 重算。OKX 的 `uplRatio`
   已是**保证金收益率**，重算会因口径差异产生偏差。

## 与门面的分工

- `positions` 由调用方传入并**原地 append**（与 `algo_protection.py` 同约定 ——
  该文件的既有注释就是这样描述"入参原地改"的）。
- 三个计数器（`long_count` / `short_count` / `total_pos_upl`）是**跨行累加**状态，
  留在门面；本函数只处理单行。
- `load_instruments` 由调用方注入：门面同名函数会被测试 `patch.object`
  （见 `tests/ui/test_dashboard_payload_seam.py`），import 期绑定会让补丁失效。
"""
from __future__ import annotations

__all__ = ["collect_position_rows"]


def collect_position_rows(pos_data, positions, trackers, *, load_instruments):
    """把 OKX 持仓原始行规范化后 append 进 `positions`。

    返回 `(long_count, short_count, total_pos_upl)` 三行累加量的**本段增量**，
    由调用方加到自己的计数器上。

    `pos_data` 不是 list 时返回 `(0, 0, 0.0)`，与原文的
    `if isinstance(pos_data, list):` 守卫等价。
    """
    long_count = 0
    short_count = 0
    total_pos_upl = 0.0

    if isinstance(pos_data, list):
        for p in pos_data:
            pos_val = float(p.get("pos", 0.0) or 0.0)
            if pos_val == 0.0:
                continue

            # ⚠️ 第一百二十三刀：**净持仓模式**（OKX `posSide="net"`、Binance 原始
            # `positionSide="BOTH"`、字段缺失）原样透传会连坏两处：
            #   ① 多空计数**两边都不涨**（`"long" in "net"` 为假）⇒ 面板多空数偏小；
            #   ② 下游按"含 long 才算多"判断 ⇒ **净多头被当成空头** ——
            #      提示词的 `方向:` 与极值/回撤分支（`account_text`）、
            #      `factors.py` 的策略标签（净多头被贴"逢高做空"）。
            # 展示用 side 一律由**带符号持仓量**归一（此处 `pos_val` 已非 0）；
            # 交易所原值不必回传：跨所/OKX 的腿匹配集合里本来就含 `"net"`。
            _raw_pos_side = str(p.get("posSide") or p.get("side") or "").lower()
            if "long" in _raw_pos_side:
                pos_side = "long"
            elif "short" in _raw_pos_side:
                pos_side = "short"
            else:
                pos_side = "long" if pos_val > 0 else "short"
            if "long" in pos_side:
                long_count += 1
            elif "short" in pos_side:
                short_count += 1

            upl = float(p.get("upl", 0.0) or 0.0)
            total_pos_upl += upl

            pos_key = f"{p.get('instId')}_{p.get('posSide', 'net')}"
            t_info = trackers.get(pos_key, {})
            trailing_sl = t_info.get("trailingStopPx", "--")
            stage_desc = t_info.get("stage_desc", "持有监控中")
            strategy_tag = t_info.get("strategy_tag") or ("🌊 低吸" if "long" in pos_side else "⚡ 高空")

            avg_px = float(p.get("avgPx", 0) or 0)
            mark_px = float(p.get("markPx", 0) or 0)
            pos_sz = float(p.get("pos", 0) or 0)

            ct_val = 1.0
            inst_id_val = p.get("instId", "")
            for target_item in load_instruments():
                if target_item["instId"] == inst_id_val:
                    ct_val = target_item.get("ctVal", 1.0)
                    break

            okx_notional = float(p.get("notionalUsd", 0) or 0)
            okx_imr = float(p.get("imr", 0) or 0)

            notional_usdt = round(okx_notional if okx_notional > 0 else (pos_sz * ct_val * (mark_px if mark_px > 0 else avg_px)), 2)
            raw_upl_ratio = float(p.get("uplRatio", 0.0) or 0.0)
            real_roi_pct = round(raw_upl_ratio * 100, 2)
            price_chg = round(((mark_px - avg_px) / avg_px * 100) if avg_px > 0 else 0, 2)

            # ⚠️ 重构中发现并修复的既有缺陷（原实现：`float(p.get("lever", "3") or 3.0)`）。
            #
            # `or 3.0` 挡的是 **falsy**，而 OKX 的 `lever` 是**字符串**：
            # `float("0" or 3.0)` == `float("0")` == `0.0` —— 字符串 "0" 是 truthy，
            # 兜底根本不生效。于是下面 `notional_usdt / lever_val` 抛
            # `ZeroDivisionError`，整个 `update_cache_cycle()` 崩掉、仪表盘整页变空。
            #
            # 空串 `""`、缺失键、`None` 都已被原兜底覆盖，**唯独 "0" / 0 漏网** ——
            # 这正是修复要补的那一种。修复后非正值一律回退 3.0（与 `or 3.0` 的
            # 原始意图一致：拿不到有效杠杆就用默认 3 倍）。
            _lever_raw = p.get("lever", "3")
            try:
                lever_val = float(_lever_raw)
            except (TypeError, ValueError):
                lever_val = 3.0
            if lever_val <= 0:
                lever_val = 3.0
            margin_usdt_val = round(okx_imr if okx_imr > 0 else (notional_usdt / lever_val), 2)

            try:
                from scripts.okx_runtime import current_environment
                _okx_env = current_environment()
                _acc_mode = "DEMO" if _okx_env.simulated else "LIVE"
                _env_mode = _okx_env.mode.lower()
            except Exception:
                _acc_mode = "DEMO"
                _env_mode = "demo"

            positions.append({
                "venue": "okx",
                "exchange": "okx",
                "instId": p.get("instId"),
                "name": p.get("instId", "").replace("-USDT-SWAP", ""),
                "posSide": pos_side,
                "side": pos_side,
                "pos": p.get("pos"),
                "pos_sz": pos_sz,
                "notional_usdt": notional_usdt,
                "margin_usdt": margin_usdt_val,
                "marginSource": "exchange_imr" if okx_imr > 0 else "notional_div_leverage",
                "imr": okx_imr or None,
                "lever": p.get("lever", "3"),
                "account_mode": _acc_mode,
                "environment": _env_mode,
                "avgPx": avg_px,
                "markPx": mark_px,
                "upl": upl,
                "uplRatio": real_roi_pct,
                "roi_pct": real_roi_pct,
                "price_change_pct": price_chg,
                "liqPx": p.get("liqPx", "--"),
                "bePx": p.get("bePx", "--"),
                "trailingSl": trailing_sl,
                "stageDesc": stage_desc,
                "strategyTag": strategy_tag,
                "tp1Hit": t_info.get("tp1_hit", False),
                "tp2Hit": t_info.get("tp2_hit", False)
            })

    return long_count, short_count, total_pos_upl

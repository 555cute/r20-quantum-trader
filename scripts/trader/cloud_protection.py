"""交易所侧云端保护单（OCO / 动态止损棘轮）（B3 抽取·trader 瘦身第六刀，第八十五刀）。

从 `scripts/ai_factor_trader.py` **纯搬家**四函数（133 行）：

| 函数 | 行数 | 职责 |
|---|---|---|
| `amend_venue_stop_loss` | 60 | 跨所云端 SL 棘轮：有旧单且该所支持 amend → 原生改单；否则先挂新再撤旧 |
| `_live_oco_coverage` | 18 | 统计 reduce-only OCO 单覆盖的合约张数 |
| `ensure_cloud_position_protection` | 31 | 校验 100% 云端 OCO 覆盖，补缺口并复验 |
| `sync_cloud_algo_stop` | 24 | 把棘轮动态止损同步到 OKX 云端条件单 |

## 同名注入（沿第八十二～八十四刀）

`okx_rest` / `_live_oco_coverage` / `_float_or_zero` 同名注入 ⇒ 函数体
（含嵌套 def）AST **零例外全等**。`patch.object(aft, "okx_rest", …)` 的既有
patch 面（batch5_d_tails / batch6 / three_tier_ratchet_and_cloud_sync）
经门面壳调用期传参保真。

⚠️ `position_mgmt.execute_ai_position_management` 收到的
`amend_venue_stop_loss` 注入项由门面**调用期**解析 —— 本刀搬家后它自动
拿到门面壳，跨模块 patch 面不断。
⚠️ `close_position_confirmed`（平仓确认，73 行）**不在本域** —— 它依赖
`query_positions`/`fetch_other_venue_positions`，属后续"平仓确认/场所查询"域。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple


def amend_venue_stop_loss(ad, symbol: str, pos_side: str, new_sl: float,
                          contracts: float) -> Tuple[bool, str]:
    """审计 C3（后半）：跨所云端 SL 棘轮——旧实现每轮只 attach 新单、不撤不改旧单，
    云端止损随棘轮轮次堆积（宽松旧单可能先于新单触发/占额度）。
    策略：先枚举现存 SL 触发单（Gate 腿带 text=t-r20sl* 标签、Binance 腿 type 含
    STOP）；有旧单且该所支持 amend_stop_loss → 原生改单（同单改触发价，天然无裸仓
    缝隙），残余旧单一律撤掉；否则安全序列：先挂新 SL（收紧即刻生效、更新无裸仓
    窗口）→ 再撤全部旧 SL。旧单撤失败只 warn——新单已生效，旧 reduce_only 双单
    竞发时后触发者无仓自动无效，绝不回滚收紧（宁可双、不可裸）。"""
    old_ids: List[str] = []
    list_error = ""
    try:
        for row in (ad.list_protective_orders(symbol) or []):
            if not isinstance(row, dict):
                continue
            _order = row.get("order")
            _init = row.get("initial")
            text = (str(_order.get("text") or "") if isinstance(_order, dict) else "") \
                + (str(_init.get("text") or "") if isinstance(_init, dict) else "") \
                + str(row.get("text") or "") + str(row.get("type") or "")
            rid = str(row.get("id") or row.get("algo_id") or row.get("order_id") or "")
            if rid and ("r20sl" in text.lower() or "STOP" in text.upper()):
                old_ids.append(rid)
    except Exception as exc:
        list_error = str(exc)[:160]

    def _cancel(oid: str):
        if hasattr(ad, "cancel_price_order"):
            ad.cancel_price_order(oid)
        elif hasattr(ad, "cancel_algo_order"):
            ad.cancel_algo_order(algo_id=oid)
        else:
            ad.cancel_order(symbol, oid)

    if old_ids and hasattr(ad, "amend_stop_loss"):
        try:
            eff = ad.amend_stop_loss(symbol, pos_side, old_ids[0], float(new_sl))
            for _oid in old_ids[1:]:
                if str(_oid) != str(eff):
                    try:
                        _cancel(_oid)
                    except Exception as exc:
                        print(f"[SL-Ratchet] warn {symbol} 清理残余旧SL失败 {_oid}: {exc}")
            return True, f"原生改单生效（{old_ids[0]}→{eff}），旧单 {len(old_ids)} 笔已处理"
        except Exception as exc:
            print(f"[SL-Ratchet] {symbol} amend 不可用（{str(exc)[:120]}），回退先挂新再撤旧")

    placed = ad.attach_protective_orders(symbol, pos_side, sl_px=float(new_sl), contracts=contracts)
    new_id = str((placed or {}).get("sl") or "")
    cancelled = 0
    for _oid in old_ids:
        if _oid == new_id:
            continue
        try:
            _cancel(_oid)
            cancelled += 1
        except Exception as exc:
            print(f"[SL-Ratchet] warn {symbol} 撤旧SL失败 {_oid}: {exc}")
    note = f"先挂新再撤旧（新单 {new_id or '?'}，撤旧 {cancelled}/{len(old_ids)}）"
    if list_error:
        note += f" [旧单未能枚举: {list_error}]"
    return True, note



def _live_oco_coverage(orders: List[Dict[str, Any]], pos_side: str,
                              *,
                              _float_or_zero) -> float:
    """Return contract size covered by live, reduce-only OCO TP/SL orders."""
    coverage = 0.0
    close_side = "sell" if pos_side == "long" else "buy"
    for order in orders:
        if str(order.get("state", "live")).lower() not in {"live", "effective"}:
            continue
        if str(order.get("posSide", "net")).lower() not in {pos_side, "net"}:
            continue
        if str(order.get("side", close_side)).lower() != close_side:
            continue
        if not order.get("tpTriggerPx") or not order.get("slTriggerPx"):
            continue
        reduce_only = str(order.get("reduceOnly", "true")).lower() in {"true", "1", "yes"}
        if not reduce_only:
            continue
        coverage += _float_or_zero(order.get("sz") or order.get("actualSz"))
    return coverage



def ensure_cloud_position_protection(inst_id: str, pos_side: str, size: float, tp_px: float, sl_px: float,
                              *,
                              okx_rest,
                              _live_oco_coverage) -> Tuple[bool, str]:
    """Verify 100% live cloud OCO coverage, repair any gap, and verify again."""
    try:
        algo_rows = okx_rest.pending_algo_orders(inst_id)
    except Exception as exc:
        return False, f"unable to verify cloud OCO: {exc}"
    coverage = _live_oco_coverage(algo_rows, pos_side)
    missing = max(0.0, float(size) - coverage)
    if missing <= max(1e-12, float(size) * 0.001):
        return True, f"cloud OCO coverage verified ({coverage:g}/{size:g})"

    close_side = "sell" if pos_side == "long" else "buy"
    try:
        okx_rest.place_algo_oco(
            inst_id, close_side, missing, pos_side=pos_side, td_mode="cross",
            tp_trigger_px=tp_px, tp_ord_px="-1", sl_trigger_px=sl_px, sl_ord_px="-1",
            reduce_only=True, cxl_on_close_pos=True,
        )
    except Exception as exc:
        return False, f"cloud OCO repair failed: {exc}"

    for _ in range(4):
        time.sleep(0.5)
        try:
            verify_rows = okx_rest.pending_algo_orders(inst_id)
        except Exception:
            continue
        verified_coverage = _live_oco_coverage(verify_rows, pos_side)
        if verified_coverage + max(1e-12, float(size) * 0.001) >= float(size):
            return True, f"cloud OCO repaired and verified ({verified_coverage:g}/{size:g})"
    return False, "cloud OCO repair was submitted but full coverage could not be verified"



def sync_cloud_algo_stop(inst_id: str, pos_side: str, new_sl: float, reason: str = "",
                              *,
                              okx_rest) -> bool:
    """Sync ratchet dynamic stop to OKX cloud conditional OCO order.
    
    Ensures that once a position reaches Breakeven (Tier 1) or Profit-Lock (Tier 2),
    the cloud trigger order is immediately amended without waiting for the 15-minute LLM cycle.
    """
    # 修复(2026-09-08)：v7.6 环境重构删除了旧全局 SIMULATED_TRADING，此处残留引用导致
    # NameError，连续 4 个交易周期崩溃(09-08 18:30~19:15 BJ)。DEMO/LIVE 统一尝试云端
    # amend：价格一致时幂等跳过；失败仅返回 False，调用方忽略返回值、由本地棘轮兜底，
    # 与 execute_ai_position_management 内联云端止损上移行为保持一致(演示盘与实盘同构)。
    try:
        algo_orders = okx_rest.pending_algo_orders(inst_id)
        # 第一百八十六刀：本文件第 105 行统计覆盖时用的是 `posSide in {pos_side, "net"}`，
        # 这里却只认精确相等 —— **同一文件里同一语义两种写法**。净持仓账户（OKX one-way）
        # 的云端单 `posSide` 是 `"net"` ⇒ 这里永远找不到活止损单 ⇒ 返回 False
        # ⇒ "云端止损收紧"静默失效（是真单也照旧不动）。统一为 net 容错。
        live_algo = next((o for o in algo_orders
                          if str(o.get("state", "")).lower() == "live"
                          and str(o.get("posSide", "net")).lower() in {pos_side, "net"}
                          and o.get("slTriggerPx")), None)
        if not live_algo:
            return False
        current_cloud_sl = float(live_algo.get("slTriggerPx") or 0.0)
        # Avoid redundant amend if price already matches
        if abs(current_cloud_sl - new_sl) < 1e-6:
            return True
        okx_rest.amend_algo_sl(live_algo["algoId"], new_sl, inst_id=inst_id, new_sl_ord_px="-1")
        return True
    except Exception as e:
        print(f"[Cloud OCO Sync Error] {inst_id} {pos_side}: {e}")
        return False


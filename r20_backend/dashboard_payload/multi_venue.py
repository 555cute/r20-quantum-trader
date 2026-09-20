"""多所持仓/挂单对齐（结构优化阶段 2·B2 第七刀）。

从 update_cache_cycle 的「2.5 Multi-Venue Parity」段整段迁出。本段不读任何会被
测试 patch 的门面路径常量（只走 r20_backend.exchanges 适配器与纯函数
`_global_env_axis`），故门面直接重导出即可，无需薄壳注入。
"""
from __future__ import annotations

from r20_backend.dashboard_payload.market import _global_env_axis
from r20_backend.exchanges.base import canonical_base

__all__ = ["collect_cross_venue_positions"]


def _protection_triggers(algos, base_sym, opposite_side):
    """从已取回的云端保护腿（Binance Algo / Gate Price Order）中取出该挂单/持仓的 (SL, TP) 触发价。

    适配双所原生口径：
    - Binance：合约存 symbol、价格存 triggerPrice、类型在 orderType / type；
    - Gate：合约存 contract 或 initial.contract、价格在 trigger.price、
      类型在 initial.text (t-r20sl*/t-r20tp*) 或 trigger.rule。
    """
    sl_val = None
    tp_val = None
    b_sym = str(base_sym or "").strip().upper()
    opp = str(opposite_side or "").strip().lower()

    for a in (algos or []):
        if not isinstance(a, dict):
            continue
        c_str = str(a.get("contract") or (a.get("initial") or {}).get("contract") or a.get("symbol") or "").upper()
        if b_sym and b_sym not in c_str:
            continue

        trig = a.get("trigger") if isinstance(a.get("trigger"), dict) else {}
        raw_px = a.get("trigger_price") or a.get("triggerPrice") or trig.get("price")
        try:
            px = float(raw_px)
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue

        kind = str((a.get("raw") or {}).get("orderType") or a.get("type", "")).upper()
        init_obj = a.get("initial") if isinstance(a.get("initial"), dict) else {}
        text = (str(init_obj.get("text") or "") + str(a.get("text") or "")).lower()
        rule = trig.get("rule")

        is_sl = "STOP" in kind or "r20sl" in text or (rule == 2 if opp in ("sell", "short") else rule == 1)
        is_tp = "TAKE_PROFIT" in kind or "r20tp" in text or (rule == 1 if opp in ("sell", "short") else rule == 2)

        # 方向严格校验：Binance 有显式 side，Gate 用 auto_size / direction
        a_side = str(a.get("side") or "").strip().lower()
        if a_side:
            if opp not in a_side:
                continue
        else:
            auto_sz = str(init_obj.get("auto_size") or a.get("direction") or "").lower()
            if opp in ("sell", "short"):
                if auto_sz and not ("short" in auto_sz or "close_long" in auto_sz):
                    continue
            elif opp in ("buy", "long"):
                if auto_sz and not ("long" in auto_sz or "close_short" in auto_sz):
                    continue

        if is_sl and sl_val is None:
            sl_val = px
        elif is_tp and tp_val is None:
            tp_val = px

    return sl_val, tp_val


def collect_cross_venue_positions(positions, pending_orders_list,
                                  long_count, short_count, total_pos_upl):
    """把 Binance/Gate 的持仓与挂单并入 OKX 主视野（就地追加，返回累计计数）。

    原样搬自 update_cache_cycle 的「2.5 Multi-Venue Parity」段：
    - positions / pending_orders_list 是**传入后原地 append**，不是返回新列表；
    - 三个计数器以「入参 → 返回」的形式流转；
    - 整段被 try/except Exception: pass 包裹（跨所接口不可用时静默降级，
      绝不影响主缓存）—— 包括那句**函数内**的 `from r20_backend.exchanges import`，
      保持惰性导入：exchanges 导入期若出错，也落在同一个 except 里。
    """
    try:
        from r20_backend.exchanges import get_adapter, is_registered
        env_axis = _global_env_axis()
        for v_name in ("binance", "gate"):
            try:
                ad = get_adapter(v_name, environment=env_axis)
                v_positions = ad.positions() if hasattr(ad, "positions") else []
                v_open_orders = ad.open_orders() if hasattr(ad, "open_orders") else []
                v_algos = ad.list_protective_orders() if hasattr(ad, "list_protective_orders") else []

                for vp in (v_positions or []):
                    amt = float(vp.get("size_signed", 0) or 0)
                    if abs(amt) < 1e-12:
                        continue
                    base_sym = canonical_base(str(vp.get("base") or vp.get("symbol", "")))
                    v_inst_id = f"{base_sym}-USDT-SWAP"
                    v_pos_side = str(vp.get("side") or ("long" if amt > 0 else "short")).lower()
                    if "long" in v_pos_side:
                        long_count += 1
                    else:
                        short_count += 1
                    v_upl = float(vp.get("unrealized_pnl", 0) or 0)
                    total_pos_upl += v_upl
                    v_sz = abs(amt)
                    v_avg = float(vp.get("entry_price", 0) or 0)
                    v_mark = float(vp.get("mark_price", 0) or v_avg)
                    v_lever = float(vp.get("leverage", 3) or 3)
                    if v_lever <= 0:
                        v_lever = 3.0

                    # 交易所官方名义价值与保证金优先：避免 Gate 等交易所的合约张数（如 BTC 1张=0.0001 BTC）
                    # 直接乘以单价导致名义额放大万倍、保证金占比失真爆表。
                    raw_dict = vp.get("raw") if isinstance(vp.get("raw"), dict) else {}
                    raw_notional = float(vp.get("notional") or raw_dict.get("notional") or raw_dict.get("value") or raw_dict.get("notionalUsd") or 0.0)
                    if raw_notional > 0:
                        v_notional = round(raw_notional, 2)
                    else:
                        v_notional = round(v_sz * (v_mark if v_mark > 0 else v_avg), 2)

                    raw_margin = float(vp.get("margin") or raw_dict.get("margin") or raw_dict.get("initial_margin") or raw_dict.get("isolatedMargin") or 0.0)
                    if raw_margin > 0:
                        v_margin = round(raw_margin, 2)
                    else:
                        v_margin = round(v_notional / max(1.0, v_lever), 2)

                    v_roi = round((v_upl / max(1.0, v_margin)) * 100, 2) if v_margin > 0 else 0.0
                    v_chg = round(((v_mark - v_avg) / v_avg * 100) if v_avg > 0 else 0, 2)

                    # Check cloud OCO protective orders (Binance & Gate unified)
                    _opp_side = "sell" if "long" in v_pos_side else "buy"
                    v_sl, v_tp = _protection_triggers(v_algos, base_sym, _opp_side)

                    positions.append({
                        "venue": v_name,
                        "exchange": v_name,
                        "instId": v_inst_id,
                        "name": base_sym,
                        "posSide": v_pos_side,
                        "side": v_pos_side,
                        "pos": str(v_sz),
                        "pos_sz": v_sz,
                        "notional_usdt": v_notional,
                        "margin_usdt": v_margin,
                        "marginSource": "exchange_imr" if raw_margin > 0 else "estimated",
                        "lever": f"{int(v_lever)}",
                        "avgPx": v_avg,
                        "markPx": v_mark,
                        "upl": v_upl,
                        "uplRatio": v_roi,
                        "roi_pct": v_roi,
                        "price_change_pct": v_chg,
                        "liqPx": vp.get("liq_price", "--"),
                        "bePx": "--",
                        "trailingSl": v_sl,
                        "stageDesc": "云端双腿防护中" if (v_sl and v_tp) else "持有监控中",
                        "strategyTag": f"🏛️ {v_name.capitalize()}",
                        "exchangeSl": v_sl,
                        "exchangeTp": v_tp,
                        "protectionStatus": "fully_protected" if (v_sl and v_tp) else ("partially_protected" if (v_sl or v_tp) else "unprotected"),
                        "protectionCoveragePct": 100.0 if (v_sl and v_tp) else (50.0 if (v_sl or v_tp) else 0.0),
                        "cloud_oco_verified": bool(v_sl and v_tp),
                        "account_mode": env_axis.upper(),
                        "environment": env_axis.lower(),
                    })

                for vo in (v_open_orders or []):
                    raw_sym = str(vo.get("base") or vo.get("symbol") or vo.get("contract") or "").upper()
                    base_sym = canonical_base(raw_sym)
                    v_inst_id = f"{base_sym}-USDT-SWAP"
                    vo_side_raw = str(vo.get("side", "")).lower()
                    if not vo_side_raw:
                        _sz_val = vo.get("size") if vo.get("size") is not None else vo.get("amount")
                        try:
                            if _sz_val is not None and float(_sz_val) != 0:
                                vo_side_raw = "buy" if float(_sz_val) > 0 else "sell"
                        except (TypeError, ValueError):
                            pass
                    vo_is_long = vo_side_raw in ("buy", "long")
                    vo_px_float = float(vo.get("price", 0) or 0)
                    _raw_sz = vo.get("size") if vo.get("size") is not None else vo.get("amount")
                    try:
                        _sz_num = float(_raw_sz or 0)
                        vo_sz = f"{abs(_sz_num):g}" if _sz_num != 0 else str(_raw_sz if _raw_sz is not None else "--")
                    except (TypeError, ValueError):
                        vo_sz = str(_raw_sz if _raw_sz is not None else "--")
                    vo_ord_id = str(vo.get("order_id") or vo.get("orderId") or vo.get("id") or "")
                    
                    _created_ts = vo.get("create_time") or vo.get("time") or vo.get("cTime") or 0
                    try:
                        _c_ts_f = float(_created_ts)
                        _c_time_ms = int(_c_ts_f * 1000) if (0 < _c_ts_f < 1e11) else int(_c_ts_f)
                    except (TypeError, ValueError):
                        _c_time_ms = 0

                    _opp_side = "sell" if vo_is_long else "buy"
                    _vo_sl, _vo_tp = _protection_triggers(v_algos, base_sym, _opp_side)
                    _vo_lever = float(vo.get("leverage") or 3.0)
                    if _vo_lever <= 0:
                        _vo_lever = 3.0
                    # 保证金 = 名义额 / 杠杆。名义额只按**该所适配器的合约面值**折算：
                    # 面值不可得时一律给 None（前端回落成原生张数），**绝不**按币名猜
                    # 一个面值 —— 保证金是交易员判断仓位大小的依据，猜出来的数字
                    # 比"没有数字"危险得多。
                    vo_margin_usdt = None
                    try:
                        if vo_px_float > 0 and abs(_sz_num) > 0:
                            cap = getattr(ad, "capabilities", None)
                            if getattr(cap, "quantity_unit", "") == "base_asset" or v_name == "binance":
                                _vo_notional = abs(_sz_num) * vo_px_float
                            else:
                                spec = ad.fetch_instrument_spec(base_sym) if hasattr(ad, "fetch_instrument_spec") else None
                                ct_val = float(getattr(spec, "ct_val", 0) or 0) if spec is not None else 0.0
                                _vo_notional = abs(_sz_num) * ct_val * vo_px_float if ct_val > 0 else 0.0
                            if _vo_notional > 0:
                                vo_margin_usdt = round(_vo_notional / max(1.0, _vo_lever), 2)
                    except Exception:
                        vo_margin_usdt = None

                    pending_orders_list.append({
                        "venue": v_name,
                        "exchange": v_name,
                        "ordId": vo_ord_id,
                        "name": base_sym,
                        "inst": base_sym,
                        "instId": v_inst_id,
                        "side": "buy" if vo_is_long else "sell",
                        "side_label": "限价买多" if vo_is_long else "限价卖空",
                        "side_raw": vo_side_raw,
                        "posSide": "long" if vo_is_long else "short",
                        "is_long": vo_is_long,
                        "side_color": "emerald" if vo_is_long else "rose",
                        "ord_type": "limit",
                        "lever": f"{_vo_lever:g}x",
                        "px": f"{vo_px_float:g}" if vo_px_float > 0 else "--",
                        "sz": vo_sz,
                        "margin_usdt": vo_margin_usdt,
                        "cTime": str(_c_time_ms) if _c_time_ms > 0 else "",
                        "time": "刚刚",
                        "state": "live",
                        "tp_px": f"{_vo_tp:g}" if _vo_tp else "--",
                        "sl_px": f"{_vo_sl:g}" if _vo_sl else "--",
                        "account_mode": env_axis.upper(),
                        "environment": env_axis.lower(),
                    })
            except Exception:
                pass
    except Exception:
        pass
    return long_count, short_count, total_pos_upl

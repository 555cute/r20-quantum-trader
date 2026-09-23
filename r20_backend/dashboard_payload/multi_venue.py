"""多所持仓/挂单对齐（结构优化阶段 2·B2 第七刀）。

从 update_cache_cycle 的「2.5 Multi-Venue Parity」段整段迁出。本段不读任何会被
测试 patch 的门面路径常量（只走 r20_backend.exchanges 适配器与纯函数
`_global_env_axis`），故门面直接重导出即可，无需薄壳注入。
"""
from __future__ import annotations

import os
import time

from r20_backend.dashboard_payload.market import _global_env_axis
from r20_backend.exchanges.base import canonical_base

__all__ = ["collect_cross_venue_positions", "_protection_verdict", "_bj_time_str"]


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


def _bj_time_str(ms: int) -> str:
    """毫秒时间戳 → 北京时间展示串（与 `order_view` 的 `c_time_str` **同格式**）。

    取不到时间（`ms <= 0`）⇒ `"--"`。⚠️ 曾经这里写死 `"刚刚"`：
    那是对"这笔委托刚挂上"的**无证据断言**，而真机两行 binance 挂单的 `cTime`
    干脆是空的 —— 面板于是把任意年龄的委托都显示成"刚刚"。
    """
    if not ms or int(ms) <= 0:
        return "--"
    import datetime as _dt
    tz = _dt.timezone(_dt.timedelta(hours=8))
    return _dt.datetime.fromtimestamp(int(ms) / 1000.0, tz=tz).strftime("%m-%d %H:%M:%S")


def _venue_orphan_summary(v_positions, v_algos, ledger_rows, *, readable):
    """该所**孤儿保护腿**汇总（可复核候选 + 一律不碰的不可判定），供面板展示。

    - 判据**只**来自 `attribute_protective_orders`（本仓归属语义的唯一来源），不另写一套；
    - 读腿失败 ⇒ `readable=False` ⇒ 明确"不可判定"（**不是**"没有孤儿腿"）；
    - `ledgerRows` 字段如实说明取证依据是否可用（台账读不到 ⇒ 候选可能偏少，须让运营看见）；
    - 这里**只报告**：绝不撤销任何腿（撤销是显式运营动作，走 `cancel_orphan_attributed_legs`）。
    """
    if not readable:
        return {"readable": False, "attributed": [], "unattributed": [],
                "sideMismatch": [], "sizeMismatch": [],
                "foreignCount": None, "unparsedCount": None,
                "matched": 0, "ledgerRows": "unknown"}
    from scripts.trader.venue_protection import attribute_protective_orders
    try:
        att = attribute_protective_orders(list(v_positions or []), v_algos, ledger_rows)
    except Exception as exc:
        return {"readable": False, "attributed": [], "unattributed": [],
                "sideMismatch": [], "sizeMismatch": [], "matched": 0,
                "ledgerRows": "unknown", "error": f"{type(exc).__name__}: {exc}"}

    def _brief(bucket):
        out = []
        for leg in att.get(bucket) or []:
            out.append({"symbol": leg.get("symbol"), "kind": leg.get("kind"),
                        "id": leg.get("id"), "triggerPrice": leg.get("trigger_price"),
                        "evidence": leg.get("evidence")})
        return out

    return {"readable": True,
            "attributed": _brief("orphan_attributed"),
            "unattributed": _brief("orphan_unattributed"),
            # 第一百八十一刀：**方向/量与任何持仓都对不上**的腿也要让运营看得见 ——
            # 它们此前只在审计输出里，面板完全没提（真机 8 条：gate 1、binance 7）。
            # 两者的语义**不同**，必须分开说：
            #   - `sideMismatch`：反向腿 ⇒ **不计入本仓覆盖**（覆盖链已按唯一判据排除）；
            #   - `sizeMismatch`：**正在计入覆盖**，但量与任何持仓都不吻合 ⇒ 归属存疑
            #     （可能是旧仓遗留的 reduceOnly 单，日后价格触及就会减仓）。
            "sideMismatch": _brief("side_mismatch"),
            "sizeMismatch": _brief("size_mismatch"),
            # 第一百八十二刀：**读到了但认不出**的腿（无本方标签、类型名也不认识 ⇒ `foreign`；
            # 行本身解析不了 ⇒ `unparsed`）。它们**不计入覆盖** ⇒ 若其实是保护腿，覆盖会被低估
            # （可能触发重复挂腿）⇒ 必须让运营看得见。两者语义不同，故分开给数。
            # ⚠️ 这两个桶在归属层是**腿列表**（不是计数）——我第一版按 int 取 ⇒ 字段恒为 None
            # （用例 `test_unclassifiable_legs_are_counted_and_disclosed` 当场抓到）。
            "foreignCount": len(att.get("foreign") or []),
            "unparsedCount": len(att.get("unparsed") or []),
            "matched": len(att.get("matched") or []),
            "ledgerRows": "ok" if ledger_rows is not None else "unavailable"}


def _protection_verdict(algos, base_sym, pos_side, pos_size, *, readable,
                       source_errors=None, venue="", inst_id=""):
    """外所持仓的保护判据 → 面板字段（复用纯判定，不新增交易所调用）。

    诚实边界见 `collect_cross_venue_positions` docstring：不可判定/读不到 ⇒ `unknown`
    且**不告警**（没有证据就不下结论）；过期腿不计入"有活止损"。
    """
    # 惰性 import 提到首行：早退分支（`not readable`）也要用 `protection_trigger_type_fields`，
    # 而它在原位置（下面 try 内）会被 Python 视为局部名 ⇒ 早退时 UnboundLocalError，
    # 再被外层 `except Exception` 吞掉 ⇒ **读腿失败会整行丢掉持仓**（本门实测抓到）。
    from scripts.trader.venue_protection import (
        protection_trigger_type_fields, scan_protective_orders,
    )
    out = {"protectionStatus": "unknown", "protectionCoveragePct": None,
           "protectionExpiry": "unknown", "protectionLegs": 0}
    if not readable:
        # 第一百六十七刀：读不到 ⇒ 触发价类型也是"unknown"（不得当"没有"，也不得填默认值）
        out.update(protection_trigger_type_fields([], readable=False))
        return out
    try:
        scan_protective_orders
        # ⚠️ 传进来的是**该所全量腿**（positions 一次拉全量）⇒ 必须开
        # `require_symbol_match`，否则**别的币的腿也会被算进覆盖**
        # （本刀真机实测：UNI 空仓一度算到 11 张腿，其中大部分是 ETH/SOL/XRP 的）。
        # `scan_protective_orders` 的 docstring 明确：该开关正是给"一次拉全量"的调用方。
        v = scan_protective_orders(algos, symbol=base_sym, pos_side=pos_side,
                                   position_size=pos_size, now_s=time.time(),
                                   require_symbol_match=True)
    except Exception:
        return out
    ours = list(v.get("ours") or [])
    live = [l for l in ours
            if not (isinstance(l.get("remaining_s"), (int, float))
                    and float(l["remaining_s"]) <= 0)]
    has_live_sl = any(l.get("kind") == "sl" for l in live)
    coverage_ok = v.get("coverage_ok")
    covered = v.get("covered_size")
    size = max(0.0, float(pos_size or 0.0))
    if coverage_ok is None:
        status = "unknown"
    elif coverage_ok and has_live_sl:
        status = "fully_protected"
    elif live:
        status = "partially_protected"
    else:
        status = "unprotected"
    if covered is not None and size > 0:
        pct = round(min(100.0, max(0.0, float(covered) / size * 100.0)), 1)
    else:
        pct = None
    if v.get("expired"):
        expiry = "expired"
    elif v.get("expiring"):
        expiry = "expiring"
    elif ours and all(l.get("expiry_state") == "never" for l in ours):
        expiry = "never"
    elif v.get("expiry_unknown"):
        expiry = "unknown"
    else:
        expiry = "unknown"
    out.update({"protectionStatus": status, "protectionCoveragePct": pct,
                "protectionExpiry": expiry, "protectionLegs": len(ours)})
    # 触发价类型（与 OKX 路径同一份三态语义、同一字段名）
    out.update(protection_trigger_type_fields(ours, readable=True))
    if source_errors is not None and not has_live_sl and status != "unknown":
        source_errors.append(
            f"保护缺口 {venue} {inst_id}: 该持仓**没有活止损腿**"
            f"（云端腿缺失/被外部撤销/已到期；保护巡检 R20_VENUE_PROTECTION_WATCHDOG 默认关闭）")
    return out


def collect_cross_venue_positions(positions, pending_orders_list,
                                  long_count, short_count, total_pos_upl,
                                  *, source_errors=None, ledger_rows=None):
    """把 Binance/Gate 的持仓与挂单并入 OKX 主视野（就地追加，返回累计计数）。

    原样搬自 update_cache_cycle 的「2.5 Multi-Venue Parity」段：
    - positions / pending_orders_list 是**传入后原地 append**，不是返回新列表；
    - 三个计数器以「入参 → 返回」的形式流转；
    - 整段被 try/except Exception: pass 包裹（跨所接口不可用时静默降级，
      绝不影响主缓存）—— 包括那句**函数内**的 `from r20_backend.exchanges import`，
      保持惰性导入：exchanges 导入期若出错，也落在同一个 except 里。

    ## 第一百一十八刀：外所持仓也带**保护度判据**（此前只有 OKX 有）

    `collect_algo_protection` 会给 **OKX** 持仓写 `protectionStatus` /
    `protectionCoveragePct`，而本函数此前只写 `exchangeSl`/`exchangeTp`
    —— 面板上外所持仓**没有保护状态**，运营看不出"这笔 binance 空仓到底有没有活止损"。
    现在复用**已经取回**的 `v_algos`（零新增交易所调用）跑
    `venue_protection.scan_protective_orders`（纯判定、已有专测），得到：
    `protectionStatus`（fully/partially/unprotected/unknown）、
    `protectionCoveragePct`、`protectionExpiry`（never/expiring/expired/unknown）、
    `protectionLegs`。并在"读到了腿但**没有活止损**"时把告警 append 进 `source_errors`
    （入参原地改，与 `collect_algo_protection` 同一套）。

    ⚠️ 三条诚实边界：
    - **不可判定 ≠ 安全**：`coverage_ok is None`（腿量读不出）⇒ `unknown`，不写"已保护"；
    - **没读成 ≠ 没保护**：适配器没有 `list_protective_orders` ⇒ `unknown` + 不告警；
    - **过期腿不算保护**：`remaining_s <= 0` 的腿不计入"有活止损"（Gate 腿 7 天到期）。
    """
    try:
        from r20_backend.exchanges import get_adapter, is_registered
        env_axis = _global_env_axis()
        for v_name in ("binance", "gate"):
            try:
                ad = get_adapter(v_name, environment=env_axis)
                v_positions = ad.positions() if hasattr(ad, "positions") else []
                v_open_orders = ad.open_orders() if hasattr(ad, "open_orders") else []
                # ⚠️ 保护腿读取**必须单独兜底**（第一百一十八刀实测的连带伤害）：
                # 它原本跟 positions/open_orders 挤在同一个 try 里 ⇒ 只要读腿抛错，
                # **该所的持仓与挂单会一起从面板消失**（"读不到"被渲染成"没有仓位"）。
                # 分开兜底后：腿读不到 ⇒ 保护判据 `unknown`（不宣称已保护），
                # 持仓/挂单照旧展示。
                _legs_readable = callable(getattr(ad, "list_protective_orders", None))
                try:
                    v_algos = ad.list_protective_orders() if _legs_readable else []
                except Exception as _leg_exc:
                    v_algos, _legs_readable = [], False
                    if source_errors is not None:
                        source_errors.append(
                            f"保护腿 {v_name}: 读取失败（保护状态不可判定）: {str(_leg_exc)[:80]}")

                # 第一百七十五刀：孤儿腿汇总（复用**已取回**的 v_algos ⇒ 零新增交易所调用）。
                # 归属层需要"该所**全部**持仓"才能判孤儿，故在这里按所算一次，再挂到该所每行。
                _orphans = _venue_orphan_summary(v_positions, v_algos, ledger_rows,
                                                 readable=_legs_readable)

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
                    # 保护度判据（复用已取回的腿，零新增调用）——见函数 docstring
                    _prot = _protection_verdict(
                        v_algos, base_sym, v_pos_side, v_sz,
                        readable=_legs_readable, source_errors=source_errors,
                        venue=v_name, inst_id=v_inst_id)

                    #: 保护判据字段（与 OKX 路径同名，面板/AI 可统一读）
                    for _k, _v in _prot.items():
                        vp[_k] = _v
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
                        "stageDesc": ("云端双腿防护中" if _prot["protectionStatus"] == "fully_protected"
                                      else "⚠️ 无活止损腿" if _prot["protectionStatus"] == "unprotected"
                                      else "保护待核验" if _prot["protectionStatus"] == "unknown"
                                      else "持有监控中"),
                        "strategyTag": f"🏛️ {v_name.capitalize()}",
                        "exchangeSl": v_sl,
                        "exchangeTp": v_tp,
                        # ⚠️ 第一百一十八刀改判：此前是
                        # `"fully_protected" if (v_sl and v_tp)` —— 只看"有没有腿"，
                        # **不看量、不看是否 live、不看是否过期** ⇒ 一张**旧量/过期**腿
                        # 也会被面板报成"完全保护"（实测隐患：Binance 13 张腿里只有 2 张
                        # 对得上活动仓）。现在用 `_protection_verdict`（复用
                        # `scan_protective_orders` 的**量+存活+到期**判据）。
                        "protectionStatus": _prot["protectionStatus"],
                        "protectionCoveragePct": _prot["protectionCoveragePct"],
                        "protectionExpiry": _prot["protectionExpiry"],
                        "protectionLegs": _prot["protectionLegs"],
                        # 第一百六十七刀：触发价类型（与 OKX 路径同名同三态：读不到 ⇒ "unknown"，
                        # 该类腿不存在 ⇒ None）。外所通常没有这个概念 ⇒ 多为 "unknown"。
                        "protectionSlTriggerPxType": _prot["protectionSlTriggerPxType"],
                        "protectionTpTriggerPxType": _prot["protectionTpTriggerPxType"],
                        #: 该所**孤儿腿**汇总（可复核候选 + 一律不碰的不可判定）；只报告不撤销
                        "protectionOrphans": _orphans,
                        #: `exchangeSl`/`exchangeTp` 仍是"首个匹配腿的触发价"（供展示与
                        #: 因子取用）；它**不代表覆盖有效** —— 是否有效看 `protectionStatus`。
                        "cloud_oco_verified": _prot["protectionStatus"] == "fully_protected",
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
                    
                    # ⚠️ 第一百二十四刀：Binance 适配器的归一挂单**没有顶层时间字段**
                    # （只有 `raw` 里带交易所的 `time`/`updateTime`）⇒ 原来这里取不到，
                    # `cTime` 留空，而展示用的 `time` 写死 `"刚刚"` ⇒ **面板对一笔可能
                    # 已挂几小时的委托宣称"刚刚"**（真机实测：两行 binance 挂单
                    # `time='刚刚'` 而 `cTime=''`）。Gate 侧 `_normalize_order_item`
                    # 是 `dict(o)` 拷贝原始行，所以 `create_time` 本来就能取到。
                    _raw_vo = vo.get("raw") if isinstance(vo.get("raw"), dict) else {}
                    _created_ts = (vo.get("create_time") or vo.get("time") or vo.get("cTime")
                                   or _raw_vo.get("time") or _raw_vo.get("createTime")
                                   or _raw_vo.get("updateTime") or _raw_vo.get("create_time") or 0)
                    try:
                        _c_ts_f = float(_created_ts)
                        # 第一百八十八刀：秒/毫秒分界委派给唯一实现（原为内联 `1e11` 判据）
                        from r20_backend.time_utils import to_millis
                        _c_time_ms = int(to_millis(_c_ts_f))
                    except (TypeError, ValueError):
                        _c_time_ms = 0

                    _opp_side = "sell" if vo_is_long else "buy"
                    _vo_sl, _vo_tp = _protection_triggers(v_algos, base_sym, _opp_side)
                    _vo_lever = float(vo.get("leverage") or 0.0)
                    if _vo_lever <= 0:
                        _sym_pos = next((p for p in (v_positions or []) if canonical_base(str(p.get("base") or p.get("symbol", ""))) == base_sym), None)
                        _vo_lever = float((_sym_pos or {}).get("leverage") or os.getenv("R20_MIN_LEVERAGE") or 3.0)
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
                        # 展示时间与 OKX 侧（`order_view` 的 `c_time_str`）**同格式**；
                        # 取不到就 `"--"`（与 OKX 一致）——**不再宣称"刚刚"**。
                        "time": _bj_time_str(_c_time_ms),
                        "state": "live",
                        "tp_px": f"{_vo_tp:g}" if _vo_tp else "--",
                        "sl_px": f"{_vo_sl:g}" if _vo_sl else "--",
                        "account_mode": env_axis.upper(),
                        "environment": env_axis.lower(),
                    })
            except Exception as _v_exc:
                # 第一百一十八刀：此前这里是**裸 pass** —— 某所持仓读取失败时
                # 面板只是"少了几行"，看不出"这所根本没读到"（读不到 ≠ 没有仓）。
                # 现在如实进 source_errors（不改降级行为：主缓存照旧不受影响）。
                if source_errors is not None:
                    source_errors.append(
                        f"跨所 {v_name}: 持仓/挂单读取失败（该所本轮未并入面板）: {str(_v_exc)[:80]}")
    except Exception:
        pass
    return long_count, short_count, total_pos_upl

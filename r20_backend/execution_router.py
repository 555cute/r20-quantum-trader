"""统一执行路由：大模型标准决策 JSON → 场所原生受保护开仓（当前仅 Gate）。

铁律（与 OKX 遗留链路同源）：
1. 分发前物理校验不可绕过——数值有限、多空几何、R:R 底线复用 scripts.order_risk
   单一事实源（fail-closed）；
2. 开仓必须 100% 云端保护单覆盖：TP/SL 双腿挂成并回读验证才算成功，
   任一步失败 → 已挂触发单回滚 + 撤入场单，绝不留裸仓；
3. 场所执行开闸由 registry.execution_open 决定（Gate 默认关，需 R20_GATE_EXECUTION=1）；
4. 本模块不吞异常语义：路由结果 {ok, stage, detail} 供上层台账归因。

决策契约（与 ai_brain 输出同构，币种为裸资产名）：
    {"asset": "BTC", "action": "BUY_LONG"|"SELL_SHORT", "margin_usdt": 150.0,
     "leverage": 3, "entry_price": 79650.0, "take_profit_price": 82000.0,
     "stop_loss_price": 78500.0}
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from .exchanges import (ExchangeCapabilityError, canonical_base, execution_open,
                        get_adapter, require_execution)

try:  # 单一事实源：几何 + R:R 底线（与执行层遗留链路同一把尺）
    from scripts.order_risk import validate_quote_geometry_and_rr
except ImportError:
    from order_risk import validate_quote_geometry_and_rr


class RouteResult(Dict[str, Any]):
    """dict 子类型：{ok, venue, stage, detail, order_id, tp_id, sl_id, size_signed...}"""


def _fail(stage: str, detail: str, **extra: Any) -> RouteResult:
    r = RouteResult(ok=False, venue="gate", stage=stage, detail=detail)
    r.update(extra)
    return r


def open_protected_position(decision: Dict[str, Any], *,
                            price_ref: Optional[float] = None,
                            trigger_expiration: int = 604800,
                            adapter: Any = None,
                            own_position: Optional[Dict[str, Any]] = None,
                            margin_mode: Optional[str] = None) -> RouteResult:
    """标准决策 → Gate 受保护开仓。全程 fail-closed：任何缺口先撤后抛。

    own_position：调用方（lab）在该合约上的在管仓位记录（含 size_signed/side）；
    交易所既有仓与之一致视为己仓放行，否则视为外部连坐风险拒开（stage=precheck）。
    margin_mode：由账户实况推导传入（cross/isolated）；缺省维持历史行为 cross。
    """
    ad = adapter or get_adapter("gate")
    asset = canonical_base(str(decision.get("asset") or decision.get("name") or ""))
    action = str(decision.get("action") or "").upper()
    if not asset:
        return _fail("validate", "决策缺少 asset/币种")
    try:
        margin = float(decision.get("margin_usdt") or 0)
        leverage = float(decision.get("leverage") or 0)
        entry = float(decision.get("entry_price") or 0)
        tp = float(decision.get("take_profit_price") or 0)
        sl = float(decision.get("stop_loss_price") or 0)
    except (TypeError, ValueError):
        return _fail("validate", "决策数值字段非法")
    for tag, v in (("margin", margin), ("leverage", leverage), ("entry", entry)):
        if not math.isfinite(v) or v <= 0:
            return _fail("validate", f"{tag} 必须为有限正数，收到 {v}")
    ok, reason, rr = validate_quote_geometry_and_rr(action, entry, tp, sl)
    if not ok:
        return _fail("risk_gate", f"物理风控拒绝: {reason}", rr=rr)

    # 执行开闸（默认关；env 显式打开且凭证就绪前一切免谈）
    require_execution("gate")

    spec = ad.fetch_instrument_spec(asset)
    if spec is None:
        return _fail("specs", f"Gate 无法获取 {asset} 合约规格（下架或网络故障）")
    # 价格对齐 Gate tick（order_price_round），防 PRICE_INVALID 拒单
    tick = float(spec.tick_size or 0.1) or 0.1

    def _q(px: float) -> float:
        import math as _m
        return round(round(px / tick) * tick, max(0, int(-_m.log10(tick)) + 1))

    entry, tp, sl = _q(entry), _q(tp), _q(sl)
    ref_price = float(price_ref or 0) or 0.0
    if ref_price <= 0:
        tick = ad.fetch_ticker(asset) or {}
        ref_price = float(tick.get("mark_price") or tick.get("last") or 0)
    if ref_price <= 0:
        return _fail("price", f"Gate {asset} 现价不可得，禁止盲单")

    notional = margin * leverage
    contracts = ad.quote_qty_to_native(notional, ref_price, spec)
    if contracts <= 0:
        return _fail("sizing",
                     f"名义 {notional:.2f}U @ {ref_price:g} 不足 Gate 最小下单量"
                     f"（每张面值 {spec.ct_val}）")
    side = "long" if action == "BUY_LONG" else "short"
    signed = int(contracts) if side == "long" else -int(contracts)

    # —— 外部持仓前置体检（US-009）：同合约存在来源不明/尺寸不符既有仓 = 连坐风险
    #    （close=true 双腿会平掉全部仓位，含非 lab 名下部分）→ 拒开。
    #    探针失败连外部仓是否存在都不可见，同样 fail-closed。——
    try:
        existing = [p for p in ad.positions()
                    if str(p.get("base") or "").upper() == asset
                    and abs(float(p.get("size_signed") or 0)) > 1e-9]
    except ExchangeCapabilityError:
        raise
    except Exception as exc:
        return _fail("precheck", f"{asset} 既有持仓探针失败，无法排除外部仓，拒开: {exc}")
    for p in existing:
        ex_signed = int(p.get("size_signed") or 0)
        own_match = bool(own_position) and ex_signed == int(own_position.get("size_signed") or 0) \
            and str(own_position.get("side") or "") == str(p.get("side") or "")
        if not own_match:
            return _fail("precheck",
                         f"{asset} 交易所存在非 lab 在管既有仓 size_signed={ex_signed}({p.get('side')})"
                         + ("，与 lab 记录不符" if own_position else "，lab 无在管记录")
                         + "——外部仓连坐拒开", existing_size=ex_signed)

    # 杠杆档位（失败即止，未下单无风险）；margin_mode 由账户实况推导，缺省 cross
    try:
        ad.set_leverage(asset, leverage, margin_mode=margin_mode or "cross")
    except ExchangeCapabilityError:
        raise
    except Exception as exc:
        return _fail("leverage", f"设置杠杆失败: {exc}")

    # 入场限价单
    try:
        placed = ad.place_order(asset, side, abs(signed), price=entry)
    except ExchangeCapabilityError:
        raise
    except Exception as exc:
        return _fail("entry", f"入场委托提交失败: {exc}")
    order_id = str(placed.get("id") or placed.get("text") or "")

    # 云端 TP/SL 双腿 + 回读验证；任何缺口撤入场单回滚
    try:
        legs = ad.attach_protective_orders(asset, side, tp_px=tp, sl_px=sl,
                                           expiration=trigger_expiration)
        open_orders = ad.list_protective_orders(asset)
        open_ids = {str(o.get("id")) for o in open_orders if isinstance(o, dict)}
        if str(legs.get("tp")) not in open_ids or str(legs.get("sl")) not in open_ids:
            raise RuntimeError("回读未见双腿触发单")
    except Exception as exc:
        rollback_note = ""
        try:
            ad.cancel_order(asset, order_id)
            rollback_note = "；入场单已撤销"
        except Exception as cexc:
            rollback_note = f"；入场单撤销失败({cexc})——交易所侧 OCO/手动兜底"
        return _fail("protective", f"保护单覆盖失败: {exc}{rollback_note}",
                     order_id=order_id)

    return RouteResult(ok=True, venue="gate", stage="done", asset=asset,
                       action=action, order_id=order_id,
                       tp_id=str(legs.get("tp")), sl_id=str(legs.get("sl")),
                       size_signed=int(contracts) if side == "long" else -int(contracts),
                       contracts=int(contracts), notional_usdt=round(notional, 2),
                       ref_price=ref_price, rr=round(rr, 3), leverage=leverage,
                       detail="入场限价挂单 + TP/SL 双腿云端触发单已回读验证")


def close_position(symbol: str, *, adapter: Any = None) -> RouteResult:
    """市价全平（close=true + ioc），依赖同前：开闸 + 凭证。"""
    ad = adapter or get_adapter("gate")
    require_execution("gate")
    asset = canonical_base(symbol)
    try:
        data = ad.fast_close_position(asset)
    except ExchangeCapabilityError:
        raise
    except Exception as exc:
        return _fail("close", f"Gate 平仓失败: {exc}", asset=asset)
    return RouteResult(ok=True, venue="gate", stage="done", asset=asset,
                       detail=f"市价全平已提交: {str(data)[:120]}")

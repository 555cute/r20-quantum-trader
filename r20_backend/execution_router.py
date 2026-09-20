"""统一执行路由：大模型标准决策 JSON → 场所原生受保护开仓（Gate/Binance 平权）。

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
import os
from typing import Any, Dict, Optional

from .execution.risk_gates import (
    check_total_exposure as _check_total_exposure,
    clamp_leverage as _clamp_leverage,
    clamp_margin as _clamp_margin,
)
from .exchanges import (ExchangeCapabilityError, canonical_base, execution_open,
                        get_adapter, is_sandbox_environment, require_execution)

try:  # 单一事实源：几何 + R:R 底线（与执行层遗留链路同一把尺）
    from scripts.order_risk import validate_quote_geometry_and_rr
except ImportError:
    from order_risk import validate_quote_geometry_and_rr

try:  # 单一事实源：全局保证金闸门（审计 P0-1 前本模块 0 处引用 risk_constants）
    from scripts.risk_constants import MAX_LEVERAGE, MAX_MARGIN_EQUITY_RATIO, MAX_SINGLE_ASSET_MARGIN, MIN_LEVERAGE
    try:
        from scripts.risk_constants import MAX_TOTAL_EXPOSURE_USDT as TOTAL_EXPOSURE_CAP
    except ImportError:
        TOTAL_EXPOSURE_CAP = 0.0
except ImportError:  # pragma: no cover - scripts/ 在 sys.path 时走上面分支
    try:
        from risk_constants import MAX_MARGIN_EQUITY_RATIO, MAX_SINGLE_ASSET_MARGIN
    except ImportError:
        MAX_MARGIN_EQUITY_RATIO, MAX_SINGLE_ASSET_MARGIN = 0.20, 0.0
        # 常量不可得时不臆造区间：夹取退化为 no-op（下面靠 `or leverage` 短路），
        # 绝不因为读不到配置就凭空放大或缩小杠杆。
        MAX_LEVERAGE = MIN_LEVERAGE = None  # type: ignore[assignment]


class RouteResult(Dict[str, Any]):
    """dict 子类型：{ok, venue, stage, detail, order_id, tp_id, sl_id, size_signed...}"""


#: 各所"检测得到、但载荷未核验"的持仓模式 → 给运维的人读危害说明。
#: 放在这里是因为它是**运维文案**（各所自己的模式词汇仍归各所 capability 声明）。
MODE_HAZARDS: Dict[str, str] = {
    "dual_plus": "拆仓语义，本系统不把它折叠成净仓/双向解读",
    "long_short": "Hedge 对冲语义，positionSide 下单/保护腿载荷未在真实账户核验",
}


def _fail(stage: str, detail: str, venue: str = "gate", **extra: Any) -> RouteResult:
    r = RouteResult(ok=False, venue=venue, stage=stage, detail=detail)
    r.update(extra)
    return r


def _load_venue_pool_soft(venue: str) -> Dict[str, Any]:
    """读取该所池配置；读取失败只告警返回 {}（池门禁是加固，不制造新的阻塞点）。"""
    try:
        from .exchanges.routing_policy import load_venue_pool
        pool = load_venue_pool(venue)
        return pool if isinstance(pool, dict) else {}
    except Exception as exc:
        print(f"[所池门禁] {venue.upper()} 池配置读取失败，按无限制继续（仅告警）: {exc}")
        return {}


def open_protected_position(decision: Dict[str, Any], *,
                            price_ref: Optional[float] = None,
                            trigger_expiration: int = 604800,
                            adapter: Any = None,
                            own_position: Optional[Dict[str, Any]] = None,
                            margin_mode: Optional[str] = None,
                            environment: Optional[str] = None,
                            max_margin_usdt: Optional[float] = None) -> RouteResult:
    """标准决策 → Gate/Binance 受保护开仓（US-005 多所对等）。全程 fail-closed：任何缺口先撤后抛。

    own_position：调用方（lab/trader）在该合约上的在管仓位记录（含 size_signed/side）；
    交易所既有仓与之一致视为己仓放行，否则视为外部连坐风险拒开（stage=precheck）。
    margin_mode：由账户实况推导传入（cross/isolated）；缺省维持历史行为 cross。
    持仓模式：对**实现了只读探测**的场所先体检 —— 探测所得必须在该所
    `capabilities.position_modes` 声明域内，且落在 `entry_ready_position_modes`
    （载荷已在真实账户核验过的子集）内才放行；否则禁新开仓并写明原因。
    本系统**永不自动切换**账户模式（审计 §2）。
    environment：显式资金环境（demo/live），优先使用；缺省读 decision.get('environment')。
    max_margin_usdt：调用方按权益算出的单笔保证金硬顶（权益×R20_MAX_MARGIN_EQUITY_RATIO）；
    缺省 0/None = 不臆造占比上限，但仍强制单标的绝对封顶（审计 P0-1）。
    """
    env_name = str(environment or decision.get("environment") or "").strip().lower() or None
    venue = str(decision.get("venue") or getattr(getattr(adapter, "capabilities", None), "venue", "gate") or "gate").strip().lower()
    if env_name and is_sandbox_environment(env_name) and venue == "gate" and env_name != "sandbox":
        env_name = "sandbox"
    ad = adapter or get_adapter(venue, environment=env_name)
    asset = canonical_base(str(decision.get("asset") or decision.get("name") or ""))
    action = str(decision.get("action") or "").upper()
    if not asset:
        return _fail("validate", "决策缺少 asset/币种", venue=venue)
    try:
        margin = float(decision.get("margin_usdt") or 0)
        leverage = float(decision.get("leverage") or 0)
        entry = float(decision.get("entry_price") or 0)
        tp = float(decision.get("take_profit_price") or 0)
        sl = float(decision.get("stop_loss_price") or 0)
    except (TypeError, ValueError):
        return _fail("validate", "决策数值字段非法", venue=venue)
    for tag, v in (("margin", margin), ("leverage", leverage), ("entry", entry)):
        if not math.isfinite(v) or v <= 0:
            return _fail("validate", f"{tag} 必须为有限正数，收到 {v}", venue=venue)

    # ── 杠杆区间（审计 P2-8，2026-09-13）──────────────────────────────────
    # 旧实现只有主脑写决策时夹 [MIN_LEVERAGE, MAX_LEVERAGE]，执行层只夹上限：
    # 决策缓存被回填/多所路径直接构造决策时，1x 这种低于配置下限的杠杆会真的发出去，
    # 「杠杆区间」在市场侧不成立。执行层是最后一道，必须有同样的两端夹取。
    # 每所池门禁（审计 P1-7，2026-09-13）：data/venue_routing.json 的 dry_run / assets /
    # max_open / margin_per_trade_usdt / min_confidence 此前在 routing_policy.py 之外
    # **零消费者** —— 管理员以为设了 per-venue 闸门其实没有。这里读一次，供下方
    # 保证金夹取与池准入判定共用。
    pool = _load_venue_pool_soft(venue)

    _cur_min_lev = float(os.getenv("R20_MIN_LEVERAGE", "") or MIN_LEVERAGE or 2.0)
    _cur_max_lev = float(os.getenv("R20_MAX_LEVERAGE", "") or MAX_LEVERAGE or 5.0)
    if _cur_min_lev > _cur_max_lev:
        _cur_min_lev = _cur_max_lev

    leverage, decision = _clamp_leverage(
        venue=venue, asset=asset, decision=decision, leverage=leverage,
        min_leverage=_cur_min_lev, max_leverage=_cur_max_lev)

    _margin_unclamped = margin
    margin, decision, margin_clamped_from = _clamp_margin(
        venue=venue, asset=asset, decision=decision, margin=margin,
        max_margin_usdt=max_margin_usdt,
        max_single_asset_margin=MAX_SINGLE_ASSET_MARGIN,
        max_margin_equity_ratio=MAX_MARGIN_EQUITY_RATIO,
        pool=pool)

    # ⚠️ `_all_positions` 在本函数里**是局部变量**（真正的赋值在下方"外部持仓前置体检"
    # 处，L209 `_all_positions = ad.positions()`）。故原代码那句
    # `_all_positions if _all_positions is not None else ad.positions()`
    # 的**前半支永远取不到**（走到这里必然 UnboundLocalError），实际只会走
    # `ad.positions()`；而同一函数后面还会**再调一次** `ad.positions()`。
    # 抽离时把这次调用收成**惰性缓存**：行为等价（每次都是新取的 ad.positions()），
    # 且顺带把原来的**两次取数收成一次**。
    _pos_cache: list = []

    def _positions_for_exposure():
        if not _pos_cache:
            _pos_cache.append(ad.positions() or [])
        return _pos_cache[0]

    _exposure_fail = _check_total_exposure(
        venue=venue, asset=asset, action=action, margin=_margin_unclamped,
        leverage=leverage, total_exposure_cap=TOTAL_EXPOSURE_CAP,
        all_positions=None, positions_reader=_positions_for_exposure,
        fail_factory=_fail)
    if _exposure_fail is not None:
        return _exposure_fail

    # 止盈宽度平滑收窄：防止 AI 规划过远天际线挂单无法落袋（受最大 R:R 与 ATR 跨度上限约束）
    try:
        from scripts.trader.brackets import clamp_take_profit_width
        _prec = len(str(entry).split(".")[1]) if "." in str(entry) else 2
        _ctx_atr = float(decision.get("atr") or decision.get("atr_1h") or 0.0)
        tp = clamp_take_profit_width(
            is_long=(action == "BUY_LONG"),
            limit_px=entry,
            sl_px=sl,
            tp_px=tp,
            atr=_ctx_atr,
            prec=_prec,
        )
        decision["take_profit_price"] = tp
    except Exception:
        pass

    ok, reason, rr = validate_quote_geometry_and_rr(action, entry, tp, sl)
    if not ok:
        return _fail("risk_gate", f"物理风控拒绝: {reason}", venue=venue, rr=rr)

    # 执行开闸（默认关；env 显式打开且凭证就绪前一切免谈）——保持既有契约：
    # 未开闸一律抛 ExchangeCapabilityError；下面才是「已开闸但该所池子仍不许发」的判定。
    require_execution(venue, environment=str(getattr(ad, "environment", "live") or "live"))

    if pool:
        if pool.get("dry_run"):
            return _fail("venue_dry_run",
                         f"{venue.upper()} 池配置 dry_run=true（本地演算不发单）；"
                         f"如需真实发送请改 data/venue_routing.json 并确认执行开关", venue=venue)
        pool_assets = [str(a).upper() for a in (pool.get("assets") or [])]
        if not pool_assets:
            return _fail("venue_pool", f"{venue.upper()} 准入币种清单为空（空池=不发单）", venue=venue)
        if asset.upper() not in pool_assets:
            return _fail("venue_pool", f"{asset} 不在 {venue.upper()} 准入币种清单（{', '.join(pool_assets)}）", venue=venue)
        pool_conf = float(pool.get("min_confidence") or 0.0)
        try:
            decision_conf = float(decision.get("confidence") or 0.0)
        except (TypeError, ValueError):
            decision_conf = 0.0
        if math.isfinite(pool_conf) and pool_conf > 0 and 0 < decision_conf < pool_conf:
            return _fail("venue_pool",
                         f"决策置信度 {decision_conf:g} 低于 {venue.upper()} 门禁 {pool_conf:g}", venue=venue)

    # 环境维合约存在性对账（US-007 扩展）：下单前核对**本环境**合约
    # 目录——已下架/未上市在发送前拦截（fail-closed 拒开）；目录拉不到 →
    # fail-open 放行（对账是增强不是风控闸门，绝不阻塞交易）。
    try:
        from .exchanges.listing import ensure_contract_listed
        _check = ensure_contract_listed(
            venue, str(getattr(ad, "environment", "live") or "live"),
            ad.native_symbol(asset))
        if not _check.ok:
            return _fail("listing", f"合约对账拒绝: {_check.reason}", venue=venue)
    except Exception:  # 对账自身异常一律 fail-open（含目录缓存污染等未知面）
        pass

    spec = ad.fetch_instrument_spec(asset)
    if spec is None:
        return _fail("specs", f"{venue.upper()} 无法获取 {asset} 合约规格（下架或网络故障）", venue=venue)

    tick = float(spec.tick_size or 0.1) or 0.1

    def _q(px: float) -> float:
        import math as _m
        return round(round(px / tick) * tick, max(0, int(-_m.log10(tick)) + 1))

    entry, tp, sl = _q(entry), _q(tp), _q(sl)
    ref_price = float(price_ref or 0) or 0.0
    if ref_price <= 0:
        tick_data = ad.fetch_ticker(asset) or {}
        ref_price = float(tick_data.get("mark_price") or tick_data.get("last") or 0)
    if ref_price <= 0:
        return _fail("price", f"{venue.upper()} {asset} 现价不可得，禁止盲单", venue=venue)

    notional = margin * leverage
    contracts = ad.quote_qty_to_native(notional, ref_price, spec)
    if contracts <= 0:
        return _fail("sizing",
                     f"名义 {notional:.2f}U @ {ref_price:g} 不足 {venue.upper()} 最小下单量"
                     f"（每张面值 {spec.ct_val}）", venue=venue)
    side = "long" if action == "BUY_LONG" else "short"
    is_base_asset = getattr(ad.capabilities, "quantity_unit", "") == "base_asset"
    signed = contracts if (side == "long") else -contracts
    if not is_base_asset:
        signed = int(signed)
        contracts = int(contracts)

    # —— 外部持仓前置体检（US-009）：同合约存在来源不明/尺寸不符既有仓 = 连坐风险 ——
    try:
        _all_positions = ad.positions()
    except ExchangeCapabilityError:
        raise
    except Exception as exc:
        return _fail("precheck", f"{asset} 既有持仓探针失败，无法排除外部仓，拒开: {exc}", venue=venue)
    existing = [p for p in _all_positions
                if str(p.get("base") or "").upper() == asset
                and abs(float(p.get("size_signed") or 0)) > 1e-9]
    # 该所池上限（审计 P1-7）：复用上面这一次探针结果，不额外触网
    if pool:
        pool_max_open = int(pool.get("max_open") or 0)
        if pool_max_open > 0:
            _open_count = len([p for p in _all_positions
                               if isinstance(p, dict) and abs(float(p.get("size_signed") or 0)) > 1e-9])
            if _open_count >= pool_max_open:
                return _fail("venue_pool",
                             f"{venue.upper()} 当前持仓 {_open_count} 笔已达池上限 max_open={pool_max_open}", venue=venue)
    for p in existing:
        ex_signed = float(p.get("size_signed") or 0)
        own_signed = float(own_position.get("size_signed") or 0) if own_position else None
        own_match = bool(own_position) and abs(ex_signed - (own_signed or 0)) < 1e-6 \
            and str(own_position.get("side") or "").lower() == str(p.get("side") or "").lower()
        if not own_match:
            return _fail("precheck",
                         f"{asset} 交易所存在非本系统在管既有仓 size_signed={ex_signed:g}({p.get('side')})"
                         + ("，与 lab 记录不符" if own_position else "，lab 无在管记录")
                         + "——外部仓连坐拒开", venue=venue, existing_size=ex_signed)

    # 持仓模式只读体检（审计 §2 Gate 的既定政策，此前只有政策没有执行）：
    # 本系统**永不自动切换**用户账户的持仓模式；"测不出来"与"dual_plus 拆仓"
    # 一律**禁止新开仓并显示原因**（fail-closed）。dual 走 auto_size 载荷、
    # single 走 close=true 载荷，二者都是本仓已实现的形态，故受支持、放行。
    declared_modes = tuple(getattr(getattr(ad, "capabilities", None), "position_modes", ()) or ())
    probe = getattr(ad, "detect_position_mode", None)
    position_mode: Optional[str] = None
    # ⚠️ 只在**该适配器真的实现了只读探测**时才体检。实测（DEMO）：Binance 声明的是
    # `('net','long_short')` 这套**不同词汇**、且没有探测方法 —— 若按"声明了就体检、
    # 探测不到就拒"处理，会**直接把币安新开仓全部停掉**（本刀实测拦下的自伤）。
    # 币安侧的持仓模式探测（dualSidePosition）是独立的一刀；在它有探测之前维持原行为。
    if declared_modes and callable(probe):
        position_mode = str(probe() or "unknown").strip().lower()
        if position_mode not in declared_modes:
            return _fail("position_mode",
                         f"{asset} 无法只读确认持仓模式（探测={position_mode}，该所声明支持="
                         f"{'/'.join(declared_modes)}）——禁新开仓；本系统不自动切换账户模式",
                         venue=venue, position_mode=position_mode)
        # "检测得到" ≠ "敢在这些模式下开仓"：`entry_ready_position_modes` 是该所
        # **载荷已在真实账户核验过**的子集。Gate 的 dual_plus（拆仓不可折叠）、
        # Binance 的 long_short（hedge 载荷未核验）都在此被拦下并说明原因。
        ready_modes = tuple(getattr(getattr(ad, "capabilities", None),
                                    "entry_ready_position_modes", ()) or ())
        if ready_modes and position_mode not in ready_modes:
            hazard = MODE_HAZARDS.get(position_mode, "该模式下单/保护腿载荷未核验")
            return _fail("position_mode",
                         f"{asset} 账户持仓模式={position_mode}（{hazard}）——本系统仅在 "
                         f"{'/'.join(ready_modes)} 下核验过下单与保护腿载荷，禁新开仓；"
                         "请在交易所侧改回受支持模式或人工处理",
                         venue=venue, position_mode=position_mode)

    # 杠杆档位（失败即止，未下单无风险）；margin_mode 由账户实况推导，缺省 cross
    try:
        ad.set_leverage(asset, leverage, margin_mode=margin_mode or "cross")
    except ExchangeCapabilityError:
        raise
    except Exception as exc:
        return _fail("leverage", f"设置杠杆失败: {exc}", venue=venue)

    # 入场限价单
    try:
        placed = ad.place_order(asset, side, abs(contracts), price=entry)
    except ExchangeCapabilityError:
        raise
    except Exception as exc:
        return _fail("entry", f"入场委托提交失败: {exc}", venue=venue)
    order_id = str(placed.get("id") or placed.get("order_id") or placed.get("text") or "")

    # 云端 TP/SL 双腿 + 回读验证；任何缺口撤入场单回滚
    legs: dict = {}
    try:
        try:
            # 只有**真的探测到模式**时才多传这个 kwarg：sandbox 等适配器没有
            # `**kwargs`，无条件传会 TypeError（本刀实测拦下的一处潜在炸点）。
            _mode_kwargs = {"position_mode": position_mode} if position_mode else {}
            legs = ad.attach_protective_orders(asset, side, tp_px=tp, sl_px=sl,
                                               expiration=trigger_expiration,
                                               contracts=contracts, **_mode_kwargs)
        except TypeError:
            legs = ad.attach_protective_orders(asset, side, tp_px=tp, sl_px=sl,
                                               expiration=trigger_expiration)
        open_orders = ad.list_protective_orders(asset)
        open_ids = {str(o.get("id") or o.get("algo_id")) for o in open_orders if isinstance(o, dict)}
        if str(legs.get("tp")) not in open_ids or str(legs.get("sl")) not in open_ids:
            raise RuntimeError("回读未见双腿触发单")
    except Exception as exc:
        # 审计④#9(2026-09-13)：铁律「任一步失败→已挂触发单回滚+撤入场单」旧实现只
        # 撤入场单——tp 挂成、sl 失败时 tp 孤儿遗留至 expiration（无仓挂保护单不可对账）。
        # 现双段清理：①已知 legs 逐腿 best-effort 撤；②枚举该资产残留触发单，只撤
        # 带 r20 前缀的本系统单（用户手动保护单绝不触碰）；枚举失败如实标注。
        rollback_notes = []
        for _leg in ("tp", "sl"):
            _lid = str((legs or {}).get(_leg) or "")
            if not _lid or _lid in ("None", ""):
                continue
            try:
                ad.cancel_order(asset, _lid)
            except Exception as leg_exc:
                rollback_notes.append(f"{_leg.upper()}腿 {_lid} 撤销失败({leg_exc})")
        try:
            residue = ad.list_protective_orders(asset) or []
            for row in residue:
                if not isinstance(row, dict):
                    continue
                _o = row.get("order") if isinstance(row.get("order"), dict) else row
                _text = (str(_o.get("text") or "") + str(row.get("text") or "")).lower()
                if "r20" not in _text:
                    continue  # 只清本系统触发单
                _rid = str(row.get("id") or row.get("algo_id") or row.get("order_id") or _o.get("id") or "")
                if not _rid or _rid in ("None",):
                    continue
                try:
                    ad.cancel_order(asset, _rid)
                    rollback_notes.append(f"孤儿触发单 {_rid} 已撤")
                except Exception as rexc:
                    rollback_notes.append(f"孤儿触发单 {_rid} 撤销失败({rexc})")
        except Exception as lexc:
            rollback_notes.append(f"孤儿触发单未能枚举({lexc})——依赖交易所侧 OCO/到期/手动兜底")
        try:
            ad.cancel_order(asset, order_id)
            rollback_notes.append("入场单已撤销")
        except Exception as cexc:
            rollback_notes.append(f"入场单撤销失败({cexc})——交易所侧 OCO/手动兜底")
        detail = f"保护单覆盖失败: {exc}"
        if rollback_notes:
            detail += "；回滚记录: " + "；".join(rollback_notes)
        return _fail("protective", detail, venue=venue, order_id=order_id)

    return RouteResult(ok=True, venue=venue, stage="done", asset=asset,
                       action=action, order_id=order_id,
                       tp_id=str(legs.get("tp")), sl_id=str(legs.get("sl")),
                       size_signed=signed,
                       contracts=contracts, notional_usdt=round(notional, 2),
                       margin_usdt=round(margin, 4),
                       margin_clamped_from_usdt=margin_clamped_from or None,
                       ref_price=ref_price, rr=round(rr, 3), leverage=leverage,
                       detail=f"{venue.upper()} 入场限价挂单 + TP/SL 双腿云端触发单已回读验证")


def close_position(symbol: str, *, venue: str = "gate", adapter: Any = None,
                   environment: Optional[str] = None, pos_side: Optional[str] = None) -> RouteResult:
    """市价全平（close=true + ioc），依赖同前：开闸 + 凭证。"""
    v = str(venue or getattr(getattr(adapter, "capabilities", None), "venue", "gate") or "gate").lower()
    if environment and is_sandbox_environment(environment) and v == "gate" and environment != "sandbox":
        environment = "sandbox"
    ad = adapter or get_adapter(v, environment=environment)
    require_execution(v, environment=str(getattr(ad, "environment", "live") or "live"))
    asset = canonical_base(symbol)
    try:
        kwargs: Dict[str, Any] = {}
        try:
            import inspect
            if "pos_side" in inspect.signature(ad.fast_close_position).parameters and pos_side:
                kwargs["pos_side"] = str(pos_side)
        except (TypeError, ValueError):
            pass
        data = ad.fast_close_position(asset, **kwargs)
    except ExchangeCapabilityError:
        raise
    except Exception as exc:
        return _fail("close", f"{v.upper()} 平仓失败: {exc}", venue=v, asset=asset)
    # 审计 B1：适配器明示未平（closed:False，如无持仓/双向歧义/非整数张数）绝不
    # 换算成功——旧实现只查异常，把 {"closed": False} 也报成 ok=True。
    if isinstance(data, dict) and data.get("closed") is False:
        return _fail("close", f"{v.upper()} 平仓未受理: {data.get('reason') or data}", venue=v, asset=asset)
    return RouteResult(ok=True, venue=v, stage="done", asset=asset,
                       detail=f"{v.upper()} 市价全平已提交: {str(data)[:120]}")

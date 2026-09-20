"""跨所（Gate/Binance）云端保护单的**覆盖核验与临期续期**（roadmap G8）。

## 为什么需要它（现状缺口，实测）

- 机械退出主流程 `position_exit.manage_position_tp_and_trailing` 只核验 **OKX** 云端
  OCO（`ensure_cloud_position_protection` 走 `okx_rest.pending_algo_orders`）；
- 因子快照的 `f["position"]` 只按 OKX 形态 `instId` 匹配，而跨所持仓汇入时用的是
  `instId = "GATE:BTC_USDT"`（见 `position_universe.merge_cross_venue_positions`），
  **永远匹配不上** ⇒ 外所仓位在每周期链路上**没有任何覆盖核验**；
- Gate 触发单带 `trigger.expiration`（默认 `604800` 秒 = 7 天，且是**相对创建时间**
  的秒数；`0` = 永不过期）。到期后触发单离开交易所 open 列表 ⇒ **仓位裸奔**，
  而系统不会发现（roadmap G8：`COORDINATION_GAPS` 里"蒸发窗"那条）。

## 两层结构（判定与动作分离）

| 层 | 函数 | 性质 |
|---|---|---|
| 判定 | `scan_protective_orders` | **纯函数**：无 IO、无副作用、时间由 `now_s` 入参 |
| 动作 | `ensure_venue_protection` | 按判定结果修复/续期，IO 全部由 `ad` 注入 |

## 三条安全铁律（照抄本仓云端棘轮的既有语义）

1. **先挂新、后撤旧**：临期续期绝不"先撤再挂" —— 那中间有一个裸仓窗口。
   新腿挂失败时**保留旧腿**（旧腿到期前仍在保护；宁可少续一次，不可裸奔）；
2. **宁可双、不可裸**：旧腿撤失败只告警，不回滚新腿（两腿都是 reduce_only，
   后触发者无仓自动无效）；
3. **不可判定 ≠ 安全**：覆盖范围算不出来（例如 Gate `size=0 + close=true` 之外的
   模糊形态）时返回 `None` 而不是 `True`，并且**绝不**把自己不认识的腿当成自己的
   （人工挂的保护单不属于本系统，只登记不触碰）。

> 本模块不读配置、不发请求、不写文件：所有 IO 由调用方（或测试）注入 ——
> 与 `scripts/trader/` 其余模块同一纪律（子模块不得在 import 期绑定门面名字）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "DEFAULT_RENEW_WITHIN_S",
    "scan_protective_orders",
    "ensure_venue_protection",
]

#: 默认提前续期窗口（24h；roadmap G8 的验收口径）
DEFAULT_RENEW_WITHIN_S = 24 * 3600
#: 覆盖缺口容忍度（相对持仓量）：小于千分之一视为浮点噪音，不修
DEFAULT_TOLERANCE_RATIO = 0.001
#: 判定"这条腿属于本系统"的文本标记（与云端棘轮同一套：Gate `t-r20sl*`、Binance 类型名）
OUR_SL_MARKERS = ("r20sl", "stop")
OUR_TP_MARKERS = ("r20tp", "take_profit")


def _as_float(value: Any) -> Optional[float]:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num


def _row_text(row: Dict[str, Any]) -> str:
    """把交易所行里所有可能带标签的文本拼起来（与云端棘轮同一口径）。

    Gate 把标签放在 `initial.text`（`t-r20sl…`），Binance 把类型放在 `type`/`raw.orderType`。
    """
    order = row.get("order") if isinstance(row.get("order"), dict) else {}
    initial = row.get("initial") if isinstance(row.get("initial"), dict) else {}
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    parts = [
        order.get("text"), initial.get("text"), row.get("text"),
        row.get("type"), row.get("orderType"), raw.get("orderType"),
        raw.get("type"), row.get("algoType"),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _leg_kind(row: Dict[str, Any]) -> Optional[str]:
    """`"sl"` / `"tp"` / None（None = 不是本系统的保护腿）。"""
    text = _row_text(row)
    if "r20sl" in text:
        return "sl"
    if "r20tp" in text:
        return "tp"
    # 没有我们的标签时，只认明确的类型名（Binance STOP_MARKET / TAKE_PROFIT_MARKET）
    if "take_profit" in text:
        return "tp"
    if "stop" in text:
        return "sl"
    return None


def _is_live(row: Dict[str, Any]) -> bool:
    state = str(row.get("state") or row.get("status") or "live").lower()
    return state in {"live", "effective", "open", "new", "active"}


def _leg_size(row: Dict[str, Any]) -> Optional[float]:
    """该腿覆盖的数量；`None` = 不可判定。"""
    for key in ("left", "size_remaining", "remaining"):
        num = _as_float(row.get(key))
        if num is not None and num > 0:
            return num
    initial = row.get("initial") if isinstance(row.get("initial"), dict) else {}
    for key in ("size", "amount", "qty", "quantity"):
        num = _as_float(row.get(key))
        if num is not None and num > 0:
            return num
    for key in ("size", "amount", "qty", "quantity"):
        num = _as_float(initial.get(key))
        if num is not None and num > 0:
            return num
    return None


def _is_full_close(row: Dict[str, Any]) -> bool:
    """该腿语义是"平掉全部仓位"（Gate `close=true` / `auto_size`）。

    Gate 挂单用 `initial.size=0 + close=true` 表示"整仓平"，此时 `size` 字段
    给不出覆盖张数 —— 但覆盖范围其实是**全部**，不能当成"不可判定"。
    """
    initial = row.get("initial") if isinstance(row.get("initial"), dict) else {}
    for container in (row, initial):
        for key in ("close", "is_close", "close_position", "closePosition"):
            val = container.get(key)
            if val is True or str(val).lower() in {"true", "1", "yes"}:
                return True
        if container.get("auto_size") or container.get("autoSize"):
            return True
    return False


def _close_side_of(pos_side: str) -> str:
    return "sell" if str(pos_side).lower().startswith("l") else "buy"


def _row_close_side(row: Dict[str, Any]) -> Optional[str]:
    for key in ("side", "order_side"):
        val = str(row.get(key) or "").lower()
        if val in {"buy", "sell"}:
            return val
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    val = str(raw.get("side") or "").lower()
    return val if val in {"buy", "sell"} else None


def _to_seconds(value: Optional[float]) -> Optional[float]:
    """把可能是**毫秒**的时间戳归一成秒。

    ⚠️ 判据必须是 `< 1e11`（秒）而不是 `< 1e9`：epoch 秒本身已经 ~1.79e9，
    用 1e9 当分界会把"秒"误判成"毫秒"、把时间除以 1000（本模块第一版就这么错过，
    结果"还剩 7 天"被算成"已过期"）。本仓同一手法见
    `dashboard_payload/multi_venue.py` 的 `0 < _c_ts_f < 1e11`。
    """
    if value is None:
        return None
    return value / 1000.0 if value >= 1e11 else value


def _expiry(row: Dict[str, Any]) -> tuple:
    """→ `(expires_at_s | None, state)`；state ∈ {"absolute","relative","never","unknown"}。

    Gate：`trigger.expiration` 是**相对创建时间**的秒数（0 = 永不过期）；
    另兼容绝对时间戳（秒或毫秒）。缺 `create_time` 时无法换算 → unknown。
    """
    trigger = row.get("trigger") if isinstance(row.get("trigger"), dict) else {}
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    raw_exp = row.get("expiration")
    if raw_exp in (None, ""):
        raw_exp = trigger.get("expiration")
    if raw_exp in (None, ""):
        raw_exp = raw.get("expiration")
    exp = _as_float(raw_exp)
    if exp is None:
        return None, "unknown"
    if exp <= 0:
        return None, "never"
    if exp >= 1e11:                    # 绝对时间戳（毫秒）
        return exp / 1000.0, "absolute"
    if exp > 1e9:                      # 绝对时间戳（秒）
        return exp, "absolute"
    created = _to_seconds(_as_float(
        row.get("create_time") or row.get("createTime") or row.get("cTime")
        or raw.get("create_time") or raw.get("createTime") or raw.get("time")))
    if created is None:
        return None, "unknown"
    return created + exp, "relative"


def scan_protective_orders(rows: Optional[Sequence[Dict[str, Any]]], *,
                           symbol: str,
                           pos_side: str,
                           position_size: float,
                           now_s: float,
                           renew_within_s: float = DEFAULT_RENEW_WITHIN_S,
                           tolerance_ratio: float = DEFAULT_TOLERANCE_RATIO,
                           require_symbol_match: bool = False) -> Dict[str, Any]:
    """纯判定：给一批交易所保护单行，回答"覆盖够不够、哪些腿要续期"。

    - 只统计**本系统**的腿（`r20sl`/`r20tp` 标签或明确类型名）；
    - 只统计方向正确（平仓方向）且 live 的腿；
    - `symbol` 默认不参与过滤（调用方通常已按合约查询）；`require_symbol_match=True`
      时才要求行内合约串包含币种，供"一次拉全量"的调用方使用。
    """
    base = str(symbol or "").split("-")[0].split("_")[0].upper()
    want_close_side = _close_side_of(pos_side)
    size = max(0.0, float(position_size or 0.0))

    ours: List[Dict[str, Any]] = []
    foreign = 0
    covered = 0.0
    full_close_leg = False
    coverage_unknown = False
    expiring: List[Dict[str, Any]] = []
    expired: List[Dict[str, Any]] = []
    expiry_unknown: List[Dict[str, Any]] = []

    for row in (rows or []):
        if not isinstance(row, dict) or not _is_live(row):
            continue
        kind = _leg_kind(row)
        if kind is None:
            foreign += 1
            continue
        if require_symbol_match and base:
            initial = row.get("initial") if isinstance(row.get("initial"), dict) else {}
            hay = " ".join(str(row.get(k) or "") for k in
                           ("contract", "symbol", "inst_id", "instId")).upper()
            hay += " " + str(initial.get("contract") or "").upper()
            if base not in hay:
                continue
        side = _row_close_side(row)
        if side is not None and side != want_close_side:
            continue

        leg = {
            "id": str(row.get("id") or row.get("algo_id") or row.get("algoId")
                      or row.get("order_id") or row.get("ordId") or ""),
            "kind": kind,
        }
        expires_at, exp_state = _expiry(row)
        leg["expires_at"] = expires_at
        leg["remaining_s"] = None if expires_at is None else round(expires_at - float(now_s), 1)
        leg["expiry_state"] = exp_state
        ours.append(leg)

        if kind == "sl":
            if _is_full_close(row):
                full_close_leg = True
            leg_size = _leg_size(row)
            if leg_size is None and not _is_full_close(row):
                coverage_unknown = True
            elif leg_size is not None:
                covered += leg_size

        if exp_state in ("relative", "absolute"):
            assert expires_at is not None
            if expires_at <= float(now_s):
                expired.append(leg)
            elif expires_at - float(now_s) <= float(renew_within_s):
                expiring.append(leg)
        elif exp_state == "unknown":
            # 没有到期字段：可能是"永不过期"之外的形态，也可能只是取数缺字段。
            # 不可判定 ⇒ 只登记，不擅自续期（续期是有成本的写操作），但也不当作安全。
            expiry_unknown.append(leg)

    if full_close_leg:
        covered = max(covered, size)
        coverage_unknown = False
    missing = max(0.0, size - covered)
    tolerance = max(1e-12, size * float(tolerance_ratio))
    coverage_ok: Optional[bool]
    if coverage_unknown:
        coverage_ok = None
    else:
        coverage_ok = missing <= tolerance
    has_live_sl = any(leg["kind"] == "sl" for leg in ours)

    return {
        "ours": ours,
        "foreign_count": foreign,
        "has_live_sl": has_live_sl,
        "covered_size": None if coverage_unknown else round(covered, 8),
        "missing_size": None if coverage_unknown else round(missing, 8),
        "coverage_ok": coverage_ok,
        "expiring": expiring,
        "expired": expired,
        "expiry_unknown": expiry_unknown,
        # 判定给调用方的三个"要不要动"：
        "needs_repair": (not has_live_sl) or coverage_ok is False,
        "needs_renew": bool(expiring or expired),
        "needs_verify": bool(expiry_unknown) or coverage_ok is None,
    }


def ensure_venue_protection(ad: Any, *, symbol: str, pos_side: str, position_size: float,
                            tp_px: Optional[float], sl_px: float,
                            now_s: float,
                            expiration_s: int = 604800,
                            renew_within_s: float = DEFAULT_RENEW_WITHIN_S,
                            tolerance_ratio: float = DEFAULT_TOLERANCE_RATIO,
                            log: Any = print) -> Dict[str, Any]:
    """按判定结果**安全**修复缺口 / 续期临期腿（先挂新、后撤旧）。

    返回 `{ok, protected_now, stage, detail, placed, cancelled, kept_old, scan}`：

    - `ok`：**本次操作是否达成目标**。只要"该修/该续"而没做完就是 `False`，
      哪怕旧腿此刻还在保护（否则一次失败的续期会被调用方当成"没事"而静默过去）；
    - `protected_now`：**当下**这个仓位是否确实有覆盖（旧腿还在时可以为 True，
      但 `ok=False` 提醒调用方"续期没成，得重试或告警"）；
    - 任何一步失败都**不回滚**已生效的保护（宁可双、不可裸），把事实写进 detail。
    """
    try:
        rows = ad.list_protective_orders(symbol) or []
    except Exception as exc:
        # 读不到 ≠ 没有：不可判定时绝不去写单（可能重复挂），如实上报。
        return {"ok": False, "protected_now": None, "stage": "list",
                "detail": f"保护单列表读取失败: {exc}",
                "placed": {}, "cancelled": [], "kept_old": [], "scan": None}

    scan = scan_protective_orders(rows, symbol=symbol, pos_side=pos_side,
                                  position_size=position_size, now_s=now_s,
                                  renew_within_s=renew_within_s,
                                  tolerance_ratio=tolerance_ratio)
    protected_now = bool(scan["has_live_sl"]) and scan["coverage_ok"] is not False
    result = {"ok": True, "protected_now": protected_now, "stage": "noop",
              "detail": "保护覆盖正常", "placed": {}, "cancelled": [], "kept_old": [],
              "scan": scan}

    need_place = bool(scan["needs_repair"] or scan["needs_renew"])
    if not need_place:
        if scan["needs_verify"]:
            result.update(ok=False, stage="verify",
                          detail="覆盖/到期不可判定，需人工或后续复验（不擅自写单）")
        return result

    # ① 先挂新（repair 时补缺口；renew 时用新腿替换临期腿）
    contracts = scan["missing_size"]
    if contracts is None or contracts <= 0:
        contracts = position_size
    try:
        placed = ad.attach_protective_orders(symbol, pos_side, tp_px=tp_px, sl_px=float(sl_px),
                                             expiration=int(expiration_s),
                                             contracts=float(contracts)) or {}
    except Exception as exc:
        # 旧腿仍在（若本来有）——保留它们，绝不在没有新腿的情况下撤旧腿。
        result.update(ok=False, stage="attach",
                      detail=f"新保护腿挂载失败（旧腿保留，但目标未达成）: {exc}",
                      protected_now=protected_now,
                      kept_old=[leg["id"] for leg in scan["ours"] if leg["id"]])
        return result

    result["placed"] = placed
    result["stage"] = "placed"
    result["protected_now"] = True

    # ② 再撤旧（只撤**我们自己的**临期/已过期腿；人工腿永不触碰）
    stale_ids = [leg["id"] for leg in (scan["expired"] + scan["expiring"]) if leg["id"]]
    new_ids = {str(v) for v in (placed or {}).values() if v}
    cancelled: List[str] = []
    kept_old: List[str] = []
    for oid in stale_ids:
        if oid in new_ids:
            continue
        try:
            if hasattr(ad, "cancel_price_order"):
                ad.cancel_price_order(oid)
            elif hasattr(ad, "cancel_algo_order"):
                ad.cancel_algo_order(algo_id=oid)
            else:
                ad.cancel_order(symbol, oid)
            cancelled.append(oid)
        except Exception as exc:
            # 宁可双、不可裸：撤旧失败不回滚新腿，只登记。
            kept_old.append(oid)
            log(f"[跨所保护续期] warn {symbol} 撤旧腿失败 {oid}: {exc}")

    result["cancelled"] = cancelled
    result["kept_old"] = kept_old
    action = "续期" if scan["needs_renew"] else "补挂"
    detail = f"{action}完成：新腿 {placed}，撤旧 {len(cancelled)}/{len(stale_ids)}"
    if kept_old:
        detail += f"，{len(kept_old)} 条旧腿未撤（新腿已生效，宁可双不可裸）"
    result["detail"] = detail
    return result

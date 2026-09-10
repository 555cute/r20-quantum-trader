"""Direct signed OKX V5 control-plane client (static API Key only, fail-closed)."""
from __future__ import annotations
import secrets
import threading
import time
import logging
from typing import Any
from scripts import okx_rest
from scripts.okx_runtime import OKXEnvironment, current_environment
from scripts.okx_rest import OKXNotConfigured, cancel_algo_orders, pending_algo_orders

logger = logging.getLogger(__name__)

_INTENTS: dict[str, dict[str, Any]] = {}
_INTENT_LOCK = threading.Lock()
INTENT_TTL_SECONDS = 90


def _request(method: str, path: str, params: dict[str, Any] | None = None, env: OKXEnvironment | None = None, timeout: int = 20) -> list[dict[str, Any]]:
    """Compatibility facade; the shared client owns signing and private HTTP."""
    selected = env or current_environment()
    return okx_rest.request(method, path, params, env=selected, timeout=timeout)


def _create_intent(env: OKXEnvironment, position: dict[str, Any]) -> tuple[str, str]:
    token = secrets.token_urlsafe(32); size = abs(float(position.get("pos", 0) or 0)); side = str(position.get("posSide") or "net").lower()
    confirmation = f"CLOSE {env.mode.upper()} {position.get('instId')} {side.upper()} {size:g}"
    record = {"environment_id":env.identity,"instId":str(position.get("instId")),"posSide":side,"posId":str(position.get("posId") or ""),"expected_size":size,"confirmation":confirmation,"expires_at":time.time()+INTENT_TTL_SECONDS}
    with _INTENT_LOCK:
        now=time.time(); stale=[key for key,value in _INTENTS.items() if value["expires_at"]<now]
        for key in stale: _INTENTS.pop(key,None)
        _INTENTS[token]=record
    return token, confirmation


def account_snapshot() -> dict[str, Any]:
    env = current_environment()
    if not env.configured:
        raise OKXNotConfigured(f"OKX {env.mode.upper()} 静态 API Key 未配置，请在后台「账户接入」配置 V5 API Key（系统 NOT READY，禁止交易）")
    positions = [p for p in _request("GET", "/api/v5/account/positions", {"instType":"SWAP"}, env) if abs(float(p.get("pos",0) or 0))>1e-12]
    orders = _request("GET", "/api/v5/trade/orders-pending", {"instType":"SWAP"}, env)
    public_positions=[]
    for position in positions:
        token, confirmation = _create_intent(env, position)
        public_positions.append({**position,"close_token":token,"close_confirmation":confirmation,"close_token_expires_in":INTENT_TTL_SECONDS})
    return {"environment":env.mode,"environment_id":env.identity,"credential_source":"static-v5-key","positions":public_positions,"orders":orders,"captured_at_ms":int(time.time()*1000)}


def _consume_intent(token: str) -> dict[str, Any]:
    with _INTENT_LOCK: intent=_INTENTS.pop(str(token),None)
    if not intent: raise ValueError("平仓令牌无效或已使用，请刷新当前持仓")
    if intent["expires_at"]<time.time(): raise ValueError("平仓令牌已过期，请刷新当前持仓")
    return intent


def _position_match(positions: list[dict[str, Any]], intent: dict[str, Any]) -> dict[str, Any] | None:
    candidates=[p for p in positions if p.get("instId")==intent["instId"] and str(p.get("posSide","")).lower()==intent["posSide"]]
    if intent["posId"]: candidates=[p for p in candidates if str(p.get("posId", ""))==intent["posId"]]
    return candidates[0] if candidates else None


def fast_close_confirmed(close_token: str, confirmation: str) -> dict[str, Any]:
    env=current_environment()
    if not env.configured: raise OKXNotConfigured(f"OKX {env.mode.upper()} 静态 API Key 未配置，请在后台配置 V5 API Key 后重试（禁止应急平仓）")
    intent=_consume_intent(close_token)
    if env.identity!=intent["environment_id"]: raise ValueError("OKX 环境或凭证已变化，请刷新当前持仓")
    if confirmation.strip().upper()!=intent["confirmation"]: raise ValueError(f"确认短语必须精确为：{intent['confirmation']}")
    target=_position_match(_request("GET","/api/v5/account/positions",{"instType":"SWAP","instId":intent["instId"]},env),intent)
    if not target: raise ValueError("目标仓位已不存在，请刷新")
    actual=abs(float(target.get("pos",0) or 0)); tolerance=max(1e-12,actual*1e-6)
    if abs(actual-intent["expected_size"])>tolerance: raise ValueError(f"仓位数量已从 {intent['expected_size']} 变化为 {actual}，请刷新")
    target_side=intent["posSide"] if intent["posSide"] in {"long","short"} else ("long" if float(target.get("pos",0) or 0)>0 else "short")
    canceled=[]; cancel_failures=[]
    # Cancel active regular orders
    for order in _request("GET","/api/v5/trade/orders-pending",{"instType":"SWAP","instId":intent["instId"]},env):
        order_side=str(order.get("posSide") or "net").lower()
        if order_side not in {target_side,"net"}: continue
        order_id=str(order.get("ordId") or "")
        if order_id:
            try:
                _request("POST","/api/v5/trade/cancel-order",{"instId":intent["instId"],"ordId":order_id},env)
                canceled.append(order_id)
            except Exception as exc:
                cancel_failures.append(f"{order_id}: {exc}")

    # Also cancel any attached/standalone algo orders (such as native cloud OCO orders) to avoid conflicts
    try:
        for ao in pending_algo_orders(intent["instId"], env=env):
            ao_side = str(ao.get("posSide") or "net").lower()
            if ao_side in {target_side, "net"}:
                algo_id = str(ao.get("algoId") or "")
                if algo_id:
                    try:
                        cancel_algo_orders([algo_id], inst_id=intent["instId"], env=env)
                        canceled.append(f"algo:{algo_id}")
                    except Exception as exc:
                        logger.warning("Cancel algo order %s failed during close: %s", algo_id, exc)
    except Exception as exc:
        logger.warning("Scanning algo orders failed during close: %s", exc)

    if cancel_failures:
        raise RuntimeError("平仓前存在无法撤销的同仓位委托：" + "; ".join(cancel_failures))
    close_side = intent["posSide"] if intent["posSide"] in {"long", "short"} else "net"
    close_result=_request("POST","/api/v5/trade/close-position",{"instId":intent["instId"],"mgnMode":str(target.get("mgnMode") or "cross"),"posSide":close_side,"autoCxl":True,"clOrdId":f"r20close{int(time.time())}"},env)
    remaining=actual
    for _ in range(10):
        time.sleep(.7); current=_position_match(_request("GET","/api/v5/account/positions",{"instType":"SWAP","instId":intent["instId"]},env),intent)
        remaining=abs(float(current.get("pos",0) or 0)) if current else 0.0
        if remaining<=tolerance: break
    if remaining>tolerance: raise RuntimeError(f"平仓请求已受理但仓位未确认归零，剩余 {remaining}；请刷新，禁止重复点击")
    return {"status":"confirmed_closed","environment":env.mode,"instId":intent["instId"],"posSide":intent["posSide"],"closed_size":actual,"canceled_entry_orders":canceled,"close_result":close_result}


# =====================================================================
# US-004 · attachAlgoOrds 保护核验（审计 2026-09-10 §2 OKX / 设计 §0-3、§6）
# attachAlgoOrds ≠ 受理时即生效的原子保护：官方 attachAlgoClOrdId 说明——普通订单
# **完全成交后**才提交附带算法单，回执含 failCode/failReason；HTTP 200 或附带字段
# 存在都不能宣称受保护，必须回读 pending algo 逐腿核验。
# 2026-08-20 起 post_only/mmp_and_post_only 失败可只收到 canceled 不先 live
# （Demo 2026-08-10 已生效）：状态机必须接受直接终态，不无限等待 live——本 helper
# 为纯函数判定，不 sleep 不轮询，等待窗口由上层节奏控制。
# =====================================================================
PROTECTION_STATES = ("PROTECTION_PENDING", "PROTECTED", "UNPROTECTED")


def _dec_eq(a: Any, b: Any) -> bool:
    """十进制字符串等值（不做 float 比较，防精度漂移）。"""
    from decimal import Decimal, InvalidOperation
    if a in (None, "") or b in (None, ""):
        return False
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return str(a) == str(b)


def _dec_num(v: Any):
    """十进制解析 → Decimal；None = 不可量化（缺失/非法），供短缺判定的保守兜底。"""
    from decimal import Decimal, InvalidOperation
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _dec_text(d) -> str:
    """Decimal → 人话数量（4.000→'4'；不做 float 往返）。"""
    s = str(d.normalize())
    return s if "E" not in s.upper() else str(int(d))


def verify_attached_protection(*, inst_id: str, expected_legs: list[dict[str, Any]],
                               attach_rows: list[dict[str, Any]] | None = None,
                               pending_rows: list[dict[str, Any]],
                               main_order_state: str | None = None) -> dict[str, Any]:
    """主单附带保护腿回读核验。

    expected_legs: 每腿 {"kind","side","sz","x_price"[,"attach_algo_cl_ord_id"]}；
                   sz 语义 = 主单成交量所需的应覆盖数量。
    attach_rows:   下单回执中的 attachAlgoOrds 结果行（failCode/failReason 非空 ⇒ 未受理）
    pending_rows:  回读 pending_algo_orders 的在途条件/OCO 行（账户与合约由
                   pending_algo_orders 的私有签名通道与本地过滤保证归属——本函数
                   另钉 instId 一致才允许记 PROTECTED）
    返回 {"status": 三态之一, "legs": [逐腿判定], "detail": 人话}；HTTP 200 ≠ 受保护。
    US-004 观察项②收口：数量维度独立短路——回读行存在且 side/触发值匹配但覆盖
    数量（含多行累加）< 期望 ⇒ **结构性短缺，显式 UNPROTECTED**（与「尚未回读到」
    区分，供下游自动补挂/告警）；空回读、不可量化行、超量行、多行合计已达期望
    但非单行精确等中间态一律保守留在 PROTECTION_PENDING（既不升 PROTECTED 也不
    冤枉成短缺）；failCode 路径维持原判不动。
    """
    if str(main_order_state or "").lower() in ("canceled", "cancelled"):
        # 直接终态（含 2026-08-20 post_only 不先 live 的 canceled 直达）：主单永不成交，
        # 附带算法单不会被提交——不存在保护，也无需再等待。
        return {"status": "UNPROTECTED", "legs": [{"kind": l.get("kind"), "state": "not_submitted",
                "reason": "主单已终态 canceled，attachAlgoOrds 永不提交"} for l in expected_legs],
                "detail": "主单直接终态（canceled），附带保护从未提交——按未保护处理，不等待 live"}
    legs_out: list[dict[str, Any]] = []
    any_failed = any_pending = all_ok = False
    any_shortfall = False
    for leg in expected_legs:
        kind = str(leg.get("kind") or "")
        # ① 受理回执 failCode/failReason 非空 ⇒ 该腿明确未创建（HTTP 200 不代表受保护）
        fail = None
        for ar in attach_rows or []:
            if not isinstance(ar, dict):
                continue
            same = (leg.get("attach_algo_cl_ord_id")
                    and str(ar.get("attachAlgoClOrdId") or "") == str(leg["attach_algo_cl_ord_id"]))
            if not same:
                same = (_dec_eq(ar.get("sz"), leg.get("sz"))
                        and _dec_eq(ar.get("tpTriggerPx") or ar.get("slTriggerPx") or ar.get("xPrice"),
                                    leg.get("x_price"))
                        and (ar.get("side") in (None, "", leg.get("side"))))
            fc = str(ar.get("failCode") or "")
            if same and fc and fc != "0":
                fail = (fc, str(ar.get("failReason") or ""))
                break
        if fail:
            legs_out.append({"kind": kind, "state": "failed", "failCode": fail[0],
                             "failReason": fail[1]})
            any_failed = True
            continue
        # ② pending 回读逐字段覆盖：合约/方向/触发值/数量。数量维度精判
        #    （US-004 观察项②）：精确等=protected；side/触发匹配但 sz 小于期望=
        #    短缺候选行（后面可能仍有精确行救场）；不可量化/超量行保守忽略——
        #    「行存在但回读未齐」的中间态按原语义留在 pending_readback，注释即此意。
        hit = None
        shortfall_rows: list[dict[str, Any]] = []
        loose_row = False   # 见过不可量化/超量行：信息不齐时不武断判短缺
        exp_sz = _dec_num(leg.get("sz"))
        for pr in pending_rows or []:
            if not isinstance(pr, dict):
                continue
            if str(pr.get("instId") or "") != str(inst_id):
                continue
            if leg.get("side") and str(pr.get("side") or "") != str(leg["side"]):
                continue
            if leg.get("x_price") not in (None, ""):
                trig = pr.get("xPrice") or pr.get("tpTriggerPx") or pr.get("slTriggerPx")
                if not _dec_eq(trig, leg["x_price"]):
                    continue
            if exp_sz is not None:
                got_sz = _dec_num(pr.get("sz"))
                if got_sz is None or got_sz > exp_sz:
                    loose_row = True           # 不判短缺也不记精确覆盖
                    continue
                if got_sz < exp_sz:
                    shortfall_rows.append(pr)
                    continue               # 暂记短缺，继续找精确行
            hit = pr
            break
        if hit is not None:
            legs_out.append({"kind": kind, "state": "protected",
                             "algoId": str(hit.get("algoId") or "")})
        else:
            rows_sum = None
            if exp_sz is not None and shortfall_rows and not loose_row:
                from decimal import Decimal
                rows_sum = sum((_dec_num(r.get("sz")) for r in shortfall_rows),
                               Decimal("0"))
            if rows_sum is not None and rows_sum < exp_sz:
                # 结构性短缺：覆盖数量 < 主单成交量——不是「还没回读到」，
                # 等待与补挂是两回事，显式 UNPROTECTED 让下游能反应。
                legs_out.append({"kind": kind, "state": "coverage_shortfall",
                                 "reason": f"covered {_dec_text(rows_sum)} < filled {_dec_text(exp_sz)}",
                                 "algo_ids": [str(r.get("algoId") or "") for r in shortfall_rows]})
                any_shortfall = True
            else:
                legs_out.append({"kind": kind, "state": "pending_readback",
                                 "reason": "回执无 failCode 但 pending 未见——attach 于完全成交后提交，"
                                           "可能尚未生效，须再回读；期间不得宣称受保护"})
                any_pending = True
    all_ok = all(l["state"] == "protected" for l in legs_out) and bool(legs_out)
    if any_failed or any_shortfall or (str(main_order_state or "").lower() == "filled"
                                       and not any_pending and not all_ok):
        status = "UNPROTECTED"
        detail = ("存在未受理/缺失/覆盖数量结构性短缺（covered<filled，非等待可解）的保护腿"
                  "——按未保护处理（审计：不得凭 200 宣称保护生效）")
    elif all_ok:
        status = "PROTECTED"
        detail = "全部保护腿经 pending 回读覆盖账户/合约/方向/数量/触发值核验"
    else:
        status = "PROTECTION_PENDING"
        detail = "部分腿尚未回读到，暂不认定受保护，窗口内复核"
    return {"status": status, "legs": legs_out, "detail": detail}


def readback_attached_protection(inst_id: str, expected_legs: list[dict[str, Any]], *,
                                 attach_rows: list[dict[str, Any]] | None = None,
                                 main_order_state: str | None = None,
                                 env: OKXEnvironment | None = None) -> dict[str, Any]:
    """便利包装：私有只读回读 pending algo 后过纯函数核验（零写请求）。"""
    env = env or current_environment()
    rows = pending_algo_orders(inst_id, env=env)
    return verify_attached_protection(inst_id=inst_id, expected_legs=expected_legs,
                                      attach_rows=attach_rows, pending_rows=rows,
                                      main_order_state=main_order_state)

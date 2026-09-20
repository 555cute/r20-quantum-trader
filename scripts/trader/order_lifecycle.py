"""挂单生命周期回收与重启接管对账（B3 抽取·trader 瘦身第五刀，第八十四刀）。

从 `scripts/ai_factor_trader.py` **纯搬家**两函数：

| 函数 | 行数 | 职责 |
|---|---|---|
| `clean_stale_open_orders` | 132 | 超时挂单回收（OKX + 执行闸开启的外所）+ 同向重复单收敛 |
| `reconcile_pending_orders` | 83 | 周期开始的归属对账（追踪器/意图接管，孤儿撤销，fail-closed） |

## 嵌套闭包**随函数整体搬家**（回答 §98 的悬置问题）

两函数各带一个闭包（`_intent_covers` / `_cancel_orphan`），捕获的是
**函数内局部量**（`_live_intents` / 循环变量 `inst_id`/`ord_id`/`side`）。
搬家时**闭包形状一字未动** —— 嵌套 `def` 属于函数体，整体搬迁即逐字保持，
比"外提成模块级 helper + 参数化捕获量"改动更小、语义更稳。
与 `scripts/trader/order_intent.py` 的"装配段外提"先例规则不同：
那是**跨函数**同构代码折叠，这是**函数内**闭包随迁，登记区分。

## 同名注入（沿第八十二/八十三刀）

注入 kw 与门面全局同名 ⇒ 函数体逐字零改动，只多签名行。
`patch.object(aft, "okx_rest"/"load_open_intents"/"_BROKEN_VENUES"/
"load_instruments"/"current_environment"/"venue_registry")` 的既有 patch 面
（`tests/audit/test_audit_batch5_d_tails.py`、`test_audit_batch6_live_incidents.py`、
`test_open_order_reconcile.py`）经门面壳调用期传参保真。

⚠️ `_BROKEN_VENUES` 是**模块级可变集合**，注入的是引用 ⇒ 子包内
`_BROKEN_VENUES.add(_v)` 仍改到门面那一个对象（本轮路由据此摘除执行资格）。
⚠️ 三个 `RECONCILE_REASON_*` 与 `OPEN_INTENT_*` 常量**留在门面**
（patch 面与文案单一来源），由壳调用期解析后传入。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple


def clean_stale_open_orders(keep_ord_ids: Optional[set] = None,
                              *,
                              load_open_intents,
                              OPEN_INTENT_TTL_MS,
                              _BROKEN_VENUES,
                              current_environment,
                              load_instruments,
                              okx_rest,
                              venue_registry) -> Tuple[bool, str]:
    """Cancel stale entry orders; any inability to verify/cancel blocks the trading cycle.

    审计④8(2026-09-13)·外所 GTC 回收：路由能把信号派到 gate/binance（三所平权+
    gate 费率占优即会被选中），但本回收过去只扫 OKX——外所入场限价 GTC 挂单
    永不超时撤除，孤儿逐日堆积占保证金并在深夜反抽时意外成交于无保护价。
    现对**执行闸开启**的场所同尺回收（闸关=该所不可能有本系统单，零触碰、其
    适配器故障也绝不拖累主链）；核验/撤销失败与 OKX 同标准 fail-closed 拦本周期。
    """
    keep_ord_ids = keep_ord_ids or set()
    STALE_MS = 240_000
    try:
        open_orders = okx_rest.pending_orders()
    except Exception as exc:
        return False, f"invalid open-orders response: {exc}"
    now_ts = int(time.time() * 1000)
    for order in open_orders:
        inst_id = str(order.get("instId") or "")
        order_id = str(order.get("ordId") or "")
        if order_id and order_id in keep_ord_ids:
            continue  # 挂单对账已判定归属（接管），不受超时生命周期清理影响
        state = str(order.get("state", "live")).lower()
        created_at = int(order.get("cTime", now_ts) or now_ts)
        if state not in {"live", "partially_filled"} or not order_id or now_ts - created_at <= STALE_MS:
            continue
        try:
            okx_rest.cancel_order(inst_id, order_id)
        except Exception as exc:
            return False, f"failed to cancel stale order {inst_id}/{order_id}: {exc}"
        print(f"[挂单生命周期管理] 自动撤销超时挂单: {inst_id} (ordId={order_id}, state={state})")

    try:
        _env_mode = str(current_environment().mode or "demo")
    except Exception:
        _env_mode = ""
    # 外所接管判定用活意图集（与 OKX 对账同一把尺：新鲜意图归属 → 保留）
    # ⚠️ 第一百三十四刀：**读不到意图 ⇒ 不撤任何单 + fail-closed**。
    # 旧写法 `except Exception: _live_intents = []` 把"文件坏了"当成"没有意图"
    # ⇒ 每笔挂单都失去归属 ⇒ 按孤儿/陈旧**撤销**（撤旧挂新循环的另一种成因），
    # 而调用方还会照常开新仓。撤单不可逆 ⇒ 未知必须保留。
    try:
        _live_intents = [i for i in load_open_intents()
                         if isinstance(i, dict) and now_ts - int(i.get("ts", 0) or 0) <= OPEN_INTENT_TTL_MS]
    except Exception as _intents_exc:
        print(f"[挂单生命周期] CRITICAL 本地意图不可读（{_intents_exc}）——本轮**不撤任何**"
              "外所挂单，并 fail-closed 禁止本周期新增下单（读不到 ≠ 没有意图）")
        return False, "本地意图不可读（不撤单，fail-closed）"

    def _intent_covers(venue_base: str, dir_word: str) -> bool:
        _tgt = f"{venue_base}-USDT-SWAP"
        _letter = "buy" if dir_word == "long" else "sell"
        return any(str(i.get("instId")) == _tgt and str(i.get("side", "")).lower() == _letter
                   for i in _live_intents)

    _AUTH_MARKERS = ("INVALID_KEY", "Invalid key", "Invalid API-key", "-2015", "50111",
                     "signature", "Signature", "not exist", "invalid timestamp")
    for _v in ("gate", "binance"):
        _ad = None
        _rows: List[Dict[str, Any]] = []
        try:
            if not (_env_mode and venue_registry.execution_open(_v, _env_mode)):
                continue
            _ad = venue_registry.get_adapter(_v, environment=_env_mode)
            if _v == "binance":
                _rows = _ad.open_orders() or []          # 全合约在途普通单
            else:
                try:
                    _pool = load_instruments()
                except Exception:
                    _pool = []
                for _ins in _pool:                        # Gate 列表端点按合约，逐标的扫
                    _base = str(_ins.get("instId") or "").split("-")[0].upper()
                    if not _base:
                        continue
                    _rows.extend(_ad.list_open_orders(_base) or [])
        except Exception as exc:
            _msg = str(exc)
            if any(m in _msg for m in _AUTH_MARKERS):
                # 执行闸开着但凭证已死：该所**不可能再收到我们的新单**（router 同样
                # 发不出去）→ 跳过回收不拦轮（审计#4教训：拿凭证错误拦全链=交易停摆）。
                _BROKEN_VENUES.add(_v)   # 本轮路由同步摘除其执行资格（见 venue_execution_ready）
                print(f"[挂单生命周期] CRITICAL {_v.upper()} 凭证无效但执行闸开启——本所生命周期"
                      f"管理跳过；请修复密钥或关闭 R20_{_v.upper()}_EXECUTION")
                continue
            # 其余不可核验（网络/未知）：与 OKX 同尺 fail-closed 拦本轮
            return False, f"{_v} 挂单回收不可用: {type(exc).__name__}: {_msg[:120]}"
        # 归一 (base, dir) → 按创建时间**只保最新**一条为候选存活单，其余降级为重复单；
        # 存活候选再按新鲜意图归属决定保留/超时撤销（修复：外所单此前既无人回收也无
        # 接管语义，每轮重挂造成 BTC/SUI 成对重复）。
        best: Dict[tuple, tuple] = {}
        dupes: List[tuple] = []
        for o in _rows:
            if not isinstance(o, dict):
                continue
            _raw = o.get("raw") if isinstance(o.get("raw"), dict) else {}
            order_id = str(o.get("order_id") or o.get("id") or _raw.get("id") or "")
            if not order_id or order_id in keep_ord_ids:
                continue
            _side = str(o.get("side") or _raw.get("side") or "").lower()
            if not _side:
                _sz_raw = o.get("size") if o.get("size") is not None else _raw.get("size")
                if _sz_raw is None:
                    _sz_raw = o.get("amount") if o.get("amount") is not None else _raw.get("amount")
                try:
                    if _sz_raw is not None and float(_sz_raw) != 0:
                        _side = "buy" if float(_sz_raw) > 0 else "sell"
                except (TypeError, ValueError):
                    pass
            _ro = o.get("reduce_only") if o.get("reduce_only") is not None else _raw.get("reduce_only")
            if _ro is None:
                _ro = o.get("is_reduce_only") if o.get("is_reduce_only") is not None else _raw.get("is_reduce_only")
            if _ro in (True, "true", "1"):
                continue          # 减仓/保护族不属入场生命周期管辖
            if _v == "binance":
                inst_disp = str(o.get("inst_id") or _raw.get("symbol") or "")
                created_ms = int(float(_raw.get("time") or _raw.get("updateTime") or now_ts))
            else:
                inst_disp = str(o.get("contract") or _raw.get("contract") or "")
                created_ms = int(float(o.get("create_time") or _raw.get("create_time") or (now_ts / 1000)) * 1000)
            _b = str(o.get("base") or "").upper() or inst_disp.replace("_USDT", "").replace("USDT", "").split("-")[0].upper()
            if not _b or _side not in ("buy", "sell"):
                continue
            entry = (created_ms, order_id, inst_disp)
            key = (_b, "long" if _side == "buy" else "short")
            if key in best and best[key][0] >= created_ms:
                dupes.append(entry)
            else:
                if key in best:
                    dupes.append(best[key])
                best[key] = entry
        for created_ms, order_id, inst_disp in dupes:
            if now_ts - created_ms <= STALE_MS:
                continue  # 宽限期内不动手
            _b0 = inst_disp.replace("_USDT", "").replace("USDT", "").split("-")[0].upper()
            try:
                _ad.cancel_order(_b0, order_id)
                print(f"[挂单生命周期管理] 同向重复单收敛撤销({_v.upper()}): {inst_disp} (id={order_id})")
            except Exception as exc:
                return False, f"failed to cancel duplicate order {_v} {inst_disp}/{order_id}: {exc}"
        for (venue_base, dir_word), (created_ms, order_id, inst_disp) in best.items():
            if now_ts - created_ms <= STALE_MS:
                continue
            if _intent_covers(venue_base, dir_word):
                continue  # 新鲜意图归属 → 保留（与 OKX kept 同语义）
            try:
                _ad.cancel_order(venue_base, order_id)
                print(f"[挂单生命周期管理] 自动撤销超时挂单({_v.upper()}): {inst_disp} (id={order_id})")
            except Exception as exc:
                return False, f"failed to cancel stale order {_v} {inst_disp}/{order_id}: {exc}"
    return True, "open orders verified"



def reconcile_pending_orders(trackers: Dict[str, Any] = None, now_ms: int = None, pending: List[Dict[str, Any]] = None,
                              *,
                              _order_pos_side,
                              load_open_intents,
                              load_trackers,
                              OPEN_INTENT_TTL_MS,
                              RECONCILE_REASON_SIDE_MISMATCH,
                              RECONCILE_REASON_INTENT_STALE,
                              RECONCILE_REASON_ORPHAN,
                              okx_rest) -> Tuple[bool, set]:
    """周期开始（新增下单前）对本账户 USDT-SWAP 存量限价挂单做归属对账（US-006）。

    语义：
    - 持仓追踪器归属（同合约同方向）→ 接管保留；
    - 本地开仓意图（决策历史）同合约同方向且未过期 → 接管保留；
    - 同合约方向不一致 → 撤销（原因=方向不一致）；
    - 意图存在但超过 TTL → 撤销（原因=周期意图已失效）；
    - 无任何本地意图可归属 → 撤销（原因=无对应意图）。

    返回 (ok, kept_ord_ids)。ok=False 表示对账自身失败（读取/撤销网络或签名异常）
    → 调用方必须 fail-closed：本周期禁止新增下单（不是清库）。
    """
    now_ms = int(now_ms or time.time() * 1000)
    if pending is None:
        try:
            # GET /api/v5/trade/orders-pending（instType=SWAP，冻结周期环境直签）
            pending = okx_rest.pending_orders()
        except Exception as exc:
            print(f"[挂单对账] warn 读取存量挂单失败: {exc} → fail-closed，本周期禁止新增下单")
            return False, set()
    if not isinstance(pending, list):
        print("[挂单对账] warn 存量挂单响应非列表 → fail-closed，本周期禁止新增下单")
        return False, set()
    if trackers is None:
        trackers = load_trackers()
    # ⚠️ 第一百三十四刀：读不到意图 ⇒ **fail-closed 且不撤任何单**（理由同上）。
    try:
        intents = load_open_intents()
    except Exception as _intents_exc:
        print(f"[挂单对账] CRITICAL 本地意图不可读（{_intents_exc}）→ fail-closed：本周期"
              "禁止新增下单，且**不撤销任何挂单**（读不到 ≠ 没有意图）")
        return False, set()
    kept: set = set()

    def _cancel_orphan(reason: str) -> bool:
        try:
            okx_rest.cancel_order(inst_id, ord_id)
        except Exception as exc:
            print(f"[挂单对账] warn 撤销失败 instId={inst_id} ordId={ord_id}: {exc} → fail-closed，本周期禁止新增下单")
            return False
        print(f"[挂单对账] 撤销孤儿单 instId={inst_id} ordId={ord_id} side={side} 原因={reason}")
        return True

    for order in pending:
        if not isinstance(order, dict):
            continue
        inst_id = str(order.get("instId") or "")
        ord_id = str(order.get("ordId") or "")
        side = str(order.get("side") or "").lower()
        state = str(order.get("state", "live")).lower()
        if state not in {"live", "partially_filled"} or not inst_id:
            continue
        pos_side = _order_pos_side(side)
        tracker_key = f"{inst_id}_{pos_side}"
        # 1) 持仓追踪器归属：同合约同方向 → 重启后接管保留
        if tracker_key in (trackers or {}):
            print(f"[挂单对账] 接管挂单 instId={inst_id} ordId={ord_id} side={side} 原因=追踪器归属")
            kept.add(ord_id)
            continue
        # 2) 本地开仓意图（决策历史）归属。
        #    审计(2026-09-13)·PEPE 永动机修复之一：旧 `next(...)` 取**最老**匹配意图——
        #    当日该标的第一笔意图越过 TTL 后，之后每一轮的新挂单都被误判「周期意图已
        #    失效」撤销、再被下一轮重新提交（撤旧挂新无限循环，实测 PEPE 10:45~15:15
        #    六连撤）。现按同标的**最新**意图判归属。
        _same_inst = [i for i in intents if str(i.get("instId")) == inst_id]
        intent = max(_same_inst, key=lambda i: int(i.get("ts", 0) or 0), default=None)
        if intent is not None:
            if str(intent.get("side", "")).lower() != side:
                if not _cancel_orphan(RECONCILE_REASON_SIDE_MISMATCH):
                    return False, kept
                continue
            if now_ms - int(intent.get("ts", 0) or 0) > OPEN_INTENT_TTL_MS:
                if not _cancel_orphan(RECONCILE_REASON_INTENT_STALE):
                    return False, kept
                continue
            print(f"[挂单对账] 接管挂单 instId={inst_id} ordId={ord_id} side={side} 原因=意图归属")
            kept.add(ord_id)
            continue
        # 3) 孤儿单：无任何本地意图可归属
        if not _cancel_orphan(RECONCILE_REASON_ORPHAN):
            return False, kept
    return True, kept


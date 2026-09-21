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

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "DEFAULT_RENEW_WITHIN_S",
    "attribute_protective_orders",
    "select_legs_to_cancel_after_close",
    "audit_cross_venue_protection",
    "cancel_orphan_attributed_legs",
    "read_ledger_rows",
    "ensure_venue_protection",
    "scan_protective_orders",
    "DEFAULT_WATCHDOG_DEBOUNCE_S",
    "watchdog_gap_key",
    "watchdog_debounce_step",
]

#: 默认提前续期窗口（24h；roadmap G8 的验收口径）
DEFAULT_RENEW_WITHIN_S = 24 * 3600
#: watchdog **防抖窗口**默认值（30 分钟）：缺口必须**持续**这么久才允许写单。
#: 续期窗口是 24h，故 30 分钟远小于它 —— 防的是"瞬时口径波动被当成缺口"，
#: 而不是拖延真实缺口（真实缺口在窗口内会一直出现，够时即动手）。
DEFAULT_WATCHDOG_DEBOUNCE_S = 30 * 60
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
        # 第一百七十五刀：**客户端订单号**（Binance `clientAlgoId`/`clientOrderId` 等）——
        # 补"标签存在但扫描器看不见"这个洞。⚠️ 如实说明：真机上 Binance 的 `clientAlgoId`
        # 目前是交易所给的**随机串**（实测 20/20 不含 `r20`）⇒ 本行**不会**让当下的 Binance
        # 腿变得可归因；给 Binance 腿打标签是**写入侧**的事，已登记（会改下单参数，需拍板）。
        row.get("clientAlgoId"), raw.get("clientAlgoId"),
        row.get("clientOrderId"), raw.get("clientOrderId"),
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
    """该腿覆盖的数量；`None` = 不可判定。

    ⚠️ 各所字段位置不同（**本机实跑真单核对过**）：
    - Gate `price_orders`：`initial.size` / 顶层 `size`（`size=0 + close=true` 走整仓平分支）；
    - Binance `algoOrder`：数量在 **`raw.quantity`**（顶层没有 `size`），`actualQty` 是
      已成交量、**不能**当覆盖量用。
    漏读 Binance 这一层会让每个币安仓位的覆盖都变成"不可判定"，巡检永远不敢动手。
    """
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    initial = row.get("initial") if isinstance(row.get("initial"), dict) else {}
    # 剩余量优先（Gate `left` / 部分成交后的余量），其次下单量本身
    for container in (row, raw, initial):
        for key in ("left", "size_remaining", "remaining"):
            num = _as_float(container.get(key))
            if num is not None and num > 0:
                return num
    for container in (row, raw, initial):
        for key in ("size", "quantity", "origQty", "qty", "amount"):
            num = _as_float(container.get(key))
            if num is not None and num > 0:
                return num
    return None


def _is_full_close(row: Dict[str, Any]) -> bool:
    """该腿语义是"平掉全部仓位"（Gate `close=true`/`auto_size`、Binance `closePosition`）。

    Gate 挂单用 `initial.size=0 + close=true` 表示"整仓平"，此时 `size` 字段
    给不出覆盖张数 —— 但覆盖范围其实是**全部**，不能当成"不可判定"。
    """
    initial = row.get("initial") if isinstance(row.get("initial"), dict) else {}
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    for container in (row, initial, raw):
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


def _trigger_price(row: Dict[str, Any]) -> Optional[float]:
    """该腿的触发价（续期时**复用**它，绝不重新定价）。

    Gate 在 `trigger.price`，Binance 在 `trigger_price`/`triggerPrice`。
    """
    trigger = row.get("trigger") if isinstance(row.get("trigger"), dict) else {}
    for candidate in (row.get("trigger_price"), row.get("triggerPrice"),
                      trigger.get("price"), row.get("price")):
        num = _as_float(candidate)
        if num is not None and num > 0:
            return num
    return None


def leg_base(row: Dict[str, Any]) -> str:
    """该腿的**币种基名**（各所字段位置不同，本机真单核对）。

    - Gate `price_orders`：`initial.contract` = `BTC_USDT`（顶层没有 `symbol`）；
    - Binance `algoOrder`：`symbol` / `raw.symbol` = `BTCUSDT`。
    """
    initial = row.get("initial") if isinstance(row.get("initial"), dict) else {}
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    for candidate in (initial.get("contract"), row.get("contract"), row.get("symbol"),
                      row.get("base"), row.get("inst_id"), row.get("instId"),
                      raw.get("symbol"), raw.get("contract")):
        text = str(candidate or "").strip()
        if text:
            # 第一百八十五刀：不再自己拼一遍归一 —— 委派给唯一实现（见 `_base_of`）。
            from r20_backend.exchanges.base import canonical_base
            return canonical_base(text)
    return ""


def _leg_position_side(row: Dict[str, Any]) -> Optional[str]:
    """该腿保护的**持仓方向**（`long`/`short`）；判不出 → `None`（不猜）。

    各所语义不同（本机真单核对）：
    - Gate：`initial.auto_size = close_short` ⇒ 保护的是**空仓**；`close_long` ⇒ 多仓；
      无 auto_size 时退回 `direction`（Gate 的 `direction` 是**平仓方向**：long=买平 ⇒ 原仓空）；
    - Binance：腿的 `side` 是**平仓方向**（BUY 平空 ⇒ 原仓 short）。
    """
    initial = row.get("initial") if isinstance(row.get("initial"), dict) else {}
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    for container in (initial, row, raw):
        auto = str(container.get("auto_size") or container.get("autoSize") or "").lower()
        if "close_long" in auto:
            return "long"
        if "close_short" in auto:
            return "short"
    for container in (initial, row, raw):
        direction = str(container.get("direction") or "").lower()
        if direction in ("long", "short") and container is not row:
            return "short" if direction == "long" else "long"
    close_side = _row_close_side(row)
    if close_side == "buy":
        return "short"
    if close_side == "sell":
        return "long"
    return None


def _to_seconds(value: Optional[float]) -> Optional[float]:
    """把可能是**毫秒**的时间戳归一成秒。

    ⚠️ 判据必须是 `< 1e11`（秒）而不是 `< 1e9`：epoch 秒本身已经 ~1.79e9，
    用 1e9 当分界会把"秒"误判成"毫秒"、把时间除以 1000（本模块第一版就这么错过，
    结果"还剩 7 天"被算成"已过期"）。

    第一百八十八刀：判据与换算**只有一处实现**（`r20_backend.time_utils` 的
    `EPOCH_MS_THRESHOLD` / `to_seconds` / `to_millis`）；本函数与
    `dashboard_payload/multi_venue.py` 都已改为委派（此前四处各写一遍）。
    """
    # 第一百八十八刀：分界与换算**只有一处实现**（`r20_backend.time_utils`），此处委派。
    from r20_backend.time_utils import to_seconds
    return to_seconds(value)


def _expiry(row: Dict[str, Any]) -> tuple:
    """→ `(expires_at_s | None, state)`；state ∈ {"absolute","relative","never","unknown"}。

    三种真实形态（本机实跑核对过）：
    - **Gate**：`trigger.expiration` 是**相对创建时间**的秒数（0 = 永不过期）；
    - **Binance**：`raw.goodTillDate` 是 GTD 绝对时间戳（0 = 无）；`timeInForce=GTC`
      是**明确语义**"撤销前一直有效"，故它等价于永不过期 —— 不能当成"不可判定"，
      否则每个币安仓位每周期都会被标记待复验（噪音会淹没真信号）；
    - 两者都读不到 ⇒ unknown（不可判定，交给上层复验，绝不假设安全）。
    """
    trigger = row.get("trigger") if isinstance(row.get("trigger"), dict) else {}
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}

    gtd = _as_float(raw.get("goodTillDate") or row.get("goodTillDate"))
    if gtd is not None and gtd > 0:
        return _to_seconds(gtd), "absolute"

    raw_exp = row.get("expiration")
    if raw_exp in (None, ""):
        raw_exp = trigger.get("expiration")
    if raw_exp in (None, ""):
        raw_exp = raw.get("expiration")
    exp = _as_float(raw_exp)
    if exp is not None:
        if exp <= 0:
            return None, "never"
        from r20_backend.time_utils import EPOCH_MS_THRESHOLD
        if exp >= EPOCH_MS_THRESHOLD:  # 绝对时间戳（毫秒）；分界取自唯一实现
            return exp / 1000.0, "absolute"
        if exp > 1e9:                  # 绝对时间戳（秒）
            return exp, "absolute"
        created = _to_seconds(_as_float(
            row.get("create_time") or row.get("createTime") or row.get("cTime")
            or raw.get("create_time") or raw.get("createTime") or raw.get("time")))
        if created is None:
            return None, "unknown"
        return created + exp, "relative"

    tif = str(row.get("timeInForce") or raw.get("timeInForce") or "").upper()
    if tif == "GTC":
        return None, "never"
    return None, "unknown"


_TRIGGER_TYPE_KEYS = ("tpTriggerPxType", "slTriggerPxType", "triggerPxType", "trigger_px_type")


def trigger_px_type(row: Dict[str, Any]) -> Optional[str]:
    """保护腿的**触发价类型**（各所字段不同，逐一查证后原样透传）。

    | 所 | 字段 | 本函数返回 |
    |---|---|---|
    | OKX | `tp/slTriggerPxType` | `mark` / `last` / `index`（**交易所自己的词**）|
    | Binance | `workingType` | `mark_price` / `contract_price`（**交易所自己的字面量**，仅小写化）|
    | Gate | `trigger.price_type`（**数字码**）| `price_type:<码>` —— **原样透传，不解释** |
    | 其它/读不到 | — | `None`（未上报；调用方须披露成 `unknown`）|

    为什么 Gate 的数字码**不翻译**：它的官方映射（0/1/2 各是什么价）本仓**未核实**
    （开发环境无法联网核对官方文档）⇒ 凭记忆写死映射就是把"没核实的东西"当成事实，
    正是本会话反复修的那类谎。原样带字段名给运营看，比猜一个中文名更有用也更诚实。

    Binance 的 `CONTRACT_PRICE` / `MARK_PRICE` 是**自描述字面量**（字面量本身说明了按哪种价），
    故只做小写化、不额外推断。

    读不到 ⇒ `None`（表示"未上报"），调用方必须把它**披露**成未知 ——
    **不得**用"我们期望的类型"顶替：读不到 ≠ 按标记价触发（读不到 ≠ 没有）。
    """
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    for container in (row, raw):
        for key in _TRIGGER_TYPE_KEYS:
            val = container.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
        # Binance：自描述字面量
        wt = container.get("workingType")
        if isinstance(wt, str) and wt.strip():
            return wt.strip().lower()
    # Gate：`trigger.price_type` 是数字码 ⇒ 原样带字段名，不做映射
    for container in (row, raw):
        trig = container.get("trigger")
        if isinstance(trig, dict) and trig.get("price_type") is not None:
            return f"price_type:{trig.get('price_type')}"
    return None


def protection_trigger_type_fields(legs: Optional[Sequence[Dict[str, Any]]],
                                   *, readable: bool = True) -> Dict[str, Any]:
    """保护腿触发价类型 → 面板/提示词字段（**两个生产路径共用同一份三态语义**）。

    三态（与"读不到 ≠ 没有"一致，且**绝不**用默认值顶替）：

    | 情形 | 取值 |
    |---|---|
    | 取数失败（`readable=False`）| `"unknown"` —— 读不到，可能本来有 |
    | 确实没有该类腿 | `None` —— 没有这东西 |
    | 有腿但该所未上报类型 | `"unknown"` —— 腿在，但"按什么价触发"不可判定 |

    顶级止损与止盈**各自**取值：同一条腿可能在修正后变成不同类型，
    合并成一个字段就会说谎。
    """
    if not readable:
        return {"protectionSlTriggerPxType": "unknown", "protectionTpTriggerPxType": "unknown"}
    out: Dict[str, Any] = {"protectionSlTriggerPxType": None, "protectionTpTriggerPxType": None}
    for leg in legs or []:
        if not isinstance(leg, dict):
            continue
        kind = str(leg.get("kind") or "").lower()
        if kind not in ("sl", "tp"):
            continue
        val = leg.get("trigger_px_type") or trigger_px_type(leg)
        key = "protectionSlTriggerPxType" if kind == "sl" else "protectionTpTriggerPxType"
        if out[key] is None:
            out[key] = val or "unknown"
    return out


def _norm_contract(text: Any) -> str:
    """合约串归一：只留字母数字并大写（`BTC_USDT` / `BTCUSDT` / `BTC-USDT-SWAP` → `BTC…`）。

    各所拼法不同（Gate 用 `BTC_USDT`、Binance 用 `BTCUSDT`、OKX 用 `BTC-USDT-SWAP`），
    而跨所持仓的 `inst_id` 又是**合成 id**（`GATE:BTC_USDT`）。不归一就没法可靠比较。
    """
    return "".join(ch for ch in str(text or "").upper() if ch.isalnum())


def _base_of(symbol: Any) -> str:
    """从合约/合成 id 里取**币种**（第一百八十刀引入，第一百八十五刀改为**委派**）。

    为什么委派：本仓"任意写法 → 裸币种"的语义**已经有唯一实现**
    （`r20_backend.exchanges.base.canonical_base`，面板/因子/符号归一都在用；
    `scripts/ai_brain_trader.py` 也早就在 import 它）。本函数此前又写了一份，于是两者在
    `GATE:BTC_USDT`（前缀）与 `BTC_USDC`（非 USDT 计价）上**给出不同答案** ——
    同一语义写两遍必然漂移，这里改为直接调用那一处。

    惰性导入：避免后端包在 import 期反向拉起本模块（本模块被面板导入）。
    """
    from r20_backend.exchanges.base import canonical_base
    return canonical_base(str(symbol or ""))


def _leg_contracts(row: Dict[str, Any]) -> List[str]:
    """该腿行里所有**可能表示合约**的串（归一后）。用于"这腿是不是本仓合约"的判断。"""
    initial = row.get("initial") if isinstance(row.get("initial"), dict) else {}
    out = []
    for value in (row.get("contract"), row.get("symbol"), row.get("inst_id"), row.get("instId"),
                  initial.get("contract"), initial.get("symbol")):
        norm = _norm_contract(value)
        if norm:
            out.append(norm)
    return out


def _leg_matches_base(row: Dict[str, Any], base: str) -> bool:
    """这条腿是否属于 `base` 这个币种。

    ⚠️ 用"归一后 **前缀相等**"而不是原来的"原始串子串包含"：
    子串包含会把 `WBTCUSDT`（包装币）当成 `BTC` 的腿；前缀相等不会。
    两种常见形态都覆盖：`base="BTC"` ↔ `BTCUSDT`；`base="BTCUSDT"` ↔ `BTC_USDT`。
    """
    if not base:
        return True
    for norm in _leg_contracts(row):
        if norm == base or norm.startswith(base) or base.startswith(norm):
            return True
    return False


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
    base = _base_of(symbol)
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
    live_sl_seen = False

    for row in (rows or []):
        if not isinstance(row, dict) or not _is_live(row):
            continue
        kind = _leg_kind(row)
        if kind is None:
            foreign += 1
            continue
        if require_symbol_match and base and not _leg_matches_base(row, base):
            continue
        # 第一百七十九刀：方向判据**改用唯一来源** `_leg_position_side`（真单核对过 Gate
        # `auto_size`/`direction` 与 Binance `side` 的语义），而不是本函数原来那段只看
        # `side`/`order_side` 的比较 —— 真机实测：**Gate 的 6 条腿一条都读不出 side**，
        # 于是"方向过滤"对 Gate **完全失效**，一条平**空**腿会被算进**多**仓的覆盖
        # （本仓 attribution 早就读得到 Gate 方向，故它一直能报 side_mismatch；
        # 只有覆盖这条链在瞎）。语义写两遍就会漂，这里是同一语义的第二次拼写。
        leg_pos_side = _leg_position_side(row)
        if leg_pos_side is not None and pos_side and leg_pos_side != pos_side:
            continue
        # 方向**读不出来** ⇒ 沿用本函数一直以来的口径：**照旧计入**覆盖（`_is_close` 的
        # 整仓平腿、无方向字段的所都依赖这条；既有 6 个用例把它钉成了有意选择）。
        # ⚠️ 残留（已登记在手册）：不报方向的所会留着"反向腿被算成覆盖"的口子 ——
        # 今天**不可达**（真机核对：Gate 6/6、Binance 18/18 都能读出方向）。
        # 将来若接入不报方向的所，这里应改判 `coverage_unknown`（而不是继续默认算覆盖）。

        leg = {
            "id": str(row.get("id") or row.get("algo_id") or row.get("algoId")
                      or row.get("order_id") or row.get("ordId") or ""),
            "kind": kind,
            "trigger_price": _trigger_price(row),
            # 第一百六十七刀：触发价类型（读不到 ⇒ None，由展示层披露成 unknown）
            "trigger_px_type": trigger_px_type(row),
        }
        expires_at, exp_state = _expiry(row)
        leg["expires_at"] = expires_at
        leg["remaining_s"] = None if expires_at is None else round(expires_at - float(now_s), 1)
        leg["expiry_state"] = exp_state
        ours.append(leg)

        # 第一百七十八刀：**已过期 ⇒ 不是覆盖**。
        # 真机反例（本刀实测）：Gate 一条 `expiration=3600`、`create_time` 在 2 小时前的止损腿，
        # 仍被算成"覆盖满量 + 有活止损"⇒ `protected_now=True`、`needs_repair=False`，
        # 审计只说 "renew" 而**不进 critical** ⇒ 一个**裸奔**的仓位被报成"已保护"。
        # 过期腿的到期信息是**确知**的（不是"不可判定"）⇒ 必须从覆盖里剔除。
        _leg_expired = (exp_state in ("relative", "absolute") and expires_at is not None
                        and expires_at <= float(now_s))

        if kind == "sl":
            if _is_full_close(row) and not _leg_expired:
                full_close_leg = True
            leg_size = _leg_size(row)
            if leg_size is None and not _is_full_close(row):
                coverage_unknown = True
            elif leg_size is not None and not _leg_expired:
                covered += leg_size
            if not _leg_expired:
                live_sl_seen = True

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
    #: ⚠️ 不能写成 `any(kind == "sl")`：那会把**已过期**的腿算成"有活止损"（本刀实测的假安心）。
    #: 口径：`expiry_state == "never"`（**显式** 0 / GTC ⇒ 明确"撤销前一直有效"）算活；
    #: 到期时间**已过**（absolute/relative 且 <= now）算死；
    #: `unknown`（**取数缺字段**，说不准）**算活但计入 `needs_verify`** —— 交易所列着它，
    #: 只是我们无法断言它的到期，按"不可判定≠安全"必须要求复验。
    has_live_sl = live_sl_seen

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
                            sl_px: Optional[float] = None,
                            tp_px: Optional[float] = None,
                            now_s: float,
                            expiration_s: int = 604800,
                            renew_within_s: float = DEFAULT_RENEW_WITHIN_S,
                            tolerance_ratio: float = DEFAULT_TOLERANCE_RATIO,
                            log: Any = print) -> Dict[str, Any]:
    """按判定结果**安全**修复缺口 / 续期临期腿（先挂新、后撤旧）。

    `sl_px` / `tp_px` 可省略：省略时**复用现有腿的触发价**（续期的常见场景 ——
    保护价位是既定策略，续期只该延长时间，不该重新定价）。若既没传、现有腿上
    也拿不到价格，**绝不去猜一个价位**，直接返回 `stage="no_price"`。

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

    # 第一百八十刀：**双保险** —— 本处虽已按合约取腿（`list_protective_orders(symbol)`），
    # 但那是"信任适配器尊重参数"。开启 `require_symbol_match` 后，即使某所适配器忽略参数
    # 返回全量腿，别的币的腿也不会被算进这个仓位的覆盖（`symbol` 常是合成 id，见 `_base_of`）。
    scan = scan_protective_orders(rows, symbol=symbol, pos_side=pos_side,
                                  position_size=position_size, now_s=now_s,
                                  renew_within_s=renew_within_s,
                                  tolerance_ratio=tolerance_ratio,
                                  require_symbol_match=True)
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

    # 价格：入参优先；否则复用现有腿的触发价（续期=只延时间、不改价位）。
    resolved_sl = sl_px
    if resolved_sl is None:
        resolved_sl = next((leg["trigger_price"] for leg in scan["ours"]
                            if leg["kind"] == "sl" and leg["trigger_price"]), None)
    resolved_tp = tp_px
    if resolved_tp is None:
        resolved_tp = next((leg["trigger_price"] for leg in scan["ours"]
                            if leg["kind"] == "tp" and leg["trigger_price"]), None)
    if resolved_sl is None:
        result.update(ok=False, stage="no_price",
                      detail="既未传入止损价、现有腿上也没有触发价 —— 不猜价位，"
                             "留给上层（需人工或用既定策略价位重挂）",
                      protected_now=protected_now)
        return result

    # ① 先挂新（repair 时补缺口；renew 时用新腿替换临期腿）
    contracts = scan["missing_size"]
    if contracts is None or contracts <= 0:
        contracts = position_size
    try:
        placed = ad.attach_protective_orders(symbol, pos_side, tp_px=resolved_tp,
                                             sl_px=float(resolved_sl),
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
            if not cancel_protective_leg(ad, oid, symbol=symbol):
                raise RuntimeError("该所适配器没有可用的撤腿方法")
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


def attribute_protective_orders(positions: Optional[Sequence[Dict[str, Any]]],
                               legs: Optional[Sequence[Dict[str, Any]]],
                               ledger_rows: Optional[Sequence[Dict[str, Any]]] = None,
                               *, tolerance_ratio: float = DEFAULT_TOLERANCE_RATIO
                               ) -> Dict[str, Any]:
    """逐腿归属：这条保护腿是**给当前哪个仓**挂的？纯判定，无 IO。

    为什么必须做（2026-09-20 实盘实测）：Binance 账户 13 张腿里只有 2 张对得上唯一活动仓
    （UNI 82 张），另有 2 张是 UNI 的**旧量**（51/55，来自更早的仓）、3 张可归因孤儿
    （ARB/XRP/ETH，台账有同向同量已平记录）、6 张**归属不可判定**（ETH 0.537 / SOL 10.45…）。
    Gate 侧 3 张腿则全部带我们的 `t-r20sl/t-r20tp` 标签、`auto_size=close_*`（整仓平，无张数）。

    危害（判据，不是"要不要撤"）：
    1. **虚假安全感**：`scan_protective_orders` 按币种+平仓方向+数量算覆盖 ⇒ 给新仓算覆盖时，
       旧仓遗留的腿会被算成"已有保护"；
    2. **会减新仓**：这些腿是 `reduceOnly` 条件单 ⇒ 同币再开仓后，价格触及**旧触发价**时
       会**真的减掉新仓**的一部分。

    ## 归属证据分三档（诚实区分"证明是我们的"与"看起来像我们的"）

    | evidence | 含义 | 可否自动清理 |
    |---|---|---|
    | `tag` | 带本系统标签（Gate `t-r20sl/t-r20tp`）⇒ **可证明**是我们的 | ✅ |
    | `ledger` | 无标签，但台账有**同向同量**记录 ⇒ 高度可能 | ✅ |
    | `None` | 两者都没有 ⇒ **归属不可判定** | ❌ 绝不自动撤（可能是用户手单） |

    ## 分桶

    | state | 含义 |
    |---|---|
    | `matched` | 有活动仓，腿保护它（整仓平腿 或 张数与仓量相符） |
    | `size_mismatch` | 有活动仓，但张数不符（且非整仓平）⇒ 多半旧仓遗留 |
    | `side_mismatch` | 有活动仓，但腿保护的是**另一个方向** ⇒ 来自已反手的旧仓 |
    | `orphan_attributed` | 无活动仓，但证据为 `tag`/`ledger` |
    | `orphan_unattributed` | 无活动仓且无证据 ⇒ 不可判定 |
    | `unparsed` | 连币种都读不出的行 ⇒ 单独登记（**不得**伪装成"不可判定孤儿"） |
    | `foreign` | 无本系统保护腿特征（标签/类型名都不匹配） |

    ⚠️ 本函数**不做任何撤销**；`cleanup_candidates` 只是"若要清理，这些是可归因项"。
    """
    tol = max(0.0, float(tolerance_ratio or 0.0))
    pos_by_base: Dict[str, Dict[str, Any]] = {}
    pos_count_by_base: Dict[str, int] = {}
    for p in (positions or []):
        if not isinstance(p, dict):
            continue
        base = leg_base({"symbol": p.get("base") or p.get("symbol") or "",
                            "inst_id": p.get("inst_id") or p.get("instId") or ""})
        if base:
            # 第一百八十三刀：同币**多仓**（对冲模式 / 异常数据）时 `setdefault` 只留第一个
            # ⇒ 归属会把腿全对到那一个仓位上，另一侧**静默消失**。改为"留首个 + 记数"，
            # 并在报告里披露 `ambiguous_positions`（读得到却只用一个 ⇒ 必须说出来）。
            pos_count_by_base[base] = pos_count_by_base.get(base, 0) + 1
            pos_by_base.setdefault(base, p)

    def _ledger_evidence(base: str, want_pos_side: Optional[str], size: float) -> Optional[Dict[str, Any]]:
        """台账里同币、同向、同量的记录（含已平）→ 证据（可能为 None）。"""
        for r in reversed(list(ledger_rows or [])):
            if not isinstance(r, dict):
                continue
            r_base = leg_base({"symbol": r.get("inst") or r.get("symbol") or r.get("name") or ""})
            if r_base != base:
                continue
            r_side = str(r.get("side") or "").strip().lower()
            r_side = {"空": "short", "多": "long"}.get(r_side, r_side)
            if r_side in ("sell",):
                r_side = "short"
            elif r_side in ("buy",):
                r_side = "long"
            if want_pos_side and r_side and r_side != want_pos_side:
                continue
            r_sz = _as_float(r.get("sz") if r.get("sz") is not None else r.get("size"))
            if r_sz is None or size <= 0:
                continue
            if abs(abs(r_sz) - size) <= max(1e-6, size * tol):
                return {"id": r.get("id"), "status": r.get("status"), "sz": r_sz,
                        "side": r.get("side")}
        return None

    buckets: Dict[str, List[Dict[str, Any]]] = {
        "matched": [], "size_mismatch": [], "side_mismatch": [],
        "orphan_attributed": [], "orphan_unattributed": [], "unparsed": [], "foreign": []}

    for row in (legs or []):
        if not isinstance(row, dict):
            continue
        if _leg_kind(row) is None:
            buckets["foreign"].append({"reason": "无本系统保护腿特征（标签/类型名都不匹配）"})
            continue
        base = leg_base(row)
        if not base:
            buckets["unparsed"].append({"reason": "读不出币种（各所字段位置不同）",
                                        "id": str(row.get("id") or row.get("algo_id") or "")})
            continue
        kind = _leg_kind(row)
        full_close = _is_full_close(row)
        size = abs(_as_float(_leg_size(row)) or 0.0)
        leg_side = _leg_position_side(row)
        tagged = ("r20sl" in _row_text(row)) or ("r20tp" in _row_text(row))
        entry = {"symbol": base, "kind": kind, "protects": leg_side,
                 "size": size, "full_close": bool(full_close),
                 "trigger_price": _trigger_price(row),
                 "id": str(row.get("id") or row.get("algo_id") or row.get("algoId")
                           or row.get("order_id") or "")}

        pos = pos_by_base.get(base)
        if pos is not None:
            pos_side = str(pos.get("side") or "").lower() or None
            pos_size = abs(_as_float(pos.get("size_signed") or pos.get("pos")) or 0.0)
            if leg_side and pos_side and leg_side != pos_side:
                buckets["side_mismatch"].append(dict(entry, position_side=pos_side,
                                                     position_size=pos_size))
                continue
            if full_close:
                buckets["matched"].append(dict(entry, position_size=pos_size))
                continue
            same = size > 0 and pos_size > 0 and abs(size - pos_size) <= max(1e-6, pos_size * tol)
            (buckets["matched"] if same else buckets["size_mismatch"]).append(
                dict(entry, position_size=pos_size))
            continue

        # 无活动仓 → 孤儿；证据优先级 tag > ledger > 无
        if tagged:
            buckets["orphan_attributed"].append(dict(entry, evidence="tag"))
            continue
        ev = _ledger_evidence(base, leg_side, size)
        if ev is not None:
            buckets["orphan_attributed"].append(dict(entry, evidence="ledger", ledger=ev))
        else:
            buckets["orphan_unattributed"].append(entry)

    cleanup = (buckets["orphan_attributed"] + buckets["size_mismatch"]
               + buckets["side_mismatch"])
    return {
        **buckets,
        #: 同币多仓（>1 行）的币种：腿会被对到**首个**仓位上 ⇒ 可能张冠李戴，须人工复核
        "ambiguous_positions": sorted(b for b, n in pos_count_by_base.items() if n > 1),
        "counts": {k: len(v) for k, v in buckets.items()},
        #: 可安全清理的候选（**仅当**调用方要清理时）：可归因孤儿 + 旧量/旧向腿
        "cleanup_candidates": cleanup,
        #: 需要人看但不能自动动的（归属不可判定 / 读不出的行）
        "needs_human": buckets["orphan_unattributed"] + buckets["unparsed"],
        "legs_total": sum(len(v) for v in buckets.values()),
        "orphan_total": len(buckets["orphan_attributed"]) + len(buckets["orphan_unattributed"]),
    }


def select_legs_to_cancel_after_close(closed_position: Optional[Dict[str, Any]],
                                      legs: Optional[Sequence[Dict[str, Any]]],
                                      ledger_rows: Optional[Sequence[Dict[str, Any]]] = None,
                                      *, tolerance_ratio: float = DEFAULT_TOLERANCE_RATIO
                                      ) -> Dict[str, Any]:
    """**平仓已核验归零之后**，挑出该撤的腿（纯选择，不发单）。

    为什么是"平仓后"而不是"清理任务"：遗留腿的产生源头就是**平仓路径从不撤腿**
    （实测 `close_position` 只提交市价全平，`cancel_protective_orders` 仅 `scale_out` 用过）。
    在源头补上，才不会一边清理一边继续产生。

    ## 只撤"能证明是这一笔的"，其余一律不碰

    - `matched`：腿保护的就是刚平掉的那个仓（张数相符，或 `auto_size` 整仓平）⇒ 撤；
    - `orphan_attributed` 且证据 `tag`（Gate `t-r20sl/t-r20tp`）⇒ **可证明是我们的** ⇒ 撤；
    - `size_mismatch` / `side_mismatch`：**同一合约上属于别的仓**的历史腿 ——
      平掉 A 仓不等于 B 仓的腿该撤，故**只报告不撤**（留给归属审计）；
    - `orphan_unattributed` / `unparsed` / `foreign`：**绝不撤**（可能是用户手单）。

    ⚠️ 前提由调用方保证：**已经核验该合约没有剩余仓位**。本函数不做该核验，
    因为它不发单、也不读交易所 —— 调用方若在未归零时调用，会把还在保护中的腿撤掉。
    """
    r = attribute_protective_orders([closed_position] if closed_position else [],
                                   legs, ledger_rows,
                                   tolerance_ratio=tolerance_ratio)
    out: List[Dict[str, Any]] = []
    for leg in r["matched"]:
        out.append({"id": leg.get("id"), "symbol": leg.get("symbol"), "kind": leg.get("kind"),
                    "reason": "matched"})
    for leg in r["orphan_attributed"]:
        if str(leg.get("evidence")) == "tag":
            out.append({"id": leg.get("id"), "symbol": leg.get("symbol"), "kind": leg.get("kind"),
                        "reason": "tag"})
    ids = [x["id"] for x in out if x.get("id")]
    return {
        "to_cancel": out,
        "ids": ids,
        "not_touched": {
            "size_mismatch": r["size_mismatch"],
            "side_mismatch": r["side_mismatch"],
            "orphan_unattributed": r["orphan_unattributed"],
            "unparsed": r["unparsed"],
            "foreign": r["foreign"],
        },
        "counts": {
            "to_cancel": len(out),
            "not_touched": sum(len(r[k]) for k in
                               ("size_mismatch", "side_mismatch", "orphan_unattributed",
                                "unparsed", "foreign")),
        },
        "attribution": r,
    }


def read_ledger_rows(path: Any, *, log: Any = print) -> Optional[List[Dict[str, Any]]]:
    """读台账行，**只**供归属取证（本模块其余部分保持无 IO，此处是显式例外）。

    ## 三态（与"读不到 ≠ 没有"一致，且方向必须**偏保守**）

    | 情形 | 返回 | 归属后果 |
    |---|---|---|
    | 文件不存在 | `None` | 不产生 `ledger` 证据 ⇒ 腿留在 `orphan_unattributed` ⇒ **绝不自动撤** |
    | 不可读 / JSON 坏 / 不是列表 | `None` + 告警 | 同上（fail-closed：读不到就不产生证据）|
    | 正常 | 行列表 | `ledger` 档证据可用（同币同向同量 ⇒ 高度可能是本方）|

    为什么保守方向是"不产生证据"而不是"当成空台账"：把读失败当"没有台账记录"，会把本可
    证明归属的腿降级为"归属不可判定"；反过来把读失败当"都是我们的"，则会**撤掉用户手单**。
    两害相权，取"什么也不做"。
    """
    try:
        fp = Path(path)
    except Exception:
        return None
    if not fp.exists():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"[台账取证] warn 读不到台账（{type(exc).__name__}: {exc}）⇒ 本轮不产生 ledger 证据")
        return None
    if isinstance(data, dict):
        # ⚠️ 不能写成 `data.get("trades") or data.get("rows") or []`：那会把"结构不认识的 dict"
        # 悄悄变成**空台账**（＝"确实没有记录"），于是本可证明归属的腿被降级成不可判定。
        # 结构认不出来 ⇒ 与"读不到"同档：不产生证据（本门用例抓到的正是这一处）。
        inner = data.get("trades")
        if inner is None:
            inner = data.get("rows")
        if inner is None:
            log("[台账取证] warn 台账是 dict 但没有 trades/rows 列表 ⇒ 本轮不产生 ledger 证据")
            return None
        data = inner
    if not isinstance(data, list):
        log("[台账取证] warn 台账结构不是列表 ⇒ 本轮不产生 ledger 证据")
        return None
    return [r for r in data if isinstance(r, dict)]


def cancel_protective_leg(ad: Any, leg_id: Any, *, symbol: Any = None) -> bool:
    """按**能力探针**撤一条保护腿；撤不动返回 False（不抛）。

    ⚠️ 为什么必须有这个探针（第一百九十一刀，真机核对）：三个适配器的撤腿能力**各不相同** ——
    `cancel_price_order` 只有 **Gate** 有，`cancel_algo_order` 只有 **Binance** 有，
    OKX 走 `okx_rest` 自己的路径。本模块的跨所孤儿腿清理此前直接 `ad.cancel_price_order(leg_id)`，
    于是对 **Binance 恒失败**（`AttributeError` 被登记成 error、腿留着）——
    而 Binance 恰恰是孤儿腿最多的那个所（真机：17 条腿）。本文件另一处早就用了
    `hasattr` 阶梯 ⇒ 同一语义两种写法，这里统一成一个探针。

    顺序与既有实现一致：`cancel_price_order` → `cancel_algo_order` → `cancel_order(symbol, id)`。
    """
    leg_id = str(leg_id or "")
    if not leg_id:
        return False
    if hasattr(ad, "cancel_price_order"):
        ad.cancel_price_order(leg_id)
        return True
    if hasattr(ad, "cancel_algo_order"):
        ad.cancel_algo_order(algo_id=leg_id)
        return True
    if symbol is not None and hasattr(ad, "cancel_order"):
        ad.cancel_order(symbol, leg_id)
        return True
    return False


def cancel_orphan_attributed_legs(ad: Any, *,
                                  positions: Optional[Sequence[Dict[str, Any]]],
                                  symbols: Sequence[str],
                                  ledger_rows: Optional[Sequence[Dict[str, Any]]] = None,
                                  dry_run: bool = True,
                                  log: Any = print) -> Dict[str, Any]:
    """撤销"**归属明确、但已无对应持仓**"的保护腿（逐腿、按 id）。

    ## 为什么需要这个函数（而 G8 巡检不替你做）

    `audit_cross_venue_protection` 明确"**只报告不撤销**"：孤儿腿该不该撤是**运营决定**。
    但"运营决定"不等于"手动乱撤" —— 本函数把那次决定变成**有护栏的动作**：

    | 护栏 | 为什么 |
    |---|---|
    | 只撤 `attribute_protective_orders` 的 `orphan_attributed` 桶 | 该桶的证据是 `tag`（本方标签）或 `ledger`（台账同向同量已平）——**可证明/高度可能**是本方的腿 |
    | `orphan_unattributed` / `side_mismatch` / `size_mismatch` **一律不碰** | 归属不可判定 ⇒ 可能是用户手单（doctrine：绝不自动撤） |
    | 该合约**仍有活动持仓** ⇒ 整合约跳过 | 此时"孤儿"判定可能只是持仓取数缺失；宁可留腿，不可裸奔 |
    | 逐腿走 `ad.cancel_price_order(id)`，**不用** `cancel_protective_orders(symbol)` | 后者会撤掉该合约**全部**触发单——包括仍在保护活动仓的那些 |

    返回 `{"dry_run", "cancelled", "would_cancel", "skipped", "not_touched", "errors", "attribution"}`。
    `dry_run=True`（默认）**只回报告不写单**。
    """
    report: Dict[str, Any] = {"dry_run": bool(dry_run), "cancelled": [], "would_cancel": [],
                              "skipped": [], "not_touched": [], "errors": [],
                              "attribution": {}}
    pos_by_base: Dict[str, List[Dict[str, Any]]] = {}
    for row in (positions or []):
        if not isinstance(row, dict):
            continue
        base = str(row.get("base") or row.get("symbol") or "").split("_")[0].split("-")[0].upper()
        if base:
            pos_by_base.setdefault(base, []).append(row)

    for symbol in symbols:
        base = str(symbol or "").split("_")[0].split("-")[0].upper()
        if not base:
            report["skipped"].append({"symbol": symbol, "why": "币种解析不出"})
            continue
        live_pos = [p for p in pos_by_base.get(base, [])
                    if abs(_as_float(p.get("size_signed") or p.get("pos")) or 0.0) > 0]
        if live_pos:
            report["not_touched"].append(
                {"symbol": base, "why": "该合约仍有活动持仓 ⇒ 整合约跳过（宁可留腿，不可裸奔）"})
            continue
        try:
            legs = list(ad.list_protective_orders(symbol) or [])
        except Exception as exc:
            report["errors"].append({"symbol": base, "stage": "list",
                                     "detail": f"{type(exc).__name__}: {exc}"})
            continue
        att = attribute_protective_orders([], legs, ledger_rows)
        # 归属层自己给了 counts（且含 cleanup_candidates/needs_human 等派生键）⇒ 直接用，不重算
        report["attribution"][base] = {"counts": att.get("counts") or {}}
        for leg in att.get("orphan_attributed", []):
            leg_id = str(leg.get("id") or "")
            item = {"symbol": base, "id": leg_id, "kind": leg.get("kind"),
                    "trigger_price": leg.get("trigger_price"),
                    "evidence": leg.get("evidence")}
            if not leg_id:
                report["skipped"].append(dict(item, why="腿没有 id ⇒ 无法逐腿撤（不用按合约全撤）"))
                continue
            if dry_run:
                report["would_cancel"].append(item)
                continue
            try:
                if not cancel_protective_leg(ad, leg_id, symbol=symbol):
                    raise RuntimeError("该所适配器没有可用的撤腿方法"
                                       "（cancel_price_order/cancel_algo_order/cancel_order 均无）")
                report["cancelled"].append(item)
                log(f"[跨所保护清理] 已撤销孤儿腿 {base} {item['kind']} id={leg_id}")
            except Exception as exc:
                report["errors"].append(dict(item, stage="cancel",
                                             detail=f"{type(exc).__name__}: {exc}"))
        for bucket in ("orphan_unattributed", "side_mismatch", "size_mismatch"):
            for leg in att.get(bucket, []):
                report["not_touched"].append({"symbol": base, "id": leg.get("id"),
                                              "bucket": bucket,
                                              "why": "归属不可判定/不匹配 ⇒ 按 doctrine 不碰"})
    return report


def audit_cross_venue_protection(xv_positions_by_venue: Optional[Dict[str, Any]], *,
                                 venue_registry: Any,
                                 environment: str,
                                 now_s: Optional[float] = None,
                                 venues: Sequence[str] = ("gate", "binance"),
                                 renew_within_s: float = DEFAULT_RENEW_WITHIN_S,
                                 expiration_s: int = 604800,
                                 dry_run: bool = False,
                                 ledger_rows: Optional[Sequence[Dict[str, Any]]] = None,
                                 log: Any = print) -> Dict[str, Any]:
    """对**已冻结的**跨所持仓快照做一遍保护巡检（每周期调用一次）。

    `dry_run=True` 时**只判定、不写单**：回答"如果开闸，这一轮会做哪些动作"
    —— 这是运营在打开 `R20_VENUE_PROTECTION_WATCHDOG` 之前的预演视图，
    也是线上排障时唯一安全的取证方式。

    返回 `{venues, actions, critical, errors, skipped, would, dry_run}`：

    - `actions`：本次真的动了单的仓位（续期/补挂），供 `executed_actions` 展示；
    - `would`：dry-run 下"**本来会做**"的动作（`stage` 为 `renew`/`repair`/`verify`/
      `no_price`），开闸前先看它，能避免把一次误判变成一串真实订单；
    - `critical`：**完全没有止损腿**的仓位 —— 这是必须吼出来的（本函数**不**替它
      定价补挂，因为那种价位是策略决定，不该由巡检层臆造）；
    - `errors`：逐所隔离的失败（一个所挂了不影响另一个所）；
    - `skipped`：所不可用/行缺字段等未处理项（如实登记，不装作巡检过）；
    - `attribution`：逐所**逐腿归属**（matched / size_mismatch / orphan_attributed /
      orphan_unattributed）——只报告不撤销；`ledger_rows` 传入本方台账行用于归因，
      不传则该所腿多为"归属不可判定"（如实，不猜）。

    读的是**调用方传入的快照**（`fetch_other_venue_positions` 的返回值），
    故本函数不额外出网取持仓；每仓一次 `list_protective_orders` 是必要的核验成本。
    """
    now = float(now_s if now_s is not None else time.time())
    report: Dict[str, Any] = {"venues": {}, "actions": [], "critical": [],
                              "errors": [], "skipped": [], "would": [],
                              "attribution": {}, "dry_run": bool(dry_run)}
    snapshot = xv_positions_by_venue or {}
    for venue in venues:
        rows = snapshot.get(venue)
        if rows is None:
            report["skipped"].append({"venue": venue, "why": "本周期快照无该所（未开闸或取数失败）"})
            continue
        try:
            ad = venue_registry.get_adapter(venue, environment=environment)
        except Exception as exc:
            report["errors"].append({"venue": venue, "stage": "adapter",
                                     "detail": f"{type(exc).__name__}: {exc}"})
            continue
        venue_stat = {"checked": 0, "renewed": 0, "repaired": 0, "missing": 0, "errors": 0}
        for row in (rows or []):
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("inst_id") or row.get("base") or "")
            pos_side = str(row.get("side") or "").lower()
            size = abs(_as_float(row.get("size_signed")) or 0.0)
            if not symbol or pos_side not in {"long", "short"} or size <= 0:
                report["skipped"].append({"venue": venue, "why": f"行字段不足: {row!r}"[:160]})
                continue
            venue_stat["checked"] += 1
            if dry_run:
                try:
                    prows = ad.list_protective_orders(symbol) or []
                except Exception as exc:
                    venue_stat["errors"] += 1
                    report["errors"].append({"venue": venue, "inst": symbol, "stage": "list",
                                             "detail": f"{type(exc).__name__}: {exc}"})
                    continue
                # 第一百八十刀：同 ensure 的双保险（本处 symbol 是合成 id `GATE:BTC_USDT`）。
                scan = scan_protective_orders(prows, symbol=symbol, pos_side=pos_side,
                                              position_size=size, now_s=now,
                                              renew_within_s=renew_within_s,
                                              require_symbol_match=True)
                would = "noop"
                # 第一百八十刀：判据用 `needs_repair`（= 没有活止损 **或** 覆盖不足），
                # 而不是只看 `has_live_sl`。此前"有活止损但量不够"（覆盖率 40% 这种）
                # 会落成 `noop` ⇒ **既不进 critical 也不进 would**，运营完全看不到缺口
                # （本刀的新用例当场把它抓出来）。
                if scan["needs_repair"]:
                    would = "repair"
                elif scan["needs_renew"]:
                    would = "renew"
                elif scan["needs_verify"]:
                    would = "verify"
                item = {"venue": venue, "inst": symbol, "side": pos_side, "would": would,
                        "covered_size": scan["covered_size"],
                        "missing_size": scan["missing_size"],
                        "coverage_ok": scan["coverage_ok"],
                        "expiring": [leg["id"] for leg in scan["expiring"]],
                        "expired": [leg["id"] for leg in scan["expired"]],
                        "expiry_unknown": [leg["id"] for leg in scan["expiry_unknown"]],
                        "foreign_count": scan["foreign_count"]}
                if would == "repair":
                    venue_stat["missing"] += 1
                    if scan["has_live_sl"]:
                        # 有活止损、只是**量不够/方向不覆盖** ⇒ 需要补量。
                        # 这不改 `critical` 的语义（docstring：critical = **完全没有**止损腿）。
                        item["why"] = ("覆盖不足：有活止损但量不够（covered="
                                       f"{scan.get('covered_size')}, missing={scan.get('missing_size')}）")
                        report["would"].append(item)
                    else:
                        report["critical"].append(item)
                elif would in ("renew", "verify"):
                    report["would"].append(item)
                if would == "renew":
                    venue_stat["renewed"] += 1
                elif would == "repair":
                    venue_stat["repaired"] += 1
                continue
            try:
                res = ensure_venue_protection(ad, symbol=symbol, pos_side=pos_side,
                                              position_size=size, now_s=now,
                                              renew_within_s=renew_within_s,
                                              expiration_s=expiration_s, log=log)
            except Exception as exc:      # 巡检自身异常绝不上抛（它只是加固层）
                venue_stat["errors"] += 1
                report["errors"].append({"venue": venue, "inst": symbol, "stage": "ensure",
                                         "detail": f"{type(exc).__name__}: {exc}"})
                continue
            item = {"venue": venue, "inst": symbol, "side": pos_side,
                    "stage": res.get("stage"), "detail": res.get("detail")}
            if res.get("scan") and res["scan"].get("needs_repair") and not res["scan"].get("has_live_sl"):
                venue_stat["missing"] += 1
                report["critical"].append(item)
            elif res.get("ok"):
                if res.get("stage") == "placed":
                    if res.get("scan", {}).get("needs_renew"):
                        venue_stat["renewed"] += 1
                    else:
                        venue_stat["repaired"] += 1
                    report["actions"].append(item)
            else:
                venue_stat["errors"] += 1
                report["errors"].append(item)
        # 逐腿归属（只读）：回答"这些腿是给当前哪个仓的"。实测 Binance 13 张腿里
        # 只有 2 张对得上唯一活动仓，其余是历史遗留 ⇒ 必须让运营看得见
        # （旧量腿会**虚假满足**覆盖判定，且是 reduceOnly 有量条件单，日后可能减到新仓）。
        # ⚠️ 本段**只报告不撤销**：归属不可判定的腿可能是用户手单，撤错不可逆；
        # 真要清理必须由调用方显式发起，且只处理 `cleanup_candidates`。
        try:
            all_legs = ad.list_protective_orders(None) or []
            report["attribution"][venue] = attribute_protective_orders(
                rows, all_legs, ledger_rows,
                tolerance_ratio=DEFAULT_TOLERANCE_RATIO)
        except Exception as exc:
            report["errors"].append({"venue": venue, "stage": "attribution",
                                     "detail": f"{type(exc).__name__}: {str(exc)[:120]}"})
        report["venues"][venue] = venue_stat
    return report

def watchdog_gap_key(item: Dict[str, Any]) -> str:
    """缺口的**稳定身份**：`venue|inst|stage`。

    ⚠️ `detail` 是逐周期措辞（含"距到期 1.2 天"这类会变的数字），**不能进键** ——
    否则同一个缺口每周期都算"新缺口"，防抖永不成立（等于没防抖）。
    """
    return "|".join((str(item.get("venue") or "").lower(),
                     str(item.get("inst") or ""),
                     str(item.get("stage") or "")))


def watchdog_debounce_step(state: Optional[Dict[str, Any]],
                           report: Optional[Dict[str, Any]], *,
                           now_s: float, debounce_s: float):
    """跨周期防抖一步（**纯函数**：无 I/O、无时间读取 —— `now_s` 由调用方给）。

    观察项 = 审计层"本来会做"的动作（`would`）。真模式下 `actions` 与 `would` **同源**
    （同一套判定，只是 `dry_run` 决定写不写），故用 `would` 做观察不需要额外取数。

    - `state`：`{gap_key: first_seen_ts}`；
    - 新缺口：记 `now_s`；本周期**未再出现**的缺口：丢弃（缺口愈合 ⇒ 状态自清，不攒垃圾）；
    - 返回 `(new_state, observed_keys, qualified_keys)`：`qualified_keys` 只含
      "已持续 ≥ `debounce_s`" 的缺口 —— **只有它们**允许触发真实写单那一轮。

    方向纪律：`debounce_s <= 0` 视为**不防抖**（立即合格）—— 这是显式的运营选择，
    不是默认值（默认见 `DEFAULT_WATCHDOG_DEBOUNCE_S`）。
    """
    _state = dict(state or {})
    observed: List[str] = []
    for item in (report or {}).get("would") or []:
        if not isinstance(item, dict):
            continue
        key = watchdog_gap_key(item)
        if key in observed:
            continue
        observed.append(key)
        if key not in _state:
            _state[key] = float(now_s)
    _kept = {k: float(v) for k, v in _state.items() if k in observed}
    _qualify_s = 0.0 if debounce_s is None else max(0.0, float(debounce_s))
    qualified = sorted(k for k in observed
                       if float(now_s) - _kept.get(k, float(now_s)) >= _qualify_s)
    return _kept, observed, qualified

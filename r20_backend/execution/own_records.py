"""己仓对账：交易所实况 × 本方台账 holding 行 → 逐仓**归属判定**。

## 为什么需要它（真实证据，2026-09-20 实盘日志 + 台账，两条都已复现）

1. **UNI/binance**：交易所 `size_signed=-82(short)`，台账有 `holding` 行
   （`UNI/binance/空/82.0`）——**是我方持仓**，但开仓预检把它报成
   「交易所存在非本系统在管既有仓……**外部仓**连坐拒开」（日志 08:00–10:45 反复出现）。
   根因：`open_protected_position(own_position=…)` 的 `own_position` **从未被任何
   生产调用方传入**（全仓唯一生产调用点 `scripts/trader/order_submit.py` 不传），
   于是 `own_match` 恒 False。
2. **ARB/binance**：交易所持有 `-2416.7(short)`，而台账里**同尺寸同方向的该行
   `status=closed`**（`open_time 2026-09-20 11:33:29`）——**台账说已平、交易所仍持有**
   ⇒ 账实不符；系统对一笔在持敞口既是"盲"的（不会走持仓管理），又永久拒开（被当外部仓）。

## 四态判定（互斥，`verdict`）

| verdict | 含义 | 危险度 |
|---|---|---|
| `own` | 台账 `holding` 行与交易所实况**一致**（尺寸+方向） | 无（本方在管） |
| `stale_closed` | 交易所持有，但台账该合约**没有 holding 行**（只有 `closed` 行） | **高**：账实不符，敞口可能无人管理 |
| `mismatch` | 有该合约 `holding` 行，但尺寸/方向与实况不符 | 中：记录漂移 |
| `untracked` | 台账该合约**一行都没有** | 中：**归属不可判定** |
| `ledger_unavailable` | 台账读不到（缺失/损坏） | 中：**归属不可判定**（≠外部仓） |

## 铁律：`untracked` / `ledger_unavailable` **不是**"确认外部仓"

本方记录缺失与"真的是别人的仓"在台账上**无从区分**。故文案必须写「归属不可判定」，
绝不宣称「外部仓」——本仓把"说谎的诊断"当缺陷类（运维会照着假线索去找不存在的仓）。

本模块**纯计算 + 可注入 IO**：不读环境变量、不打印、不写任何文件。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "classify_exchange_position", "read_holding_rows", "read_ledger_rows",
    "load_ledger", "ledger_path", "canonical_inst", "OWN_VERDICTS",
]

#: 判定取值全集（含"不可判定"两态，调用方不得把它们折叠成"外部仓"）。
OWN_VERDICTS = ("own", "stale_closed", "mismatch", "untracked", "ledger_unavailable")

#: 台账方向词汇（与 `scripts/ledger/holdings.py::judge_position_side` 同源：多/空/未知）
_LEDGER_LONG, _LEDGER_SHORT = "多", "空"
#: 适配器归一后的方向词汇
_EX_LONG, _EX_SHORT = "long", "short"

#: 尺寸比对容差（张数）：合约是离散量，正常应逐位相等；给一点浮点余量。
_SIZE_TOL = 1e-6


def ledger_path(workspace_dir: Optional[str] = None) -> Path:
    """台账文件路径：显式传入优先，否则 ``<workspace>/data/trading_ledger.json``。

    路径解析刻意**不读环境变量**（测试与生产同一条路径规则）。
    """
    if workspace_dir:
        return Path(workspace_dir) / "data" / "trading_ledger.json"
    return Path(__file__).resolve().parents[2] / "data" / "trading_ledger.json"


def canonical_inst(raw: Any) -> str:
    """把各种写法归一到**币种基名**：`ARB` / `ARB-USDT-SWAP` / `ARBUSDT` → `ARB`。

    与 `canonical_base` 同口径但**刻意不依赖它**：本模块要在最底层可单测，
    不引入 `exchanges` 子包（避免"判定件依赖适配器"的倒挂）。
    """
    s = str(raw or "").strip().upper()
    if not s:
        return ""
    if ":" in s:                      # 第一百八十九刀：剥场所前缀（GATE:BTC_USDT → BTC_USDT）
        s = s.rsplit(":", 1)[1]       # 否则合成 id 会得到 "GATE:BTC"（与 canonical_base 不一致）
    for sep in ("-", "/", "_"):
        if sep in s:
            s = s.split(sep)[0]
    for quote in ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            s = s[: -len(quote)]
    return s


def _norm_venue(raw: Any) -> str:
    return str(raw or "").strip().lower()


def _norm_ex_side(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in ("long", "buy", "b", "多"):
        return _EX_LONG
    if s in ("short", "sell", "s", "空"):
        return _EX_SHORT
    return ""


def _norm_ledger_side(raw: Any) -> str:
    s = str(raw or "").strip()
    if s in (_LEDGER_LONG, "long", "buy"):
        return _EX_LONG
    if s in (_LEDGER_SHORT, "short", "sell"):
        return _EX_SHORT
    return ""


def _to_float(raw: Any) -> Optional[float]:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def read_ledger_rows(ledger: Any) -> List[Dict[str, Any]]:
    """台账 → 行列表。接受 `{id: row}` 映射或 `[row, ...]` 列表；其它形态 → `[]`。

    `{id: row}` 时把键补成 `id`（若行内没有），便于证据里回指原行。
    """
    if isinstance(ledger, list):
        return [r for r in ledger if isinstance(r, dict)]
    if isinstance(ledger, dict):
        # 台账顶层可能包一层（如 {"trades": {...}}）——按"值大多是 dict"启发式下钻一层
        for key in ("trades", "rows", "ledger"):
            inner = ledger.get(key)
            if isinstance(inner, (dict, list)) and inner:
                return read_ledger_rows(inner)
        out: List[Dict[str, Any]] = []
        for k, v in ledger.items():
            if not isinstance(v, dict):
                continue
            row = dict(v)
            row.setdefault("id", k)
            out.append(row)
        return out
    return []


def read_holding_rows(ledger: Any) -> List[Dict[str, Any]]:
    """仅 `status == "holding"` 的行（本方**在管**记录）。"""
    return [r for r in read_ledger_rows(ledger) if str(r.get("status") or "").strip().lower() == "holding"]


def load_ledger(path: Optional[Any] = None, *, workspace_dir: Optional[str] = None) -> Optional[Any]:
    """读台账；缺失/损坏 → `None`（调用方据此判 `ledger_unavailable`，不得当成"无仓"）。

    ⚠️ 「读不到」与「读到但没有该合约」是**两件事**：前者一律判不可判定。
    """
    p = Path(path) if path else ledger_path(workspace_dir)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _row_inst(row: Dict[str, Any]) -> str:
    return canonical_inst(row.get("inst") or row.get("symbol") or row.get("name"))


def _size_match(row_sz: Optional[float], ex_abs: float) -> bool:
    if row_sz is None:
        return False
    return abs(abs(row_sz) - ex_abs) <= max(_SIZE_TOL, ex_abs * 1e-9)


def _evidence(row: Dict[str, Any]) -> Dict[str, Any]:
    """只取最小必要字段（不整行外泄；台账行含费用/策略等与本判定无关的内容）。"""
    return {
        "id": row.get("id"),
        "inst": row.get("inst"),
        "venue": row.get("venue"),
        "side": row.get("side"),
        "sz": row.get("sz"),
        "status": row.get("status"),
        "open_time": row.get("open_time"),
    }


def classify_exchange_position(*, venue: Any, asset: Any, size_signed: Any,
                              side: Any = None, ledger: Any) -> Dict[str, Any]:
    """交易所某一笔**既有仓** → 归属判定（四态 + 台账不可用态）。

    参数
    ----
    venue / asset：场所与**币种基名**（`ARB` 即可，内部再归一）；
    size_signed：交易所带符号张数（正多负空）；
    side：适配器归一方向（`long`/`short`，可省略——省略时按 `size_signed` 符号推）；
    ledger：已读入的台账对象（`None` = 读不到 ⇒ `ledger_unavailable`）。

    返回 `{"verdict", "reason", "matched", "holding_rows", "closed_rows"}`；
    `matched` 为最小证据字段（无匹配则 `None`）。
    """
    v = _norm_venue(venue)
    a = canonical_inst(asset)
    sz = _to_float(size_signed)
    ex_side = _norm_ex_side(side) or (_EX_LONG if (sz or 0) > 0 else (_EX_SHORT if (sz or 0) < 0 else ""))
    ex_abs = abs(sz or 0.0)
    base = {"matched": None, "holding_rows": 0, "closed_rows": 0, "venue": v,
            "asset": a, "size_signed": sz, "side": ex_side or None}

    if ledger is None:
        return dict(base, verdict="ledger_unavailable",
                    reason="本方台账不可读（缺失/损坏）——归属**不可判定**，不得当成外部仓")

    rows = [r for r in read_ledger_rows(ledger)
            if _norm_venue(r.get("venue")) == v and _row_inst(r) == a]

    def _is_holding(r):
        return str(r.get("status") or "").strip().lower() == "holding"

    holdings = [r for r in rows if _is_holding(r)]
    closed = [r for r in rows if str(r.get("status") or "").strip().lower() == "closed"]
    base["holding_rows"], base["closed_rows"] = len(holdings), len(closed)

    # ① holding 行尺寸+方向一致 → 本方在管
    for r in holdings:
        if _size_match(_to_float(r.get("sz")), ex_abs) and _norm_ledger_side(r.get("side")) == ex_side:
            return dict(base, verdict="own", matched=_evidence(r),
                        reason=f"本方台账 holding 行一致（{r.get('side')} {r.get('sz')}）——本方在管仓位")

    # ② 有该合约 holding 行但尺寸/方向不符 → 记录漂移
    if holdings:
        r = holdings[-1]
        return dict(base, verdict="mismatch", matched=_evidence(r),
                    reason=(f"本方台账有该合约 holding 行但**与交易所不符**"
                            f"（台账 {r.get('side')} {r.get('sz')} vs 实况 {ex_side} {sz:g}）"))

    # ③ 交易所仍持有，而台账该合约只有 closed 行 → **账实不符**（危险态）
    if closed:
        exact = [r for r in closed
                 if _size_match(_to_float(r.get("sz")), ex_abs)
                 and _norm_ledger_side(r.get("side")) == ex_side]
        r = (exact or closed)[-1]
        extra = "（同尺寸同方向的那一行已标记为已平）" if exact else ""
        return dict(base, verdict="stale_closed", matched=_evidence(r),
                    reason=(f"账实不符：交易所持有 {ex_side} {sz:g}，而台账该合约**无 holding 行**、"
                            f"最近记录为 `{r.get('status')}` {r.get('side')} {r.get('sz')}{extra}"))

    # ④ 该合约一行都没有 → 归属不可判定（**不是**"确认外部仓"）
    return dict(base, verdict="untracked",
                reason="台账该合约无任何记录 ⇒ 归属**不可判定**（外部仓或本方记录缺失，二者无从区分）")

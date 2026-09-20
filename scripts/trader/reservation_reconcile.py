"""US-010 预留对账释放器（结构优化阶段 4·B3 第五十八刀）。

## 为什么抽这一簇，以及为什么是**这一小簇**

`scripts/ai_factor_trader.py` 是 2851 行的巨石，也是**唯一**一个"看起来拆不动"
的文件：`tests/risk_test_env.py::pin_baseline_risk_env()` 的重载名单只有
`risk_constants` / `ai_factor_trader` / `ai_brain_trader`
（**不含子模块**）—— 子模块若在 import 期绑定风控常量，reload 后就会读到旧值。

本刀先跑传递纯度扫描：该文件 33 个纯函数里，**唯一的"自洽且高内聚"簇**
就是预留对账这一组：

| 函数 | 行数 | 说明 |
|---|---|---|
| `utc_age_seconds` | 11 | SQLite UTC 时间戳 → 秒龄 |
| `reconcile_reservation_ledger` | 74 | 账实相符回笼陈旧占用 |

其余"纯函数"分散在场所/组合预算等不同关注点里，凑不成簇（凑一起只会
制造一个"什么都装"的杂物模块）。

## ⚠️ 依赖一律调用期注入（本文件在 reload 约束下必须如此）

`pin_baseline_risk_env()` 会 `importlib.reload(ai_factor_trader)`。子模块不在
重载名单里，故本模块**不 import 门面的任何东西**，四个依赖全部由门面在
**调用时**作为参数传入：

| 形参 | 门面在调用时传什么 | 为什么不能 import |
|---|---|---|
| `reservation_manager` | 门面的同名函数 | 测试用 `patch.object(trader, "reservation_manager", …)` 换掉它 |
| `fetch_other_venue_positions` | 门面的同名函数 | 同上（测试 `patch.object(trader, …)`，且绝不允许出网） |
| `state_closed` | `risk_reservation.STATE_CLOSED` | 值为 `"closed"`；传值而非传模块，减少耦合面 |
| `default_ttl_s` | 门面的 `RESERVATION_RECONCILE_TTL_S` | 配置面口径，留在门面以便调整 |

`tests/core/test_reservation_reconcile.py` 的两处 `patch.object` 是本模块能安全
外提的**前提** —— 若改成 import 期绑定，那 9 条用例会当场翻红。

## ⚠️ 语义红线（改这里前先读）

- `utc_age_seconds` 不可解析时返回 **`-inf`（不是 `+inf`）**：年龄未知要按
  "没到对账窗口"处理，**永不释放**。返回 `+inf` 会把脏时间戳当成超旧而
  **错杀活占用** —— 方向绝不能反。
- 活仓 / 仍在挂单 → **保留**（无论多旧）；只有"现货两清 **且** 超 TTL"才释放。
- 释放落到终态 `closed`（不删行）—— 历史可审计。
- 单条释放失败不影响其余（下周期重试，幂等 UNIQUE 键）。
"""

from __future__ import annotations

import datetime
import time
from typing import Any, Callable, Dict, Optional, Set


def utc_age_seconds(ts_str: str, now_utc: float) -> float:
    """SQLite CURRENT_TIMESTAMP（UTC 'YYYY-MM-DD HH:MM:SS'）→ 秒龄。

    不可解析 = -inf（保守：年龄未知按「没到对账窗口」处理，**永不释放**；
    返回 +inf 会把脏时间戳当成超旧而错杀活占用——方向绝不能反）。
    """
    try:
        dt = datetime.datetime.strptime(str(ts_str).strip()[:19], "%Y-%m-%d %H:%M:%S")
        return max(0.0, now_utc - dt.replace(tzinfo=datetime.timezone.utc).timestamp())
    except (TypeError, ValueError):
        return float("-inf")


def reconcile_reservation_ledger(
    real_pos_dict: Dict[str, Any],
    pending_inst_ids: Set[str],
    environment: str,
    *,
    reservation_manager: Callable[[], Any],
    fetch_other_venue_positions: Callable[[str], Any],
    state_closed: str,
    default_ttl_s: float,
    ttl_s: Optional[float] = None,
    venue_snapshot: Optional[Dict[str, list]] = None,
    venue_snapshot_verified: bool = True,
    #: 语义：**跨所实况（持仓 + 挂单枚举）本周期是否都核验成功**。
    #: 调用方（`cycle_stages.fetch_positions_and_reconcile`）传
    #: `xv_ok and not _pending_enum_errors` —— 两者任一失败即为假。
) -> int:
    """周期级预留对账（US-010）：账实相符原则回笼陈旧占用。

    背景：confirm 只翻状态、平仓/撤单/凭证代际轮换都无释放路径——预留台账
    单向累积，面板「已预留」虚高；一旦启用组合预算封顶，陈旧 pending 会挤占
    真实额度把合法开仓挡死。recovery() 的纪律是孤儿「标记不清算」，本函数
    就是那个「对账确认后的显式释放」：

    - 意图标的在当前真实持仓（同所同环境）或仍在挂 → **保留**（无论多旧）。
      挂单判据**跨所**：`pending_inst_ids` 混装 OKX/币安/Gate 三种拼写，
      按**基名**归一后匹配（币安 `XRPUSDT`、Gate `DOGE_USDT` 与 OKX
      `XRP-USDT-SWAP` 等价）；不要求方向一致——保留是保守方向，释放不可逆；
    - 现货两清（无仓无挂）且 updated_at 超 TTL → release(state=closed) 回笼；
    - 时间戳不可解析 / 环境不匹配 / account_key 异常 → 保守保留；
    - **跨所实况未核验**（`venue_snapshot_verified=False`，或自取失败）→
      **本周期一笔都不释放**：把"读不到"当"没有仓"会误释放**活仓**的预留
      （第一百二十六刀实测：binance 726U 活仓预留被释放）；⚠️ 该标志覆盖
      **持仓与挂单两侧**（第一百二十七刀）：只核验持仓时，一笔**未成交**的入场单
      （尚无持仓）仍会被判"无仓无挂"而误释放；
    - 单条释放失败不影响其余（下周期重试，幂等 UNIQUE 键）。

    返回释放条数。调用方必须传**本周期刚核验过的**持仓/挂单实况（fail-closed
    路径不会到这里），杜绝拿陈旧视图误释放活仓预算。

    ⚠️ 四个依赖由调用方注入，原因见模块 docstring（子模块不在
    `pin_baseline_risk_env()` 的重载名单里）。
    """
    ttl = default_ttl_s if ttl_s is None else float(ttl_s)
    now_utc = time.time()
    # ⚠️ 第一百二十六刀：**跨所实况未核验 ⇒ 本周期一律不释放任何预留**。
    # 原实现把"读不到"当成"没有仓"：`fetch_other_venue_positions` 失败时返回
    # `(False, {}, err)`，而调用点（`cycle_stages`）把那个**空字典**原样透传进来，
    # 本函数便据 `{}` 判定"外所无仓无挂" ⇒ 把**活仓的外所预留**按超 TTL 释放成
    # `closed`（实测：binance 一笔 726U 的活仓预留被释放，日志还打印"无仓无挂"
    # ——假陈述）。本模块 docstring 的方向纪律摆在这儿：
    # 「保留是保守的（多占只压缩额度），释放是不可逆的」⇒ 未知必须保留。
    if not venue_snapshot_verified:
        print("[预留对账] warn 跨所实况未核验——本周期不释放任何预留"
              "（释放不可逆，宁可慢一轮；下周期核验通过再回笼）")
        return 0
    try:
        mgr = reservation_manager()
        rows = mgr.list_unreleased(environment)
    except Exception as exc:
        print(f"[预留对账] warn 台账不可读，本周期跳过（不强行释放）: {exc}")
        return 0
    # 同所同环境的真实持仓索引：venue → {base: posSide}（OKX 持仓字典是 instId→p）
    live_by_venue: Dict[str, set] = {}
    for inst_id, p in (real_pos_dict or {}).items():
        v = "okx"  # 主循环持仓字典当前仅 OKX 直签链
        base = str(inst_id).split("-")[0].upper()
        side = str(p.get("posSide", "net")).lower()
        live_by_venue.setdefault(v, set()).add(f"{base}:{side}")
    # 跨所封顶快照（gate/binance）——有仓则对应意图必须保留（复用主循环已读结果，零重复出网）
    if venue_snapshot is None:
        try:
            _xv_ok, venue_snapshot, _xv_err = fetch_other_venue_positions(environment)
        except Exception as _xv_exc:
            _xv_ok, venue_snapshot, _xv_err = False, {}, str(_xv_exc)
        if not _xv_ok:
            # 读失败 ≠ 没有仓：与上面同一条纪律（此前这里静默 `venue_snapshot = {}`，
            # 于是"未知"被当成"两清"）。返回 0 而非继续释放。
            print(f"[预留对账] warn 跨所实况自取失败（{_xv_err or '未知原因'}）"
                  "——本周期不释放任何预留")
            return 0
    for v, _rows in (venue_snapshot or {}).items():
        for _p in _rows:
            base = str(_p.get("base") or str(_p.get("inst_id", "")).split("_")[0]).upper()
            live_by_venue.setdefault(v, set()).add(f"{base}:{_p.get('side', 'net')}")
    pending = {str(x) for x in (pending_inst_ids or set())}
    # 第一百一十五刀：挂单基名集合（**按基名归一**）——见下面 `still_live` 的说明。
    # ⚠️ 校正（第一百一十六刀，我上一刀的说法有误）：生产侧 `pending_inst_ids` 由
    # `cycle_snapshot.collect_pending_inst_ids` **统一归一成 OKX 拼写**
    # （`f"{base}-USDT-SWAP"`，见该函数末段），并不混装原生拼写
    # —— 真正的缺陷只是下面那条 `venue == "okx"` 把外所排除了。
    # 这里仍按基名归一，是**防御性**的（`pending_inst_ids` 是注入集合，测试或未来
    # 调用方可能给原生拼写），且基名匹配对非 USDT 报价合约更稳（见 `QUOTE_ASSUMPTION`）。
    pending_bases = set()
    for _p_inst in pending:
        _p_base = str(_p_inst).split("-")[0].split("_")[0].upper()
        for _p_quote in ("USDT", "USDC", "USD"):
            if _p_base.endswith(_p_quote) and len(_p_base) > len(_p_quote):
                _p_base = _p_base[: -len(_p_quote)]
        if _p_base:
            pending_bases.add(_p_base)
    released_n = 0
    for row in rows:
        try:
            intent = str(row.get("intent_id") or "")
            parts = intent.split(":")
            inst_id = parts[0] if parts else ""
            pos_side = ("long" if "LONG" in intent.upper()
                        else "short" if "SHORT" in intent.upper() else "net")
            base = inst_id.split("-")[0].upper()
            venue = str(row.get("venue") or "").lower()
            # ⚠️ 挂单保留判据必须**跨所**（第一百一十五刀修）：原判据
            # `venue == "okx" and inst_id in pending` 把外所整体排除在"有挂单则保留"
            # 之外。后果：派往 gate/binance 的**未成交挂单**，其预留一过 TTL(2h) 就被
            # 释放——而单还挂在场内，成交后这笔占用已经不在台账上（预算/敞口少算）。
            # 方向纪律：**保留是保守的**（多占只压缩可用额度），释放是不可逆的
            # （活单失去登记）⇒ 按基名匹配、不要求方向一致（宁多留不漏放）。
            still_live = (f"{base}:{pos_side}" in live_by_venue.get(venue, set())
                          or (venue == "okx" and inst_id in pending)
                          or base in pending_bases)
            if still_live:
                continue
            if utc_age_seconds(row.get("updated_at"), now_utc) < ttl:
                continue  # 新周期意图（本周期刚预留/成交在途）：未到对账窗口
            mgr.release(str(row.get("account_key") or ""), intent,
                        state=state_closed)
            released_n += 1
            print(f"[预留对账] 释放 {intent}（{venue}/{environment} 无仓无挂 且 "
                  f"age>={ttl:.0f}s，state=closed 回笼 {row.get('amount_usdt')}U）")
        except Exception as exc:
            print(f"[预留对账] warn 单条释放失败（下周期重试）{row.get('intent_id')}: {exc}")
    if released_n:
        print(f"[预留对账] 本周期回笼 {released_n} 笔陈旧占用")
    return released_n

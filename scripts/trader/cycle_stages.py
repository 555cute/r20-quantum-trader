"""execute_portfolio 的阶段函数（B3 抽取·第九十一刀）。

从 `scripts/ai_factor_trader.py::execute_portfolio`（294 行）中
**纯搬家**两段：

| 函数 | 段长 | 原相位 |
|---|---|---|
| `fetch_universe_and_manage_positions` | 14 行 | 相位 2-3：并发取标的池因子 + 逐仓追踪止损退出 |
| `persist_state_and_sync_ledger` | 30 行 | 相位 5-6：面板状态持久化 + 生命周期台账/SQLite 实时同步 |
| `scan_risk_gates_and_ai_brain` | 50 行 | 相位 4 前段：熔断判定 + 单标的保证金上限自适应 + 主脑批量扫描（LLM 批次 + 持仓全景装配 + AI 健康告警）+ 池可信闸（**无 return；4 项输出**） |
| `fetch_positions_and_reconcile` | 115 行 | 相位 1：取真实持仓 + 合约对账 + 跨所汇总 + 挂单盲区守卫 + 预留对账（**4 处 `return None` = 本周期中止**；13 项输出） |
| `preflight_reconcile_and_housekeeping` | 26 行 | 相位 0/0a：引擎就绪闸 + 挂单对账 + 陈旧单回收 + 舆情采集（**段内 `return None` = 本周期中止**） |

## 准入判据（沿用第九十刀定式）

两段均 **0 个 `return`、0 个 `break`**；段内写入的外围量要么作为返回值回传，
要么无人再读（`persist_state_and_sync_ledger` 无输出 ⇒ 纯副作用段）。
所有自由名（外围局部量 + 门面全局）一律**同名 kw-only 入参** ⇒ 段体 **AST 逐字**。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple


def fetch_universe_and_manage_positions(*,
        all_positions,
        real_pos_dict,
        timestamp_full,
        usdt_available,
        TARGET_INSTRUMENTS,
        ThreadPoolExecutor,
        fetch_single_instrument_data,
        load_trackers,
        manage_position_tp_and_trailing,
        prune_trackers,
        save_trackers):
    with ThreadPoolExecutor(max_workers=len(TARGET_INSTRUMENTS)) as executor:
        all_factors = list(executor.map(lambda item: fetch_single_instrument_data(item, all_positions, usdt_available), TARGET_INSTRUMENTS))

    # 3. Process Positions & Dynamic Trailing Exits
    executed_actions = []
    trackers = load_trackers()
    stale_tracker_count = prune_trackers(trackers, real_pos_dict)
    if stale_tracker_count:
        executed_actions.append(f"清理 {stale_tracker_count} 条已失效持仓追踪记录")
    for f in all_factors:
        curr_pos = f["position"]
        if curr_pos:
            manage_position_tp_and_trailing(f, curr_pos, trackers, timestamp_full, executed_actions)
    save_trackers(trackers)
    # ⚠️ 不返回 `f`：原文段后对 `f` 的那次读（`f.write(log_entry)`）是
    # `with open(LOG_FILE) as f` **自己绑定的文件句柄**，与标的字典无关
    # （首版误判为外围输出，冒烟例当场抓出 UnboundLocalError）。
    return (all_factors, executed_actions, trackers)


def persist_state_and_sync_ledger(*,
        _xv_total,
        active_pos_count,
        all_factors,
        cb_active,
        cb_reason,
        executed_actions,
        long_count,
        short_count,
        timestamp_full,
        DATA_DIR,
        LEDGER_AUTOSYNC_ENABLED,
        LOG_FILE,
        MAX_CONCURRENT_POSITIONS,
        WORKSPACE_DIR,
        __version__,
        _atomic_write_json,
        _run_captured,
        build_state_payload,
        evaluate_asset_signal,
        os):
    state_payload = build_state_payload(
        timestamp_full=timestamp_full, active_pos_count=active_pos_count,
        max_positions=MAX_CONCURRENT_POSITIONS, long_count=long_count,
        short_count=short_count, cb_active=cb_active, cb_reason=cb_reason,
        executed_actions=executed_actions, all_factors=all_factors,
        evaluate_asset_signal=evaluate_asset_signal)

    # 审计③：原子替换（读者=面板/巡检；旧直写有撕裂窗）。异常语义不变：照旧上抛。
    _atomic_write_json(os.path.join(DATA_DIR, "trading_state.json"), state_payload)

    # 6. Always Sync Full Lifecycle Ledger and SQLite DB in Realtime
    # 批E(2026-09-13)·测试封闭闸：这两条 spawn 会打三所接口并**重写生产台账/数据库**。
    # 测试若在进程内跑一轮交易员巡检（多处如此），就会连带改写 data/trading_ledger.json
    # 与 SQLite——违反「测试不触生产文件」。tests/__init__.py 在任何测试模块导入前置位
    # R20_LEDGER_SYNC_DISABLED=1，下面的模块级快照即 False；生产不设 → 行为不变。
    if LEDGER_AUTOSYNC_ENABLED:
        try:
            sync_script = os.path.join(WORKSPACE_DIR, "scripts", "sync_full_ledger.py")
            if os.path.exists(sync_script):
                _run_captured(sync_script)
            db_script = os.path.join(WORKSPACE_DIR, "scripts", "db_manager.py")
            if os.path.exists(db_script):
                _run_captured(db_script)
        except Exception as e:
            print(f"[Ledger Sync Warning] {e}")

    log_entry = f"[{timestamp_full}] ⚡ R20 Quantum Trader v{__version__} 巡检完成 | 持仓 OKX {active_pos_count}/{MAX_CONCURRENT_POSITIONS} (多{long_count}/空{short_count})｜跨所 {_xv_total if _xv_total is not None else '未知'} 笔 | 动作: {', '.join(executed_actions) if executed_actions else '无开平仓操作'}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)
    print(log_entry.strip())


def preflight_reconcile_and_housekeeping(*,
        WORKSPACE_DIR,
        _run_captured,
        clean_stale_open_orders,
        current_environment,
        datetime,
        load_trackers,
        os,
        reconcile_pending_orders):
    """相位 0/0a：引擎就绪闸 + 挂单对账 + 陈旧单回收 + 舆情采集。

    段内 `return None` 语义 = **本周期中止** ⇒ 调用点据此提前 `return None`。
    """
    _engine_env = current_environment()
    if not _engine_env.configured:
        print(f"[Engine NOT READY] OKX {_engine_env.mode.upper()} API Key 未配置：V5 直签是唯一交易通道，本周期拒绝交易。")
        return None
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_dt = datetime.datetime.now(tz_bj)
    timestamp_full = now_dt.strftime("%Y-%m-%d %H:%M:%S")

    # 0a. US-006 周期级挂单对账：重启/新周期接管或撤销存量挂单（在新增下单之前）
    reconcile_ok, reconciled_kept_ord_ids = reconcile_pending_orders(trackers=load_trackers())
    if not reconcile_ok:
        # fail-closed：仅禁止本周期新增下单（不是清库），持仓管理照常执行
        print("[挂单对账] fail-closed：本周期禁止新增下单（对账失败，不清库）")
    entries_blocked = not reconcile_ok

    # 0. Clean Stale Open Orders & Harvest Real-time News Sentiment
    orders_ok, orders_error = clean_stale_open_orders(keep_ord_ids=reconciled_kept_ord_ids)
    if not orders_ok:
        print(f"[Trader] Abort: unable to verify/cancel stale open orders: {orders_error}")
        return None
    try:
        harvester_script = os.path.join(WORKSPACE_DIR, "scripts", "news_sentiment_harvester.py")
        if os.path.exists(harvester_script):
            _run_captured(harvester_script, label="news_harvester", timeout=25)
    except Exception as e:
        print(f"News Harvester sync warning: {e}")
    return (entries_blocked, timestamp_full)


def fetch_positions_and_reconcile(*,
        entries_blocked,
        _BROKEN_VENUES,
        collect_pending_inst_ids,
        current_environment,
        fetch_other_venue_positions,
        load_instruments,
        okx_rest,
        query_positions,
        reconcile_reservation_ledger,
        venue_execution_ready,
        broken_execution_venues,
        venue_registry):
    """相位 1：取真实持仓 + 合约对账 + 跨所汇总 + 挂单盲区守卫 + 预留对账。

    段内 4 处 `return None` 语义 = **本周期中止**（调用点判 None 后 `return None`）。
    `entries_blocked` 是 **in-out**：段内只在"跨所读取失败"分支里被置 True，
    其余路径不碰它 —— 若不把上游（preflight）的值传进来，未命中分支时它会
    **未绑定**（首版即如此，空世界 smoke 当场抓出 `UnboundLocalError`）。
    其余 12 项输出在本段内均"必然绑定"（确定赋值分析），无需入参。

    ⚠️ **输入失败语义表**（第一百二十八刀逐项实测，改动前先读）：

    | 输入 | 读失败时的行为 | 决策方是否被告知 |
    |---|---|---|
    | OKX 持仓 `query_positions` | **整周期 abort**（`return None`） | 日志 |
    | OKX 挂单 `pending_orders` | **整周期 abort** | 日志 |
    | OKX 余额 `balances` | **整周期 abort** | 日志 |
    | 清理存量挂单 `clean_stale_open_orders` | **整周期 abort** | 日志 |
    | OKX 挂单对账（preflight `reconcile_pending_orders`） | `entries_blocked=True`（禁新开仓） | 日志 |
    | 跨所**持仓** `fetch_other_venue_positions` | `entries_blocked=True` + 预留对账**不释放** + 提示词写"未知(拉取失败)" | 日志/提示词 |
    | 跨所**挂单** `collect_pending_inst_ids` | 预留对账**不释放**（第一百二十七刀）；⚠️ **不拦新开仓**，且槽位/同向占用**少算** | 仅 warn |
    | 凭证已死场所 `broken_venues` | 跳过该所枚举（不报错、不拦）；其持仓/挂单**不进配额与敞口** | 收侧 CRITICAL + **每周期"未计入"告警**（第一百三十一刀）|

    ⚠️ **凭证已死场所**（执行闸开着但密钥失效）另有一条边界：该所**读不出来**（不是没有仓），
    其仓位/挂单不进配额与敞口；现状是"跳过 + 每周期明确告知未计入"，
    而非 fail-closed 拦新开仓 —— 后者与既有审计#4教训（"拿凭证错误拦全链=交易停摆"）冲突，
    故列为**待人工拍板**（面板/提示词的跨所笔数仍不含该所）。

    唯一**残留缺口**是"跨所挂单枚举失败不拦新开仓"：计数少算是**仓位数口径**
    （槽位/同向上限），不涉及 USDT 预算（预算由预留台账与敞口闸把关）。
    改 fail-closed 是**实盘行为变更**（可能因某所一次读失败而停一轮新开仓，
    但也可能因某所**每轮**都失败的良性异常而长期静默停止开仓）⇒ 已列入待人工拍板。
    """
    positions_ok, all_positions, positions_error = query_positions()
    if not positions_ok:
        print(f"[Trader] Abort: unable to verify exchange positions: {positions_error}")
        return None
    real_pos_dict = {}
    real_long_count = 0
    real_short_count = 0

    if isinstance(all_positions, list):
        for p in all_positions:
            pos_sz = float(p.get("pos", 0) or 0)
            if pos_sz > 0:
                side = p.get("posSide", "net").lower()
                inst_id = p.get("instId")
                if inst_id in real_pos_dict:
                    print(f"[Trader] Abort: simultaneous long/short positions for {inst_id} are not supported")
                    return None
                real_pos_dict[inst_id] = p
                if "long" in side:
                    real_long_count += 1
                elif "short" in side:
                    real_short_count += 1

    active_pos_count = len(real_pos_dict)
    long_count = real_long_count
    short_count = real_short_count

    try:
        pending_orders = okx_rest.pending_orders()
    except Exception as pending_exc:
        print(f"[Trader] Abort: unable to verify pending orders: {pending_exc}")
        return None
    pending_inst_ids = set()
    pending_long_count = 0
    pending_short_count = 0
    if isinstance(pending_orders, list):
        for order in pending_orders:
            if str(order.get("state", "live")).lower() not in {"live", "partially_filled"}:
                continue
            inst_id = str(order.get("instId", ""))
            if inst_id:
                pending_inst_ids.add(inst_id)
            pos_side = str(order.get("posSide", "")).lower()
            if pos_side == "long":
                pending_long_count += 1
            elif pos_side == "short":
                pending_short_count += 1
    # 审计(2026-09-13)·外所挂单盲区修复：本守卫此前只数 OKX 在途单，路由派往
    # binance/gate 的单对周期不可见 → 同信号逐轮在外所重复挂单（实锤：binance
    # demo BTC/SUI 各成对）。执行闸开的场并入同一把尺；凭证已死的场收侧已吼
    # CRITICAL 且 router 同样发不出单，此处静默跳过不重复报警。
    _auth_markers = ("INVALID_KEY", "Invalid key", "Invalid API-key", "-2015", "50111",
                     "signature", "Signature", "not exist", "invalid timestamp")
    try:
        _gv_mode = str(current_environment().mode or "")
    except Exception:
        _gv_mode = ""
    # ⚠️ 第一百二十七刀：**记录**挂单枚举的失败（仍然照原样打印，输出不变）。
    # 该失败此前只 warn 就丢，而 `xv_ok` 只覆盖**持仓**读取 —— 于是存在这样一个组合：
    # 某所持仓读成功（`xv_ok=True`）但**挂单读失败**，若该所恰好有一笔**未成交**的
    # 入场单（还没有持仓），对账器的"无仓无挂"判据就会成立并把它的预留按超 TTL 释放
    # （释放不可逆 ⇒ 预算台账少算在场活单）。持仓侧已由 `venue_snapshot_verified`
    # 把关；这里把**挂单侧**一并纳入同一个"实况是否核验"标志。
    # ⚠️ 第一百三十一刀：**凭证已死场所必须每周期明说"未计入"**。
    # 此前只有回收侧一次性 CRITICAL，之后本函数静默 `continue` —— 而
    # `venue_execution_ready` 见 `_BROKEN_VENUES` 即否决，`fetch_other_venue_positions`
    # 也随之跳过该所（返回 ok=True 且**无错误**）⇒ 该所的持仓/挂单**不进**配额与敞口，
    # 且跨所笔数看起来"完整"。口径：凭证死的所**读不出来**（不是没有仓），
    # 所以这里如实登记"未计入"，绝不假装干净。
    # 判据精确到"执行闸开着（本该能交易）却不可就绪" ⇒ 只可能是凭证已死：
    # registry 未登记/闸没开属于"结构性无该所"，不是本告警的范围。
    try:
        _xv_broken = list(broken_execution_venues(
            ("gate", "binance"), _gv_mode, venue_registry=venue_registry,
            venue_execution_ready=venue_execution_ready))
    except Exception as _bv_exc:
        _xv_broken = []
        print(f"[跨所封顶] warn 坏所探测异常（不影响本周期）: {_bv_exc}")
    if _xv_broken:
        print(f"[跨所封顶] warn {'/'.join(_xv_broken)} 凭证已死（执行闸开着却不可就绪）——"
              "该所持仓/挂单**未计入**本周期配额与敞口（跨所笔数不含该所），"
              "修好密钥后自动恢复；请勿据面板跨所笔数当作全景")

    _pending_enum_errors: list = []

    def _pending_warn(_msg):
        _pending_enum_errors.append(_msg)
        print(_msg)

    # ⚠️ 第二百二十刀（**回归修复**，勿改回整体赋值）：上面刚从 OKX 挂单数出的
    # `pending_inst_ids`/`pending_long_count`/`pending_short_count` 是**基准值**，
    # 外所枚举（bb6cb57 抽出的 `collect_pending_inst_ids`）当年是**并进**它们；
    # 抽取时写成了整体赋值 ⇒ OKX 在途挂单被**静默丢弃**，后果两条：
    #   ① `reserved_slot_count`/同向计数少算 OKX 在途单 ⇒ 开仓闸可能**超发槽位**；
    #   ② `reconcile_reservation_ledger` 拿到的集合里没有 OKX 在场活单 ⇒
    #      对账器据「无仓无挂」把它当陈旧占用**释放**（释放不可逆，正是该函数
    #      docstring 点名的第一类错误）。
    # 恢复为**并集**：两处枚举各管一段（OKX 走本地 loop、外所走适配器），谁都不是对方的替代。
    _xv_pending_ids, _xv_pending_long, _xv_pending_short = \
        collect_pending_inst_ids(
            venues=("gate", "binance"), venue_mode=_gv_mode,
            broken_venues=_BROKEN_VENUES, venue_registry=venue_registry,
            load_instruments=load_instruments, auth_markers=_auth_markers,
            warn=_pending_warn)
    pending_inst_ids |= {str(_x) for _x in (_xv_pending_ids or set()) if _x}
    pending_long_count += int(_xv_pending_long or 0)
    pending_short_count += int(_xv_pending_short or 0)
    reserved_slot_count = active_pos_count + len(pending_inst_ids)
    reserved_long_count = long_count + pending_long_count
    reserved_short_count = short_count + pending_short_count
    # ⚠️ 第一百二十八刀（**已知残留缺口**，等策略拍板）：外所挂单枚举失败时，
    # 上面三个计数**少算**该所的在场单，而执行层的开仓闸用的正是它们
    # （`reserved_slot_count < MAX_CONCURRENT_POSITIONS` 与同向上限）⇒ 本周期可能
    # **超发槽位**。位置读取失败会 `entries_blocked=True`（禁新开仓），挂单侧目前**不拦**。
    # 口径边界：只影响**仓位数**（槽位/同向），**不涉及 USDT 预算** —— 预算由预留台账
    # 与敞口闸另行把关。本行只做**如实告知**（零行为变更）；是否改 fail-closed 见
    # `fetch_positions_and_reconcile` docstring 的"输入失败语义表"。
    if _pending_enum_errors:
        print(f"[跨所封顶] warn 外所挂单未枚举成功（{len(_pending_enum_errors)} 所）——"
              f"本周期槽位/同向占用**少算**该所在场单（{reserved_slot_count} 为下限），"
              "若照常放行新开仓可能突破仓位上限（仅仓位数口径；USDT 预算不受影响）")

    # 1a. 跨所封顶（三所平权开单后的风控收口）：开闸所（gate/binance）的
    # 活跃持仓计入总仓/同向配额；读取失败 → 本周期禁止新增开仓（fail-closed，
    # 与挂单对账同一把尺——宁停不错）。孤儿仓只计数不处置（可能是用户手动仓）。
    try:
        _xv_env = str(current_environment().mode)
    except Exception as _xv_exc:
        _xv_env = ""
        print(f"[跨所封顶] warn 周期冻结环境不可得（{_xv_exc}），按不可信环境处理")
    xv_ok, xv_positions_by_venue, xv_error = fetch_other_venue_positions(_xv_env)
    # 巡检文案口径（审计 D 级）：持仓数历来只报 OKX，跨所持仓仅在封顶逻辑里
    # 出现——面板/日志读起来「0/8」像全空，实际外所可能有数笔。此处统一算出
    # 跨所笔数供 AI 提示词与巡检日志；拉取失败显式标「未知」，绝不装 0。
    _xv_total = sum(len(v or []) for v in (xv_positions_by_venue or {}).values()) if xv_ok else None
    xv_enabled = bool(_xv_env) and any(venue_execution_ready(v, _xv_env)
                                       for v in ("gate", "binance"))
    if (xv_enabled or not _xv_env) and not xv_ok:
        print(f"[跨所封顶] fail-closed 本周期禁止新增开仓: {xv_error or '环境轴不可得'}")
        entries_blocked = True
    else:
        for _v, _rows in (xv_positions_by_venue or {}).items():
            for _p in _rows:
                print(f"[跨所封顶] {_v} {_p.get('inst_id')} {_p.get('side')} "
                      f"size={_p.get('size_signed')} 纳入本周期仓位配额（只计数不处置）")
                reserved_slot_count += 1
                if str(_p.get("side", "")).lower() == "long":
                    reserved_long_count += 1
                else:
                    reserved_short_count += 1

    # 1b. US-010 预留对账：基于本周期刚核验的持仓/挂单实况回笼陈旧占用
    #     （活仓/在途挂单一律保留；无仓无挂且超 TTL 才 closed——宁慢不错杀）。
    try:
        reconcile_reservation_ledger(real_pos_dict, pending_inst_ids, _xv_env,
                                     venue_snapshot=xv_positions_by_venue,
                                     # ⚠️ 第一百二十六/二十七刀：把"这次跨所**实况**到底
                                     # 核验成功没有"一并交给对账器 —— 持仓（`xv_ok`）与
                                     # 挂单枚举（`_pending_enum_errors`）**都要**成功。
                                     # 此前只传快照 ⇒ 读取失败时传进去的是**空字典**，
                                     # 对账器据它判"外所无仓无挂"并误释放活仓/在场活单的预留。
                                     venue_snapshot_verified=(xv_ok and not _pending_enum_errors))
    except Exception as _rc_exc:
        print(f"[预留对账] warn 对账器异常（不影响本周期交易）: {_rc_exc}")

    try:
        bal_res = okx_rest.balances()
    except Exception as bal_exc:
        print(f"[Trader] Abort: unable to verify account balance: {bal_exc}")
        return None
    usdt_available = 0.0
    if bal_res:
        for d in bal_res[0].get("details", []):
            if d.get("ccy") == "USDT":
                usdt_available = float(d.get("availBal", 0.0))
                break
    return (_xv_total, active_pos_count, all_positions, entries_blocked, long_count, pending_inst_ids, real_pos_dict, reserved_long_count, reserved_short_count, reserved_slot_count, short_count, usdt_available, xv_positions_by_venue)


def scan_risk_gates_and_ai_brain(*,
        _xv_total,
        active_pos_count,
        all_factors,
        executed_actions,
        long_count,
        short_count,
        timestamp_full,
        trackers,
        usdt_available,
        xv_positions_by_venue,
        MAX_CONCURRENT_POSITIONS,
        _collect_okx_position_payloads,
        _merge_cross_venue_positions,
        effective_single_asset_margin,
        execute_ai_position_management,
        execute_batch_ai_brain_cycle,
        is_circuit_breaker_active,
        pool_is_trustworthy,
        pool_state,
        query_positions,
        read_cycle_health,
        save_trackers):
    """相位 4 前段：熔断判定 + 单标的保证金上限自适应 + 主脑批量扫描 + 池可信闸。

    段内不动控制流（无 return/break）：`executed_actions` 由**原地 append** 回传
    （故只入参、不返回）；`brain_cache` 在段内顶层初始化为 `{}` ⇒ 必然绑定。
    4 项输出见调用点解包。
    """
    cb_active, cb_reason = is_circuit_breaker_active(usdt_available)
    # 单标的累计保证金上限按可用余额自适应，与提示词 {{risk_budget}} 同口径
    ASSET_MARGIN_CAP = effective_single_asset_margin(usdt_available)

    brain_cache = {}
    # One LLM call covers the full six-instrument universe and all active positions.
    if not cb_active and execute_batch_ai_brain_cycle:
        try:
            pos_desc = f"当前系统总持仓 OKX {active_pos_count}/{MAX_CONCURRENT_POSITIONS} (多{long_count}/空{short_count})｜跨所持仓 {_xv_total if _xv_total is not None else '未知(拉取失败)'} 笔"
            # 持仓全景装配（阶段 4·B3 第三十一刀：迁至 scripts/trader/position_universe.py）
            active_pos_list = _collect_okx_position_payloads(all_factors, trackers)
            # 汇入多所（Binance / Gate）在管持仓，形成三所平权持仓全景。
            # 审计(2026-09-13)：必须复用 1a 已冻结的周期快照（零重复出网）。
            _merge_cross_venue_positions(active_pos_list, xv_positions_by_venue, all_factors)
            brain_cache = execute_batch_ai_brain_cycle(pos_desc, active_pos_list, usdt_available=usdt_available) or {}
            if brain_cache:
                refreshed_ok, refreshed_positions, refreshed_error = query_positions()
                if not refreshed_ok:
                    executed_actions.append(f"AI持仓管理跳过：无法刷新真实仓位 ({refreshed_error})")
                else:
                    refreshed_pos_dict = {
                        p.get("instId"): p for p in refreshed_positions
                        if float(p.get("pos", 0) or 0) > 0
                    }
                    execute_ai_position_management(refreshed_pos_dict, trackers, timestamp_full, executed_actions)
                    save_trackers(trackers)
            else:
                _hf = read_cycle_health() if read_cycle_health else {}
                if _hf.get("last_status") == "failed":
                    _cf = int(_hf.get("consecutive_failures", 0) or 0)
                    _warn = f"本轮AI推理失败（连续{_cf}轮｜{_hf.get('last_error') or '未知原因'}），禁止复用旧持仓指令"
                    if _cf >= 3:
                        _warn = "🔴 AI决策链连续" + str(_cf) + "轮失败——非并发跳过，模型/密钥/额度需人工核查！" + _warn
                    executed_actions.append(_warn)
                    if _cf >= 3:
                        print(f"[AI Health] 🔴 连续 {_cf} 轮批次决策失败，最近错误: {_hf.get('last_error')}")
                else:
                    executed_actions.append("本轮AI推理并发跳过（旧指令不违规复用），禁止复用旧持仓指令")
        except Exception as e:
            print(f"[AI Brain Batch Scan Warning] {e}")

    # 审计 P2-11：标的池不可信（文件损坏/为空/条目非法）时，旧实现会拿 10 币出厂默认
    # 清单继续开新仓 —— 管理员删掉的标的会因"文件坏了"重新被交易。这里 fail-closed：
    # 只保留持仓风控接管（止损/移动止损/AI 平仓在上面的分支已跑完），不开新仓。
    if not cb_active and not pool_is_trustworthy():
        _ps = pool_state()
        _pool_warn = (f"⛔ 标的池不可信（{_ps.get('status')}: {_ps.get('detail')}）"
                      f"——本轮只做持仓风控接管，禁止开新仓")
        print(f"[交易池闸门] {_pool_warn}")
        executed_actions.append(_pool_warn)
    return (ASSET_MARGIN_CAP, brain_cache, cb_active, cb_reason)


def _load_watchdog_state(path) -> Optional[Dict[str, Any]]:
    """读防抖状态。**文件不存在 ⇒ `{}`**（首次运行，合法空态）；不可读/损坏 ⇒ `None`。

    ⚠️ 区分这两种"空"是刻意的：把"读不出来"当成"没有缺口"正是本会话反复修的
    那一类缺陷。调用方拿到 `None` 必须**不写单**。
    """
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        gaps = data.get("gaps") if isinstance(data, dict) else None
        if not isinstance(gaps, dict):
            return None
        out: Dict[str, Any] = {}
        for k, v in gaps.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                # 单条时间戳坏了 ⇒ 只丢这条（其余仍可用），但要留痕
                print(f"[跨所保护巡检] warn 防抖状态里 {k} 的时间戳不可解析，已丢弃该条")
        return out
    except Exception as exc:
        print(f"[跨所保护巡检] warn 防抖状态读取失败（{exc}）")
        return None


def _save_watchdog_state(path, state: Dict[str, Any]) -> bool:
    """原子写防抖状态（临时文件 + `os.replace`）。失败只返回 False，绝不抛。"""
    try:
        _dir = os.path.dirname(path)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"gaps": dict(list(state.items())[:500]),
                       "updated_at": time.time()}, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as exc:
        print(f"[跨所保护巡检] warn 防抖状态写入失败（{exc}）")
        return False


def venue_protection_watchdog_stage(*,
        xv_positions_by_venue,
        executed_actions,
        venue_registry,
        current_environment,
        R20_VENUE_PROTECTION_WATCHDOG,
        audit_cross_venue_protection,
        dry_run=False,
        state_path=None,
        debounce_s=None,
        debounce_step=None,
        now_s=None,
        ledger_rows=None):
    """跨所云端保护单巡检（roadmap G8 的周期接线；**默认关闭**）。

    ## 为什么单独一格、且默认关闭

    外所（Gate/Binance）的触发单带 `expiration`（Gate 默认 7 天，相对创建时间），
    到期后离开交易所 open 列表 ⇒ 仓位裸奔；而主链的 OKX 保护核验**够不到**跨所仓位
    （跨所持仓 instId 是合成 id `GATE:BTC_USDT`，与因子快照的 OKX 形态匹配不上）。
    本格把 `scripts/trader/venue_protection.py` 的判定/动作接到每周期快照上。

    **接线不等于开闸**：`R20_VENUE_PROTECTION_WATCHDOG` 未置 1 时本函数直接返回，
    **零网络、零写单**（线上行为与本刀之前逐字一致）。开闸是运营决定，需人拍板。

    ## 防抖：缺口必须**持续**够久才写单（第一百三十刀）

    `state_path` + `debounce_step` 都在时启用：每周期先跑一次**观察轮**
    （`dry_run=True`，零写单）拿"本来会做"的动作，按 `venue|inst|stage` 记首次出现时刻；
    只有**已持续 ≥ `debounce_s`** 的缺口才允许触发真实写单那一轮。缺口愈合 ⇒ 状态自清。

    未出现够久的周期**只观察不写单**，并把每个缺口的"已持续 X 分钟"报进
    `executed_actions`（运营能看到它在逼近阈值）。`debounce_s <= 0` = 显式不防抖。

    ⚠️ **状态不可读写 ⇒ 本周期不写单**（fail-closed）：状态失真的防抖等于没有防抖，
    宁可晚一轮动手，也不要在"不知道这缺口多久了"的情况下写单。
    不传 `state_path`/`debounce_step`（测试与显式调用）⇒ 防抖关闭、单轮直通。

    ## 开闸前的第一步：`dry_run=True` 预演（第一百二十九刀）

    总闸开启 + `R20_VENUE_PROTECTION_WATCHDOG_DRY_RUN=1` ⇒ 本格照常每周期判定，
    但把 `dry_run=True` 透给审计层：**只判定、不写单**，并把审计层 `would`
    里"本来会做"的动作逐条打印出来。这是把"一次误判"与"一串真实订单"隔开的那道闸，
    也是本格从"默认关闭"走向"开闸"之间**唯一安全**的过渡档。

    ## 边界（刻意的保守选择）

    - **fail-soft**：本格任何异常只打印告警、绝不中断周期 —— 它是在既有 OKX 硬核验
      之上的**加固层**，不该成为新的单点；后续若要收紧成 fail-closed，是独立的一刀；
    - 只读**已冻结的本周期快照**（不额外拉持仓）；每仓一次保护单列表核验是必要成本；
    - 完全没有止损腿的仓位只报 CRITICAL（**不替它定价补挂** —— 价位是策略决定，
      巡检层臆造价位等于偷偷改策略）。
    """
    if not R20_VENUE_PROTECTION_WATCHDOG:
        return None
    try:
        env_mode = str(current_environment().mode)
    except Exception as exc:
        print(f"[跨所保护巡检] warn 环境轴不可得（{exc}），本轮跳过")
        return None

    _now = time.time() if now_s is None else float(now_s)
    _debounce_on = bool(state_path) and callable(debounce_step)
    _observe = None
    _new_state: Dict[str, Any] = {}
    _observed: List[str] = []
    _qualified: List[str] = []
    _qualify_min = 0.0 if debounce_s is None else max(0.0, float(debounce_s)) / 60.0

    def _run_audit(_dry):
        return audit_cross_venue_protection(
            xv_positions_by_venue,
            venue_registry=venue_registry,
            environment=env_mode,
            dry_run=bool(_dry),
            # 第一百七十四刀：台账行供**归属取证**（`ledger` 档证据＝同币同向同量已平记录）。
            # 读不到（None）⇒ 不产生证据 ⇒ 腿留在"归属不可判定" ⇒ 绝不自动撤。
            ledger_rows=ledger_rows,
        )

    if _debounce_on:
        # 观察轮：**只判定不写单**，拿到"本来会做"的动作（真模式与它同源）
        try:
            _observe = _run_audit(True)
        except Exception as exc:
            # fail-soft：巡检是加固层，不该成为新的单点
            print(f"[跨所保护巡检] warn 观察轮异常（不影响本周期）: {exc}")
            return None
        _state = _load_watchdog_state(state_path)
        if _state is None:
            print("[跨所保护巡检] warn 防抖状态不可读——本周期**不写单**（不知道缺口持续多久就不动手）")
            return None
        try:
            _new_state, _observed, _qualified = debounce_step(
                _state, _observe, now_s=_now, debounce_s=debounce_s)
        except Exception as exc:
            print(f"[跨所保护巡检] warn 防抖计算异常（{exc}）——本周期不写单")
            return None
        if not _save_watchdog_state(state_path, _new_state):
            print("[跨所保护巡检] warn 防抖状态不可写——本周期**不写单**（否则下轮状态失真）")
            return None

    if dry_run:
        # 预演：观察轮即结果（若要写单的那一轮，本也不该写）
        report = _observe if _debounce_on else _run_audit(True)
        if _debounce_on:
            print(f"[跨所保护巡检] 预演：{len(_observed)} 个缺口，其中 {len(_qualified)} 个"
                  f"已持续 ≥ {_qualify_min:.0f} 分钟（开闸后这些才会真的写单）")
    elif _debounce_on and not _qualified:
        print(f"[跨所保护巡检] {len(_observed)} 个缺口尚未持续够 {_qualify_min:.0f} 分钟"
              f"——本周期只观察不写单")
        for _k in _observed:
            _age = max(0.0, _now - float(_new_state.get(_k) or _now)) / 60.0
            _line = f"[跨所保护·观察] {_k} 已持续 {_age:.1f} 分钟"
            print(_line)
            executed_actions.append(_line)
        report = _observe
    else:
        try:
            report = _run_audit(False)
        except Exception as exc:
            print(f"[跨所保护巡检] warn 巡检异常（不影响本周期）: {exc}")
            return None

    if dry_run:
        # ⚠️ 预演模式下 `actions` 必然为空（审计层不写单）——要报的是 `would`。
        # 若预演却出现了 `actions`，那是审计层违约，如实喊出来而不是悄悄展示。
        if report.get("actions"):
            print("🔴 [跨所保护] 预演模式下审计层仍返回了 actions —— 断言失败，请立即排查"
                  "（预演不得写单）")
        print("[跨所保护] ⚠️ 预演模式（dry-run）：本周期**只判定不写单**，"
              "下列是'如果开闸本来会做'的动作")
        for item in report.get("would") or []:
            _w = (f"[跨所保护·预演] {str(item.get('venue','')).upper()} "
                  f"{item.get('inst') or ''} {item.get('stage') or ''} "
                  f"{item.get('detail') or ''}").strip()
            print(_w)
            executed_actions.append(_w)
    for item in report.get("actions") or []:
        executed_actions.append(
            f"[跨所保护] {item['venue'].upper()} {item['inst']} {item['detail']}")
    for item in report.get("critical") or []:
        _line = (f"🔴 [跨所保护] {item['venue'].upper()} {item['inst']} {item['side']} "
                 f"无止损腿（{item.get('detail')}）——需人工或用既定策略价位重挂")
        print(_line)
        executed_actions.append(_line)
    for item in report.get("errors") or []:
        print(f"[跨所保护巡检] warn {item.get('venue')} {item.get('inst') or ''} "
              f"{item.get('stage')}: {item.get('detail')}")
    if _debounce_on and not dry_run:
        print(f"[跨所保护巡检] 本轮实写 {len(report.get('actions') or [])} 个动作"
              f"（防抖窗口 {_qualify_min:.0f} 分钟，观察 {len(_observed)} 项）")
    return report

def data_shape_preflight_stage(*, intents_path, trackers_path,
                               validate_intents_file, validate_trackers_file) -> list:
    """周期开跑前的**只读形状预检**（第 49 刀）：把"静默忽略"变成"指名道姓"。

    与加载侧的分工（刻意如此）：

    | 侧 | 负责 |
    |---|---|
    | 加载侧（`load_open_intents` / `load_trackers`）| **行为**：读不出来 ⇒ fail-closed / 拒绝覆盖 |
    | 本阶段 | **可见性**：读得到但形状不合规 ⇒ 打印并指出下游后果 |

    ⚠️ 本阶段**只警告、不阻断、不写盘**。它要抓的典型是那些"读得到却会被静默忽略"的
    形状问题 —— 例如追踪器键名拼写不合约定（`BTC_USDT_long`）会让水位查找落空、
    状态静默丢失，而不触发任何异常。

    返回违规行列表（调用方只用于展示/测试；不做控制流判定）。
    """
    violations = []
    for label, path, checker in (("意图", intents_path, validate_intents_file),
                                 ("追踪器", trackers_path, validate_trackers_file)):
        for line in checker(path):
            violations.append(f"[数据形状预检] {label}: {line}")
    for line in violations:
        print(line)
    if not violations:
        print("[数据形状预检] 意图/追踪器形状合规")
    return violations

def cycle_disclosure_payload(*, broken_venues=(), entries_blocked=False,
                            shape_violations=(), watchdog_report=None,
                            watchdog_enabled=True) -> dict:
    """周期披露的**结构化**载荷（第 51 刀）：供"渲染一条行"与"落盘成指标"共用。

    单一事实源：`cycle_disclosure_summary` 只负责把它渲染成一行；
    `write_cycle_disclosure_snapshot` 只负责把它原子落盘给后端 `/metrics` 读。
    两处都不再各自解释"什么算跳过" —— 这正是本仓反复吃过的"同一语义两处写"。

    ⚠️ 绝不抛异常（非 dict 的 watchdog 报告、`None` 集合、含 `None` 的列表一律宽容）：
    报告器不得成为新的单点故障。
    """
    # ⚠️ `str(None)` 是 `"None"`（真值！）—— 旧写法会把列表里的 `None` 渲染成
    # "一所名叫 None 的坏所"，披露行里就多出一条假场所（"UI 不说谎"的反面）。
    # 空串/纯空白同理：不是场所名，不该进披露。
    venues = sorted({str(v).strip() for v in (broken_venues or [])
                     if v is not None and str(v).strip()})
    bad = [str(b) for b in (shape_violations or [])]
    rep = watchdog_report if isinstance(watchdog_report, dict) else {}
    return {
        "broken_venues": venues,
        "broken_venue_count": len(venues),
        "entries_blocked": bool(entries_blocked),
        "shape_violation_count": len(bad),
        "shape_violation_head": bad[:3],
        "watchdog_enabled": bool(watchdog_enabled),
        "watchdog_errors": len(rep.get("errors") or []),
        "watchdog_critical": len(rep.get("critical") or []),
        "clean": not (venues or entries_blocked or bad),
    }


def cycle_disclosure_summary(payload: dict) -> str:
    """把披露载荷渲染成**一条可检索**的行（渲染器；判定/落盘见 payload 与快照写入）。

        [周期披露] 本轮无跳过/未核验项
        [周期披露] 本轮跳过/未核验：凭证坏所=2(binance,gate); 对账失败（禁本轮新开仓）

    判据很朴素但有效：**这条行必须每轮都出现**（门禁钉调用点）⇒ 有人删掉某处披露时，
    汇总行里的数字会随之变化，评审看日志就能发现"怎么不报了"。
    """
    payload = payload if isinstance(payload, dict) else {}
    parts = []
    n_ven = int(payload.get("broken_venue_count") or 0)
    if n_ven:
        parts.append(f"凭证坏所={n_ven}({','.join(payload.get('broken_venues') or [])})")
    if payload.get("entries_blocked"):
        parts.append("对账失败（禁本轮新开仓）")
    n_bad = int(payload.get("shape_violation_count") or 0)
    if n_bad:
        head = "; ".join(payload.get("shape_violation_head") or [])
        more = f" …共{n_bad}条" if n_bad > 3 else ""
        parts.append(f"数据形状违规={n_bad}（{head}{more}）")
    errs, crit = int(payload.get("watchdog_errors") or 0), int(payload.get("watchdog_critical") or 0)
    if errs or crit:
        parts.append(f"跨所保护：错误={errs} 严重缺口={crit}")
    line = ("[周期披露] 本轮跳过/未核验：" + "; ".join(parts)) if parts \
        else "[周期披露] 本轮无跳过/未核验项"
    if not payload.get("watchdog_enabled", True):
        # 未开闸的加固层是**没在跑的保护**，属"应当被看见"的事实（不是错误）
        line += "；跨所保护巡检未开闸"
    return line


def write_cycle_disclosure_snapshot(*, path, payload, _atomic_write_json) -> bool:
    """把披露载荷**原子**落盘给后端 `/metrics` 跨进程读取（第 51 刀）。

    与行情取数健康快照同一路数（worker 写、后端读）；`written_at_ms` 用于算新鲜度，
    让"很久没更新"与"本轮很干净"在指标上可区分（读不到 ≠ 没有）。
    失败只返回 False（绝不抛、绝不改变交易行为）。
    """
    import time as _time
    body = dict(payload or {})
    body["written_at_ms"] = int(_time.time() * 1000)
    try:
        _atomic_write_json(path, body)
        return True
    except Exception as exc:      # noqa: BLE001 - 可观测性失败绝不拖垮周期
        print(f"[周期披露] warn 快照写入失败（不影响交易）: {exc!r}")
        return False

"""仪表盘 LIVE 载荷装配（结构优化阶段 4·B3 第三十六刀）。

原样搬自 `r20_backend/dashboard_cache.py::update_cache_cycle` 的 92 行 `CACHE_DATA` 字面量 ——
该函数里最大的一块，也是 `r20_backend/dashboard_cache.py` 里唯一的大块（其余没有超过 100 行的函数）。

## 为什么整体搬成一个「载荷装配」函数

这 92 行是**一个内聚单元**：共同描述"一次成功采集后，仪表盘对外看到的世界"。
字段之间没有分支依赖，纯装配 —— 因此可以整块搬走并**按字段对拍**。

## ⚠️ 入参显式列在签名里是**刻意的**

没有拆成若干"小载荷函数"，也没有传 `**kwargs` 或一个大字典。
理由：每个字段都是**前端取值契约**，显式列在签名里才能让"某个字段没了"在
**调用点**就看得见。

> 用 `**locals()` 或 `**dict` 会把"漏传一个字段"从**编译期错误**变成**默认值**，
> 那正是这类装配代码最危险的失效方式 —— 载荷悄悄少一个字段，前端静默降级。

## ⚠️ 三个测试缝也**在签名里**，由门面在调用期传入

`load_instruments` / `build_ai_health` / `_load_cross_venue_data` 都是**既有测试缝**：
测试会 `patch.object(app, "load_instruments")`、`patch.object(app, "build_ai_health")`、
`patch.object(app, "_load_cross_venue_data")`。

门面写成 `load_instruments=load_instruments` —— Python 在**调用时**解析门面全局，
恰好就是 `patch.object` 替换后的那个对象。若本模块在 import 期绑定它们，
补丁会被**静默绕过**：测试仍绿，行为已变（见 `r20_backend/README.md` §5 铁律）。

⚠️ 我第一版写成"函数体内 `from dashboard import app as _app` 再 `getattr`"，
被既有闸 `tests/ui/test_dashboard_payload_seam.py::
test_core_modules_do_not_import_dashboard_app` **当场拦下**
（那种 import 会与 `routers/dashboard.py` 的 `import r20_backend.dashboard_cache` 构成循环）。
**是那道闸纠正了我，不是我事后自己想到的** —— 记在这里免得后人再走一遍。

## 三处易错点（均原样保留）

1. `"partial": bool(source_errors)` —— 是**布尔**；列表在 `data_health.errors`；
2. `"margin_usage_pct"` 在 `total_eq <= 0` 时给 `0`（**整数零**，不是 `0.0`）；
3. `funding_settlements.items` 按 `time` **降序**取前 30 —— 键是 `x["time"]`，
   某条缺 `time` 会 `KeyError`（既有行为，**不做**兜底）。
"""


#: 表示"这份载荷是**上次成功**的旧数据"的状态词表（`data_health.status`）。
#: 第一百九十七刀：前端 `stores/dashboard.ts` 一直在读载荷根上的 `is_stale`，而**后端从未发过**
#: ⇒ 那个判断恒为 false（`?? false`），面板的陈旧分支只剩 `status === 'STALE'` 一条腿在撑。
#: 这里把它变成真的：由**同一个**状态词推导，避免两处各写一套陈旧判据。
STALE_STATUSES = ("STALE", "NOT_READY")


def is_stale_status(status: object) -> bool:
    """`data_health.status` → 这份载荷是否为"上次成功的旧数据"。"""
    return str(status or "").strip().upper() in STALE_STATUSES


def build_live_cache_payload(
    _load_cross_venue_data,
    _load_multi_venue_portfolio, _load_portfolio_risk_data, _today_stats_source, _trader_cycle_minutes,
    adaptive_cfg, ai_history_list, ai_last_prompt_text, ai_memory_md_content,
    all_closed, all_loss_amt, all_loss_trades, all_win_amt,
    all_win_rate, all_win_trades, avail_eq, avg_loss,
    avg_win, build_ai_health, cash_bal, cum_roi_pct, cum_total_fees,
    disk_free_gb, factor_lib_snapshot, factors_list, funding_history_list,
    initial_capital_val, inst_leaderboard, load_instruments, log_lines, long_count,
    news_data, orders_data, pending_orders_list, positions,
    profit_factor, review_data, short_count, snapshots_list,
    source_errors, state_data, timestamp_full, today_bj_str,
    today_fees, today_funding, today_loss_trades, today_net_realized_pnl,
    today_realized_gross, today_win_rate, today_win_trades, total_cum_net_pnl,
    total_cum_realized_pnl, total_eq, total_pos_upl, trades_table,
    upl_acc,) -> dict:
    """装配 LIVE 载荷。字段与签名一一对应；无 I/O、不读模块状态。

    三个测试缝（`load_instruments` / `build_ai_health` / `_load_cross_venue_data`）
    由**门面在调用期**以关键字传入 —— Python 在调用时解析门面全局，
    恰好就是被 `patch.object` 替换后的那个对象。
    """
    _brain_rows = ai_history_list if isinstance(ai_history_list, (list, tuple)) else []
    _latest_brain = _brain_rows[0] if _brain_rows and isinstance(_brain_rows[0], dict) else {}
    return {
    "timestamp": timestamp_full,
    "date": today_bj_str,
    # 根级 `is_stale`（前端 `stores/dashboard.ts` 消费；TS 契约里是必填字段）
    "is_stale": is_stale_status("LIVE" if not source_errors else "PARTIAL"),
    # 根级 `macro_assessment`：TS 契约声明在根上，而真实内容一直在
    # `ai_brain_history[0].macro_assessment`（真机缓存实测）⇒ 前端根级读取永远拿不到、只显示
    # "扫描中…"。这里发一个**同源别名**（不新算，取最新一条脑内记录），嵌套原字段保留。
    "macro_assessment": _latest_brain.get("macro_assessment"),
    "data_health": {
        "status": "LIVE" if not source_errors else "PARTIAL",
        "partial": bool(source_errors),
        "errors": source_errors,
        "last_success_at": timestamp_full,
        "cache_age_seconds": 0,
        "timezone": "Asia/Shanghai",
        "bills_complete": False,
        "bills_coverage_note": "OKX latest 100 bills; NAV remains the cumulative equity source of truth",
        # 批B(2026-09-13)·决策周期单一事实源：前端 DataStatus 曾 write 死 15 分钟
        # 当事实读。此处从网关调度器 JobSpec 取真实周期，取不到给 None（前端不渲染，
        # 宁缺勿假）。trader 周期为代码常量，进程内 memo 一次即可。
        "cycle_minutes": _trader_cycle_minutes(),
    },
    "system": {
        "disk": {
            "free_gb": disk_free_gb
        }
    },
    "account": {
        "initial_capital": round(initial_capital_val, 2),
        "total_eq": round(total_eq, 2),
        "avail_eq": round(avail_eq, 2),
        "cash_bal": round(cash_bal, 2),
        "upl": round(upl_acc, 2),
        "pos_upl_total": round(total_pos_upl, 2),
        "cum_realized_pnl": round(total_cum_realized_pnl, 2),
        "cum_net_pnl": round(total_cum_net_pnl, 2),
        "cum_roi_pct": cum_roi_pct,
        "cum_total_fees": round(cum_total_fees, 2),
        "total_pos_margin": round(sum(float(p.get("margin_usdt") or 0.0) for p in positions), 2),
        "margin_usage_pct": round(((sum(float(p.get("margin_usdt") or 0.0) for p in positions)) / total_eq * 100) if total_eq > 0 else 0, 1)
    },
    "today_stats": {
        "realized_gross": round(today_realized_gross, 2),
        "fees_paid": round(today_fees, 2),
        "funding_paid": round(today_funding, 2),
        "net_realized": round(today_net_realized_pnl, 2),
        "total_pnl": round(today_net_realized_pnl + total_pos_upl, 2),
        "win_trades": today_win_trades,
        "loss_trades": today_loss_trades,
        "win_rate": today_win_rate,
        "source": _today_stats_source,
    },
    "performance": {
        "all_trades": all_closed,
        "win_trades": all_win_trades,
        "loss_trades": all_loss_trades,
        "win_rate": all_win_rate,
        "profit_factor": profit_factor,
        "total_win_amt": round(all_win_amt, 2),
        "total_loss_amt": round(all_loss_amt, 2),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "leaderboard": inst_leaderboard
    },
    "positions": positions,
    "positions_summary": {
        "total": len(positions),
        "active_count": len(positions),
        "max": len(load_instruments()),
        "max_positions": len(load_instruments()),
        "long_count": long_count,
        "short_count": short_count,
        "total_upl": round(total_pos_upl, 2),
        "items": positions
    },
    "pending_orders": pending_orders_list,
    "factors": factors_list,
    "funding_settlements": {
        "total_funding_pnl": round(today_funding, 4),
        "items": sorted(funding_history_list, key=lambda x: x["time"], reverse=True)[:30]
    },
    "adaptive_config": adaptive_cfg,
    "review": review_data,
    "ai_trading_memory_md": ai_memory_md_content,
    "ai_last_prompt": ai_last_prompt_text,
    "snapshots": snapshots_list,
    "state_snapshot": state_data,
    "logs": log_lines,
    "trades": trades_table,
    "news_intelligence": news_data,
    "ai_brain_history": ai_history_list,
    "ai_health": build_ai_health(ai_history_list),
    "factor_library": factor_lib_snapshot,
    "cross_venue": _load_cross_venue_data(),
    "portfolio_risk": _load_portfolio_risk_data(),
    "multi_venue_portfolio": _load_multi_venue_portfolio(total_eq, avail_eq, positions, orders_data)
}

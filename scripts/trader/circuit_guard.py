"""开仓风控哨兵与熔断（B3 抽取·ai_factor_trader 瘦身第一刀，第八十一刀）。

从 `scripts/ai_factor_trader.py` **纯搬家**两个函数：

| 函数 | 行数 | 职责 |
|---|---|---|
| `check_black_swan_sentinel` | 44 | BTC 行情断崖 + 新闻极端情绪的黑天鹅判定（fail-closed） |
| `is_circuit_breaker_active` | 50 | 组合入口：黑天鹅 → 熔断文件 → 当日回撤限额 |

## 为什么先动这一域

- **零交易动作**：两函数只读文件/行情并返回 `(bool, str)`，搬错最坏是
  哨兵不触发，且有专测 `test_black_swan_sentinel_revival.py` 从门面名打穿；
- 依赖面干净（全可注入），不碰 `OPEN_INTENT`/`execute_portfolio` 等深水区；
- 符合 §58 碑：子模块**不在 `risk_test_env` reload 名单** ⇒ 所有外部依赖
  一律**参数注入**，模块 import 期零常量绑定。

## 注入形状与门面的关系（patch 语义保真）

门面壳在**调用期**解析模块全局再传参：

```python
def check_black_swan_sentinel():
    return _sentinel(fetch_candles_direct=fetch_candles_direct,
                     news_sentiment_file=NEWS_SENTIMENT_FILE)
```

因此测试 `patch.object(aft, "NEWS_SENTIMENT_FILE", …)` /
`patch.object(aft, "fetch_candles_direct", …)`（既有专测的 patch 面）**照常生效**。

⚠️ `scripts/news_sentiment_harvester.py` 里另有一个**同名私有**
`is_circuit_breaker_active`（L129，harvester 自己的简化判定）—— 那是
审计回马枪④2 处理过的"孪生漂移"家族，本刀**不合并不动它**（业务不变约束），
只登记事实：门面版从本模块导入，harvester 版仍用自己的。
"""
from __future__ import annotations

import datetime
import json
import os
import time
from typing import Tuple


def check_black_swan_sentinel(*, fetch_candles_direct, news_sentiment_file: str) -> Tuple[bool, str]:
    """Minute-level Black Swan Sentinel, driven by the unified V5 REST public
    market feed (market_data_service www→aws dual-domain + alt-venue fallback, 零凭证可读).

    US-014 前置收尾（归因：3137c40/09fba6f 将 smartmoney/news CLI 信号面缺失化后，
    新闻模式熔断随之休眠）：黑天鹅熔断改由此公共行情路径**复活**，并按
    「不可判定=不放松」的 fail-closed 语义兜底——行情取不到/样本不足时触发熔断，
    绝不带着盲区继续开新仓。新闻情绪层只消费**可判定**的极端值：旧实现以缺省
    overall_score=50 冒充中性「一切正常」，已删除该假中性值——文件缺失/无该字段
    一律视为不可判定，仅当数值 ≤20 才触发熔断。"""
    # 1. BTC 15M candles extreme plunge (> 3.0% in 15 mins) via unified public REST.
    try:
        candles = fetch_candles_direct("BTC-USDT-SWAP", "15m", 3)
    except Exception as exc:
        return True, f"🚨 黑天鹅熔断：统一行情通道异常 ({type(exc).__name__})，不可判定=不放松，保守暂停新开仓"
    if not candles or len(candles) < 2:
        return True, "🚨 黑天鹅熔断：统一行情通道无有效数据（双域+备源皆断），不可判定=不放松，保守暂停新开仓"
    try:
        latest_c = candles[0]
        c_open = float(latest_c[1])
        c_close = float(latest_c[4])
        c_low = float(latest_c[3])
        drop_pct = (c_close - c_open) / c_open * 100.0
        if drop_pct <= -3.0 or ((c_low - c_open) / c_open * 100.0 <= -4.0):
            return True, f"🚨 监测到 BTC 15M 级别发生断崖式暴跌插针 ({drop_pct:.2f}%)，触发全网黑天鹅紧急熔断！"
    except (ValueError, TypeError, IndexError):
        # 行情形态不可判定同样不得放宽风控
        return True, "🚨 黑天鹅熔断：行情数据格式异常不可判定，不可判定=不放松，保守暂停新开仓"

    # 2. News sentiment file：仅认显式极端值（≤20），缺失≠中性50≠放行。
    if os.path.exists(news_sentiment_file):
        try:
            with open(news_sentiment_file, "r", encoding="utf-8") as f:
                n_data = json.load(f)
            raw_score = n_data.get("overall_score")
            if raw_score is not None:
                score = float(raw_score)
                if score <= 20.0:
                    return True, f"🚨 监测到突发黑天鹅极度恶性利空舆情 (情绪指数: {score:.1f})，触发全网黑天鹅紧急熔断！"
        except Exception:
            # 情绪文件损坏同样不可判定：不因读不到而放行（与下方熔断状态文件
            # 损坏→安全暂停 的既有语义一致）
            return True, "🚨 黑天鹅熔断：新闻情绪缓存损坏不可判定，不可判定=不放松，保守暂停新开仓"

    return False, ""


def is_circuit_breaker_active(usdt_available: float = None, *, circuit_breaker_file: str,
                              ledger_json_file: str, current_environment,
                              effective_daily_loss_limit, sentinel_check):
    # 1. Black Swan Sentinel Check
    # ⚠️ 第八十一刀：`sentinel_check` 由门面壳传入**门面全局
    # `check_black_swan_sentinel`**（必填，非默认）—— 既有活体接线测试
    # `test_audit_batch2_risk_gates_live.TestTraderBreakerLiveWiring` 靠
    # `patch.object(aft, "check_black_swan_sentinel", lambda: (False, ""))`
    # 中性化哨兵；若 breaker 直接用子包自身的，那层 patch 面就随搬家丢了。
    # 基线这里是**无参**调用门面全局，故这里也必须无参调用注入项
    # （对拍门按"两侧 sentinel 调用归一"校验形状一致）。
    bs_active, bs_reason = sentinel_check()
    if bs_active:
        return True, bs_reason

    # 2. File-based Circuit Breaker Check (shared schema with news harvester)
    if os.path.exists(circuit_breaker_file):
        try:
            with open(circuit_breaker_file, "r", encoding="utf-8") as f:
                cb = json.load(f)
            expires_at = float(cb.get("expires_at_ts", 0) or 0)
            active = bool(cb.get("active")) or cb.get("status") == "triggered"
            if active and (expires_at <= 0 or time.time() < expires_at):
                return True, cb.get("reason") or cb.get("headline") or "黑天鹅极端行情熔断中"
        except Exception as e:
            return True, f"熔断状态文件损坏，安全暂停开仓: {e}"

    # 3. Daily Max Loss Limit Check from lifecycle ledger using Beijing close_time.
    if os.path.exists(ledger_json_file):
        # 审计回马枪④2(2026-09-13)：上轮 A2 的「同步失败所→禁开仓」加固只进了
        # r20_backend.execution.circuit_breaker 模块版，而活路径走本函数（孪生漂移），
        # 等于闸装了死副本。现从模块导入同一实现，双进程单一事实源。
        try:
            from r20_backend.execution.circuit_breaker import (
                _ledger_sync_failed_venues, ledger_daily_closed_pnl)
            from r20_backend.execution.circuit_breaker import (
                _ledger_sync_sidecar_state as _sidecar_state)
            _failed_venues, _sidecar_unknown = _sidecar_state()
            if _sidecar_unknown:
                # 与模块版同源（第一百四十四刀，用户拍板 fail-closed）：不可判定 ⇒ 禁开仓
                return True, (f"台账同步状态不可判定（{_sidecar_unknown}）⇒ "
                              "当日亏损求和不可判全，安全暂停开仓")
            if _failed_venues:
                return True, ("台账跨所同步不完整（失败所: " + ",".join(_failed_venues) +
                              "），当日亏损求和不可判全，安全暂停开仓")
        except Exception as e:
            return True, f"台账同步旁车检查不可用，安全暂停开仓: {e}"
        try:
            with open(ledger_json_file, "r", encoding="utf-8") as f:
                ledger = json.load(f)
            tz_bj = datetime.timezone(datetime.timedelta(hours=8))
            today_str = datetime.datetime.now(tz_bj).strftime("%Y-%m-%d")
            # 审计④1：求和必须按 current_environment().mode 过滤环境（demo↔live 切换日
            # 两环境盈亏互抵可致熔断假阴性），规则与模块版 ledger_daily_closed_pnl 同源。
            try:
                _mode = str(current_environment().mode or "")
            except Exception:
                _mode = ""  # 环境不可判 → 保守全计（宁停不漏）
            today_pnl = ledger_daily_closed_pnl(ledger, _mode, today_str)
            _loss_cap = effective_daily_loss_limit(usdt_available)
            if today_pnl < -_loss_cap:
                return True, f"今日累计回撤 ({today_pnl:.2f}U) 触及单日最大风控熔断限额 ({_loss_cap}U｜按可用余额自适应)"
        except Exception as e:
            return True, f"日亏损风控数据读取失败，安全暂停开仓: {e}"

    return False, ""

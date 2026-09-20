"""交易台账与开仓意图写入（B3 抽取·trader 瘦身第四刀，第八十三刀）。

从 `scripts/ai_factor_trader.py` **纯搬家**两函数（36 + 27 行）：

| 函数 | 职责 |
|---|---|
| `record_trade` | 成交落账：policy_version 溯源 → JSON 台账（原子替换）→ SQLite 双写 |
| `record_open_intent` | 开仓意图簿（US-006 对账归属；PEPE 永动机修复的"写入时清理"语义在此） |

## 同名注入（沿第八十二刀先例）

注入 kw 与门面全局**同名**（`LEDGER_JSON_FILE`/`OPEN_INTENT_FILE`/
`_atomic_write_json`/`record_trade_sqlite`…）⇒ **函数体逐字零改动**
（只加签名行）：batch3 台账原子写断言的 `getsource` 文本在子包里原样成立。
门面壳调用期解析全局传参，`patch.object(aft, "OPEN_INTENT_FILE"/"LEDGER_JSON_FILE")`
的既有 patch 面（order_submit_risk_isolated / batch6 实测例）保真。

⚠️ `record_trade` 的 SQLite 侧参数 `record_trade_sqlite` 可能为 None
（db_manager 可选导入语义），body 的 `if record_trade_sqlite:` 原样保留。
"""
from __future__ import annotations

import json
import os
import time


def record_open_intent(inst_id: str, side: str, ts_ms: int = None, *,
                       OPEN_INTENT_FILE: str, OPEN_INTENT_TTL_MS: int,
                       _atomic_write_json) -> None:
    """下单成功后记录本地开仓意图，供重启后挂单对账归属（US-006）。

    审计(2026-09-13)·PEPE 永动机修复之二：写入时**清理**——过期(TTL 6h)条目丢弃、
    同标的同方向只保留最新一条。旧实现只 append（上限 200 条 FIFO），意图文件里
    永远躺着全天最老的一条，配合对账端 next() 取最老匹配 = 每轮误撤自己刚挂的单。"""
    try:
        intents = []
        if os.path.exists(OPEN_INTENT_FILE):
            try:
                with open(OPEN_INTENT_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    intents = raw
            except (ValueError, OSError):
                intents = []  # 空文件/损坏文件：从空重建，不影响本单交易
        _now_ms = int(time.time() * 1000)
        intents = [i for i in intents
                   if isinstance(i, dict) and _now_ms - int(i.get("ts", 0) or 0) <= OPEN_INTENT_TTL_MS]
        _side_l = str(side).lower()
        intents = [i for i in intents
                   if not (str(i.get("instId")) == inst_id and str(i.get("side", "")).lower() == _side_l)]
        intents.append({"instId": inst_id, "side": side, "ts": int(ts_ms or _now_ms)})
        # ⚠️ 第一百三十五刀：**原子替换**（mkstemp+fsync+os.replace）。
        # 旧体 `open(OPEN_INTENT_FILE, "w")` 是**非原子直写** —— 写崩/断电会留下
        # 0 字节或半截 JSON，而读取侧（挂单对账 / 存量挂单回收）据此把"读不到"
        # 当成"没有意图"⇒ 逐笔按孤儿撤单（第一百三十四刀已让读取侧 fail-closed，
        # 本刀**从源头消灭这个状态**：失败时旧文件原样保全）。
        _atomic_write_json(OPEN_INTENT_FILE, intents[-200:])
    except Exception as e:
        print(f"[挂单对账] 记录开仓意图失败（不影响本单交易）: {e}")



def record_trade(trade_data, *, LEDGER_JSON_FILE: str, _atomic_write_json,
                 record_trade_sqlite, current_environment, __version__: str):
    # G10 场所标注：本链路全部为 OKX V5 直签执行，源头补 venue（gate lab 写侧
    # 自带 venue="gate"）；setdefault 不覆盖显式值，旧调用方无感。
    trade_data.setdefault("venue", "okx")
    if not isinstance(trade_data, dict):
        return
    if "policy_version" not in trade_data:
        try:
            from policy_snapshot import generate_policy_snapshot
            trade_data["policy_version"] = generate_policy_snapshot().get("policy_version", f"v{__version__}@unknown")
        except Exception:
            trade_data["policy_version"] = f"v{__version__}@unknown"
    try:
        ledger = []
        if os.path.exists(LEDGER_JSON_FILE):
            with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        ledger.append(trade_data)
        # 审计③(2026-09-13)：生产台账直 open("w") 覆写 → 原子替换。读者（熔断/
        # 日报/备份/面板）不再可能撞见半截 JSON。
        _atomic_write_json(LEDGER_JSON_FILE, ledger)
    except Exception as e:
        print(f"Failed to record trade to JSON: {e}")

    try:
        if record_trade_sqlite:
            # US-003 环境轴贯通：OKX 生产写方按冻结环境传真实档 live|demo；
            # 环境不可证明时交给 db_manager 兜底 unknown_legacy，绝不冒充。
            sqlite_row = dict(trade_data)
            try:
                sqlite_row.setdefault("environment", current_environment().mode)
            except Exception:
                pass
            record_trade_sqlite(sqlite_row)
    except Exception as e:
        print(f"Failed to record trade to SQLite: {e}")


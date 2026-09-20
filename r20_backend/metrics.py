"""机构级指标中枢：Prometheus 文本 exposition（可观测性 Phase 1 落地）。

## 为什么需要它

系统已有 `logs/*.log`（人读）、`/api/v1/admin/overview`（面板读）与
`r20_admin_audit.jsonl`（审计读），但**没有机器可抓的时序指标**：Grafana/Prometheus
接不进来，"今天这根 429 尖峰是什么时候开始的""模型调用耗时是不是在爬"只能靠人翻日志。

## 四条设计约束（都是被历史事故教出来的）

1. **只读、零副作用**：本模块不写任何文件、不改任何配置、不发任何请求，只把**既有
   运行态事实**翻译成文本；
2. **每个数据源 fail-soft，且失败必须可见**：某个源读不到时不让整个 `/metrics` 500，
   而是发一条 `r20_metrics_source_ok{source="…"} 0`。抓取侧既拿到部分数据，又不会被
   静默骗过 —— "全绿"与"取数挂了"必须可区分（本仓第 137 刀就是静默 `except` 吞掉
   取数失败导致 30 小时无信号）；
3. **绝不泄露内容**：模型调用只取**计数/耗时/token 汇总**，不输出 prompt、响应或
   `prompt_fingerprint`；风控只输出数值旋钮（无凭证、无密钥）；
4. **取数与渲染分离**：`build_snapshot` 负责取（可注入），`render_prometheus` 是纯函数，
   故格式与 fail-soft 语义都能被单测钉住。

## 指标清单

| 指标 | 含义 |
|---|---|
| `r20_up` | 进程存活（恒 1；抓不到就是 scrape 失败） |
| `r20_metrics_source_ok{source}` | 各数据源本次取数是否成功（1/0） |
| `r20_venue_instruments_ok{venue}` / `_failed` | 该所本轮取数成功/失败的标的数 |
| `r20_venue_latency_avg_ms{venue}` | 该所平均取数延时 |
| `r20_venue_testnet{venue}` | 该所是否演示盘（1/0；避免把 DEMO 曲线当真金） |
| `r20_venue_health_updated_timestamp_seconds` | 场所健康快照的写入时刻（判过期用） |
| `r20_model_calls_total` / `_successful_total` | 大模型调用累计次数/成功次数 |
| `r20_model_call_duration_ms_avg` | 平均调用耗时 |
| `r20_model_tokens_total` | 累计 token 消耗（成本观测） |
| `r20_risk_limit{name}` | 执行层风控生效值（跑得对不对，先看尺子） |
| `r20_market_data_calls_total{kind}` / `_call_failures_total{kind}` | 行情取数调用次数 / 其中失败次数 |
| `r20_market_data_latency_p50_seconds{kind}` / `_p95_seconds{kind}` | 取数耗时中位数 / p95（尾延时是卡顿的先行信号） |
| `r20_market_data_last_success_age_seconds{kind}` | 该 kind 距上次成功的秒数（成功路径永不老化） |
| `r20_market_data_snapshot_age_seconds` | worker 写的健康快照年龄（worker 断档时会持续变大） |

> 反漂移：新增/改名旋钮时，`r20_risk_limit` 的名字取自本模块 `RISK_LIMIT_NAMES`
> 单一清单，取不到的键**跳过而不是补 0** —— 编一个不存在的阈值比不报更危险。
>
> 跨进程说明：行情取数发生在 **worker 进程**（每 15 分钟 respawn），而 `/metrics`
> 由**后端进程**提供 —— 进程内计数器看不到对方，故走 `data/market_data_health.json`
> 文件契约（与 `venue_health.json` 同一手法）；快照带 `schema_version`，
> 版本不认识一律当"没有数据"（`source_ok=0`），绝不用未知字段拼指标。
"""
from __future__ import annotations

import json
import math

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "REQUIRED_SOURCES",
    "RISK_LIMIT_NAMES",
    "build_snapshot",
    "render_prometheus",
    "collect_venue_health",
    "collect_model_stats",
    "collect_risk_limits",
    "collect_market_data_health",
    "collect_market_stream_health",
]

#: **必需数据源**：缺失即视为故障（告警据此触发）。
#: 不在此列的源是**可选**的：例如公共行情流探测平时并不常驻，若把"没在跑"也算故障，
#: 告警就会**永远在响** —— 而永远在响的告警等于没有告警（运维会学会忽略它）。
#: 故 `r20_metrics_source_ok` 带 `required` 标签，告警只盯 `required="1"`。
REQUIRED_SOURCES: Tuple[str, ...] = ("venue_health", "model_calls", "risk_limits",
                                     "market_data")

#: 对外暴露的风控旋钮（`指标名 → risk_constants 属性名`）。刻意是**白名单**：
#: 新增旋钮要显式登记，避免把内部实现细节或敏感键一股脑推出控制面。
RISK_LIMIT_NAMES: Tuple[Tuple[str, str], ...] = (
    ("max_leverage", "MAX_LEVERAGE"),
    ("min_leverage", "MIN_LEVERAGE"),
    ("max_margin_equity_ratio", "MAX_MARGIN_EQUITY_RATIO"),
    ("single_asset_equity_ratio", "SINGLE_ASSET_EQUITY_RATIO"),
    ("max_single_asset_margin_usdt", "MAX_SINGLE_ASSET_MARGIN"),
    ("min_risk_reward_ratio", "MIN_RISK_REWARD_RATIO"),
    ("max_risk_reward_ratio", "MAX_RISK_REWARD_RATIO"),
    ("min_entry_confidence", "MIN_ENTRY_CONFIDENCE"),
    ("max_daily_loss_usdt", "MAX_DAILY_LOSS_USDT"),
    ("daily_loss_equity_ratio", "DAILY_LOSS_EQUITY_RATIO"),
    ("max_total_exposure_usdt", "MAX_TOTAL_EXPOSURE_USDT"),
    ("portfolio_risk_budget_usdt", "PORTFOLIO_RISK_BUDGET_USDT"),
    ("max_same_direction_positions", "MAX_SAME_DIRECTION_POSITIONS"),
    ("time_stop_hours", "TIME_STOP_HOURS"),
)


def _label_value(value: Any) -> str:
    """Prometheus label 值转义（反斜杠、双引号、换行）。"""
    text = str(value if value is not None else "")
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: Any) -> Optional[float]:
    """只输出有限数：NaN/Inf 会让部分抓取器整条丢弃，故一律降级为 None。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _fmt(value: Any) -> Optional[str]:
    num = _number(value)
    if num is None:
        return None
    if num == int(num):
        return str(int(num))
    return repr(num)


def _parse_utc_seconds(text: Any) -> Optional[float]:
    """`venue_health.json` 的 `updated_utc`（"YYYY-MM-DD HH:MM:SS"，UTC）→ epoch 秒。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        stamp = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return stamp.timestamp()


def collect_venue_health(data_dir: Path) -> Optional[Dict[str, Any]]:
    """读 `data/venue_health.json`；文件缺失/损坏返回 None（由调用方标 source_ok=0）。"""
    try:
        payload = json.loads((Path(data_dir) / "venue_health.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    venues = payload.get("venues")
    return {
        "updated_utc": payload.get("updated_utc"),
        "package_count": payload.get("package_count"),
        "venues": venues if isinstance(venues, dict) else {},
    }


def collect_model_stats(store: Any) -> Optional[Dict[str, Any]]:
    """取大模型调用汇总（结构化数字，不含任何 prompt/响应/fingerprint）。"""
    try:
        stats = store.model_stats()
    except Exception:
        return None
    return stats if isinstance(stats, dict) else None


def collect_risk_limits(module: Any = None) -> Optional[Dict[str, float]]:
    """按白名单读风控生效值；取不到的键**跳过**（绝不补 0 冒充阈值）。"""
    if module is None:
        try:
            from scripts import risk_constants as module  # type: ignore[no-redef]
        except Exception:
            try:
                import risk_constants as module  # type: ignore[no-redef]
            except Exception:
                return None
    out: Dict[str, float] = {}
    for metric_name, attr in RISK_LIMIT_NAMES:
        try:
            raw = getattr(module, attr)
        except Exception:
            continue
        if isinstance(raw, bool):
            raw = 1.0 if raw else 0.0
        num = _number(raw)
        if num is not None:
            out[metric_name] = num
    return out or None


def collect_market_data_health(path: Path) -> Optional[Dict[str, Any]]:
    """读 worker 写的行情取数健康快照（`data/market_data_health.json`）。

    缺失/损坏/schema 版本不认识 → None（调用方标 `source_ok=0`）。

    这里**不需要**关心 `market_data_health` 的双拼写实例问题：`load_snapshot` 是
    **无状态文件读取**（不像计数器那样存在两个实例各背一份内存账），两个拼写读的是
    同一个文件。try/except 只是为了在两种 sys.path 布局下都能 import 到。
    """
    try:
        try:
            from scripts import market_data_health as _mdh
        except Exception:
            import market_data_health as _mdh                              # type: ignore[no-redef]
        payload = _mdh.load_snapshot(str(path))
    except Exception:
        return None
    return payload or None


def collect_market_stream_health(path: Path) -> Optional[Dict[str, Any]]:
    """读行情流健康快照（`data/market_stream_health.json`，探测进程写、这里读）。

    缺失/损坏/schema 版本不认识 → None。`load_snapshot` 是无状态文件读取，
    故不涉及双拼写实例问题（与行情取数快照同理）。
    """
    try:
        try:
            from scripts import market_stream as _ms
        except Exception:
            import market_stream as _ms                              # type: ignore[no-redef]
        payload = _ms.load_snapshot(str(path))
    except Exception:
        return None
    return payload or None


def build_snapshot(*, data_dir: Optional[Path] = None,
                   venue_health: Optional[Dict[str, Any]] = None,
                   model_stats: Optional[Dict[str, Any]] = None,
                   risk_limits: Optional[Dict[str, float]] = None,
                   market_data_health: Optional[Dict[str, Any]] = None,
                   market_stream_health: Optional[Dict[str, Any]] = None,
                   now: Optional[float] = None) -> Dict[str, Any]:
    """取数（可注入）。每个源独立 try，失败只影响该源的 `source_ok`。"""
    sources: Dict[str, bool] = {}

    if venue_health is None:
        try:
            from r20_backend.dependencies import DATA_DIR as _DATA_DIR
            base = Path(data_dir) if data_dir is not None else Path(_DATA_DIR)
        except Exception:
            base = Path(data_dir or "data")
        venue_health = collect_venue_health(base)
    sources["venue_health"] = venue_health is not None

    if model_stats is None:
        try:
            from r20_gateway.publisher import DB_PATH
            from r20_gateway.store import GatewayStore
            model_stats = collect_model_stats(GatewayStore(DB_PATH))
        except Exception:
            model_stats = None
    sources["model_calls"] = model_stats is not None

    if risk_limits is None:
        risk_limits = collect_risk_limits()
    sources["risk_limits"] = risk_limits is not None

    if market_data_health is None:
        try:
            from r20_backend.dependencies import DATA_DIR as _MD_DATA_DIR
            md_base = Path(data_dir) if data_dir is not None else Path(_MD_DATA_DIR)
        except Exception:
            md_base = Path(data_dir or "data")
        market_data_health = collect_market_data_health(md_base / "market_data_health.json")
    sources["market_data"] = market_data_health is not None

    if market_stream_health is None:
        try:
            from r20_backend.dependencies import DATA_DIR as _MS_DATA_DIR
            ms_base = Path(data_dir) if data_dir is not None else Path(_MS_DATA_DIR)
        except Exception:
            ms_base = Path(data_dir or "data")
        market_stream_health = collect_market_stream_health(ms_base / "market_stream_health.json")
    sources["market_stream"] = market_stream_health is not None

    return {
        "generated_at": float(now if now is not None else time.time()),
        "sources": sources,
        "venue_health": venue_health or {},
        "model_stats": model_stats or {},
        "risk_limits": risk_limits or {},
        "market_data_health": market_data_health or {},
        "market_stream_health": market_stream_health or {},
    }


def render_prometheus(snapshot: Dict[str, Any]) -> str:
    """纯函数：快照 → Prometheus 文本 exposition（0.0.4）。

    ⚠️ **每个指标族的 HELP/TYPE 只能出现一次**：Prometheus 文本解析器对同一
    metric name 的第二条 `# HELP`/`# TYPE` 会**直接报错并丢弃整次抓取**
    （text format parsing error: second HELP line for metric name）。所以这里先按
    族聚合样本、再逐族输出 —— 而不是"每条样本前都印一遍 HELP/TYPE"
    （第一版就是这么写的，本机实跑才抓到）。
    """
    families: "Dict[str, Dict[str, Any]]" = {}

    def emit(name: str, value: Any, labels: Optional[Iterable[Tuple[str, Any]]] = None,
             help_text: str = "", type_text: str = "gauge") -> None:
        text = _fmt(value)
        if text is None:
            return
        family = families.setdefault(name, {"help": help_text, "type": type_text, "lines": []})
        if labels:
            rendered = ",".join(f'{k}="{_label_value(v)}"' for k, v in labels)
            family["lines"].append(f"{name}{{{rendered}}} {text}")
        else:
            family["lines"].append(f"{name} {text}")

    emit("r20_up", 1, help_text="R20 后端进程存活（恒 1；抓不到即 scrape 失败）")

    sources = snapshot.get("sources") or {}
    for source in sorted(sources):
        emit("r20_metrics_source_ok", 1 if sources.get(source) else 0,
             [("source", source), ("required", "1" if source in REQUIRED_SOURCES else "0")],
             help_text="各数据源本次取数是否成功（required=1 缺失才算故障；required=0 是可选源）")

    emit("r20_metrics_generated_at_timestamp_seconds", snapshot.get("generated_at"),
         help_text="本快照生成时刻（epoch 秒）")

    health = snapshot.get("venue_health") or {}
    updated = _parse_utc_seconds(health.get("updated_utc"))
    if updated is not None:
        emit("r20_venue_health_updated_timestamp_seconds", updated,
             help_text="场所健康快照写入时刻（epoch 秒；可算 age 判过期）")
    for venue in sorted((health.get("venues") or {})):
        info = (health.get("venues") or {}).get(venue)
        if not isinstance(info, dict):
            continue
        emit("r20_venue_instruments_ok", len(info.get("ok") or []), [("venue", venue)],
             help_text="该所本轮取数成功的标的数")
        emit("r20_venue_instruments_failed", len(info.get("failed") or {}), [("venue", venue)],
             help_text="该所本轮取数失败的标的数（>0 表示该所数据不全）")
        emit("r20_venue_latency_avg_ms", info.get("avg_ms"), [("venue", venue)],
             help_text="该所平均取数延时（毫秒）")
        emit("r20_venue_testnet", 1 if info.get("testnet") else 0, [("venue", venue)],
             help_text="该所是否演示盘（1=DEMO 数据，勿与实盘曲线混淆）")

    stats = snapshot.get("model_stats") or {}
    emit("r20_model_calls_total", stats.get("total_calls"),
         help_text="大模型调用累计次数", type_text="counter")
    emit("r20_model_calls_successful_total", stats.get("successful_calls"),
         help_text="大模型调用累计成功次数", type_text="counter")
    emit("r20_model_call_duration_ms_avg", stats.get("avg_duration_ms"),
         help_text="大模型调用平均耗时（毫秒）")
    emit("r20_model_tokens_total", stats.get("total_tokens"),
         help_text="累计 token 消耗（成本观测）", type_text="counter")

    for name in sorted((snapshot.get("risk_limits") or {})):
        emit("r20_risk_limit", (snapshot.get("risk_limits") or {}).get(name), [("name", name)],
             help_text="执行层风控生效值（与引擎同一常量模块）")

    # 行情取数健康（第 137 刀事故的直接闭环：那次"取数全挂 30 小时零信号"，
    # 在这里会表现为 calls 停止增长 + failures 上升 + last_success_age 变大）。
    md = snapshot.get("market_data_health") or {}

    def _seconds(value: Any, digits: int = 6) -> Optional[float]:
        """毫秒 → 秒，并**四舍五入**：`repr()` 会把 83.347ms 印成
        `0.08334699999999999`，抓取器能读但人读不了（可观测性也要给人看）。"""
        num = _number(value)
        return None if num is None else round(num / 1000.0, digits)

    def _age(ms_value: Any, digits: int = 3) -> Optional[float]:
        num = _number(ms_value)
        if num is None:
            return None
        return round(max(0.0, (snapshot.get("generated_at") or time.time()) - num / 1000.0), digits)

    emit("r20_market_data_snapshot_age_seconds", _age(md.get("written_at_ms")),
         help_text="行情健康快照的年龄（秒；worker 断档时会持续变大）")
    for kind in sorted((md.get("calls") or {})):
        emit("r20_market_data_calls_total", (md.get("calls") or {}).get(kind), [("kind", kind)],
             help_text="行情取数调用累计次数（含成功与失败）", type_text="counter")
        emit("r20_market_data_call_failures_total", (md.get("failed_calls") or {}).get(kind),
             [("kind", kind)], help_text="行情取数调用累计失败次数", type_text="counter")
        latency = (md.get("latency") or {}).get(kind) or {}
        emit("r20_market_data_latency_p50_seconds", _seconds(latency.get("p50_ms")),
             [("kind", kind)], help_text="行情取数耗时中位数（秒）")
        emit("r20_market_data_latency_p95_seconds", _seconds(latency.get("p95_ms")),
             [("kind", kind)], help_text="行情取数耗时 p95（秒；尾延时是卡顿的先行信号）")
        emit("r20_market_data_last_success_age_seconds", _age((md.get("last_success_ms") or {}).get(kind)),
             [("kind", kind)], help_text="该 kind 距上次取数成功的秒数（成功路径永不老化）")
    for kind in sorted((md.get("failures", {}).get("by_kind") or {})):
        emit("r20_market_data_failures_reported_total",
             (md.get("failures", {}).get("by_kind") or {}).get(kind), [("kind", kind)],
             help_text="取数失败计数（与 calls/failures 同源，便于与第 137 刀的口径对齐）",
             type_text="counter")

    # 公共行情流（只读探测；**不常驻** ⇒ 这条族平时可能整块缺失 —— 那是"没在跑"，
    # 不是"流坏了"；两者由 source_ok + 快照年龄共同区分（别把"没跑"读成"坏了"）。
    st = snapshot.get("market_stream_health") or {}
    st_written = _number(st.get("written_at_ms"))
    if st_written is not None:
        emit("r20_market_stream_snapshot_age_seconds",
             round(max(0.0, (snapshot.get("generated_at") or time.time()) - st_written / 1000.0), 3),
             help_text="行情流健康快照的年龄（秒）")
    for venue in sorted((st.get("venues") or {})):
        info = (st.get("venues") or {}).get(venue) or {}
        emit("r20_market_stream_frames_total", info.get("frames"), [("venue", venue)],
             help_text="公共行情流收到的帧数（含控制帧/错误帧）", type_text="counter")
        emit("r20_market_stream_ticks_total", info.get("ticks"), [("venue", venue)],
             help_text="公共行情流归一出的 tick 数", type_text="counter")
        emit("r20_market_stream_parse_errors_total", info.get("parse_errors"), [("venue", venue)],
             help_text="帧解析失败数（非 0 说明上游改了格式或我们读错字段）", type_text="counter")
        emit("r20_market_stream_errors_total", info.get("errors"), [("venue", venue)],
             help_text="连接/订阅类错误数（含『已连接但零数据帧』）", type_text="counter")
        tick_age = info.get("tick_age_s")
        # 从未收到 tick ⇒ **不发这一条**（不可判定≠0；发 0 会被读成"刚刚有数据"）
        if tick_age is not None:
            emit("r20_market_stream_tick_age_seconds", tick_age, [("venue", venue)],
                 help_text="该所距上次收到 tick 的秒数（无 tick 时不输出该序列）")

    out: List[str] = []
    for name, family in families.items():
        if family["help"]:
            out.append(f"# HELP {name} {family['help']}")
            out.append(f"# TYPE {name} {family['type']}")
        out.extend(family["lines"])
    return "\n".join(out) + "\n"

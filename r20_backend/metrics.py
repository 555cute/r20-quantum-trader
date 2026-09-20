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

> 反漂移：新增/改名旋钮时，`r20_risk_limit` 的名字取自本模块 `RISK_LIMIT_NAMES`
> 单一清单，取不到的键**跳过而不是补 0** —— 编一个不存在的阈值比不报更危险。
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "RISK_LIMIT_NAMES",
    "build_snapshot",
    "render_prometheus",
    "collect_venue_health",
    "collect_model_stats",
    "collect_risk_limits",
]

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


def build_snapshot(*, data_dir: Optional[Path] = None,
                   venue_health: Optional[Dict[str, Any]] = None,
                   model_stats: Optional[Dict[str, Any]] = None,
                   risk_limits: Optional[Dict[str, float]] = None,
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

    return {
        "generated_at": float(now if now is not None else time.time()),
        "sources": sources,
        "venue_health": venue_health or {},
        "model_stats": model_stats or {},
        "risk_limits": risk_limits or {},
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
             [("source", source)],
             help_text="各指标数据源本次取数是否成功（0=该源数据缺失，不代表系统故障）")

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

    out: List[str] = []
    for name, family in families.items():
        if family["help"]:
            out.append(f"# HELP {name} {family['help']}")
            out.append(f"# TYPE {name} {family['type']}")
        out.extend(family["lines"])
    return "\n".join(out) + "\n"

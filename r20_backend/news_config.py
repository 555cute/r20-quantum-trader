"""News-source selection and local cache views. No network or credential loading."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config_path import env_file_path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = env_file_path(ROOT)
NEWS_CACHE_FILE = ROOT / "data" / "news_sentiment.json"
SOURCE_OPTIONS = [
    {"id": "okx", "name": "OKX 聚合资讯", "description": "通过 OKX News CLI 获取财经快讯与其提供的币种情绪统计；与交易所选择独立。"},
    {"id": "binance", "name": "币安情报中心", "description": "官方公告来自币安公开 CMS；CoinDesk 与 CoinTelegraph 固定 HTTPS RSS 折入同一源。不编正文情绪。媒体标题可展示、进模型，不能单独触发开仓熔断。"},
]
_SOURCE_NAMES = {source["id"]: source["name"] for source in SOURCE_OPTIONS}
_SOURCE_STATES = {"ok", "empty", "error", "stale", "disabled", "pending"}


def normalize_sources(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) for value in values):
        raise ValueError("新闻来源必须为列表")
    names = {value.strip().lower() for value in values}
    unknown = names - _SOURCE_NAMES.keys()
    if unknown:
        raise ValueError("不支持的新闻来源：" + ", ".join(sorted(unknown)))
    return tuple(name for name in _SOURCE_NAMES if name in names)


def selected_sources() -> tuple[str, ...]:
    """Persisted admin settings outrank startup env; absent keeps historical OKX default."""
    value = os.environ.get("R20_NEWS_SOURCES", "okx")
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            if key.strip() == "R20_NEWS_SOURCES":
                value = raw.strip().strip('"').strip("'")
    return normalize_sources([part.strip() for part in value.split(",") if part.strip()])


def _published_time(row: dict[str, Any]) -> int:
    try:
        return int(row.get("published_at") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def select_news_items(rows: list[dict[str, Any]], sources: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    """Reserve up to three items per enabled source, then fill by recency."""
    if not sources or limit <= 0:
        return []
    candidates = sorted((row for row in rows if row.get("source") in sources), key=_published_time, reverse=True)
    quota = min(3, limit // len(sources))
    counts = dict.fromkeys(sources, 0)
    selected: set[int] = set()
    for index, row in enumerate(candidates):
        source = row["source"]
        if counts[source] < quota:
            selected.add(index)
            counts[source] += 1
    for index in range(len(candidates)):
        if len(selected) >= limit:
            break
        selected.add(index)
    return [row for index, row in enumerate(candidates) if index in selected]


def filter_snapshot(snapshot: dict[str, Any], sources: tuple[str, ...], *, limit: int = 10) -> dict[str, Any]:
    """Disabled-source cache is never evidence, including after a configuration change."""
    enabled = normalize_sources(sources)
    news = []
    buckets = snapshot.get("source_news")
    if isinstance(buckets, dict):
        raw_items = [
            {**item, "source": source}
            for source in enabled
            for item in (buckets.get(source) if isinstance(buckets.get(source), list) else [])
            if isinstance(item, dict)
        ]
    else:
        raw_items = snapshot.get("latest_news")
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict):
            continue
        # Cache written before source selection existed came exclusively from OKX.
        source = item.get("source") or "okx"
        if source not in enabled:
            continue
        row = {**item, "source": source, "source_name": _SOURCE_NAMES[source]}
        try:
            url = urlsplit(str(row.get("url") or ""))
            if url.scheme not in {"http", "https"} or not url.netloc:
                row["url"] = ""
        except ValueError:
            row["url"] = ""
        news.append(row)



    previous_status = snapshot.get("source_status") or {}
    source_status = {}
    for source in _SOURCE_NAMES:
        count = sum(row["source"] == source for row in news)
        old = previous_status.get(source, {}) if isinstance(previous_status, dict) else {}
        old = old if isinstance(old, dict) else {}
        state = old.get("status")
        if source not in enabled:
            source_status[source] = {"status": "disabled", "count": 0, "updated_at": None, "error": None}
            continue
        if not isinstance(state, str) or state not in _SOURCE_STATES or state == "disabled":
            state = "stale" if count else "pending"
        source_status[source] = {**old, "status": state, "count": count,
                                 "updated_at": old.get("updated_at"), "error": old.get("error")}
    states = [source_status[source]["status"] for source in enabled]
    if not states:
        status = "disabled"
    elif all(state in {"ok", "empty"} for state in states):
        status = "ok"
    elif all(state == "pending" for state in states):
        status = "pending"
    elif news or any(state in {"ok", "empty", "stale"} for state in states):
        status = "partial"
    else:
        status = "error"

    for row in news:
        row["stale"] = bool(row.get("stale") or source_status[row["source"]]["status"] in {"stale", "error"})
    raw_sentiments = snapshot.get("coins_sentiment")
    sentiments = {
        coin: value for coin, value in raw_sentiments.items()
        if isinstance(value, dict) and any(
            isinstance(value.get(key), (int, float)) and value[key] > 0
            for key in ("mentions", "bull_cnt", "bear_cnt")
        )
    } if "okx" in enabled and isinstance(raw_sentiments, dict) else {}
    sentiment_stale = bool(sentiments and snapshot.get("sentiment_stale"))
    latest_news = select_news_items(news, enabled, limit)
    result = {
        **snapshot,
        "enabled_sources": list(enabled),
        "source_status": source_status,
        "status": status,
        "latest_news": latest_news,
        "source_news": {source: [row for row in news if row["source"] == source] for source in enabled},
        "coins_sentiment": sentiments,
        "sentiment_stale": sentiment_stale,
        "macro_sentiment": snapshot.get("macro_sentiment") if sentiments else "无可验证情绪数据",
        "news_fresh_at": latest_news[0].get("time") if latest_news else None,
        "stale_sections": sentiment_stale or any(row["stale"] for row in news) or any(state in {"stale", "error"} for state in states),
    }
    if "okx" not in enabled or source_status["okx"]["status"] in {"stale", "error", "pending"}:
        result.pop("overall_score", None)
    return result


def load_news_snapshot(path: str | Path | None = None, sources: tuple[str, ...] | None = None, *, limit: int = 10) -> dict[str, Any]:
    cache = Path(path) if path is not None else NEWS_CACHE_FILE
    try:
        snapshot = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    return filter_snapshot(snapshot, sources if sources is not None else selected_sources(), limit=limit)

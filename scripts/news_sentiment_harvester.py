#!/usr/bin/env python3
"""
Crypto news & black-swan circuit-breaker harvester.

Collects only admin-selected sources (OKX CLI and/or Binance public CMS),
normalizes rows, and writes data/news_sentiment.json. Binance contributes
official catalogs plus other CMS media titles/links from the same GET —
no fabricated body or bull/bear scores. Black-swan matching stays the
existing regex set; listing/delisting never opens or closes positions.
Media-only headlines cannot arm the 30-minute new-entry freeze.
"""


import os
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import html
import json
import time
import datetime
import subprocess
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime


from instrument_pool import load_instruments
from r20_backend.news_config import SOURCE_OPTIONS, selected_sources, select_news_items

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
NEWS_CACHE_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
CIRCUIT_BREAKER_FILE = os.path.join(DATA_DIR, "circuit_breaker.json")

SOURCE_ORDER = tuple(item["id"] for item in SOURCE_OPTIONS)
SOURCE_NAMES = {item["id"]: item["name"] for item in SOURCE_OPTIONS}

# Institutional-Grade Extreme Black-Swan Regular Expressions
# Only trigger circuit breaker for existential, catastrophic, systemic market shocks
BLACK_SWAN_PATTERNS = [
    (r"(USDT|USDC|DAI).*(严重脱锚|脱锚幅度|depeg|脱锚超过|跌破0\.9[0-8])", "头部稳定币恶性脱锚危机"),
    (r"(币安|OKX|Coinbase|Kraken).*(暂停全部提现|停止提币|申请破产重组|破产倒闭|发生严重挤兑)", "主流中心化交易所崩盘挤兑"),
    (r"(以太坊主网|比特币网络|Solana网络|BNB Chain).*(遭遇51%攻击|全网瘫痪停机|紧急硬分叉回滚)", "顶级底层公链系统性故障/51%攻击"),
    (r"(全面取缔所有加密|宣布比特币非法|宣布数字货币交易非法|爆发核危机|宣战)", "国家级极端不可抗力/战争")
]

_HARVEST_START = time.time()
# OKX CLI share of the trader's 25s harvest window (retries included).
UPSTREAM_BUDGET_SECONDS = 9.0
# Binance public CMS is a single GET, independent of the OKX CLI budget.
BINANCE_HTTP_TIMEOUT = 5.0

# The OKX CLI refuses *all* news endpoints while a demo/simulated profile is
# selected ("News features are not available in demo/simulated trading mode").
# News is public market data and is unrelated to order routing, so the harvest
# always runs against the live data profile; trading env is left untouched.
DEMO_ENV_FLAGS = ("OKX_DEMO", "OKX_SIMULATED", "R20_OKX_ENV", "OKX_ENV")

BINANCE_CMS_HOST = "www.binance.com"
BINANCE_CMS_URL = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    "?type=1&pageNo=1&pageSize=20"
)
BINANCE_DETAIL_PREFIX = "https://www.binance.com/en/support/announcement/detail/"
# 48 listing, 49 latest rules/news, 161 delisting, 157 maintenance, 51 API updates.
# CMS type=1 has no third-party media catalogs. Media is a fixed HTTPS RSS allowlist.
BINANCE_OFFICIAL_CATALOGS = {48, 49, 161, 157, 51}
BINANCE_SKIP_CATALOGS = {93, 128}
BINANCE_MEDIA_FEEDS = (
    {
        "id": "coindesk",
        "name": "CoinDesk",
        "host": "www.coindesk.com",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    },
    {
        "id": "cointelegraph",
        "name": "CoinTelegraph",
        "host": "cointelegraph.com",
        "url": "https://cointelegraph.com/rss",
    },
)


_BINANCE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ALL_COIN_TOKENS = {"ALL", "*", "全部", "全部币种"}
MACRO_UNAVAILABLE = "无可验证情绪数据"


def _news_env() -> dict:
    env = dict(os.environ)
    for flag in DEMO_ENV_FLAGS:
        env.pop(flag, None)
    return env


def _remaining_budget() -> float:
    return UPSTREAM_BUDGET_SECONDS - (time.time() - _HARVEST_START)


def run_json_cmd(cmd: str, timeout: int = 5, retries: int = 1):
    """Run an OKX CLI command and parse JSON. Transient upstream failures are retried
    with backoff and logged, so a single hiccup cannot silently freeze the news feed.
    Retries degrade to a single short attempt once the global upstream budget is spent."""
    last_err = ""
    last_empty = None
    for attempt in range(retries + 1):
        elapsed = time.time() - _HARVEST_START
        if elapsed >= UPSTREAM_BUDGET_SECONDS:
            timeout = min(timeout, 3)
            retries = attempt  # no further attempts
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                 timeout=timeout, env=_news_env())
            out = (res.stdout or "").strip()
            if out:
                try:
                    parsed = json.loads(out)
                except Exception as je:
                    last_err = f"non-JSON stdout: {out[:120]}"
                else:
                    if isinstance(parsed, dict):
                        det = parsed.get("details")
                        if isinstance(det, list) and not det:
                            last_empty = parsed
                            last_err = "empty details[]"
                        else:
                            return parsed
                    else:
                        return parsed
            else:
                last_err = (res.stderr or "").strip()[:200] or f"empty stdout (rc={res.returncode})"
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(min(1.5 * (attempt + 1), max(0.5, UPSTREAM_BUDGET_SECONDS - (time.time() - _HARVEST_START))))
    if last_empty is not None:
        return last_empty
    print(f"[news-harvester] WARN upstream failed after {retries + 1} attempts: {cmd[:60]} -> {last_err}", file=sys.stderr)
    return None


def trigger_circuit_breaker(headline: str, keyword: str):
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    now_ts = int(time.time())

    cb_data = {
        "active": True,
        "triggered_at": now_bj.strftime("%Y-%m-%d %H:%M:%S"),
        "expires_at_ts": now_ts + 1800,  # 30 minutes freeze
        "headline": headline,
        "keyword": keyword,
        "action": "暂停新开仓 30 分钟，启动存量持仓保本防御"
    }

    os.makedirs(os.path.dirname(CIRCUIT_BREAKER_FILE) or ".", exist_ok=True)
    with open(CIRCUIT_BREAKER_FILE, "w", encoding="utf-8") as f:
        json.dump(cb_data, f, ensure_ascii=False, indent=2)

    try:
        from qq_notifier import notify_circuit_breaker
        notify_circuit_breaker(headline, f"命中突发高危词汇【{keyword}】")
    except Exception:
        pass
    print(f"🚨 黑天鹅熔断已激活: {headline}")


def is_circuit_breaker_active():
    if os.path.exists(CIRCUIT_BREAKER_FILE):
        try:
            with open(CIRCUIT_BREAKER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("active") and time.time() < data.get("expires_at_ts", 0):
                    return True, data
        except Exception:
            pass
    return False, {}


def _strip_html(text) -> str:
    raw = html.unescape(str(text or ""))
    return re.sub(r"<[^>]+>", "", raw).strip()


def _safe_http_url(url) -> str:
    raw = str(url or "").strip()
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"}:
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or parts.username or parts.password:
        return ""
    return raw


def _binance_article_url(code: str) -> str:
    token = str(code or "").strip()
    if not _BINANCE_CODE_RE.fullmatch(token):
        return ""
    return f"{BINANCE_DETAIL_PREFIX}{token}"


def _as_unix_ms(value):
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return None
    if number <= 0:
        return None
    if number < 10_000_000_000:
        number *= 1000
    return number


def _prefixed_id(source: str, raw_id) -> str:
    token = str(raw_id or "").strip()
    if not token:
        return ""
    prefix = f"{source}:"
    if token.startswith(prefix):
        return token
    return prefix + token


def _row_source(item: dict) -> str:
    source = item.get("source")
    if source in SOURCE_NAMES:
        return source
    return "okx"


def _match_coins(text: str, target_coins: list) -> list:
    hits = []
    blob = str(text or "")
    for coin in target_coins:
        name = str(coin or "").strip()
        if not name or name.upper() in _ALL_COIN_TOKENS:
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", blob, re.IGNORECASE):
            hits.append(name)
    return hits


def _filter_okx_coins(ccy_list, target_coins: list) -> list:
    if not isinstance(ccy_list, list):
        return []
    allowed = set(target_coins)
    out = []
    for raw in ccy_list:
        name = str(raw or "").strip()
        if not name or name.upper() in _ALL_COIN_TOKENS:
            continue
        if name in allowed and name not in out:
            out.append(name)
    return out


def _bj_time(published_ms, tz_bj) -> str:
    if not published_ms:
        return "--"
    seconds = int(published_ms) / 1000.0
    return datetime.datetime.fromtimestamp(seconds, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S")


def _base_row(source: str, *, item_id: str, published_ms, title: str, summary: str,
              coins: list, platforms: list, importance, url: str, category: str,
              stale: bool, authority: str = "official") -> dict:
    kind = "official" if str(authority) != "media" else "media"
    return {
        "id": item_id,
        "time": _bj_time(published_ms, datetime.timezone(datetime.timedelta(hours=8))),
        "title": title,
        "summary": summary,
        "coins": coins,
        "platforms": platforms,
        "importance": importance,
        "url": url,
        "source": source,
        "source_name": SOURCE_NAMES.get(source, source),
        "category": category,
        "published_at": int(published_ms) if published_ms else 0,
        "stale": bool(stale),
        "authority": kind,
    }



def _load_previous_cache() -> dict:
    if not os.path.exists(NEWS_CACHE_FILE):
        return {}
    try:
        with open(NEWS_CACHE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _adopt_cached_row(item: dict, source: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    if _row_source(item) != source:
        return None
    row = dict(item)
    row["source"] = source
    row["source_name"] = SOURCE_NAMES.get(source, source)
    row["stale"] = True
    row["id"] = _prefixed_id(source, row.get("id"))
    if not row["id"]:
        return None
    row["url"] = _safe_http_url(row.get("url"))
    published = _as_unix_ms(row.get("published_at"))
    row["published_at"] = published or 0
    return row


def _cap_source_rows(rows: list) -> list:
    seen = set()
    ordered = []
    for row in sorted(rows, key=lambda item: int(item.get("published_at") or 0), reverse=True):
        rid = str(row.get("id") or "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        ordered.append(row)
        if len(ordered) >= 20:
            break
    return ordered


def _cached_source_rows(previous: dict, source: str) -> list:
    buckets = previous.get("source_news")
    raw_items = None
    if isinstance(buckets, dict) and isinstance(buckets.get(source), list):
        raw_items = buckets.get(source)
    elif isinstance(previous.get("latest_news"), list):
        raw_items = previous.get("latest_news")
    rows = []
    for item in raw_items or []:
        row = _adopt_cached_row(item, source)
        if row:
            rows.append(row)
    return _cap_source_rows(rows)


def _real_sentiments(sentiments, target_coins: list) -> dict:
    if not isinstance(sentiments, dict):
        return {}
    allowed = set(target_coins)
    real = {}
    for name, payload in sentiments.items():
        if name not in allowed or not isinstance(payload, dict):
            continue
        try:
            mentions = int(payload.get("mentions") or 0)
            bull_cnt = int(payload.get("bull_cnt") or 0)
            bear_cnt = int(payload.get("bear_cnt") or 0)
        except (TypeError, ValueError):
            continue
        if mentions > 0 or (bull_cnt + bear_cnt) > 0:
            real[name] = payload
    return real


def _status_block(status: str, count: int, updated_at, error):
    return {
        "status": status,
        "count": int(count),
        "updated_at": updated_at,
        "error": error,
    }


def _iter_catalog_nodes(node):
    if isinstance(node, list):
        for item in node:
            yield from _iter_catalog_nodes(item)
        return
    if not isinstance(node, dict):
        return
    yield node
    for key in ("catalogs", "children", "childCatalogs", "subCatalogs"):
        child = node.get(key)
        if child:
            yield from _iter_catalog_nodes(child)


def _fetch_binance_cms():
    parsed = urllib.parse.urlsplit(BINANCE_CMS_URL)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != BINANCE_CMS_HOST:
        raise ValueError("refusing non-Binance CMS host")
    request = urllib.request.Request(
        BINANCE_CMS_URL,
        headers={
            "User-Agent": "Mozilla/5.0 R20NewsHarvester",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=BINANCE_HTTP_TIMEOUT) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"URL error: {exc.reason}") from exc
    if not raw:
        raise RuntimeError("empty CMS body")
    text = raw.decode("utf-8", errors="replace").lstrip()
    if text.startswith("<"):
        raise RuntimeError("CMS challenge or HTML page")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise RuntimeError("CMS non-JSON body") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("CMS payload is not an object")
    code = str(payload.get("code") or "")
    if code != "000000":
        raise RuntimeError(f"CMS code {code or 'missing'}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("CMS data missing")
    catalogs = data.get("catalogs")
    if catalogs is None:
        raise RuntimeError("CMS catalogs missing")
    if not isinstance(catalogs, list):
        raise RuntimeError("CMS catalogs invalid")
    return catalogs


def _fetch_fixed_https(url: str, host: str, accept: str) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != host.lower():
        raise ValueError("refusing non-allowlisted host")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 R20NewsHarvester",
            "Accept": accept,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=BINANCE_HTTP_TIMEOUT) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"URL error: {exc.reason}") from exc
    if not raw:
        raise RuntimeError("empty body")
    return raw


def _rss_published_ms(text) -> int | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return _as_unix_ms(raw)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return int(parsed.timestamp() * 1000)


def _parse_rss_feed(feed: dict, xml_text: str, target_coins: list) -> list:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag != "item":
            continue
        fields = {}
        for child in list(node):
            fields[child.tag.rsplit("}", 1)[-1].lower()] = (child.text or "")
        title = _strip_html(fields.get("title"))
        link = _safe_http_url(fields.get("link") or fields.get("guid"))
        published_ms = _rss_published_ms(fields.get("pubdate") or fields.get("published"))
        if not title or not link or not published_ms:
            continue
        guid = _strip_html(fields.get("guid") or link)
        item_id = _prefixed_id("binance", f"{feed['id']}:{guid}"[:160])
        summary = _strip_html(fields.get("description") or "")[:400]
        items.append(_base_row(
            "binance",
            item_id=item_id,
            published_ms=published_ms,
            title=title,
            summary=summary,
            coins=_match_coins(title + " " + summary, target_coins),
            platforms=[feed["name"]],
            importance="",
            url=link,
            category=feed["name"],
            stale=False,
            authority="media",
        ))
        if len(items) >= 10:
            break


    return items


def _harvest_binance_media(target_coins: list) -> list:
    collected = []
    for feed in BINANCE_MEDIA_FEEDS:
        try:
            raw = _fetch_fixed_https(feed["url"], feed["host"], "application/rss+xml, application/xml, text/xml")
            text = raw.decode("utf-8", errors="replace")
            collected.extend(_parse_rss_feed(feed, text, target_coins))
        except Exception:
            continue
    return collected


def _harvest_binance(target_coins: list) -> dict:
    try:
        catalogs = _fetch_binance_cms()
    except Exception as exc:
        return {"status": "error", "items": [], "error": str(exc)[:200]}

    items = []
    seen = set()
    for node in _iter_catalog_nodes(catalogs):
        try:
            catalog_id = int(node.get("catalogId"))
        except (TypeError, ValueError):
            continue
        if catalog_id in BINANCE_SKIP_CATALOGS or catalog_id not in BINANCE_OFFICIAL_CATALOGS:
            continue
        category = _strip_html(node.get("catalogName") or catalog_id)
        articles = node.get("articles")
        if not isinstance(articles, list):
            continue
        for article in articles:
            if not isinstance(article, dict):
                continue
            article_type = article.get("type")
            if article_type not in (None, "", 1, "1"):
                continue
            title = _strip_html(article.get("title"))
            code = str(article.get("code") or "").strip()
            published_ms = _as_unix_ms(article.get("releaseDate"))
            if not title or not published_ms:
                continue
            url = _binance_article_url(code)
            if not url:
                continue
            raw_id = article.get("id")
            item_id = _prefixed_id("binance", raw_id if raw_id not in (None, "") else code)
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            items.append(_base_row(
                "binance",
                item_id=item_id,
                published_ms=published_ms,
                title=title,
                summary="",
                coins=_match_coins(title, target_coins),
                platforms=["Binance"],
                importance="",
                url=url,
                category=category,
                stale=False,
                authority="official",
            ))
    for row in _harvest_binance_media(target_coins):
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        items.append(row)
    return {
        "status": "ok" if items else "empty",
        "items": items,
        "error": None,
    }




def _normalize_okx_item(item: dict, target_coins: list) -> dict | None:
    if not isinstance(item, dict):
        return None
    title = _strip_html(item.get("title"))
    published_ms = _as_unix_ms(item.get("cTime") or item.get("published_at"))
    item_id = _prefixed_id("okx", item.get("id"))
    if not title or not item_id or not published_ms:
        return None
    summary = _strip_html(item.get("summary"))
    return _base_row(
        "okx",
        item_id=item_id,
        published_ms=published_ms,
        title=title,
        summary=summary,
        coins=_filter_okx_coins(item.get("ccyList"), target_coins),
        platforms=item.get("platformList") if isinstance(item.get("platformList"), list) else [],
        importance=item.get("importance", "high"),
        url=_safe_http_url(item.get("sourceUrl")),
        category=_strip_html(item.get("category") or ""),
        stale=False,
        authority="media",
    )



def _parse_okx_sentiments(sent_res, target_coins: list) -> dict:
    details = None
    if isinstance(sent_res, list) and sent_res and isinstance(sent_res[0], dict) and "details" in sent_res[0]:
        details = sent_res[0].get("details")
    elif isinstance(sent_res, dict):
        details = sent_res.get("details")
    if not isinstance(details, list):
        return {}
    out = {}
    for row in details:
        if not isinstance(row, dict):
            continue
        ccy = str(row.get("ccy") or "").strip()
        if ccy not in target_coins:
            continue
        sent = row.get("sentiment") if isinstance(row.get("sentiment"), dict) else {}
        try:
            bull_cnt = int(sent.get("bullishCnt") or 0)
            bear_cnt = int(sent.get("bearishCnt") or 0)
            neutral_cnt = int(sent.get("neutralCnt") or 0)
            mentions = int(row.get("mentionCnt") or 0)
        except (TypeError, ValueError):
            continue
        raw_bull = sent.get("bullishRatio")
        raw_bear = sent.get("bearishRatio")
        has_ratio = raw_bull is not None and raw_bear is not None
        total_dir = bull_cnt + bear_cnt
        if mentions <= 0 and total_dir <= 0 and not has_ratio:
            continue
        try:
            if has_ratio:
                bull_ratio = float(raw_bull)
                bear_ratio = float(raw_bear)
            elif total_dir > 0:
                bull_ratio = bull_cnt / total_dir
                bear_ratio = bear_cnt / total_dir
            else:
                continue
        except (TypeError, ValueError):
            continue
        ls_ratio = round(bull_cnt / max(1, bear_cnt), 2)
        if total_dir > 0:
            bull_share = f"{bull_cnt / total_dir * 100:.1f}%"
            bear_share = f"{bear_cnt / total_dir * 100:.1f}%"
        else:
            bull_share = f"{bull_ratio * 100:.1f}%"
            bear_share = f"{bear_ratio * 100:.1f}%"
        label = sent.get("label") or "neutral"
        net_sentiment = bull_ratio - bear_ratio
        out[ccy] = {
            "ccy": ccy,
            "label": label,
            "bullish_ratio": bull_share,
            "bearish_ratio": bear_share,
            "bullish_pct": f"{bull_ratio * 100:.1f}%",
            "bearish_pct": f"{bear_ratio * 100:.1f}%",
            "long_short_ratio": f"{ls_ratio:.2f}",
            "bull_cnt": bull_cnt,
            "bear_cnt": bear_cnt,
            "neutral_cnt": neutral_cnt,
            "mentions": mentions,
            "sentiment_factor_score": round(net_sentiment * 0.8, 2),
        }
    return out


def _harvest_okx(target_coins: list) -> dict:
    news_res_latest = run_json_cmd("okx news latest --lang zh-CN --limit 15 --json")
    news_res_imp = run_json_cmd("okx news important --lang zh-CN --limit 15 --json")

    raw_news = []
    seen_ids = set()
    for blob in (news_res_latest, news_res_imp):
        details = blob.get("details") if isinstance(blob, dict) else None
        if not isinstance(details, list):
            continue
        for item in details:
            nid = str((item or {}).get("id", "")) if isinstance(item, dict) else ""
            if not nid or nid in seen_ids:
                continue
            seen_ids.add(nid)
            raw_news.append(item)

    items = []
    seen_prefixed = set()
    for item in raw_news:
        row = _normalize_okx_item(item, target_coins)
        if not row or row["id"] in seen_prefixed:
            continue
        seen_prefixed.add(row["id"])
        items.append(row)

    if items:
        news_status = "ok"
        news_error = None
    elif news_res_latest is not None and news_res_imp is not None:
        news_status = "empty"
        news_error = None
    else:
        news_status = "error"
        news_error = "OKX news CLI failed"

    sentiments = {}
    sentiment_error = None
    coins_str = ",".join(target_coins)
    if _remaining_budget() <= 0:
        sentiment_error = "upstream budget exhausted"
        sent_res = None
    else:
        sent_res = run_json_cmd(f"okx news coin-sentiment --coins {coins_str} --json")
    if sent_res is None:
        if sentiment_error is None:
            sentiment_error = "OKX coin-sentiment failed"
    else:
        sentiments = _parse_okx_sentiments(sent_res, target_coins)
        if not sentiments:
            sentiment_error = "OKX coin-sentiment had no verifiable stats"

    return {
        "status": news_status,
        "items": items,
        "error": news_error,
        "sentiments": sentiments,
        "sentiment_error": sentiment_error,
    }


def _maybe_clear_expired_breaker():
    if not os.path.exists(CIRCUIT_BREAKER_FILE):
        return
    try:
        with open(CIRCUIT_BREAKER_FILE, "r", encoding="utf-8") as handle:
            cb_data = json.load(handle)
        if cb_data.get("active") and time.time() >= cb_data.get("expires_at_ts", 0):
            cb_data["active"] = False
            with open(CIRCUIT_BREAKER_FILE, "w", encoding="utf-8") as handle:
                json.dump(cb_data, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _evaluate_black_swans(rows: list, now_ts: float):
    triggered = None
    for row in rows:
        if row.get("stale"):
            continue
        if str(row.get("authority") or "official") == "media":
            continue
        published_ms = int(row.get("published_at") or 0)
        if published_ms <= 0:
            continue
        age = now_ts - (published_ms / 1000.0)
        if age < 0 or age >= 900:
            continue
        full_text = f"{row.get('title', '')} {row.get('summary', '')}"
        for pattern, threat_name in BLACK_SWAN_PATTERNS:
            if re.search(pattern, full_text, re.IGNORECASE):
                triggered = (row.get("title") or "", threat_name)
                break
        if triggered:
            break
    if triggered:
        trigger_circuit_breaker(triggered[0], triggered[1])
    else:
        _maybe_clear_expired_breaker()



def _overall_status(enabled: tuple, source_status: dict) -> str:
    if not enabled:
        return "disabled"
    states = [source_status[source]["status"] for source in enabled]
    if all(state in {"ok", "empty"} for state in states):
        return "ok"
    if all(state == "pending" for state in states):
        return "pending"
    if any(state in {"ok", "empty", "stale"} for state in states):
        return "partial"
    return "error"


def _synthesize_macro(cb_active: bool, coin_sentiments: dict) -> str:
    if cb_active:
        return "🚨 避险熔断中"
    if not coin_sentiments:
        return MACRO_UNAVAILABLE
    bull_count = sum(1 for s in coin_sentiments.values() if float(s.get("sentiment_factor_score", 0) or 0) > 0.25)
    bear_count = sum(1 for s in coin_sentiments.values() if float(s.get("sentiment_factor_score", 0) or 0) < -0.1)
    if bull_count > bear_count:
        return "偏多震荡"
    if bear_count > bull_count:
        return "偏空承压"
    return "中性平衡"


def _write_cache(payload: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_file = NEWS_CACHE_FILE + f".tmp.{os.getpid()}"
    with open(tmp_file, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_file, NEWS_CACHE_FILE)


def fetch_and_analyze_news_sentiment():
    global _HARVEST_START
    _HARVEST_START = time.time()
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    now_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")
    now_ts = time.time()

    enabled = selected_sources()
    previous = _load_previous_cache()
    target_coins = [item["name"] for item in load_instruments()]

    source_news = {source: [] for source in SOURCE_ORDER}
    source_status = {}
    coin_sentiments = {}
    sentiment_stale = False
    breaker_rows = []

    if "okx" in enabled:
        okx = _harvest_okx(target_coins)
        if okx["status"] == "ok":
            rows = _cap_source_rows(okx["items"])
            breaker_rows.extend(okx["items"])
            status = "ok"
        elif okx["status"] == "empty":
            rows = []
            status = "empty"
        else:
            rows = _cached_source_rows(previous, "okx")
            status = "stale" if rows else "error"
        prev_status = previous.get("source_status") if isinstance(previous.get("source_status"), dict) else {}
        prev_okx = prev_status.get("okx") if isinstance(prev_status.get("okx"), dict) else {}
        source_status["okx"] = _status_block(
            status,
            len(rows),
            now_str if okx["status"] in {"ok", "empty"} else prev_okx.get("updated_at"),
            None if status in {"ok", "empty"} else okx.get("error"),
        )
        source_news["okx"] = rows
        if okx.get("sentiments"):
            coin_sentiments = okx["sentiments"]
        else:
            coin_sentiments = _real_sentiments(previous.get("coins_sentiment"), target_coins)
            if coin_sentiments:
                sentiment_stale = True
            if okx.get("sentiment_error") and not coin_sentiments:
                sentiment_stale = True
    else:
        source_status["okx"] = _status_block("disabled", 0, None, None)

    if "binance" in enabled:
        bn = _harvest_binance(target_coins)
        if bn["status"] == "ok":
            rows = _cap_source_rows(bn["items"])
            breaker_rows.extend(bn["items"])
            status = "ok"
        elif bn["status"] == "empty":
            rows = []
            status = "empty"
        else:
            rows = _cached_source_rows(previous, "binance")
            status = "stale" if rows else "error"
        prev_bn = (previous.get("source_status") or {}).get("binance", {}) if isinstance(previous.get("source_status"), dict) else {}
        source_status["binance"] = _status_block(
            status,
            len(rows),
            now_str if bn["status"] in {"ok", "empty"} else (prev_bn.get("updated_at") if isinstance(prev_bn, dict) else None),
            None if status in {"ok", "empty"} else bn.get("error"),
        )
        source_news["binance"] = rows
    else:
        source_status["binance"] = _status_block("disabled", 0, None, None)

    collected = []
    for source in SOURCE_ORDER:
        if source in enabled:
            collected.extend(source_news[source])
    latest_news = select_news_items(collected, enabled, 10)

    _evaluate_black_swans(breaker_rows, now_ts)


    cb_active, cb_info = is_circuit_breaker_active()
    if "okx" not in enabled:
        coin_sentiments = {}
        sentiment_stale = False
    macro_env = _synthesize_macro(cb_active, coin_sentiments)

    overall = _overall_status(enabled, source_status)
    stale_sections = bool(
        sentiment_stale
        or any(row.get("stale") for source in enabled for row in source_news[source])
        or any(source_status[source]["status"] in {"stale", "error"} for source in enabled)
    )

    payload = {
        "timestamp": now_str,
        "updated_at": now_str,
        "macro_sentiment": macro_env,
        "circuit_breaker": cb_info if cb_active else {"active": False},
        "coins_sentiment": coin_sentiments,
        "sentiment_stale": bool(sentiment_stale),
        "latest_news": latest_news,
        "source_news": source_news,
        "news_fresh_at": (latest_news[0]["time"] if latest_news else None),
        "enabled_sources": [source for source in SOURCE_ORDER if source in enabled],
        "source_status": source_status,
        "status": overall,
        "stale_sections": stale_sections,
    }

    try:
        _write_cache(payload)
    except Exception as exc:
        print(f"Failed to write news cache: {exc}")

    return payload


if __name__ == "__main__":
    res = fetch_and_analyze_news_sentiment()
    flag = " ⚠️STALE(upstream empty, serving last cache)" if res.get("stale_sections") else ""
    print(
        f"✅ News harvest complete. Macro: {res['macro_sentiment']}, "
        f"News Count: {len(res['latest_news'])}{flag} 最新快讯: {res.get('news_fresh_at') or '--'}"
    )

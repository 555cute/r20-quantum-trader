#!/usr/bin/env python3
"""
OKX Crypto News & Black-Swan Circuit Breaker Harvester
Features:
1. Harvest high-impact crypto news from OKX (Golden Finance, BlockBeats, TechFlow, WallStreetCN)
2. Aggregate real-time multi-coin social & news sentiment (Bullish vs Bearish Ratio)
3. Detect Black-Swan / Extreme Macro Events and trigger Automatic Circuit Breaker (30-min opening freeze)
4. Push critical alerts to QQ Channel
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

import json
import time
import datetime
import re

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
NEWS_CACHE_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
CIRCUIT_BREAKER_FILE = os.path.join(DATA_DIR, "circuit_breaker.json")
from instrument_pool import load_instruments
TARGET_COINS = [item["name"] for item in load_instruments()]

# Institutional-Grade Extreme Black-Swan Regular Expressions
# Only trigger circuit breaker for existential, catastrophic, systemic market shocks
BLACK_SWAN_PATTERNS = [
    (r"(USDT|USDC|DAI).*(严重脱锚|脱锚幅度|depeg|脱锚超过|跌破0\.9[0-8])", "头部稳定币恶性脱锚危机"),
    (r"(币安|OKX|Coinbase|Kraken).*(暂停全部提现|停止提币|申请破产重组|破产倒闭|发生严重挤兑)", "主流中心化交易所崩盘挤兑"),
    (r"(以太坊主网|比特币网络|Solana网络|BNB Chain).*(遭遇51%攻击|全网瘫痪停机|紧急硬分叉回滚)", "顶级底层公链系统性故障/51%攻击"),
    (r"(全面取缔所有加密|宣布比特币非法|宣布数字货币交易非法|爆发核危机|宣战)", "国家级极端不可抗力/战争")
]

# （US-014 前置）OKX CLI 的 news 抓取通道（run_json_cmd/_news_env/subprocess）已随
# CLI 移除整体删除；news latest/important/coin-sentiment 无公开 V5 等价接口，
# 数据源缺失语义见 fetch_and_analyze_news_sentiment() 内注释与 source_available。

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

def fetch_and_analyze_news_sentiment():
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    now_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")

    # 1. News sources：OKX CLI 已移除（news latest/important/coin-sentiment 均无公开
    #    V5 等价接口）。显式缺失化：不抓取、不以空数据冒充新鲜信号；下方既有
    #    fail-closed 路径会继续以最后有效缓存供页面并标 stale_sections。
    #    注意：依赖新闻流的极端黑天鹅熔断在接入新数据源前处于休眠（仅能由
    #    其它风控层兜底）——这是数据源缺失的显式后果，不是静默降级。
    news_res_latest: dict = {}
    news_res_imp: dict = {}
    
    raw_news_latest = news_res_latest.get("details", []) if isinstance(news_res_latest, dict) else []
    raw_news_imp = news_res_imp.get("details", []) if isinstance(news_res_imp, dict) else []
    
    seen_ids = set()
    raw_news = []
    for item in raw_news_latest + raw_news_imp:
        nid = str(item.get("id", ""))
        if nid and nid not in seen_ids:
            seen_ids.add(nid)
            raw_news.append(item)
            
    # Sort strictly by creation timestamp descending
    raw_news.sort(key=lambda x: int(x.get("cTime", 0) or 0), reverse=True)
    raw_news = raw_news[:20]

    parsed_news = []
    triggered_threat = None

    for item in raw_news:
        c_time = int(item.get("cTime", 0) or 0) / 1000.0
        dt_str = datetime.datetime.fromtimestamp(c_time, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_time > 0 else "--"
        title = item.get("title", "")
        summary = item.get("summary", "")
        full_text = f"{title} {summary}"

        # Only evaluate black-swan patterns for news within last 15 minutes
        if time.time() - c_time < 900:
            for pattern, threat_name in BLACK_SWAN_PATTERNS:
                if re.search(pattern, full_text, re.IGNORECASE):
                    triggered_threat = (title, threat_name)
                    break

        parsed_news.append({
            "id": item.get("id"),
            "time": dt_str,
            "title": title,
            "summary": summary,
            "coins": item.get("ccyList", []),
            "platforms": item.get("platformList", []),
            "importance": item.get("importance", "high"),
            "url": item.get("sourceUrl", "")
        })

    if triggered_threat:
        trigger_circuit_breaker(triggered_threat[0], triggered_threat[1])
    else:
        # If no genuine black-swan is active, ensure circuit breaker is cleared if expired
        if os.path.exists(CIRCUIT_BREAKER_FILE):
            try:
                with open(CIRCUIT_BREAKER_FILE, "r", encoding="utf-8") as f:
                    cb_data = json.load(f)
                if cb_data.get("active") and time.time() >= cb_data.get("expires_at_ts", 0):
                    cb_data["active"] = False
                    with open(CIRCUIT_BREAKER_FILE, "w", encoding="utf-8") as f:
                        json.dump(cb_data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    # 2. Multi-Coin Sentiment：CLI 已移除，无公开 V5 等价（见步骤 1 说明）。
    active_instruments = load_instruments()
    target_coins = [item["name"] for item in active_instruments]
    sent_res: list = []
    coin_sentiments = {}

    # Load existing valid sentiments as fallback to prevent 0-mentions overwrite if API rate limits or drops temporarily
    existing_sentiments = {}
    if os.path.exists(NEWS_CACHE_FILE):
        try:
            with open(NEWS_CACHE_FILE, "r", encoding="utf-8") as f:
                old_cache = json.load(f)
                existing_sentiments = old_cache.get("coins_sentiment", {})
        except Exception:
            pass

    if isinstance(sent_res, list) and sent_res and "details" in sent_res[0]:
        for d in sent_res[0]["details"]:
            ccy = d.get("ccy", "")
            if ccy not in target_coins:
                continue
            sent = d.get("sentiment", {})
            bull_ratio = float(sent.get("bullishRatio", 0.5) or 0.5)
            bear_ratio = float(sent.get("bearishRatio", 0.1) or 0.1)
            neutral_cnt = int(sent.get("neutralCnt", 0) or 0)
            bull_cnt = int(sent.get("bullishCnt", 0) or 0)
            bear_cnt = int(sent.get("bearishCnt", 0) or 0)
            total_dir = bull_cnt + bear_cnt
            # Calculate standard Long/Short Ratio (多空比 = 看多数 / 看空数)
            ls_ratio = round(bull_cnt / max(1, bear_cnt), 2)

            # Normalized Bull/Bear Share among active sentiment opinions
            if total_dir > 0:
                bull_share = f"{bull_cnt / total_dir * 100:.1f}%"
                bear_share = f"{bear_cnt / total_dir * 100:.1f}%"
            else:
                bull_share = f"{bull_ratio*100:.1f}%"
                bear_share = f"{bear_ratio*100:.1f}%"

            total_mentions = int(d.get("mentionCnt", 0) or 0)
            label = sent.get("label", "neutral")

            net_sentiment = bull_ratio - bear_ratio
            sentiment_score = round(net_sentiment * 0.8, 2)

            coin_sentiments[ccy] = {
                "ccy": ccy,
                "label": label,
                "bullish_ratio": bull_share,
                "bearish_ratio": bear_share,
                "bullish_pct": f"{bull_ratio*100:.1f}%",
                "bearish_pct": f"{bear_ratio*100:.1f}%",
                "long_short_ratio": f"{ls_ratio:.2f}",
                "bull_cnt": bull_cnt,
                "bear_cnt": bear_cnt,
                "neutral_cnt": neutral_cnt,
                "mentions": total_mentions,
                "sentiment_factor_score": sentiment_score
            }

    # Ensure all active coins are represented in the map; fallback to previous good value if available
    for ccy in target_coins:
        if ccy not in coin_sentiments:
            old_item = existing_sentiments.get(ccy)
            if old_item and old_item.get("mentions", 0) > 0:
                coin_sentiments[ccy] = old_item
            else:
                coin_sentiments[ccy] = {
                    "ccy": ccy,
                    "label": "neutral",
                    "bullish_ratio": "50.0%",
                    "bearish_ratio": "50.0%",
                    "bullish_pct": "50.0%",
                    "bearish_pct": "50.0%",
                    "long_short_ratio": "1.00",
                    "bull_cnt": 0,
                    "bear_cnt": 0,
                    "neutral_cnt": 0,
                    "mentions": 0,
                    "sentiment_factor_score": 0.0
                }

    # 3. Overall Macro Sentiment Synthesis
    cb_active, cb_info = is_circuit_breaker_active()
    if cb_active:
        macro_env = "🚨 避险熔断中"
    else:
        bull_count = sum(1 for c, s in coin_sentiments.items() if s["sentiment_factor_score"] > 0.25)
        bear_count = sum(1 for c, s in coin_sentiments.items() if s["sentiment_factor_score"] < -0.1)
        macro_env = "偏多震荡" if bull_count > bear_count else ("偏空承压" if bear_count > bull_count else "中性平衡")

    payload = {
        "timestamp": now_str,
        "updated_at": now_str,
        "source_available": False,
        "source_reason": "OKX CLI 已移除，news 无公开 V5 等价接口（黑天鹅熔断待新数据源，当前显示缺失而非中性）",
        "macro_sentiment": macro_env,
        "circuit_breaker": cb_info if cb_active else {"active": False},
        "coins_sentiment": coin_sentiments,
        "latest_news": parsed_news[:10],
        # Freshness of the *content* (newest item time), not of this run.
        "news_fresh_at": (parsed_news[0]["time"] if parsed_news else None),
    }

    # Fail-closed: an upstream hiccup must not wipe a good cache into an empty page.
    if not payload["latest_news"] or not payload["coins_sentiment"]:
        try:
            if os.path.exists(NEWS_CACHE_FILE):
                with open(NEWS_CACHE_FILE, "r", encoding="utf-8") as f:
                    previous = json.load(f)
                if previous.get("latest_news") or previous.get("coins_sentiment"):
                    if not payload["latest_news"] and previous.get("latest_news"):
                        payload["latest_news"] = previous["latest_news"]
                        payload["news_fresh_at"] = previous.get("news_fresh_at") or (
                            previous["latest_news"][0].get("time") if previous["latest_news"] else None
                        )
                    if not payload["coins_sentiment"] and previous.get("coins_sentiment"):
                        payload["coins_sentiment"] = {k: v for k, v in previous["coins_sentiment"].items() if k in target_coins}
                        bull_count = sum(1 for s in payload["coins_sentiment"].values() if float(s.get("sentiment_factor_score", 0)) > 0.25)
                        bear_count = sum(1 for s in payload["coins_sentiment"].values() if float(s.get("sentiment_factor_score", 0)) < -0.1)
                        if not cb_active:
                            payload["macro_sentiment"] = "偏多震荡" if bull_count > bear_count else ("偏空承压" if bear_count > bull_count else "中性平衡")
                    payload["stale_sections"] = True
        except Exception:
            pass

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_file = NEWS_CACHE_FILE + f".tmp.{os.getpid()}"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, NEWS_CACHE_FILE)
    except Exception as exc:
        print(f"Failed to write news cache: {exc}")

    return payload

if __name__ == "__main__":
    res = fetch_and_analyze_news_sentiment()
    flag = " ⚠️STALE(upstream empty, serving last cache)" if res.get("stale_sections") else ""
    print(f"✅ OKX News & Sentiment Engine complete. Macro: {res['macro_sentiment']}, News Count: {len(res['latest_news'])}{flag} 最新快讯: {res.get('news_fresh_at') or '--'}")
    # 数据源缺失（CLI 已移除、无公开 V5 等价）：按既有失败路径语义非零退出，
    # 调度/上层据 exit code 与 source_available 显式感知缺失（缓存回退仍生效）。
    if not res.get("source_available"):
        raise SystemExit(3)

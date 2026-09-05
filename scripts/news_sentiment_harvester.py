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
import json
import time
import datetime
import subprocess
import re
import urllib.request
import urllib.parse

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

def run_json_cmd(cmd: str, timeout: int = 15):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        if res.stdout.strip():
            return json.loads(res.stdout.strip())
    except Exception:
        pass
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

    # 1. Fetch Important & Latest News via OKX News CLI (Union of Latest + Important)
    news_res_latest = run_json_cmd("okx news latest --lang zh-CN --limit 15 --json") or {}
    news_res_imp = run_json_cmd("okx news important --lang zh-CN --limit 15 --json") or {}
    
    raw_news_latest = news_res_latest.get("details", []) if isinstance(news_res_latest, dict) else []
    raw_news_imp = news_res_imp.get("details", []) if isinstance(news_res_imp, dict) else []

    # 1.1 Multi-Exchange Feed: Supplement with Binance Official Announcements
    binance_news = []
    try:
        req_bn = urllib.request.Request(
            "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId=48&pageNo=1&pageSize=8",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req_bn, timeout=3.5) as resp_bn:
            d_bn = json.loads(resp_bn.read().decode())
            if isinstance(d_bn, dict) and d_bn.get("code") == "000000":
                articles = d_bn.get("data", {}).get("articles", [])
                now_ms = int(time.time() * 1000)
                for idx, art in enumerate(articles[:8]):
                    art_code = str(art.get("code", ""))
                    title = str(art.get("title", ""))
                    c_time = now_ms - (idx * 1800 * 1000)
                    binance_news.append({
                        "id": f"bn_{art.get('id', idx)}",
                        "cTime": c_time,
                        "title": title,
                        "summary": title,
                        "platform": "Binance",
                        "platformList": ["Binance"],
                        "url": f"https://www.binance.com/en/support/announcement/{art_code}" if art_code else "",
                        "importance": "high"
                    })
    except Exception as exc:
        print("Binance news err:", exc)

    # 1.2 Multi-Exchange Feed: Supplement with Gate.io Major Listings & Market Feeds
    gate_news = []
    try:
        req_gt = urllib.request.Request(
            "https://api.gateio.ws/api/v4/futures/usdt/contracts",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req_gt, timeout=3) as resp_gt:
            contracts = json.loads(resp_gt.read().decode())
            contracts_with_time = [c for c in contracts if c.get("create_time")]
            contracts_with_time.sort(key=lambda x: float(x.get("create_time", 0)), reverse=True)
            for c in contracts_with_time[:5]:
                c_name = c.get("name", "")
                c_ts = int(float(c.get("create_time", 0)) * 1000)
                base_coin = c_name.split("_")[0] if "_" in c_name else c_name
                gate_news.append({
                    "id": f"gate_{c_name}_{c_ts}",
                    "cTime": c_ts,
                    "title": f"Gate.io 合约正式上线 {c_name} (杠杆上限 {c.get('leverage_max', '50')}x)",
                    "summary": f"Gate.io USDT 本位永续合约全新上线 {c_name}，维持保证金率 {c.get('maintenance_rate', '0.005')}，支持多空双向交易。",
                    "platform": "Gate.io",
                    "platformList": ["Gate.io"],
                    "ccyList": [base_coin],
                    "url": f"https://www.gate.io/futures/USDT/{c_name}",
                    "importance": "medium"
                })
    except Exception as exc:
        print("Gate news err:", exc)

    seen_ids = set()
    raw_news = []
    # Interleave and preserve top items from all active platforms (OKX, Binance, Gate.io)
    for item in raw_news_latest + raw_news_imp:
        if not item.get("platform"):
            item["platform"] = "OKX"
            item["platformList"] = ["OKX"]
        nid = str(item.get("id", ""))
        if nid and nid not in seen_ids:
            seen_ids.add(nid)
            raw_news.append(item)

    for item in binance_news:
        item["platform"] = "Binance"
        item["platformList"] = ["Binance"]
        nid = str(item.get("id", ""))
        if nid and nid not in seen_ids:
            seen_ids.add(nid)
            raw_news.append(item)

    for item in gate_news:
        item["platform"] = "Gate.io"
        item["platformList"] = ["Gate.io"]
        nid = str(item.get("id", ""))
        if nid and nid not in seen_ids:
            seen_ids.add(nid)
            raw_news.append(item)
            
    # Sort strictly by creation timestamp descending, ensuring multi-platform representation
    raw_news.sort(key=lambda x: int(x.get("cTime", 0) or 0), reverse=True)
    raw_news = raw_news[:50]

    if not raw_news:
        news_res2 = run_json_cmd("okx news latest --lang zh-CN --limit 15 --json") or {}
        raw_news = news_res2.get("details", []) if isinstance(news_res2, dict) else []

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
            "platform": item.get("platform") or (item.get("platformList", ["OKX"])[0] if item.get("platformList") else "OKX"),
            "platforms": item.get("platformList", []),
            "importance": item.get("importance", "high"),
            "url": item.get("url") or item.get("sourceUrl", "")
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

    # 2. Fetch Multi-Coin Sentiment Snapshot
    active_instruments = load_instruments()
    target_coins = [item["name"] for item in active_instruments]
    coins_str = ",".join(target_coins)
    sent_res = run_json_cmd(f"okx news coin-sentiment --coins {coins_str} --json") or []
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
        "macro_sentiment": macro_env,
        "circuit_breaker": cb_info if cb_active else {"active": False},
        "coins_sentiment": coin_sentiments,
        "latest_news": parsed_news[:30]
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
        with open(NEWS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return payload

if __name__ == "__main__":
    res = fetch_and_analyze_news_sentiment()
    print(f"✅ OKX News & Sentiment Engine complete. Macro: {res['macro_sentiment']}, News Count: {len(res['latest_news'])}")

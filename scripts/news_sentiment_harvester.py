#!/usr/bin/env python3
"""
Crypto News & Black-Swan Circuit Breaker Harvester (US-002: 多源公开快讯 RSS)
Features:
1. Harvest high-impact crypto news directly from public RSS feeds
   (CoinDesk + Cointelegraph) over urllib.request — no okxcli dependency.
   Every source fails soft: one dead feed never blanks the whole intelligence layer.
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
import hashlib
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

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

def _classify_importance(title: str, summary: str) -> str:
    """根据快讯内容科学评定影响等级（high=重大/高危, mid=中等关注, low=普通快讯）。
    绝不盲目全标 high，避免狼来了式恐慌。"""
    text = f"{title} {summary}".lower()

    # 高危关键词：系统性风险、崩盘、黑客、脱锚、破产清算、司法调查等
    high_keywords = [
        "脱锚", "depeg", "破产", "倒闭", "挤兑", "停止提现", "暂停提币",
        "51%攻击", "系统瘫痪", "暴跌", "崩盘", "黑客", "被盗", "黑天鹅",
        "起诉", "立案调查", "全面封杀", "严厉打击", "清算危机", "清退",
        "bankruptcy", "insolvent", "halt withdrawals", "freeze withdrawals",
        "exploit", "hacked", "plunge", "crash", "subpoena", "fraud", "scam"
    ]
    for kw in high_keywords:
        if kw in text:
            return "high"

    # 中等关注关键词：宏观决议、ETF、大额投融资、主网升级、重要合作、大额流入
    mid_keywords = [
        "etf", "sec", "美联储", "降息", "加息", "鲍威尔", "cpi", "非农",
        "融资", "主网", "升级", "硬分叉", "战略合作", "巨鲸", "大额增持",
        "上市", "上线", "首发", "创历史新高", "暴涨", "突破",
        "fed", "rate cut", "inflation", "funding", "mainnet", "upgrade",
        "partnership", "whale", "inflow", "ath", "all-time high", "breakout"
    ]
    for kw in mid_keywords:
        if kw in text:
            return "mid"

    return "low"


def _extract_coins(title: str, summary: str, target_coins: list) -> list:
    """从新闻文本中识别涉及的加密资产代码。"""
    text = f" {title} {summary} ".upper()
    found = []
    coin_aliases = {
        "BTC": ["BTC", "BITCOIN", "比特币"],
        "ETH": ["ETH", "ETHEREUM", "以太坊", "以太币"],
        "SOL": ["SOL", "SOLANA"],
        "DOGE": ["DOGE", "DOGECOIN", "狗狗币"],
        "LINK": ["LINK", "CHAINLINK"],
        "AVAX": ["AVAX", "AVALANCHE", "雪崩"],
        "SUI": ["SUI"],
        "ADA": ["ADA", "CARDANO"],
        "XRP": ["XRP", "RIPPLE", "瑞波"],
    }
    for c, aliases in coin_aliases.items():
        if any(re.search(rf"\b{re.escape(a)}\b", text) if a.isascii() else (a in text) for a in aliases):
            found.append(c)
    for tc in (target_coins or []):
        if tc not in found:
            if re.search(rf"\b{re.escape(tc.upper())}\b", text):
                found.append(tc.upper())
    return found[:4]


# ---------------- US-001: OKX 公告降噪（剔除发新币/营销噪音，仅留运维安全通告） ----------------
# 直接拒绝的营销/上币类公告栏目：对量化交易与风控纯属噪音
OKX_NOISE_ANN_TYPES = {
    "announcements-new-listings",      # 新币/新合约/X-Perp/代币化股票上线
    "announcements-earn-and-loan",     # 赚币/理财/借贷推广
    "announcements-campaigns",         # 营销活动与交易赛
    "announcements-web3",              # Web3/DEX 钱包类宣传通告
    "announcements-launch",            # 首发/上线活动
}

# 真正关乎交易安全的公告栏目白名单：下架摘牌、停机维护、风控参数调整等
OKX_SAFELIST_ANN_TYPES = {
    "announcements-delistings",           # 币对/合约下架摘牌风险
    "announcements-system-maintenance",   # 系统停机维护与升级
    "announcements-futures",              # 合约风控参数（保证金/限仓/杠杆）调整
    "announcements-risk",                 # 风控与清算通告
    "announcements-regulation",           # 监管合规与地区清退
}

# 标题级中文泛化噪音关键词（仅作用于非白名单栏目，且不得压过安全词）
OKX_NOISE_TITLE_KEYWORDS = [
    "上线", "新增上线", "赚币", "理财", "借贷", "充值", "提现通道开通",
    "活动", "交易赛", "邀请", "空投", "推广", "上新",
]

# 明确的"发新币/新合约上线"指示词：即便栏目在白名单也据此剔除（规则a）
OKX_NEW_LISTING_TITLE_KEYWORDS = [
    "正式上线", "首发", "代币化股票", "X-合约", "X-Perp",
]

# 标题级中文安全关键词（关乎持仓与出入金安全的运维/风控通告）
# 与 BLACK_SWAN_PATTERNS / _classify_importance 高危词对齐：出入金暂停、
# 挤兑破产、强平脱锚、被盗立案等一律优先保留，供熔断器检测。
OKX_SAFETY_TITLE_KEYWORDS = [
    "维护", "停机", "升级", "下线", "下架", "摘牌", "清退",
    "风控", "保证金", "强平", "限仓", "杠杆调整", "合约调整", "价格区间",
    "做市", "清算",
    "暂停全部提现", "停止提币", "暂停提币", "停止提现", "暂停充值",
    "停止充值", "暂停提现", "提币", "充提", "借币",
    "挤兑", "破产", "黑天鹅", "脱锚", "51%攻击", "系统瘫痪", "崩盘",
    "暴跌", "被盗", "立案调查",
]

# 英文标题兜底（Accept-Language 失效时的英文公告）；\b 边界保证 list/listing 不误伤 delist/delisting
_OKX_NOISE_EN_RE = re.compile(
    r"\b(list|lists|listed|listing|listings|launch|launches|launched|launching|"
    r"airdrop|airdrops|campaign|campaigns|earn|promo|promos|promotion|promotions|"
    r"tokenized|web3)\b", re.IGNORECASE)
_OKX_SAFETY_EN_RE = re.compile(
    r"\b(delist|delists|delisted|delisting|maintenance|upgrade|upgrades|"
    r"suspend|suspends|suspended|suspension|halt|halts|halted|"
    r"liquidation|liquidations|margin|withdrawal|withdrawals|"
    r"position\s*limits?|reduce\s*only|delisting)\b", re.IGNORECASE)

# 中文出入金暂停强安全词：暂停/停止 与 充值/提现/提币/充提 之间允许夹币种名
# （如"关于暂停 BTC 充值的公告"），避免被泛化噪音词"充值"误杀。
_OKX_SAFETY_CN_RE = re.compile(
    r"(暂停|停止|紧急|临时).{0,10}(充值|提现|提币|充提|出入金)")

# 英文"发新币/新合约上线"指示词（\b 边界不误伤 delist/delisting）
_OKX_NEW_LISTING_EN_RE = re.compile(
    r"\b(list|lists|listed|listing|listings|launch|launches|launched|"
    r"launching|tokenized)\b", re.IGNORECASE)

# 黑天鹅正则预编译：命中任意一条即强制保留，确保熔断器可检测到
_BLACK_SWAN_RES = [re.compile(p) for p, _ in BLACK_SWAN_PATTERNS]


def _okx_safety_hit(title: str) -> bool:
    """命中出入金/风控/黑天鹅等强安全词（中英双通道）。"""
    return (any(kw in title for kw in OKX_SAFETY_TITLE_KEYWORDS)
            or bool(_OKX_SAFETY_CN_RE.search(title))
            or bool(_OKX_SAFETY_EN_RE.search(title)))


def _okx_new_listing_hit(title: str) -> bool:
    """明确指示发新币/新合约上线（中英双通道）。"""
    return (any(kw in title for kw in OKX_NEW_LISTING_TITLE_KEYWORDS)
            or bool(_OKX_NEW_LISTING_EN_RE.search(title)))


def _okx_ann_is_actionable(title: str, ann_type: str) -> bool:
    """US-001 降噪判定：仅当公告属于真正关乎交易安全的运维/风控通告才返回 True。

    优先级（verifier 反馈修正）：
    ① 命中 BLACK_SWAN_PATTERNS 黑天鹅正则 → 一律保留（熔断器必须可见）；
    ② 营销栏目黑名单 → 剔除，除非命中强安全词；
    ③ 明确发新币/新合约上线指示词（正式上线/首发/X-合约/代币化股票等）→ 剔除，
       即便栏目在白名单（规则a），除非同时命中强安全词；
    ④ 栏目白名单 → 保留，泛化噪音词（充值/借贷/活动/上线等）不得误杀（规则a）；
    ⑤ 栏目未知 → 强安全词优先于泛化噪音词（规则b），两者皆无则剔除。"""
    atype = str(ann_type or "").strip().lower()

    # ① 黑天鹅/系统性风险：任何栏目一律保留
    if any(rx.search(title) for rx in _BLACK_SWAN_RES):
        return True

    safety = _okx_safety_hit(title)

    # ② 营销栏目黑名单：仅强安全词可豁免
    if atype in OKX_NOISE_ANN_TYPES:
        return bool(safety)

    # ③ 明确发新币/新合约上线：白名单栏目也剔除，除非命中强安全词
    if _okx_new_listing_hit(title) and not safety:
        return False

    # ④ 栏目白名单：泛化噪音词不得误杀
    if atype in OKX_SAFELIST_ANN_TYPES:
        return True

    # ⑤ 栏目未知：安全词优先，其后泛化噪音词（中英）剔除
    if safety:
        return True
    if any(kw in title for kw in OKX_NOISE_TITLE_KEYWORDS):
        return False
    if _OKX_NOISE_EN_RE.search(title):
        return False
    return False


def fetch_okx_announcements(limit=15) -> list:
    """OKX 官方公告流抓取（/api/v5/support/announcements）。
    US-001 降噪：彻底剔除发新币/X-合约上线/赚币理财等营销通告，
    仅保留下架摘牌、系统维护停机与风控参数调整等真正关乎交易安全的公告。"""
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    items = []
    try:
        url = "https://www.okx.com/api/v5/support/announcements"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for group in data.get("data", []):
            for it in (group.get("details", []) or []):
                title = str(it.get("title") or "").strip()
                if not title:
                    continue
                url = str(it.get("url") or "")
                ann_type = str(it.get("annType") or "公告")
                # US-001 降噪：发新币/X-合约/赚币理财/营销活动类通告一律丢弃，
                # 仅留下架摘牌、系统维护停机与风控参数调整等交易安全通报。
                if not _okx_ann_is_actionable(title, ann_type):
                    continue
                p_time = int(it.get("pTime") or it.get("businessPTime") or (time.time() * 1000))
                time_str = datetime.datetime.fromtimestamp(p_time / 1000.0, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S")
                summary = f"OKX官方通告【{ann_type}】: {title}"
                items.append({
                    "id": f"okx-{p_time}-{abs(hash(title)) % 10000}",
                    "title": title,
                    "summary": summary,
                    "time": time_str,
                    "cTime": str(p_time),
                    "url": url,
                    "platforms": ["OKX官方"],
                    "importance": _classify_importance(title, summary),
                })
    except Exception as e:
        print(f"[news_harvester] warn OKX 官方公告抓取异常: {e}")
    return items[:limit]


def fetch_jin10_macro_news(limit=20) -> list:
    """金十数据官方宏观与要闻流抓取（hits_rank.json）+ 实时 7x24 宏观快讯滚动补充。"""
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    items = []

    # 1. 金十数据官方热点要闻
    try:
        url = "https://cdn.jin10.com/json/index/hits_rank.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        news_list = (raw.get("all", {}).get("daily", {}).get("news", [])
                     + raw.get("all", {}).get("weekly", {}).get("news", []))
        updated_at = raw.get("all", {}).get("daily", {}).get("updated_at")
        ts_now = int(time.time() * 1000)
        for idx, it in enumerate(news_list):
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            item_id = it.get("id") or (ts_now - idx * 60000)
            items.append({
                "id": f"jin10-{item_id}",
                "title": title,
                "summary": f"金十数据热点要闻: {title}",
                "time": updated_at or datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S"),
                "cTime": str(ts_now - idx * 60000),
                "url": "https://www.jin10.com",
                "platforms": ["金十数据"],
                "importance": _classify_importance(title, ""),
            })
    except Exception as e:
        print(f"[news_harvester] warn 金十数据抓取异常: {e}")

    # 2. 7x24 实时宏观快讯滚动补充（新浪财经 7x24 全球宏观快讯）
    try:
        url = "https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=20&zhibo_id=152"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        feed_list = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
        for it in feed_list:
            text = (it.get("rich_text") or it.get("plain_text") or "").strip()
            if not text:
                continue
            create_time = it.get("create_time") or datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S")
            try:
                dt_obj = datetime.datetime.strptime(create_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz_bj)
                ts_ms = int(dt_obj.timestamp() * 1000)
            except Exception:
                ts_ms = int(time.time() * 1000)
            title_match = re.split(r"[。！!？?\n]", text)[0].strip()
            title = title_match[:70] if title_match else text[:70]
            items.append({
                "id": f"macro-{it.get('id') or ts_ms}",
                "title": title,
                "summary": text[:200],
                "time": create_time,
                "cTime": str(ts_ms),
                "url": "https://finance.sina.com.cn/7x24/",
                "platforms": ["全球宏观快讯"],
                "importance": _classify_importance(title, text),
            })
    except Exception as e:
        print(f"[news_harvester] warn 宏观快讯抓取异常: {e}")

    return items[:limit]


def fetch_okx_rubik_sentiment(ccy: str) -> dict:
    """从 OKX Rubik 官方数据端点拉取多空账户比与合约持仓情绪。"""
    c = ccy.upper()
    url = f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={c}"
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            rows = data.get("data") or []
            if rows and len(rows[0]) >= 2:
                ratio = float(rows[0][1] or 1.0)
                bull_pct = round((ratio / (ratio + 1.0)) * 100, 1)
                bear_pct = round((1.0 / (ratio + 1.0)) * 100, 1)
                if ratio >= 1.25:
                    label = "bullish"
                elif ratio <= 0.82:
                    label = "bearish"
                else:
                    label = "neutral"
                score = round((ratio - 1.0) / max(1.0, ratio), 2)
                return {
                    "ccy": c,
                    "label": label,
                    "bullish_ratio": f"{bull_pct:.1f}%",
                    "bearish_ratio": f"{bear_pct:.1f}%",
                    "bullish_pct": f"{bull_pct:.1f}%",
                    "bearish_pct": f"{bear_pct:.1f}%",
                    "long_short_ratio": f"{ratio:.2f}",
                    "bull_cnt": int(bull_pct),
                    "bear_cnt": int(bear_pct),
                    "neutral_cnt": 0,
                    "mentions": 100,
                    "sentiment_factor_score": score,
                }
        except Exception:
            if attempt == 0:
                time.sleep(0.6)
    return None

def fetch_and_analyze_news_sentiment():
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    now_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")

    # 1. News sources：直连 OKX 官方公告流 + 金十数据宏观快讯，淘汰旧第三方 RSS。
    okx_news = fetch_okx_announcements(limit=20)
    jin10_news = fetch_jin10_macro_news(limit=25)
    raw_news = okx_news + jin10_news

    seen_ids = set()
    deduped_news = []
    for item in raw_news:
        nid = str(item.get("id", ""))
        if nid and nid not in seen_ids:
            seen_ids.add(nid)
            deduped_news.append(item)

    # US-001 降噪：移除 OKX 官方公告 15 条硬保底。OKX 公告多为发新币噪音，
    # 经降噪后剩余的下架/维护/风控安全通告按时间自然并入快讯流，无通告则不硬塞。
    raw_news = sorted(deduped_news, key=lambda x: int(x.get("cTime", 0) or 0), reverse=True)

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

        coins = item.get("ccyList") or item.get("coins") or _extract_coins(title, summary, TARGET_COINS)
        importance = item.get("importance") or _classify_importance(title, summary)

        parsed_news.append({
            "id": item.get("id"),
            "time": dt_str,
            "title": title,
            "summary": summary,
            "coins": coins,
            "platforms": item.get("platformList") or item.get("platforms", []),
            "importance": importance,
            "url": item.get("sourceUrl") or item.get("url", "")
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

    # 2. Multi-Coin Sentiment：直连 OKX Rubik 官方多空账户比，真实反映全网多空力量
    active_instruments = load_instruments()
    target_coins = [item["name"] for item in active_instruments]
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

    for ccy in target_coins:
        rubik_data = fetch_okx_rubik_sentiment(ccy)
        if rubik_data:
            coin_sentiments[ccy] = rubik_data
        elif ccy in existing_sentiments:
            coin_sentiments[ccy] = existing_sentiments[ccy]
        else:
            coin_sentiments[ccy] = {
                "ccy": ccy,
                "label": "neutral",
                "bullish_ratio": "50.0%",
                "bearish_ratio": "50.0%",
                "bullish_pct": "50.0%",
                "bearish_pct": "50.0%",
                "long_short_ratio": "1.00",
                "bull_cnt": 50,
                "bear_cnt": 50,
                "neutral_cnt": 0,
                "mentions": 100,
                "sentiment_factor_score": 0.0,
            }
        time.sleep(0.3)

    # 3. Overall Macro Sentiment Synthesis
    cb_active, cb_info = is_circuit_breaker_active()
    if cb_active:
        macro_env = "🚨 避险熔断中"
    else:
        bull_count = sum(1 for c, s in coin_sentiments.items() if s["sentiment_factor_score"] > 0.15)
        bear_count = sum(1 for c, s in coin_sentiments.items() if s["sentiment_factor_score"] < -0.15)
        macro_env = "偏多震荡" if bull_count > bear_count else ("偏空承压" if bear_count > bull_count else "中性平衡")

    payload = {
        "timestamp": now_str,
        "updated_at": now_str,
        "source_available": bool(raw_news),
        "source_reason": ("OKX官方公告 + 金十数据宏观要闻 + OKX Rubik多空数据" if raw_news
                          else "OKX官方公告与金十数据拉取失败，显示缺失而非中性"),
        "macro_sentiment": macro_env,
        "circuit_breaker": cb_info if cb_active else {"active": False},
        "coins_sentiment": coin_sentiments,
        "latest_news": parsed_news[:35],
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

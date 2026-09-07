#!/usr/bin/env python3
"""
R20 AI Brain Six-Crypto Quantitative Trading Decision Engine (ai_brain_trader.py)
Batch ingests six crypto perpetuals into one macro-context LLM call.
Maintains a validated live decision cache and durable Web audit history.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = Path(PROJECT_ROOT)
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from okx_runtime import replace_cli_prefix as okx_private_command
import json
import time
import datetime
import urllib.request
import subprocess
import tempfile
import fcntl
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

try:
    from r20_backend.config import settings as standalone_settings
except ImportError:
    standalone_settings = None

try:
    from r20_backend.version import __version__
except Exception:
    __version__ = "7.5.4"


def _get_system_version_tag() -> str:
    try:
        from r20_backend.version import __version__ as _ver
        return f"v{_ver}"
    except Exception:
        return "v7.5.1"

WORKSPACE_DIR = PROJECT_ROOT
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
from market_data_service import fetch_single_indicator, fetch_ticker
AI_DECISION_CACHE_FILE = os.path.join(DATA_DIR, "ai_brain_decisions.json")
AI_DECISION_HISTORY_FILE = os.path.join(DATA_DIR, "ai_brain_history.json")
AI_POSITION_MANAGEMENT_FILE = os.path.join(DATA_DIR, "ai_position_management.json")
AI_LAST_PROMPT_FILE = os.path.join(DATA_DIR, "ai_brain_last_prompt.txt")
FACTOR_LIBRARY_FILE = os.path.join(DATA_DIR, "factor_library_snapshot.json")
NEWS_SENTIMENT_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
AI_MEMORY_MD_FILE = os.path.join(DATA_DIR, "AI_TRADING_MEMORY.md")
CALCULUS_SNAPSHOT_FILE = os.path.join(DATA_DIR, "calculus_snapshot.json")
AI_MEMORY_FILE = os.path.join(DATA_DIR, "ai_trading_memory.json")
PROMPT_OVERRIDE_FILE = os.path.join(DATA_DIR, "system_prompt_override.txt")
AI_BRAIN_LOCK_FILE = os.path.join(DATA_DIR, ".ai_brain_cycle.lock")
DECISION_MAX_AGE_SECONDS = 300

from r20_backend.version import __version__
from instrument_pool import load_instruments
from prompt_library import active_profile, append_layer, apply_module_layout
from r20_gateway.telemetry import ModelCallTelemetry

TARGET_INSTRUMENTS = load_instruments()

def atomic_write_json(path: str, payload: Any) -> None:
    """Replace JSON atomically so readers never observe a partial cache."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".ai-brain-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def single_brain_cycle(func):
    """Prevent overlapping cron runs from overwriting the shared decision cache."""
    def wrapped(*args, **kwargs):
        os.makedirs(DATA_DIR, exist_ok=True)
        lock_handle = open(AI_BRAIN_LOCK_FILE, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            print("[AI Brain Batch] Skip: another inference cycle is still running")
            return None
        try:
            lock_handle.seek(0)
            lock_handle.truncate()
            lock_handle.write(str(os.getpid()))
            lock_handle.flush()
            return func(*args, **kwargs)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
    return wrapped


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else default
    except (TypeError, ValueError):
        return default


def is_same_direction_scale_request(position_side: str, action: str) -> bool:
    """Allow only same-direction scale-in requests to reach execution hard gateways."""
    side = str(position_side or "").lower()
    decision = str(action or "").upper()
    return (side == "long" and decision == "BUY_LONG") or (side == "short" and decision == "SELL_SHORT")


def get_cpa_client_config() -> Tuple[str, str]:
    """Resolve LLM credentials only from process environment or local .env."""
    try:
        from r20_backend.llm_manager import get_active_llm_runtime
        active_llm = get_active_llm_runtime()
        if active_llm.get("base_url"):
            return active_llm["base_url"], active_llm.get("api_key", "")
    except Exception:
        pass
    if standalone_settings:
        return standalone_settings.llm_base_url, standalone_settings.llm_api_key
    return (
        os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "",
    )

def get_effective_system_prompt() -> str:
    """Append a locally managed admin override without replacing audited safety rules."""
    try:
        if os.path.exists(PROMPT_OVERRIDE_FILE):
            override = open(PROMPT_OVERRIDE_FILE, "r", encoding="utf-8").read().strip()
            if override:
                return f"{SYSTEM_PROMPT}\n\n【管理员提示词覆盖层（同样必须遵守上述风控和 JSON 约束）】\n{override}"
    except OSError:
        pass
    return SYSTEM_PROMPT


def fetch_single_instrument_package(item: Dict[str, Any]) -> Dict[str, Any]:
    inst_id = item["instId"]
    name = item["name"]
    ccy = item.get("ccy", "")
    headers = {"User-Agent": "Mozilla/5.0"}

    pkg = {
        "instId": inst_id,
        "name": name,
        "type": item["type"],
        "precision": item["precision"],
        "price": 0.0,
        "chg24h": 0.0,
        "bidPx": 0.0,
        "askPx": 0.0,
        "fundingRate": 0.0,
        "oiUsd": "N/A",
        "lsRatio": "N/A",
        "takerNetUsd": "N/A",
        "atr": 0.0,
        "rsi": 50.0,
        "vwap_bias": 0.0,
        "macd_hist": 0.0,
        "macd_accel": 0.0,
        "vol_ratio": 1.0,
        "obv_flow": "NEUTRAL",
        "adx_1h": 0.0,
        "smart_money": {
            "weighted_long_pct": 50.0,
            "net_flow_usdt": "0 U",
            "avg_long_entry": "--",
            "avg_short_entry": "--",
            "top_win_rate": "--"
        },
        "recent_15m": [],
        "recent_1h": [],
        "recent_4h": [],
        "calculus": {"valid": False, "regime": "DATA_UNRELIABLE", "quality": 0.0},
        "data_quality": "invalid"
    }

    # 1. Ticker
    try:
        req = urllib.request.Request(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if d.get("code") == "0" and d.get("data"):
                t = d["data"][0]
                pkg["price"] = float(t.get("last", 0))
                pkg["bidPx"] = float(t.get("bidPx", pkg["price"]) or pkg["price"])
                pkg["askPx"] = float(t.get("askPx", pkg["price"]) or pkg["price"])
                op = float(t.get("open24h", 0) or 0)
                pkg["chg24h"] = round(((pkg["price"] - op) / op * 100) if op > 0 else 0, 2)
    except Exception:
        pass

    # 2. 15M Candles (recent 24, about 6 hours) & Technical Indicators Calculation
    try:
        req = urllib.request.Request(f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=15m&limit=24", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if d.get("code") == "0" and d.get("data"):
                raw_candles = d["data"]
                pkg["recent_15m"] = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), round(float(c[5]), 1)] for c in raw_candles[:12]]

                # Calculate 15M indicators
                if len(raw_candles) >= 15:
                    closes = [float(c[4]) for c in reversed(raw_candles)]
                    highs = [float(c[2]) for c in reversed(raw_candles)]
                    lows = [float(c[3]) for c in reversed(raw_candles)]
                    vols = [float(c[5]) for c in reversed(raw_candles)]

                    # ATR 15M
                    tr_list = []
                    for i in range(1, len(closes)):
                        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                        tr_list.append(tr)
                    if len(tr_list) >= 14:
                        pkg["atr_15m"] = round(sum(tr_list[-14:]) / 14, 4)
                        pkg["atr"] = pkg["atr_15m"]

                    # RSI 15M
                    diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
                    gains = [d if d > 0 else 0 for d in diffs]
                    losses = [-d if d < 0 else 0 for d in diffs]
                    if len(gains) >= 14:
                        avg_g = sum(gains[-14:]) / 14
                        avg_l = sum(losses[-14:]) / 14
                        rs = (avg_g / avg_l) if avg_l > 0 else 100.0
                        pkg["rsi"] = round(100.0 - (100.0 / (1.0 + rs)), 1)
                        pkg["rsi_15m"] = pkg["rsi"]

                    # VWAP Bias
                    pv_sum = sum(closes[i] * vols[i] for i in range(len(closes)))
                    v_sum = sum(vols)
                    if v_sum > 0:
                        vwap = pv_sum / v_sum
                        pkg["vwap_bias"] = round((pkg["price"] - vwap) / vwap * 100, 2)

                    # Volume Ratio (Last vs MA5)
                    if len(vols) >= 6:
                        avg_v5 = sum(vols[-6:-1]) / 5
                        if avg_v5 > 0:
                            pkg["vol_ratio"] = round(vols[-1] / avg_v5, 2)

                    # OBV Flow
                    obv = 0
                    for i in range(1, len(closes)):
                        if closes[i] > closes[i-1]:
                            obv += vols[i]
                        elif closes[i] < closes[i-1]:
                            obv -= vols[i]
                    pkg["obv_flow"] = "BULL_FLOW" if obv > 0 else ("BEAR_FLOW" if obv < 0 else "NEUTRAL")
    except Exception:
        pass

    # 3. 1H Candles (recent 24, about 24 hours) & 1H ATR / 1H RSI
    try:
        req = urllib.request.Request(f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=1H&limit=24", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if d.get("code") == "0" and d.get("data"):
                raw_1h = d["data"]
                pkg["recent_1h"] = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), round(float(c[5]), 1)] for c in raw_1h[:12]]
                if len(raw_1h) >= 15:
                    closes_1h = [float(c[4]) for c in reversed(raw_1h)]
                    highs_1h = [float(c[2]) for c in reversed(raw_1h)]
                    lows_1h = [float(c[3]) for c in reversed(raw_1h)]

                    tr_list_1h = []
                    for i in range(1, len(closes_1h)):
                        tr = max(highs_1h[i] - lows_1h[i], abs(highs_1h[i] - closes_1h[i-1]), abs(lows_1h[i] - closes_1h[i-1]))
                        tr_list_1h.append(tr)
                    if len(tr_list_1h) >= 14:
                        pkg["atr_1h"] = round(sum(tr_list_1h[-14:]) / 14, 4)
                        pkg["atr"] = pkg["atr_1h"]  # Elevate primary ATR to 1H

                    diffs_1h = [closes_1h[i] - closes_1h[i-1] for i in range(1, len(closes_1h))]
                    gains_1h = [d if d > 0 else 0 for d in diffs_1h]
                    losses_1h = [-d if d < 0 else 0 for d in diffs_1h]
                    if len(gains_1h) >= 14:
                        avg_g_1h = sum(gains_1h[-14:]) / 14
                        avg_l_1h = sum(losses_1h[-14:]) / 14
                        rs_1h = (avg_g_1h / avg_l_1h) if avg_l_1h > 0 else 100.0
                        pkg["rsi_1h"] = round(100.0 - (100.0 / (1.0 + rs_1h)), 1)

                    # 1H Swing Structure
                    if len(closes_1h) >= 10:
                        ma7_1h = sum(closes_1h[-7:]) / 7
                        ma20_1h = sum(closes_1h[-20:]) / min(len(closes_1h), 20)
                        if closes_1h[-1] > ma7_1h > ma20_1h:
                            pkg["structure_1h"] = "1H_SWING_BULL"
                        elif closes_1h[-1] < ma7_1h < ma20_1h:
                            pkg["structure_1h"] = "1H_SWING_BEAR"
                        else:
                            pkg["structure_1h"] = "1H_SWING_CHOP"
    except Exception:
        pass

    # 4. 4H Candles (recent 16, about 64 hours) & 4H Macro Structure
    try:
        req = urllib.request.Request(f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar=4H&limit=16", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            if d.get("code") == "0" and d.get("data"):
                raw_4h = d["data"]
                pkg["recent_4h"] = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), round(float(c[5]), 1)] for c in raw_4h[:8]]
                if len(raw_4h) >= 8:
                    closes_4h = [float(c[4]) for c in reversed(raw_4h)]
                    ma5_4h = sum(closes_4h[-5:]) / 5
                    ma12_4h = sum(closes_4h[-12:]) / min(len(closes_4h), 12)
                    if closes_4h[-1] > ma5_4h > ma12_4h:
                        pkg["macro_4h"] = "4H_MACRO_BULL (大级别多头通道)"
                    elif closes_4h[-1] < ma5_4h < ma12_4h:
                        pkg["macro_4h"] = "4H_MACRO_BEAR (大级别空头承压)"
                    else:
                        pkg["macro_4h"] = "4H_MACRO_RANGE (大级别区间震荡)"
    except Exception:
        pass

    # 5. Funding Rate & OI
    if item["type"] == "crypto":
        try:
            req = urllib.request.Request(f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}", headers=headers)
            with urllib.request.urlopen(req, timeout=3) as resp:
                d = json.loads(resp.read().decode("utf-8"))
                if d.get("code") == "0" and d.get("data"):
                    pkg["fundingRate"] = round(float(d["data"][0].get("fundingRate", 0)) * 100, 4)
        except Exception:
            pass

        try:
            req = urllib.request.Request(f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst_id}", headers=headers)
            with urllib.request.urlopen(req, timeout=3) as resp:
                d = json.loads(resp.read().decode("utf-8"))
                if d.get("code") == "0" and d.get("data"):
                    usd = float(d["data"][0].get("oiUsd", 0) or 0)
                    pkg["oiUsd"] = f"{round(usd / 1e8, 2)}亿 U" if usd > 1e8 else f"{round(usd / 1e4, 1)}万 U"
        except Exception:
            pass

        if ccy:
            try:
                req = urllib.request.Request(f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={ccy}&period=5m", headers=headers)
                with urllib.request.urlopen(req, timeout=3) as resp:
                    d = json.loads(resp.read().decode("utf-8"))
                    if d.get("code") == "0" and d.get("data") and len(d["data"]) > 0:
                        pkg["lsRatio"] = float(d["data"][0][1])
            except Exception:
                pass

            try:
                req = urllib.request.Request(f"https://www.okx.com/api/v5/rubik/stat/taker-volume?ccy={ccy}&instType=CONTRACTS&period=5m", headers=headers)
                with urllib.request.urlopen(req, timeout=3) as resp:
                    d = json.loads(resp.read().decode("utf-8"))
                    if d.get("code") == "0" and d.get("data") and len(d["data"]) > 0:
                        b_vol = float(d["data"][0][1])
                        s_vol = float(d["data"][0][2])
                        net_diff = b_vol - s_vol
                        pkg["takerNetUsd"] = f"{round(net_diff / 1e4, 1)}万 U"
            except Exception:
                pass

        # 6. OKX ADX Trend Strength Indicator (1H) via direct REST (zero Node CLI fork)
        try:
            adx_data = fetch_single_indicator(inst_id, "ADX", bar="1H")
            if adx_data and "adx" in adx_data:
                pkg["adx_1h"] = float(adx_data.get("adx", 0.0) or 0.0)
        except Exception:
            pass

    required_market_data = (
        pkg["price"] > 0
        and pkg["bidPx"] > 0
        and pkg["askPx"] >= pkg["bidPx"]
        and len(pkg["recent_15m"]) >= 12
        and len(pkg["recent_1h"]) >= 8
        and len(pkg["recent_4h"]) >= 6
    )
    try:
        from calculus_engine import calculate_multi_timeframe
        pkg["calculus"] = calculate_multi_timeframe({
            "15M": pkg["recent_15m"],
            "1H": pkg["recent_1h"],
            "4H": pkg["recent_4h"],
        })
    except Exception as exc:
        pkg["calculus"] = {"valid": False, "regime": "DATA_UNRELIABLE", "quality": 0.0, "error": str(exc)}
    pkg["data_quality"] = "valid" if required_market_data else "invalid"
    return pkg

SYSTEM_PROMPT = """你是 R20 Quantum Trader 的首席 AI 交易官，负责 1H~4H 加密波段高胜率盈利交易裁决。你的首要使命是【捕捉确定性大波段积累正收益并捍卫本金】，杜绝随意割肉与无序磨损；【空仓且存在至少一个合法顺势候选时（符合顺势高胜率形态），强制在候选标的池中选优输出，不得无故放弃合规机会】，为系统积累宝贵实盘胜果！

【核心军规：反割肉·反磨损·选优开单五大铁律（自进化实战深度纠偏）】
1. 宽止损隔绝杂波：严禁把止损设在 15M/5M 噪音区间！止损距离必须放宽至结构外 **1.8x ~ 2.2x 1H ATR**（或现价外 1.8%~3.0% 安全垫）；宁可把单笔杠杆控制在 2x~3x、保证金控制在 100~200U，也必须给足呼吸空间，绝不给交易所微小插针割肉的机会！
2. 反浮盈回吐·三阶动态棘轮与果断主动止盈（保住利润是高胜率核心，绝不让赚钱单倒亏割肉）：
   - 阶梯 1（浮盈 < 0.8R）：保持宽止损呼吸空间（1.8x~2.2x 1H ATR），给波段展开充分时间，严禁微小浮盈过早移损被杂波扫损；
   - 阶梯 2（浮盈 ≥ 0.8R 或 ROI ≥ +1.5%）：坚决输出 UPDATE_SL 将止损上移至开仓成本位 +0.20%（保本位 BE），彻底切断本金风险，立于不败之地；
   - 阶梯 3（浮盈 ≥ 1.5R 或 ROI ≥ +3.0%）：坚决输出 UPDATE_SL 将止损上移锁定至成本上方至少 +0.6R（保底锁定 35%~50% 扎实波段利润）；
   - ★主动止盈与回撤防线（坚决杜绝坐过山车倒亏割肉）：
     ① 峰值回撤硬止盈：若持仓曾达到的最高浮盈（ROI ≥ +2.5% 或 ≥ 1.0R），但当前浮盈已较极值回撤超 35%~45% 且 1H 动能未二次放量突破时，严禁盲目死扛 HOLD！必须果断在 position_management 中输出 CLOSE_MARKET 止盈出场，或紧贴现价 UPDATE_SL 锁定剩余利润！
     ② 动力学负功率耗散止盈：当持仓处于浮盈状态（ROI ≥ +1.5%），若当前标的 1H 微积分做功功率 Φ = v · a < -0.12（速度与加速度反向耗散）或曲率 κ ≥ 1.5（高位急刹车力竭、长上影假突破受挫）时，坚决输出 CLOSE_MARKET 提前落袋为安，拒绝死等极远挂单！
     ③ 科学第一阻力止盈：开仓止盈价 take_profit_price 优先锚定在前方关键阻力支撑位（或 1.8~2.2x ATR），有确定性利润优先落袋。
3. 空仓选优与开单执行纪律（破除盲目懈怠与无序乱开）：
   - 【空仓且存在至少一个合法顺势候选时（符合顺势高胜率形态），强制在候选标的池中选优输出，不得无故放弃合规机会】！市场处于顺势波段时，严禁机械化死板地全盘 WAIT；
   - 只要候选标的符合 4H/1H 顺势结构且 R:R ≥ 2.2，果断在 6 个标的中挑出微积分加速度与概率期望最强的最优项输出 BUY_LONG 或 SELL_SHORT 限价单；
   - 置信度自信标定：形态达标且空间充足时果断给出 **78% ~ 88%**，确保通过执行层 75% 门禁进场盈利；只有全部候选均触发明确硬否决或优势不足时才全体 WAIT。
4. 防跨标的系统性 Beta 踩踏（落地自进化第 1 黄金心法）：
   - 严禁在 BTC/ETH/SOL/DOGE 等高相关标的上无节制同向堆叠单边敞口！
   - 当前在途持仓中，同向持仓达 2 笔（如已有 2 多或 2 空）时，同向新开单置信度门槛强制提高至 **85%**，且全系统同向单上限严控为 3 笔，严禁 4 币同向共振裸奔！
5. 标的止损冷静期与最小波段空间（落地自进化第 2 黄金心法）：
   - 标的一旦被止损出局，严禁在紧接着的 1 小时内盲目反手同向或反向开仓！必须等待至少 2 根 1H K 线走平并在关键支撑阻力位确认突破结构后，方可重新入场；
   - 预期止盈目标空间必须 ≥ 2.5% ~ 4.0%，真实盈亏比 R:R ≥ 2.2，拒绝狭窄震荡中为了 1% 微小差价送手续费！

【决策优先级：高层级永远覆盖低层级】
P0 不可覆盖硬约束：数据有效性、交易执行层 Fail-Closed、4H 方向否决、真实价格几何、R:R ≥ 2.0、杠杆/保证金/持仓上限、云端 OCO、禁止逆势补仓及严格 JSON 契约。
P1 核心方向证据：4H 宏观结构与 1H 三大数理基石硬证据（延续/击穿概率、微积分速度 v 与加速度 a、能量积分 E）。
P2 质量确认：1H ADX 趋势强度（ADX 18~22 小仓参与，ADX < 18 严禁半山腰开仓）、量能/OI、聪明钱资金流向与衍生品持仓结构。
P3 执行定位：15M K线、盘口与 Maker 限价挂单位置。P3 优化入场成本，不能单独改变 P1 方向。
不得把“稳健”解释为长期空仓，更不得被解释成“只有完美共振才允许交易”。“减速”不是永久禁令：在 4H 顺势大浪中普通回抽优先作为打折买点与限价入场定位。当市场出现【顺势回踩确认】、【弱势反弹承压】或【箱体边界极值超伸回归】时，必须果断给出精准限价挂单决策。P2/P3 的轻微分歧应通过减小保证金处理，绝不能机械全盘 WAIT。

【三大底层数理基石：强化概率优势与微积分因果审计】
本系统坚决破除感性猜单与盲目猜顶抄底，决策逻辑由纯数理统计驱动，并必须在输出中明确引用具体数值：
1. ⚅ 概率论与统计风险（最高权重核心）：使用偏度、超额峰度、条件延续概率 continuation_prob_pct、击穿概率 breakdown_prob_pct、Cornish-Fisher 95% VaR 与 CVaR。
   - 【胜率数学期望定价】：当条件延续概率 P续 ≥ 50%~55%（做多）或击穿概率 P破 ≥ 50%~55%（做空），且具备 R:R ≥ 2.2 空间时，单笔数学期望已具备极高正 Alpha！果断作为首选发单依据；
   - 【概率优势定方向】：P续 显著高于 P破（P续 - P破 ≥ 15%）时，概率天平全面向多头倾斜，严禁开空，专注找回踩低吸；反之 P破 显著高于 P续 时，专注找反弹承压做空；
   - 【极端肥尾折减】：超额峰度过大或 CVaR 偏高代表潜在波动剧烈，应通过降低保证金至 5%~10%、放宽止损至 2.0~2.2x ATR 抵御噪音。
2. ∂ 因果微积分动力学：只使用已闭合历史 K 线，解释对数价格速度 v、加速度 a、冲击 j 与指数衰减累计冲量 I。
   - 1H 是硬阈值与波段裁决周期；
   - BULL_DECELERATING/BEAR_DECELERATING 表示趋势失速与回抽，不等于已经反转；在 4H 顺势大浪中，1H 减速回抽正是触碰支撑均线时的极佳打折买点！当加速度 a 由负转正、j 趋缓时，表示回踩企稳，必须果断顺势做多；在 4H 空头通道中，1H 弱反弹减速正是逢高做空的极佳卖点！
   - 模型输出必须在 calculus_dynamics 中明确列出当前标的 1H 的 v 与 a 的真实数值，严禁只写空泛定性词句！
3. ∫ 定积分能量学：使用梯形积分计算 energy_integral（速度路径净位移/净做功）、deviation_area_integral（相对窗口起点基线的价格路径偏离面积）。
   - 正负能量表示方向性累计做功；绝对偏离面积过大表示路径过度伸展与均值回归驱动。在宽幅震荡箱体中，偏离面积积分超伸至极限且伴随超买超卖时，是高胜率箱体边界反转契机！

【多空对称研判与四大王牌高胜率入场形态】
1. 多空双向对称顺势原则（Dual-Direction Trend Following）：
   - 系统多与空同等重要，核心是绝对顺应 4H 宏观与 1H 动量中枢的方向，两手都要抓、两手都要硬！
   - 做多条件（4H多头主浪或箱体下沿）：当 4H 顺势向上，或 1H 处于均线多头排列时，专注顺势做多；1H 回调减速定性为寻找支撑位的打折买点，在现价下方 0.2%~0.6% 挂限价多单；100% 严禁任何逆势摸顶开空。
   - 做空条件（4H空头承压或箱体上沿）：当 4H 宏观受压（4H_MACRO_BEAR），或 1H 均线空头排列时，专注顺势做空；1H 向上弱反弹遇阻回落时逢高做空，在现价上方 0.2%~0.6% 挂限价空单；100% 严禁任何逆势抄底做多。
   - 震荡箱体双向作战：当 4H 处于区间震荡（CHOP / RANGE）时，下沿支撑低吸做多，上沿阻力高抛做空；箱体中间（半山腰）禁止开仓。
2. 四大王牌高胜率入场形态（形态达标必须果断发单）：
   ① 顺势回踩均线/支撑位缩量企稳（Pullback to Value / 做多）；
   ② 顺势空头反弹承压阻力位遇阻回落（Throwback to Resistance / 做空）；
   ③ 假跌破流动性掠夺后迅速收回（Liquidity Sweep & Reclaim / 诱空收网做多）；
   ④ 假突破流动性衰竭后迅速跌回（Liquidity Sweep & Fail / 诱多受挫做空）。
3. 选优开单契约：
   - 【空仓且存在至少一个合法顺势候选时（符合顺势高胜率形态），强制在候选标的池中选优输出，不得无故放弃合规机会】！
   - 只要形态达标且风险收益比 R:R ≥ 2.2，置信度果断给出 78% ~ 88%，通过 75% 门禁进场盈利！

【开仓参数与科学价格几何】
- 顺势铁律（Fail-Closed）：4H_MACRO_BULL 大级别多头通道下 100% 严禁输出 SELL_SHORT 逆势摸顶；4H_MACRO_BEAR 大级别空头承压下 100% 严禁输出 BUY_LONG 逆势抄底！
- 震荡过滤：箱体正中间无序乱跳时一律强制 WAIT，严禁追涨杀跌磨损手续费。
- 价格几何与科学止损（胜率核心保障）：
  - BUY_LONG 必须满足 stop_loss_price < entry_price < take_profit_price；SELL_SHORT 必须满足 take_profit_price < entry_price < stop_loss_price。
  - 目标 R:R ≥ 2.2；执行层绝对拒绝 R:R < 2.0 的报价。
  - 进场必须使用 Maker 限价单挂在支撑/阻力位附近（如现价下方 0.1%~0.4%），严禁市价追单。
  - 止损必须基于结构性保护点（如前低支撑位或箱体边缘下方 0.3%~0.5%），参考 1.8~2.2x 1H ATR，绝不把止损设得过近以防被杂波插针。
  - 单笔保证金建议 5%~15%，强信号可使用 15%~20%；杠杆 2x~5x。

【顺势浮盈金字塔加仓：模型只能申请，执行层拥有最终否决权】
- 已有多仓只能申请同向 BUY_LONG，已有空仓只能申请同向 SELL_SHORT；反向指令不得借加仓通道执行。
- 底仓必须 ROI ≥ +0.8% 且止损已经移至保本/盈利区；最多追加 1 次；单标的累计保证金 ≤ 600 USDT；AI 置信度 ≥ 75%。
- 加多门禁：多周期聚合加速度 a ≥ -0.25 且 continuation_prob_pct ≥ 40%。
- 加空门禁：多周期聚合加速度 a ≤ +0.25 且 breakdown_prob_pct ≥ 40%。
- 浮亏、未脱离成本区、顶部/底部失速、概率不足或肥尾冲击时不得申请加仓。即使模型申请，执行器仍会再次硬校验。

【严格 JSON 规范契约与完整输出骨架 (JSON Schema)】
你必须直接输出一个严格合法的 JSON 对象，禁止输出任何 Markdown 代码围栏、前缀或额外文字。结构必须严格完全符合以下 JSON Schema 骨架：

{
  "macro_assessment": "30字内全市场宏观流动性与大盘走势总结",
  "position_management": [
    {
      "instId": "BTC-USDT-SWAP",
      "action": "HOLD",
      "suggested_sl_price": 0.0,
      "confidence": 85.0,
      "reason": "30字内持仓调整原因与动能简述"
    }
  ],
  "pending_orders_management": [
    {
      "ordId": "在途挂单ID",
      "instId": "BTC-USDT-SWAP",
      "action": "KEEP",
      "reason": "30字内维持或撤单原因"
    }
  ],
  "decisions": {
    "BTC-USDT-SWAP": {
      "action": "BUY_LONG",
      "confidence": 85.0,
      "leverage": 3,
      "margin_usdt": 100.0,
      "entry_price": 79500.0,
      "take_profit_price": 83000.0,
      "stop_loss_price": 77800.0,
      "summary_reason": "顺势回踩支撑企稳限价做多",
      "market_structure": "4H大势多头，1H均线回踩企稳",
      "calculus_dynamics": "1H: v=+0.05, a=+0.20 动能转正",
      "math_prob_rationale": "延续概率65%显著占优，R:R=2.5",
      "volume_and_oi": "量能缩量企稳，主力净流入"
    }
  }
}

【字段审计说明】：
- position_management.action 只允许: "HOLD" | "CLOSE_MARKET" | "UPDATE_SL"；触发峰值回撤超 35% 或 1H 负功率衰竭时果断输出 CLOSE_MARKET 止盈；action 为 UPDATE_SL 时 suggested_sl_price 填目标价格，否则必须填 0.0；
- pending_orders_management.action 只允许: "KEEP" | "CANCEL"；
- decisions[标的].action 只允许: "BUY_LONG" | "SELL_SHORT" | "WAIT"；action 为 WAIT 时 entry_price/take_profit_price/stop_loss_price 填 0.0；
- 每个决策的 calculus_dynamics 与 math_prob_rationale 必须明确引用具体 1H v, a 与概率数值，严禁只写空泛定性词句！"""

def construct_full_market_prompt(packages: List[Dict[str, Any]], pos_summary: str = "[MISSING_CONTEXT:account_positions]", active_positions_detail: List[Dict[str, Any]] = None, pending_orders_detail: List[Dict[str, Any]] = None, current_time_str: str = "", usdt_available: float = None, runtime_context_out: Dict[str, Any] = None, policy_snapshot: Dict[str, Any] = None) -> str:
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj_str = current_time_str or datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S (北京时间)")
    market_lines = []
    for p in packages:
        k15 = p.get("recent_15m", [])
        k1h = p.get("recent_1h", [])
        k4h = p.get("recent_4h", [])
        quality = p.get("data_quality", "invalid")

        sm = p.get("smart_money", {})
        adx_val = p.get("adx_1h", "--")
        calc = p.get("calculus", {})
        calc_tfs = calc.get("timeframes", {}) if isinstance(calc, dict) else {}
        d_int = calc.get("definite_integrals", {}) if isinstance(calc, dict) else {}
        p_th = calc.get("probability_theory", {}) if isinstance(calc, dict) else {}
        calc_1h = calc_tfs.get("1H", {}) if isinstance(calc_tfs.get("1H", {}), dict) else {}
        int_1h = calc_1h.get("definite_integrals", {}) if isinstance(calc_1h, dict) else {}
        prob_1h = calc_1h.get("probability_theory", {}) if isinstance(calc_1h, dict) else {}

        calc_line = (
            f"动力学态={calc.get('regime', 'DATA_UNRELIABLE')} | 速度={calc.get('velocity', '--')} "
            f"| 加速度={calc.get('acceleration', '--')} | 累计冲量={calc.get('impulse', '--')} "
            f"| 冲击变化={calc.get('max_abs_jerk', '--')} | 质量={calc.get('quality', 0)}"
        )
        integral_line = (
            f"多周期净做功积分={d_int.get('energy_integral', 'UNKNOWN')} | 路径偏离面积积分={d_int.get('deviation_area_integral', 'UNKNOWN')} "
            f"| 量价作用积分={d_int.get('volume_action_integral', 'UNKNOWN')} | 能量态={d_int.get('regime', 'UNKNOWN')}"
        )
        prob_line = (
            f"多头延续估计概率={p_th.get('continuation_prob_pct', 'UNKNOWN')}% | 空头击穿估计概率={p_th.get('breakdown_prob_pct', 'UNKNOWN')}% "
            f"| 偏度S={p_th.get('skewness', 'UNKNOWN')} | 超额峰度K={p_th.get('kurtosis', 'UNKNOWN')} "
            f"| 95%VaR={p_th.get('var_95_pct', 'UNKNOWN')}% | 95%CVaR={p_th.get('cvar_95_pct', 'UNKNOWN')}% "
            f"| 尾部风险态={p_th.get('regime', 'UNKNOWN')}"
        )
        core_math_line = (
            f"1H:v={calc_1h.get('velocity', 'UNKNOWN')},a={calc_1h.get('acceleration', 'UNKNOWN')},"
            f"j={calc_1h.get('jerk', 'UNKNOWN')},I={calc_1h.get('impulse', 'UNKNOWN')},态={calc_1h.get('regime', 'UNKNOWN')} "
            f"| E={int_1h.get('energy_integral', 'UNKNOWN')},A={int_1h.get('deviation_area_integral', 'UNKNOWN')} "
            f"| P续={prob_1h.get('continuation_prob_pct', 'UNKNOWN')}%,P破={prob_1h.get('breakdown_prob_pct', 'UNKNOWN')}%,"
            f"VaR={prob_1h.get('var_95_pct', 'UNKNOWN')}%,CVaR={prob_1h.get('cvar_95_pct', 'UNKNOWN')}%"
        )
        calc_tf_line = "；".join(
            f"{tf}:v={v.get('velocity', '--')},a={v.get('acceleration', '--')},I={v.get('impulse', '--')},态={v.get('regime', '--')}"
            for tf, v in calc_tfs.items() if isinstance(v, dict)
        )
        info = f"""---------------------------------------------------------
【{p['name']} ({p['instId']})】| 数据质量: {quality} | 现价: {p['price']} | 24H涨跌: {p['chg24h']}% | 盘口买/卖: {p['bidPx']}/{p['askPx']}
- 🏛️ 三重滤网宏观结构: 4H宏观大势={p.get('macro_4h', '4H_MACRO_RANGE')} | 1H波段结构={p.get('structure_1h', '1H_SWING_CHOP')}
- 👑 顶级聪明钱 (SmartMoney Top100): 加权做多占比={sm.get('weighted_long_pct', 50)}% | 24H净流入={sm.get('net_flow_usdt', '--')} | 多头均价={sm.get('avg_long_entry', '--')} | 空头均价={sm.get('avg_short_entry', '--')} | {sm.get('top_win_rate', '')}
- 📐 1H核心波段指标: 1H ATR(14)={p.get('atr_1h', p.get('atr', '--'))} (止损基准: 1.5~2.0x 1H ATR) | 1H RSI(14)={p.get('rsi_1h', '--')} | 1H ADX趋势强度={adx_val} (注:<20无趋势垃圾市, ≥22强单边)
- ⚡ 15M微观执行参考: 15M ATR={p.get('atr_15m', '--')} | 15M RSI={p.get('rsi_15m', '--')} | VWAP乖离={p.get('vwap_bias', '--')}% | 15M量比={p.get('vol_ratio', '--')}x | OBV资金流={p.get('obv_flow', '--')}
- 📐 1H三大数理基石硬证据: {core_math_line}
- ∂ 多周期微积分动力学摘要: {calc_line}
- ∫ 定积分能量学: {integral_line}
- ⚅ 概率论与统计风险: {prob_line}
- ∂ 分周期速度/加速度/冲量: {calc_tf_line or 'UNKNOWN'}
- 衍生品博弈: 资金费率: {p['fundingRate']}% | OI未平仓: {p['oiUsd']} | 多空比: {p['lsRatio']} | 5M主动吃单净差: {p['takerNetUsd']}
- 15M K线(倒序12根 [O,H,L,C,V]): {k15}
- 1H K线(倒序12根 [O,H,L,C,V]): {k1h}
- 4H K线(倒序8根 [O,H,L,C,V]): {k4h}"""
        market_lines.append(info)

    all_market_str = "\n".join(market_lines)

    pos_lines = []
    if active_positions_detail and len(active_positions_detail) > 0:
        for p in active_positions_detail:
            inst_name = p.get('name') or p.get('instId')
            side = p.get('side') or p.get('posSide', 'long')
            is_long = "long" in str(side).lower()
            entry_px = safe_float(p.get('avgPx', 0))
            cur_px = safe_float(p.get('markPx') or p.get('lastPx') or entry_px)
            hwm = safe_float(p.get('highWaterMark', 0))
            lwm = safe_float(p.get('lowWaterMark', 0))
            tp_px = p.get('takeProfitPx', '--')
            stage_desc = p.get('stage_desc', '持有监控中')

            profit_desc = ""
            if is_long and hwm > entry_px and entry_px > 0:
                peak_gain_pct = round((hwm - entry_px) / entry_px * 100, 2)
                dd_from_peak = round((hwm - cur_px) / (hwm - entry_px) * 100, 1) if hwm > entry_px else 0.0
                profit_desc = f" | 曾最高到: {hwm} (极值浮盈 +{peak_gain_pct}%, 现已从极值回撤 {dd_from_peak}%)"
            elif not is_long and lwm > 0 and lwm < entry_px and entry_px > 0:
                peak_gain_pct = round((entry_px - lwm) / entry_px * 100, 2)
                dd_from_peak = round((cur_px - lwm) / (entry_px - lwm) * 100, 1) if lwm < entry_px else 0.0
                profit_desc = f" | 曾最低到: {lwm} (极值浮盈 +{peak_gain_pct}%, 现已从极值回撤 {dd_from_peak}%)"

            pos_lines.append(
                f"- 标的: {inst_name} | 方向: {side} {p.get('lever', '3')}x | 开仓均价: {p.get('avgPx')} | 当前价: {cur_px} | 浮盈: {p.get('upl')} U (ROI: {round(safe_float(p.get('uplRatio')) * 100, 2)}%){profit_desc} | 动态止损线: {p.get('trailingStopPx', p.get('trailingSl', '--'))} | 目标止盈: {tp_px} | 状态: {stage_desc}"
            )
    else:
        pos_lines.append("[MISSING_CONTEXT:account_positions]" if active_positions_detail is None else "当前无任何在途持仓敞口 (100% 现金空仓状态)")

    active_pos_text = "\n".join(pos_lines)

    # Format Pending Limit Orders
    pending_lines = []
    if pending_orders_detail and len(pending_orders_detail) > 0:
        for o in pending_orders_detail:
            c_ts = int(o.get("cTime", 0) or 0) / 1000.0
            c_time_str = datetime.datetime.fromtimestamp(c_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_ts > 0 else "--"
            inst_id = o.get("instId", "")
            side_raw = str(o.get("side", "")).lower()
            pos_side = str(o.get("posSide", "net")).lower()
            reduce_only = str(o.get("reduceOnly", "false")).lower() == "true"
            ord_type = str(o.get("ordType", "limit")).lower()

            if reduce_only:
                side_str = "市价平多" if (side_raw == "sell" and ord_type == "market") else ("限价平多" if side_raw == "sell" else ("市价平空" if ord_type == "market" else "限价平空"))
            else:
                side_str = "限价买多" if (side_raw == "buy" and ord_type != "market") else ("市价买多" if side_raw == "buy" else ("限价卖空" if ord_type != "market" else "市价卖空"))

            raw_px = str(o.get("px") or "").strip()
            px_val = raw_px if raw_px and raw_px != "0" else ("市价" if ord_type == "market" else "--")
            sz_val = str(o.get("sz", "--"))
            ord_id = str(o.get("ordId", ""))

            attach_list = o.get("attachAlgoOrds", [])
            tp_sl_info = ""
            if attach_list and len(attach_list) > 0:
                att = attach_list[0]
                tp_p = att.get("tpTriggerPx", "--")
                sl_p = att.get("slTriggerPx", "--")
                tp_sl_info = f" | 附带云端止盈: {tp_p} / 止损: {sl_p}"

            pending_lines.append(
                f"- [挂单ID: {ord_id}] {inst_id} | {side_str} {sz_val}张 @ {px_val} | 挂单时间: {c_time_str}{tp_sl_info}"
            )
    else:
        pending_lines.append("[MISSING_CONTEXT:pending_orders]" if pending_orders_detail is None else "当前无任何在途未成交限价挂单 (挂单池为空)")

    pending_orders_text = "\n".join(pending_lines)

    from scripts.evolution_shield import render_trading_memory
    # Damaged authority raises; empty authority never falls back to legacy text.
    memory_lessons = render_trading_memory(AI_MEMORY_MD_FILE, AI_MEMORY_FILE)

    # Harvest Latest Live News & Multi-Coin Sentiment
    news_briefs = []
    macro_env = "中性平衡"
    if os.path.exists(NEWS_SENTIMENT_FILE):
        try:
            with open(NEWS_SENTIMENT_FILE, "r", encoding="utf-8") as f:
                ns_data = json.load(f)
                macro_env = ns_data.get("macro_sentiment", "中性平衡")
                for n in ns_data.get("latest_news", [])[:6]:
                    news_briefs.append(f"- [{n.get('time', '')}] {n.get('title', '')} ({n.get('summary', '')[:80]}...)")
        except Exception:
            pass

    news_text = "\n".join(news_briefs) if news_briefs else "无可验证新闻输入；不得据此推断市场平稳或不存在事件风险"

    avail_balance_str = f"{usdt_available:.2f} USDT" if usdt_available is not None and usdt_available >= 0 else "[MISSING_CONTEXT:account_balance]"

    prompt = f"""======================= 【当前决策时间戳与市场时效】 =======================
【推演基准时间】: {now_bj_str}
【当前账户可用资金】: {avail_balance_str}

======================= 【全网实时重大快讯与宏观情报】 =======================
【宏观环境基调】: {macro_env}
【最新核心资讯要闻】:
{news_text}

======================= 【账户当前持仓与风险敞口全景】 =======================
【账户持仓概况】: {pos_summary}
【当前活动在途持仓明细】:
{active_pos_text}

======================= 【在途未成交限价挂单 (Pending Maker Orders)】 =======================
【当前在途挂单列表】:
{pending_orders_text}

{memory_lessons}

======================= 【六币种原生行情、技术指标与筹码矩阵】 =======================
{all_market_str}

================================================================================
【推演与决策任务】:
你只能在 System Prompt 的 P0 硬约束内进行综合裁决。按“数据有效性 → 4H方向 → 1H三大数理基石 → 量能/OI/聪明钱 → 15M执行位置”的顺序逐项检查；任一硬条件失败或证据无法闭环时，开仓输出 WAIT：
1. 【在途持仓管理 (科学持仓与动态风控)】：
   - 逐一分析当前在途持仓：
     • 若 1H 波段趋势完好且微积分动能平稳，坚决坚定持有 (HOLD)，给大波段充分呼吸空间；
     • 若出现【1H 结构破位 / 动能加速度严重逆转 / 聪明钱反向出逃】等真实趋势逆转信号且置信度 ≥ 85%，果断输出 CLOSE_MARKET 提前斩仓止损，杜绝死等硬止损；
     • 若底仓浮盈已超过 1.2x 1H ATR 且需锁定利润，输出 UPDATE_SL 并确保新止损与现价保留 0.7x 1H ATR 安全缓冲，严禁贴脸移动止损。
2. 【在途限价挂单生命周期审查与裁决 (Pending Orders Management)】：
   - 仔细审查上述在途未成交挂单：若挂单价格已大幅偏离最新盘口、或者行情动能/突发要闻已转变导致原挂单计划失效，必须在 pending_orders_management 中为该挂单输出 CANCEL 立即撤单指令，防止挂单成交在不利价格；若原计划仍然有效且价格合适，输出 KEEP 维持挂单。
3. 【多空开仓与顺势浮盈加仓全权裁决 (Opening & Pyramiding)】：
   - 【首发开仓】：自主判断未持仓品种是否具备确定性爆发机会，结合最新资讯、多周期形态与筹码，决定多空方向 (action: BUY_LONG / SELL_SHORT / WAIT)；
   - 【顺势浮盈金字塔加仓申请】：已有多仓仅可输出同向 BUY_LONG，已有空仓仅可输出同向 SELL_SHORT；这只是加仓申请，执行层仍将复核底仓 ROI/保本、最多1次、累计保证金≤600U、置信度≥75%、加速度与延续/击穿概率门禁。任何不确定均输出 WAIT；
   - 自主规划拟开仓/加仓保证金 (margin_usdt: 可用余额的 5%~20%，且不得超过系统上限) 与杠杆 (2~5x)；
   - 自主规划 entry_price、take_profit_price 与 stop_loss_price；目标 R:R ≥ 2.5，且任何 R:R < 2.0 的报价会被执行层拒绝。
4. 必须输出严格 JSON，格式如下：
{{
  "macro_assessment": "30字内全市场宏观流动性与情绪总结",
  "position_management": [
    {{
      "instId": "LINK-USDT-SWAP",
      "action": "HOLD" | "CLOSE_MARKET" | "UPDATE_SL",
      "suggested_sl_price": float (若调整止损填具体价格，否则0),
      "confidence": 0~100,
      "reason": "30字内持仓调整原因与当前动能分析"
    }}
  ],
  "pending_orders_management": [
    {{
      "ordId": "3879092142614409217",
      "instId": "LINK-USDT-SWAP",
      "action": "KEEP" | "CANCEL",
      "reason": "30字内撤单或维持挂单原因"
    }}
  ],
  "decisions": {{
    "BTC-USDT-SWAP": {{
      "action": "BUY_LONG" | "SELL_SHORT" | "WAIT",
      "confidence": 0~100,
      "leverage": 3 (推荐杠杆2~5),
      "margin_usdt": 50.0 (推荐保证金),
      "entry_price": float,
      "take_profit_price": float,
      "stop_loss_price": float,
      "summary_reason": "30字内核心逻辑",
      "market_structure": "4H/1H趋势与15M短线形态",
      "calculus_dynamics": "必须引用1H具体 v/a/j/I、状态及方向解释；WAIT也需说明冲突或缺失",
      "math_prob_rationale": "必须引用具体 E/A、延续或击穿估计概率、VaR/CVaR与肥尾风险",
      "volume_and_oi": "量能/筹码流向简述"
    }},
    ... (依次包含全部标的)
  }}
}}
"""
    runtime_vars = {
        "decision_timestamp": f"【推演基准时间】: {now_bj_str}",
        "account_balance": f"【当前账户可用资金】: {avail_balance_str}",
        "account_positions": f"【账户持仓概况】: {pos_summary}\n【当前活动在途持仓明细】:\n{active_pos_text}",
        "pending_orders": f"【当前在途挂单列表】:\n{pending_orders_text}",
        "news_intelligence": f"【宏观环境基调】: {macro_env}\n【最新核心资讯要闻】:\n{news_text}",
        "trading_memory": memory_lessons.strip(),
        "market_matrix": all_market_str,
    }
    try:
        from r20_backend.version import __version__ as _sys_ver
    except Exception:
        _sys_ver = "7.5.1"
    profile = active_profile()
    policy_ver = (policy_snapshot or {}).get("policy_version") or os.getenv("R20_VERSION", f"v{_sys_ver}")
    policy_hash = (policy_snapshot or {}).get("policy_hash") or ""
    runtime_vars.update({
        "timestamp": now_bj_str, "timezone": "Asia/Shanghai",
        "active_instruments": ",".join(str(p.get("name") or p.get("instId") or "") for p in packages),
        "strategy_version": policy_ver,
        "policy_version": policy_ver,
        "policy_hash": policy_hash,
        "profile_name": profile.get("name", ""),
    })
    if runtime_context_out is not None:
        runtime_context_out.update(runtime_vars)
        if policy_snapshot:
            runtime_context_out["policy_snapshot"] = policy_snapshot
    return apply_module_layout(prompt, profile, "trading_user", f"{profile.get('name', '稳健')}交易用户提示词模板", context=runtime_vars)

def validate_and_filter_decision(p: Dict[str, Any], d_item: Dict[str, Any], active_inst_ids: set, active_position_sides: Dict[str, str]) -> tuple[str, str, float]:
    """
    Fail-closed execution layer gatekeeper powered by pluggable interceptors.
    1. Base pre-checks: data completeness & opposing position collision
    2. Dynamic interceptor pipeline: runs all enabled Python interceptor plugins
    """
    context = {
        "active_inst_ids": active_inst_ids,
        "active_position_sides": active_position_sides,
    }
    try:
        from r20_backend.interceptor_manager import run_interceptor_pipeline
        return run_interceptor_pipeline(p, d_item, context)
    except Exception as exc:
        # Fail-closed fallback in case interceptor manager cannot be reached
        inst_id = p.get("instId", "")
        raw_action = str((d_item or {}).get("action", "WAIT")).upper()
        if raw_action not in {"BUY_LONG", "SELL_SHORT", "WAIT"}:
            raw_action = "WAIT"
        entry = safe_float((d_item or {}).get("entry_price"))
        take_profit = safe_float((d_item or {}).get("take_profit_price"))
        stop_loss = safe_float((d_item or {}).get("stop_loss_price"))
        rr = 0.0
        if raw_action == "BUY_LONG" and entry > stop_loss > 0 and take_profit > entry:
            rr = (take_profit - entry) / (entry - stop_loss)
        elif raw_action == "SELL_SHORT" and stop_loss > entry > take_profit > 0:
            rr = (entry - take_profit) / (stop_loss - entry)
        return "WAIT", f"拦截插件管线调用异常: {exc}，安全降级为 WAIT", rr


def assemble_decision_cache(
    packages: List[Dict[str, Any]],
    decisions_dict: Dict[str, Any],
    active_inst_ids: set,
    active_position_sides: Dict[str, str],
    time_str: str,
    macro_summary: str,
    policy_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pure assembly of validated decisions into the standard cache contract, bound to policy snapshot."""
    policy_snapshot = policy_snapshot or {}
    p_ver = policy_snapshot.get("policy_version", f"{_get_system_version_tag()}@unknown")
    p_hash = policy_snapshot.get("policy_hash", "unknown")
    p_summary = policy_snapshot.get("summary", "")

    standard_cache = {}
    # Load dynamic asset multipliers from self-improvement review if present
    asset_multipliers = {}
    try:
        mult_file = os.path.join(DATA_DIR, "asset_multipliers.json")
        if os.path.isfile(mult_file):
            with open(mult_file, "r", encoding="utf-8") as f:
                mult_data = json.load(f)
            asset_multipliers = mult_data.get("multipliers") or {}
    except Exception:
        pass

    for p in packages:
        inst_id = p["instId"]
        d_item = decisions_dict.get(inst_id, {})
        if not isinstance(d_item, dict):
            d_item = {}
        # Smooth field alias normalization (support both standard contract and council desk outputs)
        entry = safe_float(d_item.get("entry_price") or d_item.get("limit_price"))
        take_profit = safe_float(d_item.get("take_profit_price") or d_item.get("take_profit"))
        stop_loss = safe_float(d_item.get("stop_loss_price") or d_item.get("stop_loss"))
        confidence = max(0.0, min(100.0, safe_float(d_item.get("confidence"))))
        ai_leverage = int(max(2, min(5, round(safe_float(d_item.get("leverage", 3))))))
        raw_margin = safe_float(d_item.get("margin_usdt") or d_item.get("margin_usd", 0.0))

        # Dynamically apply self-improvement asset multiplier (e.g. BTC 1.2x, DOGE 0.8x)
        sym_key = inst_id.split("-")[0] if "-" in inst_id else inst_id
        mult = float(asset_multipliers.get(sym_key, asset_multipliers.get(inst_id, 1.0)))
        mult = max(0.5, min(1.5, mult))
        ai_margin = round(raw_margin * mult, 2) if raw_margin > 0 else 0.0

        # Ensure normalized keys exist for downstream interceptors
        normalized_d_item = dict(d_item)
        normalized_d_item["entry_price"] = entry
        normalized_d_item["take_profit_price"] = take_profit
        normalized_d_item["stop_loss_price"] = stop_loss
        normalized_d_item["margin_usdt"] = ai_margin
        normalized_d_item["leverage"] = ai_leverage

        final_action, rejection_reason, rr = validate_and_filter_decision(
            p, normalized_d_item, active_inst_ids, active_position_sides
        )

        standard_cache[inst_id] = {
            "instId": inst_id,
            "name": p["name"],
            "timestamp": int(time.time()),
            "time_str": time_str,
            "policy_version": p_ver,
            "policy_hash": p_hash,
            "policy_snapshot": {
                "policy_version": p_ver,
                "policy_hash": p_hash,
                "summary": p_summary,
            },
            "macro_assessment": macro_summary,
            "thought_process": {
                "market_structure": d_item.get("market_structure", "多周期结构中性"),
                "calculus_dynamics": d_item.get("calculus_dynamics", "模型未提供具体微积分证据"),
                "math_prob_rationale": d_item.get("math_prob_rationale", "模型未提供具体定积分与概率证据"),
                "volume_and_oi": d_item.get("volume_and_oi", f"OI: {p.get('oiUsd', '--')}, Taker: {p.get('takerNetUsd', '--')}"),
                "risk_reward_evaluation": "目标 R:R ≥ 2.5；执行底线 2.0"
            },
            "smart_money": p.get("smart_money", {}),
            "adx_1h": p.get("adx_1h", "--"),
            "decision": {
                "action": final_action,
                "confidence": confidence,
                "leverage": ai_leverage,
                "margin_usdt": ai_margin,
                "entry_price": entry,
                "take_profit_price": take_profit,
                "stop_loss_price": stop_loss,
                "risk_reward_ratio": f"{rr:.2f} : 1" if rr > 0 else "--",
                "summary_reason": rejection_reason or str(d_item.get("summary_reason", "全市场矩阵综合评估中"))[:120]
            },
            "data_quality": p.get("data_quality", "invalid"),
            "raw_ticker": {
                "last": p.get("price"),
                "bidPx": p.get("bidPx"),
                "askPx": p.get("askPx"),
                "chg24h": p.get("chg24h")
            },
            "raw_funding_rate": f"{p['fundingRate']}%" if p.get('fundingRate') else "--",
            "raw_oi": p.get('oiUsd') or "--",
            "raw_taker_vol": p.get('takerNetUsd') or "--",
            "raw_ls_ratio": str(p.get('lsRatio')) if p.get('lsRatio') is not None else "--"
        }

    return standard_cache


@single_brain_cycle
def execute_batch_ai_brain_cycle(
    pos_summary: str = "[MISSING_CONTEXT:account_positions]",
    active_positions_detail: List[Dict[str, Any]] = None,
    usdt_available: float = None,
    policy_snapshot: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch all six crypto symbols, call the LLM once, then persist an auditable result."""
    base_url, api_key = get_cpa_client_config()
    if not api_key:
        print("[AI Brain Batch] Error: CPA API Key not found")
        return None

    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    time_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")

    # Capture immutable Policy Snapshot at start of decision cycle
    if policy_snapshot is None:
        try:
            from policy_snapshot import generate_policy_snapshot
            policy_snapshot = generate_policy_snapshot()
        except Exception:
            try:
                from r20_backend.policy_snapshot import generate_policy_snapshot
                policy_snapshot = generate_policy_snapshot()
            except Exception as exc:
                print(f"[AI Brain Batch] Policy snapshot warning: {exc}")
                policy_snapshot = {
                    "policy_version": f"{_get_system_version_tag()}@unknown",
                    "policy_hash": "unknown",
                    "summary": "policy_snapshot_fallback",
                    "units": {},
                }
    policy_version = policy_snapshot.get("policy_version", f"{_get_system_version_tag()}@unknown")
    policy_hash = policy_snapshot.get("policy_hash", "unknown")
    policy_summary = policy_snapshot.get("summary", "")
    print(f"[AI Brain Batch] 📌 当前决策策略快照: {policy_version} ({policy_hash})")

    print(f"[AI Brain Batch] 并行获取 {len(TARGET_INSTRUMENTS)} 币种原生行情、技术指标与顶级聪明钱数据...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        packages = list(executor.map(fetch_single_instrument_package, TARGET_INSTRUMENTS))

    # Fetch OKX Smart Money Signals
    try:
        instruments_ccy = ",".join([p["name"] for p in packages])
        sm_cmd = f"okx smartmoney signal-overview-by-filter --instCcyList {instruments_ccy} --json 2>/dev/null"
        sm_res = subprocess.run(sm_cmd, shell=True, capture_output=True, text=True, timeout=8)
        if sm_res.stdout:
            sm_data = json.loads(sm_res.stdout).get("data", [])
            sm_dict = {item.get("ccy"): item for item in sm_data if item.get("ccy")}
            for p in packages:
                ccy = p["name"]
                if ccy in sm_dict:
                    item = sm_dict[ccy]
                    ls = item.get("longShortRatio", {})
                    notional = item.get("notional", {})
                    win = item.get("winRate", {})
                    w_long = round(float(ls.get("weightedLongRatio", 0.5)) * 100, 1)
                    net_usdt = float(notional.get("netNotionalUsdt", 0) or 0)
                    net_flow_str = f"{round(net_usdt / 1e4, 1)}万 U" if abs(net_usdt) >= 1e4 else f"{round(net_usdt, 0)} U"
                    long_cost = notional.get("smartMoneyLongAvgEntry") or "--"
                    short_cost = notional.get("smartMoneyShortAvgEntry") or "--"
                    top_win = f"多胜率{round(float(win.get('avgLongWinRate', 0))*100, 1)}%" if win.get('avgLongWinRate') else "--"

                    p["smart_money"] = {
                        "weighted_long_pct": w_long,
                        "net_flow_usdt": net_flow_str,
                        "avg_long_entry": str(long_cost)[:10],
                        "avg_short_entry": str(short_cost)[:10],
                        "top_win_rate": top_win
                    }
    except Exception as e:
        print(f"[AI Brain Batch] SmartMoney fetch warning: {e}")

    positions_context = active_positions_detail
    active_positions_detail = active_positions_detail or []
    active_inst_ids = {
        str(p.get("instId", "")) for p in active_positions_detail if p.get("instId")
    }
    active_position_sides = {
        str(p.get("instId", "")): str(p.get("side", p.get("posSide", ""))).lower()
        for p in active_positions_detail if p.get("instId")
    }
    package_by_id = {p["instId"]: p for p in packages}

    # Automatically Update & Persist Comprehensive Factor Library Snapshot
    try:
        sys.path.append(os.path.join(WORKSPACE_DIR, "scripts"))
        import factor_library
        factor_library.update_factor_library()
    except Exception as e:
        print(f"[AI Brain Batch] Factor Library update warning: {e}")

    # Fetch live pending limit orders from exchange
    pending_orders_list = None
    try:
        ord_cmd = okx_private_command("okx swap orders --json 2>/dev/null")
        ord_res = subprocess.run(ord_cmd, shell=True, capture_output=True, text=True, timeout=8)
        if ord_res.returncode == 0 and ord_res.stdout:
            pending_orders_list = json.loads(ord_res.stdout)
            if not isinstance(pending_orders_list, list):
                pending_orders_list = None
    except Exception as e:
        print(f"[AI Brain Batch] Pending orders fetch warning: {e}")

    try:
        calculus_snapshot = {
            "timestamp": time_str,
            "engine": "causal-calculus-v1",
            "instruments": [
                {"name": p.get("name"), "instId": p.get("instId"), "calculus": p.get("calculus", {})}
                for p in packages
            ],
        }
        tmp_calc = CALCULUS_SNAPSHOT_FILE + ".tmp"
        with open(tmp_calc, "w", encoding="utf-8") as f:
            json.dump(calculus_snapshot, f, ensure_ascii=False, indent=2)
        os.replace(tmp_calc, CALCULUS_SNAPSHOT_FILE)
    except Exception as exc:
        print(f"[AI Brain] Calculus snapshot warning: {exc}")

    runtime_context = {}
    prompt = construct_full_market_prompt(packages, pos_summary, positions_context, pending_orders_detail=pending_orders_list, current_time_str=time_str, usdt_available=usdt_available, runtime_context_out=runtime_context, policy_snapshot=policy_snapshot)

    profile = active_profile()
    effective_system_prompt = apply_module_layout(
        get_effective_system_prompt(), profile, "trading_system", f"{profile.get('name', '稳健')}交易系统提示词模板", context=runtime_context
    )

    # Save Realtime Prompt Snapshot for Web Transparent Inspection
    try:
        tmp_prompt = AI_LAST_PROMPT_FILE + ".tmp"
        with open(tmp_prompt, "w", encoding="utf-8") as f:
            f.write(f"【SYSTEM PROMPT】:\n{effective_system_prompt.strip()}\n\n{'='*70}\n【USER PROMPT ({time_str})】：\n{prompt.strip()}")
        os.replace(tmp_prompt, AI_LAST_PROMPT_FILE)
    except Exception:
        pass

    model_name = os.environ.get("LLM_MODEL") or ""
    effort = os.environ.get("LLM_REASONING_EFFORT") or "high"
    api_format = "openai_chat"
    thinking_timeout = float(os.environ.get("LLM_THINKING_TIMEOUT", os.environ.get("LLM_TIMEOUT_SECONDS", 120.0)))
    try:
        from r20_backend.llm_manager import get_active_llm_runtime, execute_llm_request
        active_llm = get_active_llm_runtime()
        model_name = os.environ.get("LLM_MODEL") or active_llm.get("model") or model_name
        effort = os.environ.get("LLM_REASONING_EFFORT") or active_llm.get("reasoning_effort") or effort
        api_format = active_llm.get("api_format", "openai_chat")
        base_url = active_llm.get("base_url") or base_url
        api_key = active_llm.get("api_key") or api_key
        thinking_timeout = float(active_llm.get("thinking_timeout") or thinking_timeout)
    except Exception:
        execute_llm_request = None

    telemetry = ModelCallTelemetry(
        "trading_brain", model_name, str(effort), effective_system_prompt, prompt
    )
    try:
        t0 = time.time()
        raw_res = None
        brain_output = None

        # Transparent check: is Multi-Agent Council enabled?
        council_enabled = False
        try:
            from r20_backend.council_manager import load_council_config, execute_council_debate
            c_cfg = load_council_config()
            council_enabled = bool(c_cfg.get("enabled"))
        except Exception:
            council_enabled = False

        if council_enabled:
            print(f"[AI Brain Council] 🏛️ 多模型委员会已开启，正在启动各专家参谋现场辩论与首席仲裁...")
            try:
                brain_output, council_transcript = execute_council_debate(
                    market_prompt=prompt,
                    original_system_prompt=effective_system_prompt,
                    timeout=float(c_cfg.get("timeout_seconds", 60.0)),
                )
                print(f"[AI Brain Council] ✅ 委员会辩论与终审完成，耗时: {council_transcript.get('total_duration_ms', 0)}ms")
            except Exception as e:
                print(f"[AI Brain Council] ⚠️ 委员会决策超时或异常: {e}，自动降级为单模型极速决策！")
                brain_output = None

        if brain_output is None:
            print(f"[AI Brain Batch] 🚀 正在发起单次全市场大模型宏观决策推演 ({model_name} / {api_format} / 思考上限 {thinking_timeout:.0f}s)...")
            if execute_llm_request:
                content, _, usage_dict, _ = execute_llm_request(
                    messages=[
                        {"role": "system", "content": effective_system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    model=model_name,
                    base_url=base_url,
                    api_key=api_key,
                    api_format=api_format,
                    reasoning_effort=effort,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    timeout=thinking_timeout,
                )
                raw_res = {"usage": usage_dict} if isinstance(usage_dict, dict) else {}
            else:
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": effective_system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"}
                }
                if effort not in ("none", "auto"):
                    payload["reasoning_effort"] = effort
                req = urllib.request.Request(
                    f"{base_url}/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=thinking_timeout) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    content = res["choices"][0]["message"]["content"].strip()
                    raw_res = res

            if content.startswith("```json"): content = content[7:]
            if content.startswith("```"): content = content[3:]
            if content.endswith("```"): content = content[:-3]

            brain_output = json.loads(content.strip())
            if not isinstance(brain_output, dict):
                raise ValueError("LLM response root must be an object")
        decisions_dict = brain_output.get("decisions", {})
        pos_mgmt_list = brain_output.get("position_management", [])
        macro_summary = str(brain_output.get("macro_assessment", "宏观中性震荡"))[:120]
        if not isinstance(decisions_dict, dict):
            decisions_dict = {}
        if not isinstance(pos_mgmt_list, list):
            pos_mgmt_list = []

        validated_pos_mgmt = []
        seen_positions = set()
        for item in pos_mgmt_list:
            if not isinstance(item, dict):
                continue
            inst_id = str(item.get("instId", ""))
            if inst_id not in active_inst_ids or inst_id in seen_positions:
                continue
            seen_positions.add(inst_id)
            action = str(item.get("action", "HOLD")).upper()
            if action not in {"HOLD", "CLOSE_MARKET", "UPDATE_SL"}:
                action = "HOLD"
            confidence = max(0.0, min(100.0, safe_float(item.get("confidence"))))
            suggested_sl = safe_float(item.get("suggested_sl_price"))
            if action != "UPDATE_SL":
                suggested_sl = 0.0
            validated_pos_mgmt.append({
                "instId": inst_id,
                "action": action,
                "suggested_sl_price": suggested_sl,
                "confidence": confidence,
                "reason": str(item.get("reason", "模型未提供持仓理由"))[:120]
            })

        for inst_id in sorted(active_inst_ids - seen_positions):
            validated_pos_mgmt.append({
                "instId": inst_id,
                "action": "HOLD",
                "suggested_sl_price": 0.0,
                "confidence": 0.0,
                "reason": "模型遗漏该持仓，安全降级为 HOLD"
            })
        pos_mgmt_list = validated_pos_mgmt

        # Execute Pending Orders Cancellation if AI Brain decides CANCEL
        pending_mgmt_list = brain_output.get("pending_orders_management", [])
        if isinstance(pending_mgmt_list, list):
            for p_order in pending_mgmt_list:
                if not isinstance(p_order, dict):
                    continue
                p_act = str(p_order.get("action", "")).upper()
                p_ord_id = str(p_order.get("ordId", ""))
                p_inst_id = str(p_order.get("instId", ""))
                p_reason = str(p_order.get("reason", "模型指示撤销该挂单"))
                if p_act == "CANCEL" and p_ord_id and p_inst_id:
                    cxl_cmd = okx_private_command(f"okx swap cancel {p_inst_id} --ordId {p_ord_id} --json")
                    cxl_res = subprocess.run(cxl_cmd, shell=True, capture_output=True, text=True, timeout=10)
                    print(f"[AI Brain Batch] 🛑 AI自主撤回失效/过时限价单: {p_inst_id} (ordId={p_ord_id}, 原因={p_reason})")

        standard_cache = assemble_decision_cache(
            packages=packages,
            decisions_dict=decisions_dict,
            active_inst_ids=active_inst_ids,
            active_position_sides=active_position_sides,
            time_str=time_str,
            macro_summary=macro_summary,
            policy_snapshot=policy_snapshot,
        )

        atomic_write_json(AI_DECISION_CACHE_FILE, standard_cache)
        atomic_write_json(AI_POSITION_MANAGEMENT_FILE, {
            "timestamp": int(time.time()),
            "time_str": time_str,
            "policy_version": policy_version,
            "policy_hash": policy_hash,
            "instructions": pos_mgmt_list
        })

        # Record durable history for Web Audit
        full_prompt_text = f"【SYSTEM PROMPT ({policy_version})】：\n{effective_system_prompt.strip()}\n\n{'='*70}\n【USER PROMPT ({time_str})】：\n{prompt.strip()}"
        history_record = {
            "time": time_str,
            "policy_version": policy_version,
            "policy_hash": policy_hash,
            "policy_snapshot": policy_snapshot,
            "policy_snapshot_summary": policy_summary,
            "macro_assessment": macro_summary,
            "ai_last_prompt": full_prompt_text,
            "position_management": pos_mgmt_list,
            "council_transcript": brain_output.get("council_transcript") if isinstance(brain_output, dict) else None,
            "top_opportunities": [
                {
                    "inst": p["name"],
                    "action": standard_cache[p["instId"]]["decision"]["action"],
                    "confidence": standard_cache[p["instId"]]["decision"]["confidence"],
                    "leverage": standard_cache[p["instId"]]["decision"].get("leverage", 3),
                    "margin_usdt": standard_cache[p["instId"]]["decision"].get("margin_usdt", 0.0),
                    "risk_reward_ratio": standard_cache[p["instId"]]["decision"]["risk_reward_ratio"],
                    "data_quality": standard_cache[p["instId"]]["data_quality"],
                    "policy_version": policy_version,
                    "reason": standard_cache[p["instId"]]["decision"]["summary_reason"]
                }
                for p in packages
            ]
        }

        history_list = []
        if os.path.exists(AI_DECISION_HISTORY_FILE):
            try:
                with open(AI_DECISION_HISTORY_FILE, "r", encoding="utf-8") as f:
                    history_list = json.load(f)
            except Exception:
                pass

        history_list.insert(0, history_record)
        history_list = history_list[:50] # Keep recent 50 rounds

        atomic_write_json(AI_DECISION_HISTORY_FILE, history_list)

        latency = round(time.time() - t0, 2)
        telemetry.finish("success", raw_res, output_chars=len(content))
        print(f"[AI Brain Batch] ✅ 6 币种全景决策完成 (耗时 {latency}s, 宏观基调: {macro_summary})")
        return standard_cache

    except Exception as e:
        telemetry.finish("failed", error=e)
        print(f"[AI Brain Batch] Error in batch inference: {e}")
        return None

def get_latest_ai_decision(inst_id: str, max_age_seconds: int = DECISION_MAX_AGE_SECONDS) -> Optional[Dict[str, Any]]:
    """Read a validated decision only while its cache timestamp is fresh."""
    if os.path.exists(AI_DECISION_CACHE_FILE):
        try:
            with open(AI_DECISION_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            item = data.get(inst_id)
            if not isinstance(item, dict):
                return None
            timestamp = int(item.get("timestamp", 0) or 0)
            if timestamp <= 0 or int(time.time()) - timestamp > max_age_seconds:
                return None
            return item
        except Exception:
            pass
    return None

if __name__ == "__main__":
    res = execute_batch_ai_brain_cycle("当前无持仓")
    if res:
        print("\n--- 示例标的 AI 决策结果 ---")
        for k in ["BTC-USDT-SWAP", "SOL-USDT-SWAP", "LINK-USDT-SWAP"]:
            if k in res:
                print(f"[{k}]", json.dumps(res[k]["decision"], ensure_ascii=False))

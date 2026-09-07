"""Shared, validated R20 trading universe configuration."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POOL_FILE = ROOT / "data" / "instrument_pool.json"

TIER_PROFILES = {
    "tier_1_bluechip": {
        "label": "蓝筹主流",
        "max_leverage": 5,
        "base_risk_ratio": 1.0,
        "sl_atr_mult": 1.8,
        "min_vol_24h_usd": 100_000_000,
    },
    "tier_2_momentum": {
        "label": "高弹性动量",
        "max_leverage": 3,
        "base_risk_ratio": 0.75,
        "sl_atr_mult": 2.2,
        "min_vol_24h_usd": 20_000_000,
    }
}

DEFAULT_INSTRUMENTS = [
    {"instId": "BTC-USDT-SWAP", "name": "BTC", "type": "crypto", "ccy": "BTC", "tier": "tier_1_bluechip", "max_leverage": 5, "sl_atr_mult": 1.8, "base_sz": 1, "precision": 1, "ctVal": 0.01, "tickSz": "0.1", "minSz": "0.01", "risk_per_trade_usd": 15.0},
    {"instId": "ETH-USDT-SWAP", "name": "ETH", "type": "crypto", "ccy": "ETH", "tier": "tier_1_bluechip", "max_leverage": 5, "sl_atr_mult": 1.8, "base_sz": 3, "precision": 2, "ctVal": 0.1, "tickSz": "0.01", "minSz": "0.01", "risk_per_trade_usd": 15.0},
    {"instId": "SOL-USDT-SWAP", "name": "SOL", "type": "crypto", "ccy": "SOL", "tier": "tier_2_momentum", "max_leverage": 3, "sl_atr_mult": 2.2, "base_sz": 7, "precision": 2, "ctVal": 1.0, "tickSz": "0.01", "minSz": "0.01", "risk_per_trade_usd": 15.0},
    {"instId": "DOGE-USDT-SWAP", "name": "DOGE", "type": "crypto", "ccy": "DOGE", "tier": "tier_2_momentum", "max_leverage": 3, "sl_atr_mult": 2.2, "base_sz": 10, "precision": 4, "ctVal": 1000.0, "tickSz": "0.0001", "minSz": "0.01", "risk_per_trade_usd": 15.0},
    {"instId": "SUI-USDT-SWAP", "name": "SUI", "type": "crypto", "ccy": "SUI", "tier": "tier_2_momentum", "max_leverage": 3, "sl_atr_mult": 2.2, "base_sz": 50, "precision": 4, "ctVal": 1.0, "tickSz": "0.0001", "minSz": "0.01", "risk_per_trade_usd": 15.0},
    {"instId": "LINK-USDT-SWAP", "name": "LINK", "type": "crypto", "ccy": "LINK", "tier": "tier_2_momentum", "max_leverage": 3, "sl_atr_mult": 2.2, "base_sz": 64, "precision": 3, "ctVal": 1.0, "tickSz": "0.001", "minSz": "0.01", "risk_per_trade_usd": 15.0},
]


def evaluate_instrument_tier(inst_id: str, name: str = "") -> str:
    """Classify instrument into Tier-1 Bluechip or Tier-2 Momentum."""
    name_upper = (name or inst_id.split("-")[0]).upper()
    if name_upper in ("BTC", "ETH"):
        return "tier_1_bluechip"
    return "tier_2_momentum"


def score_universe_candidate(
    candidate: dict[str, Any],
    vol_24h_usd: float = 0.0,
    atr_pct: float = 0.0,
    funding_rate: float = 0.0
) -> dict[str, Any]:
    """Evaluate candidate instrument suitability and rank quality score (0 ~ 100)."""
    name = candidate.get("name", "")
    tier = evaluate_instrument_tier(candidate.get("instId", ""), name)
    profile = TIER_PROFILES[tier]

    score = 50.0
    # Liquidity check
    if vol_24h_usd > 0:
        if vol_24h_usd >= profile["min_vol_24h_usd"]:
            score += 20.0
        else:
            score -= 30.0

    # Volatility band check (healthy swing trading band: 1.5% ~ 6.0%)
    if atr_pct > 0:
        if 1.5 <= atr_pct <= 6.0:
            score += 20.0
        elif atr_pct < 1.0:
            score -= 15.0  # too sleepy
        elif atr_pct > 9.0:
            score -= 25.0  # extreme rug risk

    # Extreme funding rate penalty (abs(funding) > 0.05% implies crowding)
    if abs(funding_rate) > 0.0005:
        score -= 15.0

    candidate["tier"] = tier
    candidate["max_leverage"] = profile["max_leverage"]
    candidate["sl_atr_mult"] = profile["sl_atr_mult"]
    candidate["universe_score"] = round(max(0.0, min(100.0, score)), 1)
    return candidate


def _precision(tick_size: str) -> int:
    normalized = tick_size.rstrip("0")
    return len(normalized.split(".", 1)[1]) if "." in normalized else 0


def from_okx_instrument(raw: dict[str, Any]) -> dict[str, Any]:
    inst_id = str(raw.get("instId", "")).upper()
    base = str(raw.get("baseCcy") or inst_id.split("-", 1)[0]).upper()
    tick_size = str(raw.get("tickSz") or "0.0001")
    tier = evaluate_instrument_tier(inst_id, base)
    profile = TIER_PROFILES[tier]
    return {
        "instId": inst_id,
        "name": base,
        "type": "crypto",
        "ccy": base,
        "tier": tier,
        "max_leverage": profile["max_leverage"],
        "sl_atr_mult": profile["sl_atr_mult"],
        "base_sz": 1,
        "precision": _precision(tick_size),
        "ctVal": float(raw.get("ctVal") or 1.0),
        "tickSz": tick_size,
        "minSz": str(raw.get("minSz") or "1"),
        "risk_per_trade_usd": 15.0,
    }


def load_instruments() -> list[dict[str, Any]]:
    if not POOL_FILE.exists():
        return [dict(item) for item in DEFAULT_INSTRUMENTS]
    try:
        payload = json.loads(POOL_FILE.read_text(encoding="utf-8"))
        instruments = payload.get("instruments", payload) if isinstance(payload, dict) else payload
        if isinstance(instruments, list) and instruments:
            for item in instruments:
                if "tier" not in item:
                    item["tier"] = evaluate_instrument_tier(item.get("instId", ""), item.get("name", ""))
                    item["max_leverage"] = TIER_PROFILES[item["tier"]]["max_leverage"]
                    item["sl_atr_mult"] = TIER_PROFILES[item["tier"]]["sl_atr_mult"]
            return instruments
    except (OSError, json.JSONDecodeError):
        pass
    return [dict(item) for item in DEFAULT_INSTRUMENTS]


def save_instruments(instruments: list[dict[str, Any]]) -> None:
    POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".instrument-pool-", suffix=".tmp", dir=POOL_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "instruments": instruments}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, POOL_FILE)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    try:
        sync_instruments_state()
    except Exception:
        pass


def sync_instruments_state() -> None:
    """Synchronize trading_state.json, factor_library_snapshot.json, news_sentiment.json,
    and dashboard cache when the trading instrument pool changes."""
    active_pool = load_instruments()
    active_ids = {item["instId"] for item in active_pool}
    active_names = {item["name"] for item in active_pool}

    # 1. Update data/trading_state.json
    state_file = ROOT / "data" / "trading_state.json"
    state_data: dict[str, Any] = {}
    if state_file.exists():
        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            state_data = {}

    current_insts = state_data.get("instruments", [])
    existing_by_id = {ins.get("instId"): ins for ins in current_insts if isinstance(ins, dict) and ins.get("instId")}

    new_insts = []
    for target in active_pool:
        inst_id = target["instId"]
        if inst_id in existing_by_id:
            new_insts.append(existing_by_id[inst_id])
        else:
            # New coin baseline
            new_insts.append({
                "name": target.get("name"),
                "instId": inst_id,
                "type": target.get("type", "crypto"),
                "price": "--",
                "rsi": 50.0,
                "rsi_7": 50.0,
                "vwap_bias": 0.0,
                "macd_hist": 0.0,
                "macd_accel": 0.0,
                "obv_flow": "NEUTRAL",
                "bb_bandwidth": 0.0,
                "vol_ratio": 1.0,
                "market_regime": "CHOP",
                "structure_1h": "CHOP",
                "trend_1h": "震荡",
                "trend_4h": "震荡",
                "score": 0.0,
                "action": "WAIT",
                "strategy": "⚪ 观望",
                "desc": "新配置资产，微结构与特征雷达已初始化",
                "position": None,
            })
    state_data["instruments"] = new_insts
    state_data["max_positions"] = len(active_pool)
    try:
        state_file.write_text(json.dumps(state_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    # 2. Update data/factor_library_snapshot.json to prune deleted coins
    factor_file = ROOT / "data" / "factor_library_snapshot.json"
    if factor_file.exists():
        try:
            factor_data = json.loads(factor_file.read_text(encoding="utf-8"))
            if isinstance(factor_data, dict) and "instruments" in factor_data:
                factor_data["instruments"] = [
                    item for item in factor_data["instruments"]
                    if isinstance(item, dict) and item.get("instId") in active_ids
                ]
                factor_file.write_text(json.dumps(factor_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # 3. Update data/news_sentiment.json to prune deleted coins and ensure active coins
    news_file = ROOT / "data" / "news_sentiment.json"
    if news_file.exists():
        try:
            news_data = json.loads(news_file.read_text(encoding="utf-8"))
            if isinstance(news_data, dict) and "coins_sentiment" in news_data:
                coins_dict = news_data["coins_sentiment"]
                cleaned_coins = {c: s for c, s in coins_dict.items() if c in active_names}
                for name in active_names:
                    if name not in cleaned_coins:
                        cleaned_coins[name] = {
                            "ccy": name,
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
                            "sentiment_factor_score": 0.0,
                        }
                news_data["coins_sentiment"] = cleaned_coins
                news_file.write_text(json.dumps(news_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # 4. Invalidate dashboard cache file so next fetch generates fresh state
    dashboard_cache = ROOT / "data" / "dashboard_last_good.json"
    if dashboard_cache.exists():
        try:
            dashboard_cache.unlink(missing_ok=True)
        except Exception:
            pass

    # 5. Run factor_library and news_sentiment in a non-blocking background thread
    import subprocess
    import threading
    def _run_bg() -> None:
        try:
            fl_script = ROOT / "scripts" / "factor_library.py"
            if fl_script.exists():
                subprocess.run(f"python3 {fl_script}", shell=True, capture_output=True, timeout=45)
            nh_script = ROOT / "scripts" / "news_sentiment_harvester.py"
            if nh_script.exists():
                subprocess.run(f"python3 {nh_script}", shell=True, capture_output=True, timeout=45)
        except Exception:
            pass
    threading.Thread(target=_run_bg, daemon=True).start()


"""Binance USDT-M 合约只读适配器（免登录公共端点，fapi.binance.com）。

注意（来自 09-08 调研的坑位声明，已固化进能力表）：
- 触发单默认 MARK_PRICE（照搬 OKX 最新价语义会大面积误触发）；
- 无附属 TP/SL，合约端点无原生 OCO —— Phase 3 需「独立条件单 + 本地盯盘补撤单」双轨；
- 429 后不停手会升级 418 封 IP（最长 3 天）—— 本只读通道限频消耗极低，仍要求
  调用方复用适配器内 session 而不要另起子进程狂轮询。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .base import BaseExchangeAdapter, ExchangeCapabilities, InstrumentSpec

INTERVAL_MAP = {  # 内部周期 → Binance interval
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1H": "1h", "2H": "2h", "4H": "4h", "6H": "6h", "12H": "12h",
    "1D": "1d", "1W": "1w",
}


class BinanceAdapter(BaseExchangeAdapter):
    base_url = "https://fapi.binance.com"
    live_url = "https://fapi.binance.com"
    test_url = "https://demo-fapi.binance.com"   # 官方 Demo Trading 新域名（旧 testnet.binancefuture.com 已过时）
    capabilities = ExchangeCapabilities(
        venue="binance",
        display_name="Binance 币安 USDT-M 合约",
        symbol_template="{base}USDT",
        quantity_unit="base_asset",
        signed_size=False,
        supports_attached_tp_sl=False,
        trigger_price_default="mark",
        max_candle_limit=1500,
        bar_case="lower",
        has_top_trader_ratio=True,
        has_taker_ratio=True,
        supports_account=False,
        supports_orders=False,
        mainland_ip_restricted=True,
        rate_limit_note="IP 权重 1200/min；429 继续打会 418 封 IP 最长 3 天",
    )

    # ------------------------------------------------------------------
    def _interval(self, bar: str) -> str:
        return INTERVAL_MAP.get(str(bar or "15m").strip(), str(bar or "15m").strip().lower())

    def fetch_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        inst = self.native_symbol(symbol)
        stats = self._public_get("/fapi/v1/ticker/24hr", {"symbol": inst})
        if not isinstance(stats, dict) or not stats.get("lastPrice"):
            return None
        bid = ask = 0.0
        book = self._public_get("/fapi/v1/bookTicker", {"symbol": inst})
        if isinstance(book, dict) and book.get("bidPrice"):
            bid = float(book.get("bidPrice") or 0)
            ask = float(book.get("askPrice") or 0)
        else:
            # 机房/受限出口 IP 上 bookTicker 可能被 WAF 拦截返回 HTML——回退 depth 档一
            depth = self._public_get("/fapi/v1/depth", {"symbol": inst, "limit": 5})
            if isinstance(depth, dict) and depth.get("bids") and depth.get("asks"):
                bid = float(depth["bids"][0][0])
                ask = float(depth["asks"][0][0])
        return {
            "venue": "binance", "inst_id": inst,
            "last": float(stats["lastPrice"]),
            "bid": bid or None, "ask": ask or None,
            "open_24h": float(stats.get("openPrice") or 0) or None,
            "high_24h": float(stats.get("highPrice") or 0) or None,
            "low_24h": float(stats.get("lowPrice") or 0) or None,
            "chg_24h_pct": float(stats.get("priceChangePercent") or 0),
            "vol_24h_base": float(stats.get("volume") or 0),
            "quote_vol_24h": float(stats.get("quoteVolume") or 0),
            "ts_ms": int(stats.get("closeTime") or time.time() * 1000),
        }

    def fetch_candles(self, symbol: str, bar: str = "15m",
                      limit: int = 100) -> Optional[List[List[Any]]]:
        """返回 OKX 同构 K 线：[[ts, o, h, l, c, vol], ...] 升序。"""
        data = self._public_get("/fapi/v1/klines", {
            "symbol": self.native_symbol(symbol),
            "interval": self._interval(bar),
            "limit": min(int(limit), self.capabilities.max_candle_limit),
        })
        if not isinstance(data, list) or not data:
            return None
        out = []
        for k in data:
            try:
                out.append([int(k[0]), str(k[1]), str(k[2]), str(k[3]),
                            str(k[4]), str(k[5])])
            except (IndexError, ValueError):
                continue
        # /fapi/v1/klines 原生按时间升序（旧→新），与契约一致，直接返回。
        return out

    def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        data = self._public_get("/fapi/v1/premiumIndex",
                                {"symbol": self.native_symbol(symbol)})
        if isinstance(data, dict) and data.get("lastFundingRate"):
            try:
                return float(data["lastFundingRate"])
            except ValueError:
                return None
        return None

    def fetch_orderbook(self, symbol: str, depth: int = 20) -> Optional[Dict[str, Any]]:
        data = self._public_get("/fapi/v1/depth",
                                {"symbol": self.native_symbol(symbol), "limit": depth})
        if isinstance(data, dict) and data.get("bids"):
            return {"venue": "binance", "bids": data["bids"], "asks": data.get("asks", [])}
        return None

    def fetch_open_interest(self, symbol: str) -> Optional[float]:
        data = self._public_get("/fapi/v1/openInterest",
                                {"symbol": self.native_symbol(symbol)})
        if isinstance(data, dict) and data.get("openInterest"):
            try:
                return float(data["openInterest"])
            except ValueError:
                return None
        return None

    def fetch_top_trader_ratio(self, symbol: str) -> Optional[float]:
        """大户持仓量多空比 topLongShortPositionRatio（免费无 key，滚动 30 天）。"""
        data = self._public_get("/futures/data/topLongShortPositionRatio", {
            "symbol": self.native_symbol(symbol), "period": "1h", "limit": 1,
        })
        if isinstance(data, list) and data:
            try:
                return float(data[-1].get("longShortRatio"))
            except (ValueError, TypeError, AttributeError):
                return None
        return None

    # ------------------------------------------------------------------
    def _load_spec(self, inst_id: str) -> Optional[InstrumentSpec]:
        data = self._public_get("/fapi/v1/exchangeInfo", {"symbol": inst_id}, timeout=6.0)
        if not isinstance(data, dict):
            return None
        symbols = data.get("symbols") or []
        raw = next((s for s in symbols if s.get("symbol") == inst_id), None)
        if not raw:
            return None
        tick, step, min_qty = 0.1, 0.001, 0.001
        for f in raw.get("filters", []):
            if f.get("filterType") == "PRICE_FILTER":
                tick = float(f.get("tickSize") or tick)
            elif f.get("filterType") == "LOT_SIZE":
                step = float(f.get("stepSize") or step)
                min_qty = float(f.get("minQty") or min_qty)
        return InstrumentSpec(
            venue="binance", inst_id=inst_id, base=self.canonical(inst_id),
            tick_size=tick, step_size=step, ct_val=1.0, min_size=min_qty,
            max_leverage=0.0, status=str(raw.get("status", "TRADING")).lower(), raw=raw,
        )

"""Gate.io 永续合约（V4 USDT 结算）只读适配器（免登录，api.gateio.ws）。

Gate 特性（能力表已声明）：
- 数量为**带符号张数**：正=开多、负=开空（signed_size=True）——上层方向语义在
  Phase 3 执行路由内转换，本层换算函数只产出正数张数；
- contract_stats 数据维度四所最全（大户账户/持仓多空比、taker 比、清算），
  与 OKX 口径最对齐 —— 社区版用户首选场所；
- 签名算法 HMAC-SHA512（Phase 3 实现私有面时用）；
- testnet fx-api-testnet.gateio.ws 实测不稳（09-08 502），接实盘前需双轨验证。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .base import BaseExchangeAdapter, ExchangeCapabilities, InstrumentSpec

INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1H": "1h", "4H": "4h", "8H": "8h", "1D": "1d", "1W": "7d",
}


class GateAdapter(BaseExchangeAdapter):
    base_url = "https://api.gateio.ws"
    capabilities = ExchangeCapabilities(
        venue="gate",
        display_name="Gate.io 芝麻开门 USDT 永续",
        symbol_template="{base}_USDT",
        quantity_unit="contracts",
        signed_size=True,
        supports_attached_tp_sl=False,   # 独立 /price_orders 资源族，非附属
        trigger_price_default="last",    # price_type 0/1/2 可选，默认最新价
        max_candle_limit=2000,
        bar_case="lower",
        has_top_trader_ratio=True,
        has_taker_ratio=True,
        supports_account=False,
        supports_orders=False,
        mainland_ip_restricted=False,
        rate_limit_note="公共端点约 100~200 req/s，四所最宽松",
    )

    def _interval(self, bar: str) -> str:
        return INTERVAL_MAP.get(str(bar or "15m").strip(), str(bar or "15m").strip().lower())

    def fetch_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        inst = self.native_symbol(symbol)
        data = self._public_get("/api/v4/futures/usdt/tickers", {"contract": inst})
        rows = data if isinstance(data, list) else []
        t = next((x for x in rows if x.get("contract") == inst), None)
        if not t:
            return None
        last = float(t.get("last") or 0)
        return {
            "venue": "gate", "inst_id": inst,
            "last": last or None,
            "bid": float(t.get("highest_bid") or 0) or None,
            "ask": float(t.get("lowest_ask") or 0) or None,
            "mark_price": float(t.get("mark_price") or 0) or None,
            "change_24h_pct": float(t.get("change_percentage") or 0),
            "funding_rate": float(t.get("funding_rate") or 0) or None,
            "ts_ms": int(time.time() * 1000),
        }

    def fetch_candles(self, symbol: str, bar: str = "15m",
                      limit: int = 100) -> Optional[List[List[Any]]]:
        """升序 [[ts, o, h, l, c, vol(base)], ...]，与 OKX/Binance 同构。"""
        data = self._public_get("/api/v4/futures/usdt/candlesticks", {
            "contract": self.native_symbol(symbol),
            "interval": self._interval(bar),
            "limit": min(int(limit), self.capabilities.max_candle_limit),
        })
        if not isinstance(data, list) or not data:
            return None
        out = []
        for k in data:  # Gate: {t,v,l,h,o,c,sum} dict 项
            try:
                out.append([int(k["t"]), str(k["o"]), str(k["h"]),
                            str(k["l"]), str(k["c"]), str(k.get("v", 0))])
            except (KeyError, ValueError, TypeError):
                continue
        out.sort(key=lambda r: r[0])  # 统一升序契约
        return out or None

    def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        data = self._public_get("/api/v4/futures/usdt/contracts",
                                {"contract": self.native_symbol(symbol)})
        rows = data if isinstance(data, list) else []
        c = next((x for x in rows if x.get("name") == self.native_symbol(symbol)), None)
        if c and c.get("funding_rate_indicative") is not None:
            try:
                return float(c["funding_rate_indicative"])
            except (TypeError, ValueError):
                pass
        return None

    def fetch_orderbook(self, symbol: str, depth: int = 20) -> Optional[Dict[str, Any]]:
        data = self._public_get("/api/v4/futures/usdt/order_book", {
            "contract": self.native_symbol(symbol), "limit": min(depth, 50)})
        if isinstance(data, dict) and data.get("bids"):
            return {"venue": "gate", "bids": data["bids"], "asks": data.get("asks", [])}
        return None

    def fetch_top_trader_ratio(self, symbol: str) -> Optional[float]:
        """大户持仓量多空比 top_lsr_size（contract_stats，口径优于全局账户数比）。"""
        data = self._public_get("/api/v4/futures/usdt/contract_stats", {
            "contract": self.native_symbol(symbol), "interval": "1h", "limit": 1,
        })
        rows = data if isinstance(data, list) else []
        if rows:
            try:
                v = rows[-1].get("top_lsr_size")
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
        return None

    def _load_spec(self, inst_id: str) -> Optional[InstrumentSpec]:
        data = self._public_get("/api/v4/futures/usdt/contracts", {"contract": inst_id})
        rows = data if isinstance(data, list) else []
        raw = next((x for x in rows if x.get("name") == inst_id), None)
        if not raw:
            return None
        mult = float(raw.get("quanto_multiplier") or 0.0001)   # 每张面值（币本位）
        # 价格档位：order_price_round 为 Gate 合约权威 tick 字段
        tick = float(raw.get("order_price_round") or 0.1) or mult
        return InstrumentSpec(
            venue="gate", inst_id=inst_id, base=self.canonical(inst_id),
            tick_size=tick, step_size=mult, ct_val=mult,
            min_size=float(raw.get("order_size_min") or 1),
            max_leverage=float(raw.get("lever", {}).get("max", 0) or 0)
            if isinstance(raw.get("lever"), dict) else 0.0,
            status="trading" if raw.get("in_delisting") is not True else "delisting",
            raw=raw,
        )

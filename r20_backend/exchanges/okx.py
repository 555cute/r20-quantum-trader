"""OKX V5 公共行情只读适配器（www→aws 双域名容灾，免登录）。

定位：多所聚合矩阵的统一入口。生产交易热路径仍走 scripts/market_data_service
与 ai_factor_trader 既有链路（本阶段零行为变化）；OKX 执行迁适配器属 Phase 3。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import BaseExchangeAdapter, ExchangeCapabilities, InstrumentSpec, canonical_base

OKX_HOSTS = ("https://www.okx.com", "https://aws.okx.com")


class OKXPublicAdapter(BaseExchangeAdapter):
    base_url = OKX_HOSTS[0]
    capabilities = ExchangeCapabilities(
        venue="okx",
        display_name="OKX 欧易 V5 永续",
        symbol_template="{base}-USDT-SWAP",
        quantity_unit="contracts",
        signed_size=False,
        supports_attached_tp_sl=True,      # attachAlgoOrds 附带 TP/SL——但**非原子即时保护**，
                                           # 完全成交后提交且有 failCode，见 protection_semantics
        trigger_price_default="last",
        max_candle_limit=300,
        bar_case="upper",                  # 15m/1H/4H 混合大小写
        has_top_trader_ratio=True,
        has_taker_ratio=True,
        supports_account=False,
        supports_orders=False,             # 执行现居 ai_factor_trader 遗留路径
        mainland_ip_restricted=False,
        rate_limit_note="公共行情约 20req/2s，429 常见需退避",
        # ---- US-004 真实语义声明（审计 2026-09-10 §2 OKX）----
        order_id_type="string",                 # ordId/algoId 原生字符串
        native_amend=False,                     # 未核验改单端点，不宣称
        decimal_amount=False,
        position_modes=("net", "long_short"),   # posSide 语义；执行在遗留直签链路
        conditional_family="attached",          # attachAlgoOrds 附带保护
        protection_semantics="attachAlgoOrds **非受理即原子保护**：官方 attachAlgoClOrdId 说明"
                             "普通订单完全成交后才提交附带算法单，回执字段含 failCode/failReason"
                             "——HTTP 200≠受保护，必须回读 pending algo 核验账户/合约/方向/数量/"
                             "触发值（核验 helper：okx_trade_service.verify_attached_protection）。"
                             "另 2026-08-20 起 post_only/mmp_and_post_only 失败可只收 canceled 直达"
                             "终态不先 live（Demo 2026-08-10 生效）——状态机须接受直接终态，"
                             "不无限等待 live；此变更不针对普通 limit/market/ioc/fok",
    )

    # ------------------------------------------------------------------
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None,
             timeout: float = 4.0) -> Any:
        """返回 V5 data 数组；双域名逐一试。"""
        query = urlencode(params or {})
        url_path = path + (f"?{query}" if query else "")
        for host in OKX_HOSTS:
            try:
                req = Request(host + url_path, headers={"User-Agent": "R20-Quant-Desk/7.8"})
                with urlopen(req, timeout=timeout) as resp:
                    payload = __import__("json").loads(resp.read().decode("utf-8"))
                if str(payload.get("code", "0")) == "0":
                    return payload.get("data")
            except Exception:
                continue
        return None

    def fetch_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        inst = self.native_symbol(symbol)
        rows = self._get("/api/v5/market/ticker", {"instId": inst})
        if not rows:
            return None
        t = rows[0]
        try:
            return {
                "venue": "okx", "inst_id": inst,
                "last": float(t.get("last") or 0) or None,
                "bid": float(t.get("bidPx") or 0) or None,
                "ask": float(t.get("askPx") or 0) or None,
                "open_24h": float(t.get("open24h") or 0) or None,
                "high_24h": float(t.get("high24h") or 0) or None,
                "low_24h": float(t.get("low24h") or 0) or None,
                "chg_24h_pct": float(t.get("24hPct") or 0),
                "vol_24h_base": float(t.get("volCcy24h") or 0),
                "quote_vol_24h": round(float(t.get("volCcy24h") or 0)
                                       * float(t.get("last") or 0), 2),
                "ts_ms": int(t.get("ts") or time.time() * 1000),
            }
        except (ValueError, TypeError):
            return None

    def fetch_candles(self, symbol: str, bar: str = "15m",
                      limit: int = 100) -> Optional[List[List[Any]]]:
        """升序 [[ts,o,h,l,c,vol],...]（内部统一升序，消费方自行 reverse）。"""
        rows = self._get("/api/v5/market/candles", {
            "instId": self.native_symbol(symbol),
            "bar": self.to_bar(bar),
            "limit": min(int(limit), self.capabilities.max_candle_limit),
        })
        if not rows:
            return None
        out = [[int(r[0]), r[1], r[2], r[3], r[4], r[5]] for r in rows if len(r) >= 6]
        out.sort(key=lambda x: x[0])
        return out or None

    def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        rows = self._get("/api/v5/public/funding-rate",
                         {"instId": self.native_symbol(symbol)})
        if rows:
            try:
                return float(rows[0].get("fundingRate"))
            except (TypeError, ValueError):
                return None
        return None

    def fetch_orderbook(self, symbol: str, depth: int = 20) -> Optional[Dict[str, Any]]:
        rows = self._get("/api/v5/market/books",
                         {"instId": self.native_symbol(symbol), "sz": min(depth, 400)})
        if rows:
            return {"venue": "okx", "bids": rows[0].get("bids", []),
                    "asks": rows[0].get("asks", [])}
        return None

    def fetch_top_trader_ratio(self, symbol: str) -> Optional[float]:
        """前 5% 大户账户数多空比（rubik，OKX 免费口径）。"""
        rows = self._get(
            "/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader",
            {"ccy": canonical_base(symbol), "period": "1h"})
        if rows:
            try:
                return float(rows[-1][1])
            except (TypeError, ValueError, IndexError):
                return None
        return None

    def _load_spec(self, inst_id: str) -> Optional[InstrumentSpec]:
        rows = self._get("/api/v5/public/instruments",
                         {"instType": "SWAP", "instId": inst_id}, timeout=6.0)
        raw = rows[0] if rows else None
        if not raw:
            return None
        return InstrumentSpec(
            venue="okx", inst_id=inst_id, base=self.canonical(inst_id),
            tick_size=float(raw.get("tickSz") or 0.1),
            step_size=float(raw.get("lotSz") or 1),
            ct_val=float(raw.get("ctVal") or 1),
            min_size=float(raw.get("minSz") or 1),
            max_leverage=0.0,   # 各所杠杆上限走 /public/limit-price 另查，此处不臆造
            status="trading" if str(raw.get("state", "trading")) == "trading" else "closed",
            raw=raw,
        )

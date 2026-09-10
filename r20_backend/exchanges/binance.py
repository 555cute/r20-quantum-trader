"""Binance USDT-M 合约只读适配器（免登录公共端点，fapi.binance.com）。

注意（US-004 按 2026-09-10 时效审计 §2 重写旧坑位声明，冲突以审计为准）：
- 条件单属独立 **Algo Service 资源族**：POST /fapi/v1/algoOrder 新建，
  query/current-all-algo-open/cancel 均有独立方法——普通 /fapi/v1/order 与
  openOrders **不代表保护单全集**，双源合并读取；
- Algo 请求字段是 triggerPrice/clientAlgoId——不得把普通订单旧字段
  stopPrice/newClientOrderId 盲传过来；workingType 必须显式传入
  （官方默认 CONTRACT_PRICE，旧代码断言 MARK_PRICE 是错的，不依赖任何默认）；
- closePosition=true 仅适用指定条件市价单，且与 quantity/reduceOnly 互斥；
  Hedge Mode 不可传 reduceOnly，positionSide 要显式映射；
- 官方当前常量 PROD/TESTNET/DEMO 三域并存（US-001 profile 已钉），限频按端点
  文档/响应头读取：new_algo_order 源码记载计订单 10s/1min 计数、IP 权重 0，
  勿再用「全局 1200/min」概数套所有端点；429 升级 418 封 IP 的教训仍有效；
- 私有面本仓库无签名器，下单/查询发送通道保持显式未实装（fail-closed）。
"""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .base import (BaseExchangeAdapter, ExchangeCapabilities,
                   ExchangeCapabilityError, InstrumentSpec)

INTERVAL_MAP = {  # 内部周期 → Binance interval
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1H": "1h", "2H": "2h", "4H": "4h", "6H": "6h", "12H": "12h",
    "1D": "1d", "1W": "1w",
}


class BinanceAdapter(BaseExchangeAdapter):
    base_url = "https://fapi.binance.com"
    live_url = "https://fapi.binance.com"
    test_url = "https://demo-fapi.binance.com"   # 官方 Demo 域。时效审计（2026-09-10）：
    # PROD=fapi / TESTNET=testnet.binancefuture / DEMO=demo-fapi 三常量在当前官方 SDK 并存，
    # 旧测试域并未被删除——三域均可经 env_profiles 显式 environment 请求；
    # 旧 R20_BINANCE_TESTNET=1 兼容映射到 demo（与本仓既有行为逐字节一致）。
    capabilities = ExchangeCapabilities(
        venue="binance",
        display_name="Binance 币安 USDT-M 合约",
        symbol_template="{base}USDT",
        quantity_unit="base_asset",
        signed_size=False,
        supports_attached_tp_sl=False,      # 无附带 TP/SL；条件腿走独立 algo 资源族
        trigger_price_default="explicit_only",  # US-004：workingType 必须显式传，官方默认
                                                # CONTRACT_PRICE 仅作平台侧行为记录，本系统不依赖任何默认值
        max_candle_limit=1500,
        bar_case="lower",
        has_top_trader_ratio=True,
        has_taker_ratio=True,
        supports_account=False,
        supports_orders=False,              # 门禁不变：下单仍恒被拒
        mainland_ip_restricted=True,
        rate_limit_note="按端点读取勿用概数：new_algo_order 源码记载计订单 10s/1min 计数、"
                        "IP 权重 0；普通面 IP 权重 1200/min，429 继续打会 418 封 IP 最长 3 天",
        # ---- US-004 真实语义声明（审计 2026-09-10 §2 Binance）----
        order_id_type="int64_precision_risk",   # orderId int64 无 id_string → 全链路 str 归一
        native_amend=False,                      # 改单=撤+重下（algo 族无原生 amend 依据，未验不宣称）
        decimal_amount=False,                    # base_asset 十进制数量是原生语义，非 Gate 式张数 amount
        position_modes=("net", "long_short"),    # Hedge Mode 显式 positionSide，禁 reduceOnly
        conditional_family="algo_service",       # /fapi/v1/algoOrder 独立新建/查询/撤销
        protection_semantics="独立 algo 资源族（STOP/TP/TRAILING 等 CONDITIONAL）：普通 openOrders "
                             "不代表保护单全集，必须双源合并读取；closePosition=true 仅指定条件市价单"
                             "且与 quantity/reduceOnly 互斥",
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

    # ==================================================================
    # US-004 · Algo Service 双轨契约（纯构造器可单测；发送通道显式未实装）
    # 审计 2026-09-10 §2：POST/GET/DELETE /fapi/v1/algoOrder 资源族独立于普通
    # /fapi/v1/order；SDK 独立方法 query_algo_order /
    # current_all_algo_open_orders / cancel_algo_order / cancel_all_algo_open_orders。
    # ==================================================================
    ALGO_ORDER_PATH = "/fapi/v1/algoOrder"
    ALGO_TYPES = ("STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET",
                  "TRAILING_STOP_MARKET")
    WORKING_TYPES = ("MARK_PRICE", "CONTRACT_PRICE", "INDEX_PRICE")

    @staticmethod
    def _require_working_type(working_type: Optional[str]) -> str:
        """workingType 显式必传——不依赖任何平台默认值（审计 §0-2）。"""
        wt = str(working_type or "").strip().upper()
        if wt not in BinanceAdapter.WORKING_TYPES:
            raise ValueError(
                f"workingType 必须显式传入且合法（{'/'.join(BinanceAdapter.WORKING_TYPES)}），"
                f"收到 {working_type!r}——官方 Algo 默认是 CONTRACT_PRICE，本系统禁止吃默认值")
        return wt

    @classmethod
    def build_algo_order_request(cls, *, symbol: str, side: str, type_: str,
                                 trigger_price, working_type: str,
                                 quantity=None, price=None,
                                 reduce_only: Optional[bool] = None,
                                 close_position: Optional[bool] = None,
                                 position_side: Optional[str] = None,
                                 client_algo_id: Optional[str] = None) -> Dict[str, Any]:
        """条件单新建请求体（字段名按当前 Algo Service：triggerPrice/clientAlgoId，
        绝不是普通订单的 stopPrice/newClientOrderId）。非法组合 fail-closed。"""
        if not str(symbol or "").strip().upper().endswith("USDT"):
            raise ValueError(f"symbol 需为 USDⓈ-M 原生名（如 BTCUSDT），收到 {symbol!r}")
        t = str(type_ or "").strip().upper()
        if t not in cls.ALGO_TYPES:
            raise ValueError(f"type 需为 CONDITIONAL 族 {cls.ALGO_TYPES}，收到 {type_!r}")
        try:
            tp = Decimal(str(trigger_price))
        except Exception:
            raise ValueError(f"triggerPrice 非法: {trigger_price!r}")
        if tp <= 0:
            raise ValueError("triggerPrice 必须为正")
        body: Dict[str, Any] = {
            "symbol": str(symbol).upper(), "side": str(side).upper(), "type": t,
            "triggerPrice": str(tp),
            "workingType": cls._require_working_type(working_type),
        }
        cp = bool(close_position)
        if cp:
            # closePosition=true 仅适用指定条件市价单，且与 quantity/reduceOnly 互斥（审计 §2）
            if not t.endswith("_MARKET"):
                raise ValueError("closePosition=true 仅适用 *_MARKET 条件市价单")
            if quantity not in (None, "", 0) or reduce_only:
                raise ValueError("closePosition=true 与 quantity/reduceOnly 互斥，不得并传")
            body["closePosition"] = "true"
        else:
            if quantity in (None, ""):
                raise ValueError("非 closePosition 条件单必须给 quantity")
            body["quantity"] = str(Decimal(str(quantity)))
            if reduce_only is not None:
                ro = bool(reduce_only)
                ps = str(position_side or "BOTH").upper()
                if ps in ("LONG", "SHORT") and ro:
                    # Hedge Mode 不可传 reduceOnly——平仓方向由 positionSide 表达（审计 §2）
                    raise ValueError("Hedge Mode（positionSide=LONG/SHORT）不可传 reduceOnly，"
                                     "用 positionSide 显式映射减仓方向")
                body["reduceOnly"] = "true" if ro else "false"
        ps = str(position_side or "").upper()
        if ps:
            if ps not in ("BOTH", "LONG", "SHORT"):
                raise ValueError(f"positionSide 非法: {position_side!r}")
            body["positionSide"] = ps
        if price not in (None, ""):
            body["price"] = str(Decimal(str(price)))
        if client_algo_id:
            body["clientAlgoId"] = str(client_algo_id)
        # 普通订单旧字段防呆：绝不明传
        for legacy in ("stopPrice", "newClientOrderId"):
            if legacy in body:
                raise ValueError(f"{legacy} 属普通订单字段，禁止传入 Algo API")
        return {"method": "POST", "path": cls.ALGO_ORDER_PATH, "body": body}

    @classmethod
    def build_algo_query_request(cls, *, algo_id=None, client_algo_id=None) -> Dict[str, Any]:
        """query_algo_order：按 algoId 或 clientAlgoId 单查。"""
        params: Dict[str, Any] = {}
        if algo_id is not None:
            params["algoId"] = str(algo_id)
        if client_algo_id:
            params["clientAlgoId"] = str(client_algo_id)
        if not params:
            raise ValueError("查询条件单需 algoId 或 clientAlgoId")
        return {"method": "GET", "path": cls.ALGO_ORDER_PATH, "params": params}

    @classmethod
    def build_algo_open_orders_request(cls, *, symbol: Optional[str] = None) -> Dict[str, Any]:
        """current_all_algo_open_orders：当前 algo 挂单（普通 openOrders 不含此族）。"""
        params: Dict[str, Any] = {"status": "open"}
        if symbol:
            params["symbol"] = str(symbol).upper()
        return {"method": "GET", "path": cls.ALGO_ORDER_PATH, "params": params}

    @classmethod
    def build_algo_cancel_request(cls, *, algo_id=None, client_algo_id=None) -> Dict[str, Any]:
        """cancel_algo_order：单撤。"""
        params: Dict[str, Any] = {}
        if algo_id is not None:
            params["algoId"] = str(algo_id)
        if client_algo_id:
            params["clientAlgoId"] = str(client_algo_id)
        if not params:
            raise ValueError("撤销条件单需 algoId 或 clientAlgoId")
        return {"method": "DELETE", "path": cls.ALGO_ORDER_PATH, "params": params}

    @classmethod
    def build_algo_cancel_all_request(cls, *, symbol: str) -> Dict[str, Any]:
        """cancel_all_algo_open_orders：按合约全撤。"""
        if not str(symbol or "").strip():
            raise ValueError("全撤需指定 symbol")
        return {"method": "DELETE", "path": f"{cls.ALGO_ORDER_PATH}/all",
                "params": {"symbol": str(symbol).upper()}}

    # ---- 私有发送通道：本仓库无币安签名器，一切私有调用显式未实装（fail-closed）----
    def _private_algo_send(self, request: Dict[str, Any]) -> Any:
        raise ExchangeCapabilityError(
            f"Binance Algo 私有通道未实装（请求已构造: {request['method']} {request['path']}）——"
            "supports_orders=False 门禁不变，实装需先接签名器并过执行开闸")

    def query_algo_order(self, *, algo_id=None, client_algo_id=None) -> Any:
        return self._private_algo_send(self.build_algo_query_request(
            algo_id=algo_id, client_algo_id=client_algo_id))

    def current_all_algo_open_orders(self, *, symbol: Optional[str] = None) -> Any:
        return self._private_algo_send(self.build_algo_open_orders_request(symbol=symbol))

    def cancel_algo_order(self, *, algo_id=None, client_algo_id=None) -> Any:
        return self._private_algo_send(self.build_algo_cancel_request(
            algo_id=algo_id, client_algo_id=client_algo_id))

    def cancel_all_algo_open_orders(self, *, symbol: str) -> Any:
        return self._private_algo_send(self.build_algo_cancel_all_request(symbol=symbol))

    @staticmethod
    def merged_protection_view(normal_open: List[Dict[str, Any]],
                               algo_open: List[Dict[str, Any]]) -> Dict[str, Any]:
        """双源合并视图：普通挂单 + algo 条件单；ID 一律 str 归一
        （order_id_type=int64_precision_risk，JSON Number 链路不保精度）。"""
        def _norm(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            out = []
            for r in rows or []:
                if not isinstance(r, dict):
                    continue
                rr = dict(r)
                for k in ("orderId", "algoId", "origClientOrderId", "clientAlgoId",
                          "clientOrderId"):
                    if rr.get(k) is not None:
                        rr[k] = str(rr[k])
                out.append(rr)
            return out
        n, a = _norm(normal_open), _norm(algo_open)
        return {"entries": n, "conditional": a,
                "protection_total": len(a),
                "open_orders_only": False}   # 明示：普通 openOrders ≠ 保护单全集

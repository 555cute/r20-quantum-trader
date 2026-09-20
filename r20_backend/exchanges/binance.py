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

import hashlib
import hmac
import json
import time
import warnings
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Gate 与 Binance 的持仓模式**词汇不同**（实测：Gate `position_mode='dual'`、
# Binance `dualSidePosition` 布尔）。故各所有各所的判定函数，绝不共用一个枚举。


def interpret_dual_side_position(payload: Any) -> str:
    """由 `/fapi/v1/positionSide/dual` 回包判定 → `"net"|"long_short"|"unknown"`。

    实测回包：`{"dualSidePosition": False}`（DEMO 账户为**净模式**）。
    - `True` → `"long_short"`（对冲/Hedge：positionSide 必须显式 LONG/SHORT，且禁传 reduceOnly）
    - `False` → `"net"`
    - 读不到/类型不对 → `"unknown"`（不拿默认值冒充事实；调用方据此禁新开仓）

    刻意与 Gate 的 `interpret_position_mode` **分开**：两所模式词汇不同，合并枚举
    迟早会把 `dual` 与 `long_short` 混为一谈（那是两套完全不同的下单契约）。
    """
    if not isinstance(payload, dict):
        return "unknown"
    value = payload.get("dualSidePosition")
    if isinstance(value, bool):
        return "long_short" if value else "net"
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return "long_short" if value.strip().lower() == "true" else "net"
    return "unknown"


from .base import (BaseExchangeAdapter, ExchangeCapabilities,
                   ExchangeCapabilityError, InstrumentSpec)
from .binance_orders import (
    build_order_params,
    send_protective_order,
)
from .binance_signing import build_signed_query
from .binance_algo import BinanceAlgoRequestsMixin


class BinanceAPIError(RuntimeError):
    """Binance 私有 API 业务错误（code 为官方错误码，如 -2014/-2015）。"""

    def __init__(self, code: Any, message: str, status: int = 0):
        super().__init__(f"Binance [{code}]: {message or 'request failed'}")
        self.code = code
        self.message = message
        self.status = status


INTERVAL_MAP = {  # 内部周期 → Binance interval
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1H": "1h", "2H": "2h", "4H": "4h", "6H": "6h", "12H": "12h",
    "1D": "1d", "1W": "1w",
}


class BinanceAdapter(BinanceAlgoRequestsMixin, BaseExchangeAdapter):
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
        supports_account=True,
        supports_orders=True,               # US-005：开启下单与保护单执行
        adapter_execution_flag="R20_BINANCE_EXECUTION",  # US-005：独立执行总闸
        mainland_ip_restricted=True,
        rate_limit_note="按端点读取勿用概数：new_algo_order 源码记载计订单 10s/1min 计数、"
                        "IP 权重 0；普通面 IP 权重 1200/min，429 继续打会 418 封 IP 最长 3 天",
        # ---- US-004 真实语义声明（审计 2026-09-10 §2 Binance）----
        order_id_type="int64_precision_risk",   # orderId int64 无 id_string → 全链路 str 归一
        native_amend=False,                      # 改单=撤+重下（algo 族无原生 amend 依据，未验不宣称）
        decimal_amount=False,                    # base_asset 十进制数量是原生语义，非 Gate 式张数 amount
        position_modes=("net", "long_short"),    # Hedge Mode 显式 positionSide，禁 reduceOnly
        # 仅 net **载荷已验证**（2026-09-20 实测 DEMO `dualSidePosition=False`、
        # 740 行 positionRisk 全为 positionSide=BOTH）；long_short 虽在声明域内，
        # 但没有真实对冲账户核验过下单/保护腿载荷 ⇒ 检测到它时禁新开仓并说明原因。
        entry_ready_position_modes=("net",),
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
        if not (isinstance(data, list) and data):
            try:
                resp = self.get_session().get(
                    "https://fapi.binance.com/futures/data/topLongShortPositionRatio",
                    params={"symbol": self.native_symbol(symbol), "period": "1h", "limit": 1},
                    timeout=4.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
            except Exception:
                pass
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

    # ---- 私有面：凭证、签名通道与只读适配（US-004）----
    def _keys(self) -> tuple[str, str]:
        from .registry import venue_credentials
        key, secret = venue_credentials("binance", self.environment)
        if not key or not secret:
            raise ExchangeCapabilityError(
                f"Binance ({self.environment}档) 凭证未配置——请在后台「多交易所凭证」录入 API Key/Secret")
        return key, secret

    #: 服务器校时缓存：base_url -> (测量时刻, 偏差毫秒)。偏差 = 交易所服务器时间 − 本机时间。
    #: 按**域**分键：demo-fapi 与 fapi 是两台主机，各测各的。
    _SERVER_TIME_CACHE: Dict[str, tuple] = {}
    #: 校时缓存有效期（秒）与"偏差过大"告警阈值（毫秒）。
    SERVER_TIME_TTL_S = 300.0
    SERVER_TIME_WARN_MS = 3000.0

    def server_time_offset_ms(self, *, force: bool = False) -> float:
        """本机时钟相对该域交易所服务器时间的偏差（毫秒，正=本机慢）。

        ⚠️ 为什么需要它（2026-09-16 P1）：宿主机无 NTP、容器内无 CAP_SYS_TIME
        （`date -s` 被拒），实测本机比交易所**慢 ~2.1s**；而 Binance 私有请求的
        `recvWindow=5000ms` ⇒ 只剩 ~2.9s 的传输/排队余量，实测已撞到
        `[-1021] Timestamp for this request is outside of the recvWindow`。
        取 `GET /fapi/v1/time`（公共、免签）按 RTT/2 折算，缓存 `SERVER_TIME_TTL_S`；
        任何失败 → 0.0（fail-open，只告警，绝不因校时不可用而阻断交易）。
        """
        import time as _time
        cache = self._SERVER_TIME_CACHE.get(self.base_url)
        now = _time.time()
        if not force and cache and (now - cache[0]) < self.SERVER_TIME_TTL_S:
            return float(cache[1])
        start = _time.time()
        try:
            with urlopen(Request(f"{self.base_url}/fapi/v1/time",
                                 headers={"User-Agent": "R20-Binance/1.0"}), timeout=8.0) as resp:
                server_ms = float(json.loads(resp.read().decode("utf-8"))["serverTime"])
            rtt_ms = (_time.time() - start) * 1000.0
            offset = server_ms - (start * 1000.0 + rtt_ms / 2.0)   # 扣掉半程 RTT
        except Exception as exc:
            warnings.warn(
                f"[binance] {self.base_url} 服务器校时失败，按本机时钟签名（-1021 风险仍在）: {exc!r}",
                RuntimeWarning)
            return 0.0
        self._SERVER_TIME_CACHE[self.base_url] = (now, offset)
        if abs(offset) >= self.SERVER_TIME_WARN_MS:
            warnings.warn(
                f"[binance] 本机时钟与 {self.base_url} 偏差 {offset:+.0f}ms（recvWindow="
                f"{5000}ms）：余量已被吃掉 {abs(offset) / 5000 * 100:.0f}%，建议宿主机启用 NTP",
                RuntimeWarning)
        return float(offset)

    def _server_aligned_ms(self) -> int:
        """服务器校时后的毫秒时间戳（= 本机 + 实测偏差）。"""
        return int(time.time() * 1000 + self.server_time_offset_ms())

    def signed_request(self, method: str, path: str,
                       params: Optional[Dict[str, Any]] = None,
                       body: Optional[Dict[str, Any]] = None,
                       timeout: float = 15.0) -> Any:
        """Binance USDⓈ-M 私有请求（HMAC-SHA256 签名）。

        `timestamp` 一律用**服务器校时**值（见 `server_time_offset_ms`）；若仍被
        交易所判 `-1021`（时间戳超出 recvWindow —— 通常是某次慢请求把仅剩的余量
        吃光），强制重测偏差并**重试一次**：重试会重新取时间戳与签名，不复述旧时间。
        签名与 HTTP 传送仍在**本方法内**（`tests/extraction/test_binance_signing_extraction.py`
        的接缝门据此钉住"传送仍在门面"）。
        """
        key, secret = self._keys()
        m = method.upper()
        for attempt in (1, 2):
            if attempt == 2:
                self.server_time_offset_ms(force=True)   # 重测偏差后再签一次
            timestamp_ms = self._server_aligned_ms()
            full_query = build_signed_query(
                params=params,
                secret=secret,
                timestamp_ms=timestamp_ms)

            body_bytes = None
            headers = {
                "X-MBX-APIKEY": key,
                "Accept": "application/json",
                "User-Agent": "R20-Binance/1.0",
            }

            url = f"{self.base_url}{path}?{full_query}"
            if m not in ("GET", "DELETE") and body is not None:
                body_bytes = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"

            req = Request(url, data=body_bytes, headers=headers, method=m)
            try:
                with urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    return json.loads(raw) if raw else None
            except HTTPError as exc:
                raw = ""
                try:
                    raw = exc.read().decode("utf-8", errors="replace")
                    payload = json.loads(raw or "{}")
                except Exception:
                    payload = {}
                code = payload.get("code") if isinstance(payload, dict) else exc.code
                msg = payload.get("msg") if isinstance(payload, dict) else (raw[:200] or exc.reason)
                err = BinanceAPIError(code or exc.code, msg or "request failed", status=exc.code)
            except Exception as exc:
                raise BinanceAPIError("network", f"{type(exc).__name__}: {exc}") from exc

            if str(err.code) == "-1021" and attempt == 1:
                warnings.warn(
                    f"[binance] {path} 返回 -1021（时间戳超出 recvWindow），"
                    f"重测校时后重试一次", RuntimeWarning)
                continue
            raise err
        raise AssertionError("unreachable")

    def account_snapshot(self) -> Dict[str, Any]:
        """获取账户权益与可用保证金快照（USDT-M）。"""
        data = self.signed_request("GET", "/fapi/v2/account")
        if not isinstance(data, dict):
            raise BinanceAPIError("bad_response", "account 返回结构异常")

        equity = float(data.get("totalMarginBalance") or data.get("totalWalletBalance") or 0.0)
        avail = float(data.get("availableBalance") or data.get("maxWithdrawAmount") or 0.0)
        pos_m = float(data.get("totalPositionInitialMargin") or data.get("totalInitialMargin") or 0.0)
        ord_m = float(data.get("totalOpenOrderInitialMargin") or 0.0)
        upnl = float(data.get("totalUnrealizedProfit") or 0.0)

        return {
            "venue": "binance",
            "currency": "USDT",
            "equity_usdt": equity,
            "available_usdt": avail,
            "position_margin": pos_m,
            "order_margin": ord_m,
            "unrealized_pnl": upnl,
            "can_trade": bool(data.get("canTrade", True)),
            "raw": data,
        }

    def positions(self) -> List[Dict[str, Any]]:
        """获取当前活跃持仓列表（USDT-M）。仅返回 positionAmt != 0 的真实持仓。"""
        data = self.signed_request("GET", "/fapi/v2/positionRisk")
        rows = data if isinstance(data, list) else []
        out = []
        for p in rows:
            if not isinstance(p, dict):
                continue
            amt = float(p.get("positionAmt") or 0.0)
            if abs(amt) < 1e-12:
                continue
            symbol = str(p.get("symbol") or "")
            out.append({
                "venue": "binance",
                "inst_id": symbol,
                "base": self.canonical(symbol),
                "side": "long" if amt > 0 else "short",
                "size_signed": amt,
                "entry_price": float(p.get("entryPrice") or 0.0),
                "mark_price": float(p.get("markPrice") or 0.0),
                "leverage": float(p.get("leverage") or 0.0),
                "margin": float(p.get("isolatedMargin") or p.get("positionInitialMargin") or 0.0),
                "notional": float(p.get("notional") or 0.0),
                "margin_mode": str(p.get("marginType") or "cross").lower(),
                "unrealized_pnl": float(p.get("unRealizedProfit") or 0.0),
                "liq_price": float(p.get("liquidationPrice") or 0.0) or None,
                "raw": p,
            })
        return out

    def open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取当前普通在途订单列表（USDT-M）。"""
        params = {}
        if symbol:
            params["symbol"] = self.native_symbol(symbol)
        data = self.signed_request("GET", "/fapi/v1/openOrders", params=params)
        rows = data if isinstance(data, list) else []
        out = []
        for o in rows:
            if not isinstance(o, dict):
                continue
            out.append({
                "venue": "binance",
                "order_id": str(o.get("orderId") or ""),
                "client_order_id": str(o.get("clientOrderId") or ""),
                "inst_id": str(o.get("symbol") or ""),
                "base": self.canonical(str(o.get("symbol") or "")),
                "side": str(o.get("side") or "").lower(),
                "price": float(o.get("price") or 0.0),
                "size": float(o.get("origQty") or 0.0),
                "status": str(o.get("status") or ""),
                "raw": o,
            })
        return out

    # ---- 下单与执行闭环（US-005）----
    def detect_position_mode(self) -> str:
        """**只读**探测账户持仓模式（net / long_short），失败/读不到 → "unknown"。

        端点：`GET /fapi/v1/positionSide/dual`（实测回包 `{"dualSidePosition": false}`）。
        永不抛异常、永不改账户（本系统**不会**调用 `/fapi/v1/positionSide/dual` 的
        POST 去切换模式）。
        """
        try:
            data = self.signed_request("GET", "/fapi/v1/positionSide/dual")
        except Exception:
            return "unknown"
        return interpret_dual_side_position(data)

    def place_order(self, symbol: str, side: str, contracts: float,
                    price: Optional[float] = None, tif: str = "gtc",
                    text: str = "", position_side: Optional[str] = None,
                    reduce_only: bool = False) -> Dict[str, Any]:
        """Binance 下单接口（US-005）。
        contracts: base_asset 下单量（币数，正数）；
        side: 'long'/'buy' -> 'BUY', 'short'/'sell' -> 'SELL'。
        reduce_only: 净模式平仓专用（对冲模式禁传，由调用方按 positionSide 分流）。
        """
        inst = self.native_symbol(symbol)
        s = "BUY" if str(side).lower() in ("long", "buy") else "SELL"
        qty = float(contracts)
        if qty <= 0:
            raise ValueError(f"下单数量必须为正数，收到: {contracts}")

        spec = self.fetch_instrument_spec(symbol)
        params = build_order_params(
            inst=inst,
            position_side=position_side,
            price=price,
            qty=qty,
            reduce_only=reduce_only,
            s=s,
            spec=spec,
            text=text,
            tif=tif        )

        data = self.signed_request("POST", "/fapi/v1/order", params=params)
        if not isinstance(data, dict):
            raise BinanceAPIError("bad_response", "下单响应结构异常")

        order_id = str(data.get("orderId") or "")
        client_oid = str(data.get("clientOrderId") or "")
        return {
            "venue": "binance",
            "id": order_id,
            "order_id": order_id,
            "client_order_id": client_oid,
            "text": client_oid,
            "status": str(data.get("status") or ""),
            "symbol": inst,
            "side": s.lower(),
            "price": float(data.get("price") or 0.0),
            "origQty": float(data.get("origQty") or 0.0),
            "executedQty": float(data.get("executedQty") or 0.0),
            "raw": data,
        }

    def create_order(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """place_order 别名。"""
        return self.place_order(*args, **kwargs)

    def cancel_order(self, symbol: str, order_id: Optional[str] = None,
                     client_order_id: Optional[str] = None) -> Dict[str, Any]:
        """撤销普通委托（US-005）。"""
        inst = self.native_symbol(symbol)
        params: Dict[str, Any] = {"symbol": inst}
        if order_id is not None:
            # 审计 D6：上游 execution_router 的回退链可能把 client text
            # （t-r20e*，非数字）当 order_id 传入——Binance 只认纯数字 orderId，
            # 混族直传会被 -2011 拒撤 → 回滚漏网孤儿入场单裸挂。非数字一律
            # 改走所方原生 origClientOrderId。
            _oid = str(order_id)
            if _oid.isdigit():
                params["orderId"] = _oid
            else:
                params["origClientOrderId"] = _oid
        elif client_order_id:
            params["origClientOrderId"] = str(client_order_id)
        else:
            raise ValueError("撤单需 order_id 或 client_order_id")

        data = self.signed_request("DELETE", "/fapi/v1/order", params=params)
        # 审计 D3：status 必须回显交易所真实状态——旧实现硬编码 "CANCELED"，
        # 抢撤竞态（下单方先成交）时响应实为 FILLED 也会被伪报撤成功。
        return {"venue": "binance", "order_id": str(data.get("orderId") or ""),
                "status": str(data.get("status") or "UNKNOWN"), "raw": data}

    def cancel_all_orders(self, symbol: str) -> Dict[str, Any]:
        """撤销该标的所有普通挂单（US-005）。"""
        inst = self.native_symbol(symbol)
        data = self.signed_request("DELETE", "/fapi/v1/allOpenOrders", params={"symbol": inst})
        return {"venue": "binance", "symbol": inst, "result": data}

    #: `/fapi/v1/marginType` 在 **Binance Demo 域**（demo-fapi.binance.com）不可用
    #: 时的两种表现（2026-09-16 逐格实测，tests/venues/test_binance_adapter_private.py）：
    #: 参数放 query → -1102「margintype 缺失」；放 JSON body → -1022「签名无效」。
    MARGIN_TYPE_ENDPOINT_UNAVAILABLE = (-1102, -1022)

    def position_margin_type(self, inst: str) -> Optional[str]:
        """读回该合约当前保证金档（`'cross'` / `'isolated'`）；读不到 → `None`（不猜）。"""
        try:
            rows = self.signed_request("GET", "/fapi/v2/positionRisk", params={"symbol": inst})
        except Exception:
            return None
        row: Any = rows[0] if isinstance(rows, list) and rows else rows
        if not isinstance(row, dict):
            return None
        raw = str(row.get("marginType") or "").strip().lower()
        return raw or None

    def set_leverage(self, symbol: str, leverage: float, margin_mode: str = "cross") -> Any:
        """设置标的杠杆倍数与保证金模式（US-005）。

        ⚠️ 2026-09-16 修 P1（"Binance 一单都下不出去"）：旧实现**无条件**先调
        `/fapi/v1/marginType`，而该端点在 Binance **Demo 域不可用**（见上常量），
        于是每一笔 Binance 下单都死在这一步——实盘日志 2026-09-13/14 十次
        「设置杠杆失败: Binance [-1102]: Mandatory parameter 'margintype'…」，
        而账户当时的档位**本来就是 cross**（positionRisk 读回一致）。
        改为「**先读、后写、以读回为准**」：
          1. 读回已是目标档 → 跳过写入（压根不碰这个坏端点）；
          2. 不一致才写；写入失败后**必须再读回核对**——读回已是目标档 →
             端点坏但现状正确，告警放行；读回仍不一致 → 上抛 fail-closed；
             读不回档位且错误码属"端点不可用" → 告警放行（不假装核对通过）。
        审计 C1 的意图不变：**确定处于错误档位**时绝不继续强设杠杆。
        """
        inst = self.native_symbol(symbol)
        want = str(margin_mode or "cross").strip().lower()
        have = self.position_margin_type(inst)
        if have is None:
            warnings.warn(
                f"[binance] {inst} 保证金档读回失败，将直接尝试设置 {want}（无法核对现状）",
                RuntimeWarning)
        elif have == want:
            return self.signed_request("POST", "/fapi/v1/leverage", params={
                "symbol": inst, "leverage": int(leverage)})
        try:
            self.signed_request("POST", "/fapi/v1/marginType", params={
                "symbol": inst, "marginType": want.upper()})
        except BinanceAPIError as exc:
            if exc.code == -4046:      # 无需变更：现状即目标（旧语义保留）
                pass
            else:
                after = self.position_margin_type(inst)
                if after == want:
                    warnings.warn(
                        f"[binance] /fapi/v1/marginType 失败({exc.code}: {exc})，"
                        f"但读回档位已={after}（目标），继续——该端点在 Demo 域不可用",
                        RuntimeWarning)
                elif after is None and exc.code in self.MARGIN_TYPE_ENDPOINT_UNAVAILABLE:
                    warnings.warn(
                        f"[binance] /fapi/v1/marginType 端点不可用({exc.code}) 且档位读不回，"
                        f"按现状继续（未核对通过，仅告警）", RuntimeWarning)
                else:
                    raise   # 确定处于错误档位（或 -4059 对冲冲突等）→ fail-closed 上抛
        return self.signed_request("POST", "/fapi/v1/leverage", params={
            "symbol": inst, "leverage": int(leverage)})

    def attach_protective_orders(self, symbol: str, side: str,
                                 tp_px: Optional[float] = None,
                                 sl_px: Optional[float] = None,
                                 working_type: str = "CONTRACT_PRICE",
                                 position_side: Optional[str] = None,
                                 expiration: Optional[int] = None,
                                 contracts: Optional[float] = None,
                                 **kwargs: Any) -> Dict[str, str]:
        """挂云端条件止盈止损单（/fapi/v1/algoOrder）（US-005）。
        - 显式 workingType（默认 CONTRACT_PRICE）；
        - 多头（long）-> 平仓反向 SELL；空头（short）-> 平仓反向 BUY；
        - 未成交挂单阶段带 contracts/quantity 走 reduceOnly；已有仓位兜底 closePosition。
        """
        inst = self.native_symbol(symbol)
        opp_side = "SELL" if str(side).lower() in ("long", "buy") else "BUY"
        wt = self._require_working_type(working_type)

        qty_val = float(contracts or kwargs.get("size") or 0.0)
        spec = self.fetch_instrument_spec(symbol)
        step = Decimal(str(spec.step_size if spec else 1e-6))
        qty_str = None
        if qty_val > 0:
            qty_dec = (Decimal(str(qty_val)) / step).to_integral_value(rounding=ROUND_DOWN) * step
            qty_str = format(qty_dec, "f").rstrip("0").rstrip(".") if "." in format(qty_dec, "f") else format(qty_dec, "f")

        res = {"tp": "", "sl": ""}

        res["tp"] = send_protective_order(
            build_algo_order_request=self.build_algo_order_request,
            inst=inst,
            opp_side=opp_side,
            position_side=position_side,
            private_algo_send=self._private_algo_send,
            qty_str=qty_str,
            trigger_price=tp_px,
            type_="TAKE_PROFIT_MARKET",
            wt=wt,
        )

        res["sl"] = send_protective_order(
            build_algo_order_request=self.build_algo_order_request,
            inst=inst,
            opp_side=opp_side,
            position_side=position_side,
            private_algo_send=self._private_algo_send,
            qty_str=qty_str,
            trigger_price=sl_px,
            type_="STOP_MARKET",
            wt=wt,
        )

        return res

    def list_protective_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取当前活跃的 Algo 条件单列表（US-005）。"""
        req = self.build_algo_open_orders_request(symbol=self.native_symbol(symbol) if symbol else None)
        data = self._private_algo_send(req)
        rows = data if isinstance(data, list) else []
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            algo_id = str(r.get("algoId") or r.get("orderId") or "")
            out.append({
                "id": algo_id,
                "algo_id": algo_id,
                "symbol": str(r.get("symbol") or ""),
                "side": str(r.get("side") or "").lower(),
                "trigger_price": float(r.get("triggerPrice") or 0.0),
                "type": str(r.get("algoType") or r.get("type") or ""),
                "raw": r,
            })
        return out

    def fast_close_position(self, symbol: str, pos_side: Optional[str] = None) -> Dict[str, Any]:
        """市价全平当前持仓（US-005；审计 B1）：
        - 净模式（positionSide=BOTH/缺省）带 reduceOnly=true——与云端 TP 竞态时
          只减不增，绝不反向开新仓；
        - 对冲模式（positionSide=LONG/SHORT）按所方契约禁传 reduceOnly，显式钉腿；
        - 双向同存且调用方未指明方向 = 拒绝盲平，返回 closed:False 由上层 fail-closed。
        """
        inst = self.native_symbol(symbol)
        rows = [p for p in self.positions() if p.get("inst_id") == inst]
        want = str(pos_side or "").strip().lower()
        if want in ("long", "short"):
            rows = [p for p in rows if str(p.get("side", "")).lower() == want]
        if not rows:
            return {"venue": "binance", "symbol": inst, "closed": False, "reason": "无持仓"}
        if len(rows) > 1:
            return {"venue": "binance", "symbol": inst, "closed": False,
                    "reason": "对冲模式双向同存，拒绝盲平——须指定 pos_side"}
        target = rows[0]
        size = abs(float(target.get("size_signed") or 0.0))
        if size <= 1e-12:
            return {"venue": "binance", "symbol": inst, "closed": False, "reason": "持仓为0"}
        close_side = "SELL" if str(target.get("side", "")).lower() == "long" else "BUY"
        raw = target.get("raw") if isinstance(target.get("raw"), dict) else {}
        ps = str(raw.get("positionSide") or "").upper()
        if ps in ("LONG", "SHORT"):
            return self.place_order(symbol, close_side, size, price=None, position_side=ps)
        return self.place_order(symbol, close_side, size, price=None, reduce_only=True)

    # ---- Algo 私有通道发送入口（US-005 实装）----
    def _private_algo_send(self, request: Dict[str, Any]) -> Any:
        m = request["method"]
        p = request["path"]
        params = request.get("params") or request.get("body")
        return self.signed_request(m, p, params=params)

    def query_algo_order(self, *, algo_id=None, client_algo_id=None) -> Any:
        return self._private_algo_send(self.build_algo_query_request(
            algo_id=algo_id, client_algo_id=client_algo_id))

    def current_all_algo_open_orders(self, *, symbol: Optional[str] = None) -> Any:
        return self._private_algo_send(self.build_algo_open_orders_request(symbol=symbol))

    def cancel_algo_order(self, *, algo_id=None, client_algo_id=None) -> Any:
        return self._private_algo_send(self.build_algo_cancel_request(
            algo_id=algo_id, client_algo_id=client_algo_id))

    def cancel_protective_orders(self, symbol: str) -> Any:
        """撤销指定合约的全部活跃云端 Algo 保护单（TP/SL）。"""
        return self.cancel_all_algo_open_orders(symbol=symbol)

    def cancel_all_algo_open_orders(self, *, symbol: str) -> Any:
        inst = self.native_symbol(symbol) if symbol else ""
        open_algos = self.list_protective_orders(symbol=inst)
        results = []
        failures = []
        for o in open_algos:
            aid = o.get("algo_id") or o.get("id")
            if aid:
                try:
                    res = self.cancel_algo_order(algo_id=aid)
                    results.append(res)
                except Exception as exc:
                    # 审计 D3：逐笔失败必须收集上报——旧实现 pass 吞掉后仍
                    # 伪造 {"code":"200","msg":"success"}，全败也报成功（保护单
                    # 清场链路据此放行 = 裸旧单残留）。
                    failures.append({"algo_id": str(aid), "error": str(exc)[:200]})
        return {"success": not failures, "attempted": len(results) + len(failures),
                "canceled": results, "failed": failures}

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

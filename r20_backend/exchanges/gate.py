"""Gate.io 永续合约（V4 USDT 结算）适配器：公共只读 + 私有执行（Phase 3-Gate）。

签名逐行核对官方 gateapi-python `api_client.gen_sign`（非凭记忆）：
    sign_string = METHOD\\nPATH\\nQUERY\\nsha512hex(BODY)\\nTIMESTAMP(seconds)
    SIGN = HMAC-SHA512(secret, sign_string).hexdigest()
    headers: KEY / Timestamp / SIGN
语义要点：
- 带符号张数：正=开多、负=开空；平仓 close=true + reduce_only=true；
- TP/SL 为独立 /price_orders 触发单：price_type 0=最新价（对齐 OKX 语义），
  rule 1=价格≥触发、2=价格≤触发；reduce_only 保证对手腿成交后残留触发单自然
  失效，绝不反向开仓；
- 沙盒 fx-api-testnet.gateio.ws 连续实测 502（09-08/09-09），暂不可依赖；
- 执行总开关 R20_GATE_EXECUTION=1（默认关闭 fail-closed）+ 凭证必填。
80% 返佣经济（用户 2026-09-09 确认）：taker 净成本 ~0.01%，maker 更低——
限价入场在 Gate 上费率摩擦几乎归零。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import (BaseExchangeAdapter, ExchangeCapabilities,
                   ExchangeCapabilityError, InstrumentSpec)

INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1H": "1h", "4H": "4h", "8H": "8h", "1D": "1d", "1W": "7d",
}

# Gate 触发单 rule：1=价格≥触发价，2=价格≤触发价
RULE_ABOVE, RULE_BELOW = 1, 2


class GateAPIError(RuntimeError):
    """Gate 私有 API 业务错误（label 为官方错误码）。"""

    def __init__(self, label: str, message: str, status: int = 0):
        super().__init__(f"Gate {label or '--'}: {message or 'request failed'}")
        self.label = str(label or "")
        self.status = status


class GateAdapter(BaseExchangeAdapter):
    base_url = "https://api.gateio.ws"
    live_url = "https://api.gateio.ws"
    test_url = "https://fx-api-testnet.gateio.ws"   # 连续实测 502，暂不可依赖
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
        supports_account=True,
        supports_orders=True,
        mainland_ip_restricted=False,
        rate_limit_note="公共端点约 100~200 req/s，四所最宽松；下单 100/s",
    )

    # ======================================================================
    # 公共只读面
    # ======================================================================
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
        chg = float(t.get("change_percentage") or 0)
        open_24h = round(last / (1 + chg / 100.0), 8) if last and chg > -100 else None
        return {
            "venue": "gate", "inst_id": inst,
            "last": last or None,
            "bid": float(t.get("highest_bid") or 0) or None,
            "ask": float(t.get("lowest_ask") or 0) or None,
            "mark_price": float(t.get("mark_price") or 0) or None,
            "open_24h": open_24h,
            "high_24h": float(t.get("high_24h") or 0) or None,
            "low_24h": float(t.get("low_24h") or 0) or None,
            "chg_24h_pct": chg,
            "vol_24h_base": float(t.get("volume_24h_base") or 0),
            "quote_vol_24h": float(t.get("volume_24h_quote") or 0),
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

    # ======================================================================
    # 私有面：签名器（纯函数可测）+ 请求器 + 账户/下单/保护单
    # ======================================================================
    @staticmethod
    def sign_string(method: str, path: str, query: str, body: str, timestamp: str) -> str:
        return "%s\n%s\n%s\n%s\n%s" % (
            method.upper(), path, query or "",
            hashlib.sha512((body or "").encode("utf-8")).hexdigest(), timestamp,
        )

    def sign(self, method: str, path: str, query: str, body: str,
             timestamp: str, secret: str) -> str:
        msg = self.sign_string(method, path, query, body, timestamp)
        return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha512).hexdigest()

    def _keys(self) -> tuple:
        try:
            from r20_gateway.secrets import load_secrets
            vals = load_secrets()
        except Exception:
            vals = {}
        key, secret = str(vals.get("GATE_API_KEY") or ""), str(vals.get("GATE_SECRET_KEY") or "")
        if not key or not secret:
            raise ExchangeCapabilityError(
                "Gate 凭证未配置——请在后台「交易所与标的池 → 5. 多交易所数据源与凭证」录入 API Key/Secret")
        return key, secret

    def signed_request(self, method: str, path: str,
                       params: Optional[Dict[str, Any]] = None,
                       body: Optional[Dict[str, Any]] = None,
                       timeout: float = 15.0) -> Any:
        """私有 V4 请求：params→query（GET/DELETE/POST-query 端点通用），body→JSON。"""
        key, secret = self._keys()
        clean_params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        query = urlencode(clean_params, doseq=True) if clean_params else ""
        body_text = json.dumps({k: v for k, v in (body or {}).items() if v is not None},
                               separators=(",", ":"), ensure_ascii=False) if body is not None else ""
        ts = str(int(time.time()))
        headers = {
            "KEY": key,
            "Timestamp": ts,
            "SIGN": self.sign(method, path, query, body_text, ts, secret),
            "Accept": "application/json",
        }
        if body_text:
            headers["Content-Type"] = "application/json"
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        # 沙盒档位：base_url 已由 __init__ 按环境选定；Request 直接用 self.base_url
        req = Request(url, data=body_text.encode("utf-8") if body_text else None,
                      headers=headers, method=method.upper())
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8")
                payload = json.loads(raw or "{}")
            except Exception:
                payload = {}
            raise GateAPIError(str(payload.get("label") or exc.code),
                               str(payload.get("message") or raw[:200] or exc.reason),
                               status=exc.code) from exc
        except Exception as exc:
            raise GateAPIError("network", f"{type(exc).__name__}: {exc}") from exc

    # ---- 账户 ----
    def account_snapshot(self) -> Dict[str, Any]:
        data = self.signed_request("GET", "/api/v4/futures/usdt/accounts")
        if not isinstance(data, dict):
            raise GateAPIError("bad_response", "accounts 返回结构异常")
        return {
            "venue": "gate", "currency": str(data.get("currency") or "USDT"),
            "equity_usdt": float(data.get("total") or 0),
            "available_usdt": float(data.get("available") or 0),
            "position_margin": float(data.get("position_margin") or 0),
            "order_margin": float(data.get("order_margin") or 0),
            "unrealized_pnl": float(data.get("unrealised_pnl") or 0),
            "in_dual_mode": bool(data.get("in_dual_mode")),
            "raw": data,
        }

    def positions(self) -> List[Dict[str, Any]]:
        data = self.signed_request("GET", "/api/v4/futures/usdt/positions", {"holding": "true"})
        rows = data if isinstance(data, list) else []
        out = []
        for p in rows:
            size = float(p.get("size") or 0)
            if abs(size) < 1e-12:
                continue
            out.append({
                "venue": "gate", "inst_id": str(p.get("contract") or ""),
                "base": self.canonical(str(p.get("contract") or "")),
                "side": "long" if size > 0 else "short",
                "size_signed": size,
                "entry_price": float(p.get("entry_price") or 0),
                "mark_price": float(p.get("mark_price") or 0),
                "leverage": float(p.get("leverage") or 0),
                "margin": float(p.get("margin") or 0),
                "unrealized_pnl": float(p.get("unrealised_pnl") or 0),
                "liq_price": float(p.get("liq_price") or 0) or None,
                "raw": p,
            })
        return out

    def set_leverage(self, symbol: str, leverage: float, margin_mode: str = "cross") -> Any:
        inst = self.native_symbol(symbol)
        return self.signed_request(
            "POST", f"/api/v4/futures/usdt/positions/{inst}/leverage",
            params={"leverage": str(int(leverage)), "margin_mode": margin_mode})

    # ---- 下单 ----
    def place_order(self, symbol: str, side: str, contracts: float,
                    price: Optional[float] = None, tif: str = "gtc",
                    text: str = "") -> Dict[str, Any]:
        """contracts 为正张数；side long→+、short→−（Gate 带符号张数语义）。"""
        inst = self.native_symbol(symbol)
        n = int(round(abs(contracts)))
        if n <= 0:
            raise ExchangeCapabilityError("下单张数必须为正整数")
        signed = n if str(side).lower() in ("long", "buy", "b") else -n
        order = {
            "contract": inst, "size": signed,
            "price": ("0" if price is None else str(price)),
            "tif": "ioc" if price is None else tif,
            "text": text or f"t-r20{int(time.time() * 1000) % 100000000}",
        }
        data = self.signed_request("POST", "/api/v4/futures/usdt/orders", body=order)
        if not isinstance(data, dict) or not (data.get("id") or data.get("text")):
            raise GateAPIError("bad_response", f"下单回执异常: {str(data)[:160]}")
        return data

    def cancel_order(self, symbol: str, order_id: Any) -> Any:
        inst = self.native_symbol(symbol)
        return self.signed_request("DELETE", "/api/v4/futures/usdt/orders",
                                   params={"contract": inst, "id": str(order_id)})

    def list_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """该合约未成交普通挂单（G7 联动：试验田对账前先撤孤儿入场挂单用）。"""
        inst = self.native_symbol(symbol)
        data = self.signed_request("GET", "/api/v4/futures/usdt/orders",
                                   params={"contract": inst, "status": "open", "limit": "100"})
        return data if isinstance(data, list) else []

    def fast_close_position(self, symbol: str, text: str = "") -> Dict[str, Any]:
        inst = self.native_symbol(symbol)
        order = {"contract": inst, "size": 0, "close": True, "price": "0", "tif": "ioc",
                 "text": text or f"t-r20c{int(time.time() * 1000) % 100000000}"}
        data = self.signed_request("POST", "/api/v4/futures/usdt/orders", body=order)
        return data if isinstance(data, dict) else {"raw": data}

    # ---- 保护单（TP/SL 触发单）----
    @staticmethod
    def trigger_rule(pos_side: str, kind: str) -> int:
        """多 TP：价≥tp；多 SL：价≤sl；空 TP：价≤tp；空 SL：价≥sl。"""
        long = str(pos_side).lower().startswith("l")
        if kind == "tp":
            return RULE_ABOVE if long else RULE_BELOW
        return RULE_BELOW if long else RULE_ABOVE

    def attach_protective_orders(self, symbol: str, pos_side: str,
                                 tp_px: Optional[float] = None,
                                 sl_px: Optional[float] = None,
                                 expiration: int = 604800,
                                 price_type: int = 0) -> Dict[str, Any]:
        """挂 TP/SL 双腿 reduce_only 全平触发单；任一半途失败自动回滚已挂腿。"""
        inst = self.native_symbol(symbol)
        placed: Dict[str, Any] = {}
        legs = []
        if tp_px:
            legs.append(("tp", float(tp_px), self.trigger_rule(pos_side, "tp")))
        if sl_px:
            legs.append(("sl", float(sl_px), self.trigger_rule(pos_side, "sl")))
        if not legs:
            raise ExchangeCapabilityError("TP/SL 至少给一条")
        try:
            for kind, px, rule in legs:
                payload = {
                    "initial": {
                        "contract": inst, "size": 0, "price": "0",
                        "close": True, "tif": "ioc", "reduce_only": True,
                        "text": f"t-r20{kind}{int(time.time() * 1000) % 100000000}",
                    },
                    "trigger": {"strategy_type": 0, "price_type": price_type,
                                "price": str(px), "rule": rule,
                                "expiration": int(expiration)},
                }
                data = self.signed_request("POST", "/api/v4/futures/usdt/price_orders",
                                           body=payload)
                oid = (data or {}).get("id") if isinstance(data, dict) else None
                if not oid:
                    raise GateAPIError("bad_response", f"{kind} 触发单回执缺 id: {str(data)[:120]}")
                placed[kind] = str(oid)
        except Exception:
            for kind, oid in placed.items():   # 回滚：不留裸单
                try:
                    self.cancel_price_order(oid)
                except Exception:
                    pass
            raise
        return placed

    def list_protective_orders(self, symbol: str) -> List[Dict[str, Any]]:
        inst = self.native_symbol(symbol)
        data = self.signed_request("GET", "/api/v4/futures/usdt/price_orders",
                                   params={"contract": inst, "status": "open", "limit": "100"})
        return data if isinstance(data, list) else []

    def cancel_price_order(self, order_id: Any) -> Any:
        return self.signed_request("DELETE", f"/api/v4/futures/usdt/price_orders/{order_id}")

    def amend_stop_loss(self, symbol: str, pos_side: str, old_sl_id: Optional[str],
                        new_sl_px: float, expiration: int = 604800) -> str:
        """棘轮上移止损：先挂新 SL（保护无缝隙）→ 成功后撤旧 SL；
        新挂失败则旧单保持原状（宁可松，不可裸）。返回新 SL 触发单 id。"""
        placed = self.attach_protective_orders(symbol, pos_side, sl_px=new_sl_px,
                                               expiration=expiration)
        new_id = placed.get("sl", "")
        if new_id and old_sl_id and str(old_sl_id) != str(new_id):
            try:
                self.cancel_price_order(old_sl_id)
            except Exception:
                pass   # 双 SL 短暂共存是安全的（reduce_only）
        return new_id

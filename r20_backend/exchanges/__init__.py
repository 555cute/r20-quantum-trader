"""Unified Exchange Abstraction Base and Adapters for OKX, Binance, and Gate.io.
Provides standardized symbol translation, public market data fetching (Ticker, Klines, Orderbook, Funding),
and private trading stubs.
"""
from __future__ import annotations

import abc
import hashlib
import hmac
import json
import logging
import math
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

STANDARD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def http_get_json(url: str, timeout: float = 6.0) -> Any:
    """Helper to perform an unauthenticated GET request and return parsed JSON."""
    req = urllib.request.Request(url, headers=STANDARD_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class BaseExchangeAdapter(abc.ABC):
    """Abstract base class for all crypto exchange adapters."""

    def __init__(self, api_key: str = "", api_secret: str = "", passphrase: str = "", testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.testnet = testnet

    @property
    @abc.abstractmethod
    def exchange_id(self) -> str:
        """Returns unique exchange identifier: 'okx', 'binance', or 'gate'."""
        pass

    @abc.abstractmethod
    def normalize_symbol(self, raw_symbol: str) -> str:
        """Converts exchange-specific contract name to unified symbol key (e.g. 'BTC-USDT-SWAP' -> 'BTC')."""
        pass

    @abc.abstractmethod
    def format_symbol(self, base_asset: str) -> str:
        """Converts unified symbol key to exchange-specific contract name (e.g. 'BTC' -> 'BTCUSDT')."""
        pass

    # Public Market Data (Unauthenticated)
    @abc.abstractmethod
    def fetch_ticker(self, base_asset: str, timeout: float = 5.0) -> Dict[str, Any]:
        """Fetches standardized ticker data: last, mark, high24h, low24h, volume24h, funding_rate."""
        pass

    @abc.abstractmethod
    def fetch_klines(self, base_asset: str, interval: str = "1h", limit: int = 100, timeout: float = 6.0) -> List[List[float]]:
        """Fetches standardized candlestick list: [[timestamp_ms, open, high, low, close, volume], ...]."""
        pass

    @abc.abstractmethod
    def fetch_orderbook(self, base_asset: str, limit: int = 20, timeout: float = 5.0) -> Dict[str, List[List[float]]]:
        """Fetches standardized orderbook: {'bids': [[px, sz], ...], 'asks': [[px, sz], ...]}."""
        pass

    # Standardized Private Execution Core
    @abc.abstractmethod
    def place_limit_order_with_sl_tp(
        self,
        base_asset: str,
        action: str,  # BUY_LONG or SELL_SHORT
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        margin_usdt: float,
        leverage: int = 3,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Places entry limit order and attaches/links SL & TP orders.
        Returns: (success: bool, order_id_or_err: str, details: dict)
        """
        pass

    @abc.abstractmethod
    def cancel_order(self, base_asset: str, order_id: str) -> bool:
        """Cancels a live order."""
        pass


class OKXAdapter(BaseExchangeAdapter):
    """OKX V5 API Adapter (Native)."""

    @property
    def exchange_id(self) -> str:
        return "okx"

    def normalize_symbol(self, raw_symbol: str) -> str:
        clean = raw_symbol.upper()
        if "-" in clean:
            return clean.split("-")[0]
        return clean

    def format_symbol(self, base_asset: str) -> str:
        asset = base_asset.upper()
        if asset.endswith("-USDT-SWAP"):
            return asset
        return f"{asset}-USDT-SWAP"

    def fetch_ticker(self, base_asset: str, timeout: float = 5.0) -> Dict[str, Any]:
        inst_id = self.format_symbol(base_asset)
        url = f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}"
        data = http_get_json(url, timeout=timeout)
        row = data.get("data", [{}])[0]
        return {
            "exchange": "okx",
            "symbol": base_asset.upper(),
            "inst_id": inst_id,
            "last": float(row.get("last", 0) or 0),
            "high24h": float(row.get("high24h", 0) or 0),
            "low24h": float(row.get("low24h", 0) or 0),
            "vol24h": float(row.get("vol24h", 0) or 0),
            "timestamp": int(row.get("ts", 0) or 0),
        }

    def fetch_klines(self, base_asset: str, interval: str = "1h", limit: int = 100, timeout: float = 6.0) -> List[List[float]]:
        inst_id = self.format_symbol(base_asset)
        bar_map = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
        bar = bar_map.get(interval.lower(), "1H")
        url = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}"
        data = http_get_json(url, timeout=timeout)
        raw_candles = data.get("data", [])
        standard_klines: List[List[float]] = []
        for c in reversed(raw_candles):
            # OKX: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
            standard_klines.append([
                float(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])
            ])
        return standard_klines

    def fetch_orderbook(self, base_asset: str, limit: int = 20, timeout: float = 5.0) -> Dict[str, List[List[float]]]:
        inst_id = self.format_symbol(base_asset)
        url = f"https://www.okx.com/api/v5/market/books?instId={inst_id}&sz={limit}"
        data = http_get_json(url, timeout=timeout)
        book = data.get("data", [{}])[0]
        bids = [[float(b[0]), float(b[1])] for b in book.get("bids", [])]
        asks = [[float(a[0]), float(a[1])] for a in book.get("asks", [])]
        return {"bids": bids, "asks": asks}

    def place_limit_order_with_sl_tp(
        self,
        base_asset: str,
        action: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        margin_usdt: float,
        leverage: int = 3,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """OKX execution: delegates to native CLI / OKX V5 trade service with OCO attaching."""
        from scripts.ai_factor_trader import place_contract_order
        inst_id = self.format_symbol(base_asset)
        pos_side = "long" if "LONG" in action.upper() or "BUY" in action.upper() else "short"
        side = "buy" if pos_side == "long" else "sell"
        # In OKX, position size is in contracts (1 cont = 0.01 BTC, 0.1 ETH etc)
        # Approximate contracts from margin and leverage
        notional = margin_usdt * leverage
        ct_val = 0.01 if "BTC" in inst_id else (0.1 if "ETH" in inst_id else 1.0)
        size = max(1, int(notional / max(1e-6, entry_price * ct_val)))

        success, order_id = place_contract_order(
            inst_id=inst_id,
            pos_side=pos_side,
            side=side,
            price=entry_price,
            size=size,
            tp_px=take_profit,
            sl_px=stop_loss,
            leverage=leverage,
        )
        return success, str(order_id), {"inst_id": inst_id, "size": size, "pos_side": pos_side}

    def cancel_order(self, base_asset: str, order_id: str) -> bool:
        from scripts.ai_factor_trader import okx_private_command, run_cmd_result
        inst_id = self.format_symbol(base_asset)
        res = run_cmd_result(okx_private_command(f"okx swap cancel {inst_id} --ordId {order_id} --json"), timeout=15)
        return bool(res.get("ok"))


class BinanceAdapter(BaseExchangeAdapter):
    """Binance USDT-M Futures API Adapter."""

    @property
    def exchange_id(self) -> str:
        return "binance"

    @property
    def fapi_host(self) -> str:
        return "https://testnet.binancefuture.com" if self.testnet else "https://fapi.binance.com"

    def normalize_symbol(self, raw_symbol: str) -> str:
        clean = raw_symbol.upper().replace("_", "").replace("-", "")
        if clean.endswith("USDT"):
            return clean[:-4]
        return clean

    def format_symbol(self, base_asset: str) -> str:
        asset = base_asset.upper().replace("-USDT-SWAP", "").replace("_USDT", "")
        return f"{asset}USDT"

    def fetch_ticker(self, base_asset: str, timeout: float = 5.0) -> Dict[str, Any]:
        symbol = self.format_symbol(base_asset)
        url = f"{self.fapi_host}/fapi/v1/ticker/24hr?symbol={symbol}"
        row = http_get_json(url, timeout=timeout)
        return {
            "exchange": "binance",
            "symbol": base_asset.upper(),
            "inst_id": symbol,
            "last": float(row.get("lastPrice", 0) or 0),
            "high24h": float(row.get("highPrice", 0) or 0),
            "low24h": float(row.get("lowPrice", 0) or 0),
            "vol24h": float(row.get("volume", 0) or 0),
            "price_change_percent": float(row.get("priceChangePercent", 0) or 0),
            "timestamp": int(row.get("closeTime", 0) or 0),
        }

    def fetch_klines(self, base_asset: str, interval: str = "1h", limit: int = 100, timeout: float = 6.0) -> List[List[float]]:
        symbol = self.format_symbol(base_asset)
        binance_interval = interval.lower()
        url = f"{self.fapi_host}/fapi/v1/klines?symbol={symbol}&interval={binance_interval}&limit={limit}"
        raw_klines = http_get_json(url, timeout=timeout)
        standard_klines: List[List[float]] = []
        for k in raw_klines:
            standard_klines.append([
                float(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
            ])
        return standard_klines

    def fetch_orderbook(self, base_asset: str, limit: int = 20, timeout: float = 5.0) -> Dict[str, List[List[float]]]:
        symbol = self.format_symbol(base_asset)
        url = f"{self.fapi_host}/fapi/v1/depth?symbol={symbol}&limit={limit}"
        book = http_get_json(url, timeout=timeout)
        bids = [[float(b[0]), float(b[1])] for b in book.get("bids", [])]
        asks = [[float(a[0]), float(a[1])] for a in book.get("asks", [])]
        return {"bids": bids, "asks": asks}

    def fetch_long_short_ratio(self, base_asset: str, period: str = "1h", timeout: float = 5.0) -> Optional[float]:
        try:
            symbol = self.format_symbol(base_asset)
            url = f"https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={symbol}&period={period}&limit=1"
            data = http_get_json(url, timeout=timeout)
            if data and isinstance(data, list):
                return float(data[0].get("longShortRatio", 1.0))
        except Exception as e:
            logger.warning("Binance long/short ratio fetch error: %s", e)
        return None

    def _sign_params(self, params: Dict[str, Any]) -> str:
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return f"{query_string}&signature={signature}"

    def place_limit_order_with_sl_tp(
        self,
        base_asset: str,
        action: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        margin_usdt: float,
        leverage: int = 3,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if not self.api_key or not self.api_secret:
            return False, "Binance API Key/Secret not configured", {}

        symbol = self.format_symbol(base_asset)
        is_long = "LONG" in action.upper() or "BUY" in action.upper()
        side = "BUY" if is_long else "SELL"
        close_side = "SELL" if is_long else "BUY"

        notional = margin_usdt * leverage
        raw_qty = notional / max(1e-6, entry_price)
        qty_precision = 3 if "BTC" in symbol or "ETH" in symbol else 1
        qty = round(raw_qty, qty_precision)

        entry_params = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": qty,
            "price": str(entry_price),
            "timestamp": int(time.time() * 1000),
        }
        signed_query = self._sign_params(entry_params)
        url = f"{self.fapi_host}/fapi/v1/order?{signed_query}"
        req = urllib.request.Request(url, headers={**STANDARD_HEADERS, "X-MBX-APIKEY": self.api_key}, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                d = json.loads(resp.read().decode("utf-8"))
                entry_order_id = str(d.get("orderId"))
        except Exception as e:
            return False, f"Binance Entry Order Failed: {e}", {}

        # 2. Attach Stop Loss via STOP_MARKET
        sl_order_id = None
        try:
            sl_params = {
                "symbol": symbol,
                "side": close_side,
                "type": "STOP_MARKET",
                "stopPrice": str(stop_loss),
                "closePosition": "true",
                "timestamp": int(time.time() * 1000),
            }
            sl_query = self._sign_params(sl_params)
            req_sl = urllib.request.Request(f"{self.fapi_host}/fapi/v1/order?{sl_query}", headers={**STANDARD_HEADERS, "X-MBX-APIKEY": self.api_key}, method="POST")
            with urllib.request.urlopen(req_sl, timeout=8.0) as r_sl:
                d_sl = json.loads(r_sl.read().decode("utf-8"))
                sl_order_id = str(d_sl.get("orderId"))
        except Exception as exc:
            logger.warning("Binance SL order setup warning: %s", exc)

        # 3. Attach Take Profit via TAKE_PROFIT_MARKET
        tp_order_id = None
        try:
            tp_params = {
                "symbol": symbol,
                "side": close_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": str(take_profit),
                "closePosition": "true",
                "timestamp": int(time.time() * 1000),
            }
            tp_query = self._sign_params(tp_params)
            req_tp = urllib.request.Request(f"{self.fapi_host}/fapi/v1/order?{tp_query}", headers={**STANDARD_HEADERS, "X-MBX-APIKEY": self.api_key}, method="POST")
            with urllib.request.urlopen(req_tp, timeout=8.0) as r_tp:
                d_tp = json.loads(r_tp.read().decode("utf-8"))
                tp_order_id = str(d_tp.get("orderId"))
        except Exception as exc:
            logger.warning("Binance TP order setup warning: %s", exc)

        return True, entry_order_id, {
            "symbol": symbol,
            "entry_order_id": entry_order_id,
            "sl_order_id": sl_order_id,
            "tp_order_id": tp_order_id,
            "quantity": qty,
            "testnet": self.testnet,
        }

    def cancel_order(self, base_asset: str, order_id: str) -> bool:
        if not self.api_key or not self.api_secret:
            return False
        symbol = self.format_symbol(base_asset)
        params = {"symbol": symbol, "orderId": order_id, "timestamp": int(time.time() * 1000)}
        signed_query = self._sign_params(params)
        url = f"{self.fapi_host}/fapi/v1/order?{signed_query}"
        req = urllib.request.Request(url, headers={**STANDARD_HEADERS, "X-MBX-APIKEY": self.api_key}, method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                d = json.loads(resp.read().decode("utf-8"))
                return bool(d.get("status") in ("CANCELED", "CANCELLED") or d.get("orderId"))
        except Exception:
            return False


class GateAdapter(BaseExchangeAdapter):
    """Gate.io Futures V4 API Adapter."""

    @property
    def exchange_id(self) -> str:
        return "gate"

    @property
    def gate_host(self) -> str:
        # Gate.io Testnet Futures Host: https://fx-api-testnet.gateio.ws
        return "https://fx-api-testnet.gateio.ws" if self.testnet else "https://api.gateio.ws"

    def normalize_symbol(self, raw_symbol: str) -> str:
        clean = raw_symbol.upper()
        if "_" in clean:
            return clean.split("_")[0]
        return clean

    def format_symbol(self, base_asset: str) -> str:
        asset = base_asset.upper().replace("-USDT-SWAP", "").replace("USDT", "")
        return f"{asset}_USDT"

    def fetch_ticker(self, base_asset: str, timeout: float = 5.0) -> Dict[str, Any]:
        contract = self.format_symbol(base_asset)
        url = f"{self.gate_host}/api/v4/futures/usdt/tickers?contract={contract}"
        data = http_get_json(url, timeout=timeout)
        row = data[0] if isinstance(data, list) and data else {}
        return {
            "exchange": "gate",
            "symbol": base_asset.upper(),
            "inst_id": contract,
            "last": float(row.get("last", 0) or 0),
            "high24h": float(row.get("high_24h", 0) or 0),
            "low24h": float(row.get("low_24h", 0) or 0),
            "vol24h": float(row.get("volume_24h", 0) or 0),
            "funding_rate": float(row.get("funding_rate", 0) or 0),
            "timestamp": int(time.time() * 1000),
        }

    def fetch_klines(self, base_asset: str, interval: str = "1h", limit: int = 100, timeout: float = 6.0) -> List[List[float]]:
        contract = self.format_symbol(base_asset)
        url = f"{self.gate_host}/api/v4/futures/usdt/candlesticks?contract={contract}&interval={interval.lower()}&limit={limit}"
        raw_candles = http_get_json(url, timeout=timeout)
        standard_klines: List[List[float]] = []
        for c in raw_candles:
            standard_klines.append([
                float(c.get("t", 0)) * 1000,
                float(c.get("o", 0)),
                float(c.get("h", 0)),
                float(c.get("l", 0)),
                float(c.get("c", 0)),
                float(c.get("v", 0)),
            ])
        return standard_klines

    def fetch_orderbook(self, base_asset: str, limit: int = 20, timeout: float = 5.0) -> Dict[str, List[List[float]]]:
        contract = self.format_symbol(base_asset)
        url = f"{self.gate_host}/api/v4/futures/usdt/order_book?contract={contract}&limit={limit}"
        book = http_get_json(url, timeout=timeout)
        bids = [[float(b.get("p", 0)), float(b.get("s", 0))] for b in book.get("bids", [])]
        asks = [[float(a.get("p", 0)), float(a.get("s", 0))] for a in book.get("asks", [])]
        return {"bids": bids, "asks": asks}

    def _sign_gate(self, method: str, path: str, query: str = "", body: str = "") -> Dict[str, str]:
        t = str(int(time.time()))
        body_hash = hashlib.sha512(body.encode("utf-8")).hexdigest()
        sign_string = f"{method}\n{path}\n{query}\n{body_hash}\n{t}"
        sign = hmac.new(self.api_secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha512).hexdigest()
        return {
            "KEY": self.api_key,
            "Timestamp": t,
            "SIGN": sign,
            "Content-Type": "application/json",
            **STANDARD_HEADERS
        }

    def place_limit_order_with_sl_tp(
        self,
        base_asset: str,
        action: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        margin_usdt: float,
        leverage: int = 3,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if not self.api_key or not self.api_secret:
            return False, "Gate API Key/Secret not configured", {}

        contract = self.format_symbol(base_asset)
        is_long = "LONG" in action.upper() or "BUY" in action.upper()
        notional = margin_usdt * leverage
        ct_val = 0.0001 if "BTC" in contract else (0.01 if "ETH" in contract else 1.0)
        size = max(1, int(notional / max(1e-6, entry_price * ct_val)))
        if not is_long:
            size = -size

        path = "/api/v4/futures/usdt/orders"
        body_dict = {
            "contract": contract,
            "size": size,
            "price": str(entry_price),
            "tif": "gtc",
        }
        body_str = json.dumps(body_dict)
        headers = self._sign_gate("POST", path, body=body_str)
        req = urllib.request.Request(f"{self.gate_host}{path}", data=body_str.encode("utf-8"), headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                d = json.loads(resp.read().decode("utf-8"))
                order_id = str(d.get("id"))
                return True, order_id, {"contract": contract, "size": size, "id": order_id, "testnet": self.testnet}
        except Exception as e:
            return False, f"Gate Order Failed: {e}", {}

    def cancel_order(self, base_asset: str, order_id: str) -> bool:
        if not self.api_key or not self.api_secret:
            return False
        path = f"/api/v4/futures/usdt/orders/{order_id}"
        headers = self._sign_gate("DELETE", path)
        req = urllib.request.Request(f"{self.gate_host}{path}", headers=headers, method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                d = json.loads(resp.read().decode("utf-8"))
                return bool(d.get("status") in ("finished", "cancelled") or d.get("id"))
        except Exception:
            return False


# Factory Registry
EXCHANGE_REGISTRY: Dict[str, type[BaseExchangeAdapter]] = {
    "okx": OKXAdapter,
    "binance": BinanceAdapter,
    "gate": GateAdapter,
}


def get_exchange_adapter(exchange_id: str, **kwargs) -> BaseExchangeAdapter:
    """Returns an initialized exchange adapter instance."""
    cls = EXCHANGE_REGISTRY.get(exchange_id.lower())
    if not cls:
        raise ValueError(f"Unsupported exchange: {exchange_id}. Supported: {list(EXCHANGE_REGISTRY.keys())}")
    return cls(**kwargs)

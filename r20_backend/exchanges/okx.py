"""OKX V5 公共行情与私有交易适配器（三所平权架构）。

定位：多所聚合矩阵的统一入口，统一实现行情、持仓、挂单、保护单与快速平仓。
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
        supports_attached_tp_sl=True,      # attachAlgoOrds 附带 TP/SL
        trigger_price_default="last",
        max_candle_limit=300,
        bar_case="upper",                  # 15m/1H/4H 混合大小写
        has_top_trader_ratio=True,
        has_taker_ratio=True,
        supports_account=True,
        supports_orders=True,
        mainland_ip_restricted=False,
        rate_limit_note="公共行情约 20req/2s，429 常见需退避",
        order_id_type="string",
        native_amend=False,
        decimal_amount=False,
        position_modes=("net", "long_short"),
        # 第一百九十三刀：本仓的下单/保护腿载荷是在**双向（long_short）**账户上核验的
        # （显式 posSide、平仓按腿方向）；净持仓（net）模式未核验 ⇒ 不列为准入模式。
        # 不声明 = 空元组 = 闸不生效（旧状），故这里必须显式声明。
        entry_ready_position_modes=("long_short",),
        conditional_family="attached",
        protection_semantics="attachAlgoOrds **非受理即原子保护**：官方 attachAlgoClOrdId 说明"
                             "普通订单完全成交后才提交附带算法单，回执字段含 failCode/failReason"
                             "——HTTP 200≠受保护，必须回读 pending algo 核验账户/合约/方向/数量/"
                             "触发值（核验 helper：okx_trade_service.verify_attached_protection）。"
                             "另 2026-08-20 起 post_only/mmp_and_post_only 失败可只收 canceled 直达"
                             "终态不先 live（Demo 2026-08-10 生效）——状态机须接受直接终态，"
                             "不无限等待 live；此变更不针对普通 limit/market/ioc/fok",
    )

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
            max_leverage=0.0,
            status="trading" if str(raw.get("state", "trading")) == "trading" else "closed",
            raw=raw,
        )


def interpret_position_mode(account_payload: Any) -> str:
    """OKX `/api/v5/account/config` 回包 → 本仓词表（`long_short` / `net`）；读不出 ⇒ `unknown`。

    真机字段：`data[0].posMode` ∈ {`long_short_mode`, `net_mode`}。
    ⚠️ 读不出**绝不**给默认值：`execution_router` 的持仓模式闸对 `unknown` 的处置是
    **禁新开仓**（fail-closed）——"读不到"必须与"干净"区分开。
    """
    rows = account_payload
    if isinstance(rows, dict):
        rows = rows.get("data")
    if not isinstance(rows, (list, tuple)) or not rows:
        return "unknown"
    first = rows[0]
    if not isinstance(first, dict):
        return "unknown"
    mode = str(first.get("posMode") or "").strip().lower()
    return {"long_short_mode": "long_short", "net_mode": "net"}.get(mode, "unknown")


class OKXAdapter(OKXPublicAdapter):
    """Full-featured OKX V5 adapter supporting both public market data and private trading."""

    def __init__(self, api_key: str = "", secret_key: str = "", passphrase: str = "",
                 environment: str = "demo", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = api_key or ""
        self.secret_key = secret_key or ""
        self.passphrase = passphrase or ""
        self.environment = environment or "demo"

    def _get_okx_env(self):
        from scripts.okx_runtime import OKXEnvironment, current_environment
        if self.api_key and self.secret_key and self.passphrase:
            # 审计 D4：OKXEnvironment 是 frozen dataclass——simulated/configured
            # 是派生 property，旧写法当构造参数传=一调用即 TypeError（接线即炸）。
            return OKXEnvironment(
                mode=str(self.environment).strip().lower(),
                api_key=self.api_key,
                secret_key=self.secret_key,
                passphrase=self.passphrase,
            )
        return current_environment()

    def detect_position_mode(self) -> str:
        """**只读**探测账户持仓模式（`long_short` / `net`）；读不到 ⇒ `"unknown"`。

        端点 `GET /api/v5/account/config`（真机回包 `data[0].posMode`）。永不抛异常、永不改账户
        ——本系统**绝不**调用 `POST /api/v5/account/set-position-mode` 自动切换用户账户模式。

        第一百九十三刀补此洞：此前 OKX 是全仓**唯一没有该探测**的所，而
        `execution_router` 的模式闸写成"声明了模式**且**有探测方法才体检" ⇒ 对 OKX
        **整段跳过**——偏偏 OKX 是持仓最多的那个所。现在与 Gate/Binance 同尺。
        """
        try:
            from scripts import okx_rest
            return interpret_position_mode(
                okx_rest.request("GET", "/api/v5/account/config", env=self._get_okx_env()))
        except Exception:
            return "unknown"

    def positions(self) -> List[Dict[str, Any]]:
        from scripts import okx_rest
        env = self._get_okx_env()
        raw_positions = okx_rest.positions(env=env)
        out = []
        for p in (raw_positions or []):
            amt = float(p.get("pos", 0) or 0)
            if abs(amt) < 1e-12:
                continue
            inst_id = p.get("instId", "")
            base = canonical_base(inst_id)
            # 审计②#2(2026-09-13)：净持仓模式 posSide="net" 旧实现直接透传 →
            # side 越出 {long,short} 词表且 size_signed=-abs(amt) 把多头显示为负
            # （全仓消费方以 size_signed 符号定方向 → 接线即整所反读）。
            # 规则：仅双向持仓信 posSide；net/缺失一律用 pos 有符号数派生。
            raw_side = str(p.get("posSide") or "").strip().lower()
            if raw_side in ("long", "short"):
                side = raw_side
            else:
                side = "long" if amt > 0 else "short"
            upl = float(p.get("upl", 0) or 0)
            out.append({
                "venue": "okx",
                "exchange": "okx",
                "symbol": inst_id,
                "instId": inst_id,
                "base": base,
                "side": side,
                "posSide": side,
                "amount": abs(amt),
                "size_signed": amt if side == "long" else -abs(amt),
                "entry_price": float(p.get("avgPx", 0) or 0),
                "mark_price": float(p.get("markPx", 0) or 0),
                "unrealized_pnl": upl,
                "upl": upl,
                "leverage": float(p.get("lever", 1) or 1),
                "margin_mode": p.get("mgnMode", "cross"),
                "raw": p,
            })
        return out

    def open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        from scripts import okx_rest
        env = self._get_okx_env()
        inst = self.native_symbol(symbol) if symbol else None
        # 审计 D4：真实 API 名是 pending_orders（orders_pending 不存在，AttributeError）
        raw_orders = okx_rest.pending_orders(inst_id=inst, env=env)
        out = []
        for o in (raw_orders or []):
            inst_id = o.get("instId", "")
            out.append({
                "venue": "okx",
                "order_id": str(o.get("ordId", "")),
                "client_order_id": str(o.get("clOrdId", "")),
                "symbol": inst_id,
                "instId": inst_id,
                "side": str(o.get("side", "")).lower(),
                "pos_side": str(o.get("posSide", "")).lower(),
                "price": float(o.get("px", 0) or 0),
                "contracts": float(o.get("sz", 0) or 0),
                "order_type": str(o.get("ordType", "")).lower(),
                "state": str(o.get("state", "")).lower(),
                "created_time_ms": int(o.get("cTime", 0) or 0),
                "raw": o,
            })
        return out

    def place_order(self, symbol: str, side: str, contracts: float, price: Optional[float] = None,
                    order_type: str = "limit", pos_side: Optional[str] = None,
                    client_order_id: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        from scripts import okx_rest
        env = self._get_okx_env()
        inst_id = self.native_symbol(symbol)
        # 审计②#3(2026-09-13)双缺陷修复：
        # ①旧传 sz= —— okx_rest.place_order 形参名是 size，此前每次调用即 TypeError、
        #   网络零发起（并被关闸掩护，与 orders_pending 同族）；
        # ②旧默认 buy→long/sell→short —— 净持仓模式下恒发 posSide=long/short 会被
        #   OKX 拒（51006 族）；双向持仓下「sell 平多」被默认成 posSide=short，等于
        #   反向开新空仓。现规则：显式 pos_side 原样透传；未显式时不传 posSide（净
        #   模式默认形态）——双向持仓的调用方必须显式传，绝不猜。
        if pos_side:
            pos_s = str(pos_side).strip().lower()
            if pos_s not in ("long", "short", "net"):
                raise ValueError(f"pos_side 仅允许 long/short/net，收到 {pos_side!r}")
            kwargs["pos_side"] = None if pos_s == "net" else pos_s
        res = okx_rest.place_order(
            inst_id=inst_id,
            side=side.lower(),
            size=str(contracts),
            ord_type=order_type.lower(),
            px=str(price) if price else None,
            cl_ord_id=client_order_id,
            env=env,
            **kwargs,
        )
        return {"venue": "okx", "symbol": inst_id, "result": res}

    def set_leverage(self, symbol: str, leverage: float, margin_mode: str = "cross",
                     pos_side: Optional[str] = None) -> Any:
        """审计④5(2026-09-13)：补齐三所契约对称（Binance/Gate 早有）。OKX 杠杆是
        账户级、按 instId+mgnMode（双向另分 posSide）的持久档位——AI 裁决的杠杆
        必须落档，否则保证金/强平价按账户旧档算。net 模式省略 posSide（该 instId
        全模式生效，官方语义）。"""
        from scripts import okx_rest
        env = self._get_okx_env()
        inst_id = self.native_symbol(symbol)
        return okx_rest.set_leverage(inst_id, int(leverage), mgn_mode=margin_mode,
                                     pos_side=pos_side, env=env)

    def cancel_order(self, symbol: str, order_id: Optional[str] = None,
                     client_order_id: Optional[str] = None) -> Dict[str, Any]:
        from scripts import okx_rest
        env = self._get_okx_env()
        inst_id = self.native_symbol(symbol)
        res = okx_rest.cancel_order(inst_id=inst_id, ord_id=order_id or "", cl_ord_id=client_order_id, env=env)
        return {"venue": "okx", "symbol": inst_id, "result": res}

    def list_protective_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        from scripts import okx_rest
        env = self._get_okx_env()
        inst = self.native_symbol(symbol) if symbol else None
        # 审计 D4：真实 API 名是 pending_algo_orders（list_algo_orders 不存在）
        return okx_rest.pending_algo_orders(inst_id=inst, env=env)

    def fast_close_position(self, symbol: str) -> Dict[str, Any]:
        from scripts import okx_rest
        env = self._get_okx_env()
        inst_id = self.native_symbol(symbol)
        positions = self.positions()
        target = next((p for p in positions if p["instId"] == inst_id), None)
        if not target:
            raise ValueError(f"未找到 {inst_id} 的活动持仓")
        res = okx_rest.close_position(inst_id=inst_id, pos_side=target["posSide"], env=env)
        return {"venue": "okx", "symbol": inst_id, "result": res}

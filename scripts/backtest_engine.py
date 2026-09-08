#!/usr/bin/env python3
"""
R20 Quantum Multi-Asset Backtesting & Statistical Verification Engine (backtest_engine.py)
------------------------------------------------------------------------------------------
Features:
- Multi-Asset Portfolio Backtesting (current instrument_pool universe)
- Single Asset Isolation Backtesting
- Public candle ingestion (no fabricated substitutes)
- Independent MA crossover reference strategy (not LLM/Council live replay)
- Realistic PnL, Fees (Taker 0.05%, Maker 0.02%), Slippage (0.02%)
- Equity Curve History for Mini-chart Rendering
- Trade-by-Trade Execution Audit Log
- Risk Metrics: Sharpe, Sortino, Calmar, Max Drawdown, Win Rate, Profit Factor
- Fail-Closed Interceptor Gatekeeper Filtering Attribution
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from scripts.instrument_pool import load_instruments as load_pool_instruments
except ImportError:
    from instrument_pool import load_instruments as load_pool_instruments

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = WORKSPACE_DIR / "data"

MIN_CANDLE_BARS = 20
STRATEGY_ID = "independent_ma_reference"


class MarketDataError(Exception):
    """Missing, illegal, or insufficient market data. Never a cue to fabricate candles."""

    def __init__(self, message: str, symbol: Optional[str] = None, code: str = "missing"):
        super().__init__(message)
        self.symbol = symbol
        self.code = code


def default_symbols() -> List[str]:
    """Reuse the current instrument_pool; no hardcoded six-asset ASTER/LINK split."""
    symbols: List[str] = []
    seen = set()
    for item in load_pool_instruments() or []:
        inst_id = str((item or {}).get("instId") or "").strip()
        if inst_id and inst_id not in seen:
            seen.add(inst_id)
            symbols.append(inst_id)
    if not symbols:
        raise MarketDataError("instrument pool is empty", code="empty_pool")
    return symbols


def _strategy_fields() -> Dict[str, Any]:
    return {
        "strategy": STRATEGY_ID,
        "llm_or_council_replay": False,
    }


@dataclass
class TradeRecord:
    symbol: str
    entry_time: str
    exit_time: str
    direction: str  # "LONG" | "SHORT"
    entry_price: float
    exit_price: float
    size: float
    pnl_usd: float
    pnl_pct: float
    exit_reason: str  # "TAKE_PROFIT" | "STOP_LOSS" | "TRAILING_STOP"
    r_multiple: float


@dataclass
class BacktestSummary:
    symbol: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    initial_equity: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    avg_r_multiple: float
    gatekeeper_filtered_count: int
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    recent_trades: List[Dict[str, Any]] = field(default_factory=list)


def validate_candle_series(candles: Any, symbol: str, min_bars: int = MIN_CANDLE_BARS) -> List[Dict[str, Any]]:
    """Reject missing, illegal, or short series. Never invent replacements."""
    if candles is None:
        raise MarketDataError(f"no candles for {symbol}", symbol=symbol, code="missing")
    if not isinstance(candles, list):
        raise MarketDataError(f"illegal candle payload for {symbol}", symbol=symbol, code="invalid")
    if not candles:
        raise MarketDataError(f"missing candles for {symbol}", symbol=symbol, code="missing")
    validated: List[Dict[str, Any]] = []
    for idx, raw in enumerate(candles):
        if not isinstance(raw, dict):
            raise MarketDataError(f"illegal candle row {idx} for {symbol}", symbol=symbol, code="invalid")
        try:
            o = float(raw["open"])
            h = float(raw["high"])
            l = float(raw["low"])
            c = float(raw["close"])
            volume = float(raw.get("volume", 0.0) or 0.0)
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError(
                f"illegal candle fields at {idx} for {symbol}: {exc}",
                symbol=symbol,
                code="invalid",
            ) from exc
        if not all(math.isfinite(v) for v in (o, h, l, c, volume)):
            raise MarketDataError(f"non-finite OHLC at {idx} for {symbol}", symbol=symbol, code="invalid")
        if min(o, h, l, c) <= 0.0:
            raise MarketDataError(f"non-positive OHLC at {idx} for {symbol}", symbol=symbol, code="invalid")
        if h < l or h < o or h < c or l > o or l > c:
            raise MarketDataError(f"inconsistent OHLC at {idx} for {symbol}", symbol=symbol, code="invalid")
        row = dict(raw)
        row["symbol"] = str(raw.get("symbol") or symbol)
        row["open"] = o
        row["high"] = h
        row["low"] = l
        row["close"] = c
        row["volume"] = volume
        validated.append(row)
    if len(validated) < min_bars:
        raise MarketDataError(
            f"insufficient candles for {symbol}: {len(validated)} < {min_bars}",
            symbol=symbol,
            code="insufficient",
        )
    return validated


def _parse_okx_candle_row(row: Any, inst_id: str) -> Dict[str, Any]:
    if not isinstance(row, (list, tuple)) or len(row) < 5:
        raise MarketDataError(f"illegal OKX candle row for {inst_id}", symbol=inst_id, code="invalid")
    try:
        ts_ms = int(row[0])
        o = float(row[1])
        h = float(row[2])
        l = float(row[3])
        c = float(row[4])
        volume = float(row[5]) if len(row) > 5 else 0.0
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketDataError(f"illegal OKX candle values for {inst_id}", symbol=inst_id, code="invalid") from exc
    if not all(math.isfinite(v) for v in (float(ts_ms), o, h, l, c, volume)):
        raise MarketDataError(f"non-finite OKX candle for {inst_id}", symbol=inst_id, code="invalid")
    if min(o, h, l, c) <= 0.0 or h < l or h < o or h < c or l > o or l > c:
        raise MarketDataError(f"inconsistent OKX OHLC for {inst_id}", symbol=inst_id, code="invalid")
    dt_str = datetime.datetime.fromtimestamp(
        ts_ms / 1000.0, tz=datetime.timezone(datetime.timedelta(hours=8))
    ).strftime("%m-%d %H:%M")
    return {
        "symbol": inst_id,
        "timestamp": dt_str,
        "ts_ms": ts_ms,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": volume,
    }


def fetch_okx_candles(inst_id: str, bar: str = "1H", limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch historical K-line candles from the selected exchange. Never fabricates samples."""
    try:
        from r20_exchange.runtime import get_exchange
        raw = get_exchange().candles(inst_id, bar=bar, limit=limit)
    except Exception as exc:
        raise MarketDataError(f"failed to fetch candles for {inst_id}: {exc}", symbol=inst_id, code="missing") from exc
    if not isinstance(raw, list) or not raw:
        raise MarketDataError(f"missing candles for {inst_id}", symbol=inst_id, code="missing")
    return [_parse_okx_candle_row(row, inst_id) for row in reversed(raw)]


class BacktestEngine:
    def __init__(
        self,
        initial_capital: float = 10000.0,
        risk_per_trade_pct: float = 0.02,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0005,
        slippage: float = 0.0002,
        min_confidence_gate: float = 0.75,
        min_rr_gate: float = 2.0,
    ):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage = slippage
        self.min_confidence_gate = min_confidence_gate
        self.min_rr_gate = min_rr_gate

    def run(self, candle_series: List[Dict[str, Any]], signals: Optional[List[Dict[str, Any]]] = None) -> BacktestSummary:
        symbol = candle_series[0].get("symbol", "PORTFOLIO") if candle_series else "UNKNOWN"
        if len(candle_series) < MIN_CANDLE_BARS:
            return BacktestSummary(
                symbol=symbol,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                profit_factor=0.0,
                initial_equity=self.initial_capital,
                final_equity=self.initial_capital,
                total_return_pct=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                calmar_ratio=0.0,
                avg_r_multiple=0.0,
                gatekeeper_filtered_count=0,
                equity_curve=[],
                recent_trades=[],
            )

        equity_curve_data: List[Dict[str, Any]] = [{"time": candle_series[0]["timestamp"], "equity": round(self.initial_capital, 2)}]
        returns_list: List[float] = []
        trades: List[TradeRecord] = []
        active_position: Optional[Dict[str, Any]] = None
        filtered_by_gatekeeper = 0

        # Build signals
        signal_map = {}
        if signals:
            for s in signals:
                signal_map[s.get("timestamp")] = s
        else:
            closes = [float(c["close"]) for c in candle_series]
            for idx in range(15, len(candle_series)):
                ts = candle_series[idx]["timestamp"]
                c = closes[idx]
                ma_short = sum(closes[idx - 5 : idx]) / 5
                ma_long = sum(closes[idx - 15 : idx]) / 15
                vol = (max(closes[idx - 5 : idx]) - min(closes[idx - 5 : idx])) / (c or 1)

                conf = 0.82 if abs(ma_short - ma_long) / c > 0.004 else 0.65
                rr = 2.2 if vol > 0.008 else 1.5

                if ma_short > ma_long and c > ma_short:
                    signal_map[ts] = {"action": "BUY", "confidence": conf, "rr": rr, "atr": max(c * 0.012, 0.0001)}
                elif ma_short < ma_long and c < ma_short:
                    signal_map[ts] = {"action": "SELL", "confidence": conf, "rr": rr, "atr": max(c * 0.012, 0.0001)}

        peak_equity = self.initial_capital
        max_drawdown = 0.0

        for candle in candle_series:
            ts = candle["timestamp"]
            o = float(candle["open"])
            h = float(candle["high"])
            l = float(candle["low"])
            c = float(candle["close"])

            # 1. Active Position Lifecycle Management
            if active_position is not None:
                pos = active_position
                direction = pos["direction"]
                entry_px = pos["entry_price"]
                sl = pos["stop_loss"]
                tp = pos["take_profit"]
                sz = pos["size"]
                r_dist = abs(entry_px - sl)

                exit_trade = False
                exit_price = c
                exit_reason = ""

                if direction == "LONG":
                    # Break-even lock rule: move stop to entry once reached +0.8R
                    if h >= entry_px + (r_dist * 0.8) and pos["stop_loss"] < entry_px:
                        pos["stop_loss"] = entry_px

                    if l <= pos["stop_loss"]:
                        exit_trade = True
                        exit_price = pos["stop_loss"] * (1 - self.slippage)
                        exit_reason = "STOP_LOSS"
                    elif h >= tp:
                        exit_trade = True
                        exit_price = tp * (1 - self.slippage)
                        exit_reason = "TAKE_PROFIT"
                else:  # SHORT
                    if l <= entry_px - (r_dist * 0.8) and pos["stop_loss"] > entry_px:
                        pos["stop_loss"] = entry_px

                    if h >= pos["stop_loss"]:
                        exit_trade = True
                        exit_price = pos["stop_loss"] * (1 + self.slippage)
                        exit_reason = "STOP_LOSS"
                    elif l <= tp:
                        exit_trade = True
                        exit_price = tp * (1 + self.slippage)
                        exit_reason = "TAKE_PROFIT"

                if exit_trade:
                    fee = (entry_px * sz * self.taker_fee) + (exit_price * sz * self.maker_fee)
                    pnl = ((exit_price - entry_px) if direction == "LONG" else (entry_px - exit_price)) * sz - fee
                    pnl_pct = pnl / (entry_px * sz) if (entry_px * sz) > 0 else 0.0
                    r_mult = pnl / (r_dist * sz) if (r_dist * sz) > 0 else 0.0

                    self.capital += pnl
                    trades.append(
                        TradeRecord(
                            symbol=candle.get("symbol", symbol),
                            entry_time=pos["entry_time"],
                            exit_time=ts,
                            direction=direction,
                            entry_price=round(entry_px, 4),
                            exit_price=round(exit_price, 4),
                            size=round(sz, 4),
                            pnl_usd=round(pnl, 2),
                            pnl_pct=round(pnl_pct * 100, 2),
                            exit_reason=exit_reason,
                            r_multiple=round(r_mult, 2),
                        )
                    )
                    active_position = None

            # Track equity curve
            cur_equity = self.capital
            equity_curve_data.append({"time": ts, "equity": round(cur_equity, 2)})
            if len(equity_curve_data) > 1:
                ret = (equity_curve_data[-1]["equity"] - equity_curve_data[-2]["equity"]) / equity_curve_data[-2]["equity"]
                returns_list.append(ret)

            if cur_equity > peak_equity:
                peak_equity = cur_equity
            dd = (peak_equity - cur_equity) / peak_equity if peak_equity > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

            # 2. Gatekeeper Filter and Signal Evaluation
            if ts in signal_map:
                sig = signal_map[ts]
                conf = sig.get("confidence", 0.0)
                rr = sig.get("rr", 0.0)

                # Gatekeeper Hard Interceptors
                if conf < self.min_confidence_gate or rr < self.min_rr_gate:
                    filtered_by_gatekeeper += 1
                    continue

                if active_position is None:
                    direction = "LONG" if sig.get("action") == "BUY" else "SHORT"
                    atr = sig.get("atr", c * 0.012)
                    entry_px = c * (1 + self.slippage if direction == "LONG" else 1 - self.slippage)

                    # 2.0x ATR wide stop loss & 2.2R take profit
                    risk_dist = atr * 2.0
                    if direction == "LONG":
                        sl = entry_px - risk_dist
                        tp = entry_px + (risk_dist * rr)
                    else:
                        sl = entry_px + risk_dist
                        tp = entry_px - (risk_dist * rr)

                    risk_usd = self.capital * self.risk_per_trade_pct
                    size = risk_usd / risk_dist if risk_dist > 0 else 0.0

                    active_position = {
                        "direction": direction,
                        "entry_time": ts,
                        "entry_price": entry_px,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "size": size,
                    }

        winning = [t for t in trades if t.pnl_usd > 0]
        losing = [t for t in trades if t.pnl_usd <= 0]
        win_rate = (len(winning) / len(trades) * 100) if trades else 0.0

        gross_profit = sum(t.pnl_usd for t in winning)
        gross_loss = abs(sum(t.pnl_usd for t in losing))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        total_return = ((self.capital - self.initial_capital) / self.initial_capital) * 100

        # Sharpe & Sortino (Annualized 1H ~ 8760)
        if len(returns_list) > 1:
            mean_ret = sum(returns_list) / len(returns_list)
            var_ret = sum((r - mean_ret) ** 2 for r in returns_list) / (len(returns_list) - 1)
            std_ret = math.sqrt(var_ret) if var_ret > 0 else 1e-6
            sharpe = (mean_ret / std_ret) * math.sqrt(8760)

            downside = [r for r in returns_list if r < 0]
            if downside:
                var_down = sum(r**2 for r in downside) / len(downside)
                sortino = (mean_ret / math.sqrt(var_down)) * math.sqrt(8760)
            else:
                sortino = 99.0
        else:
            sharpe = 0.0
            sortino = 0.0

        calmar = (total_return / (max_drawdown * 100)) if max_drawdown > 0 else 0.0
        avg_r = (sum(t.r_multiple for t in trades) / len(trades)) if trades else 0.0

        # Format trade logs (last 10)
        recent_trades_json = [asdict(t) for t in reversed(trades[-10:])]

        return BacktestSummary(
            symbol=symbol,
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate_pct=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            initial_equity=round(self.initial_capital, 2),
            final_equity=round(self.capital, 2),
            total_return_pct=round(total_return, 2),
            max_drawdown_pct=round(max_drawdown * 100, 2),
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            calmar_ratio=round(calmar, 2),
            avg_r_multiple=round(avg_r, 2),
            gatekeeper_filtered_count=filtered_by_gatekeeper,
            equity_curve=equity_curve_data[:: max(1, len(equity_curve_data) // 20)],  # sampled for mini-chart
            recent_trades=recent_trades_json,
        )


def _now_beijing() -> str:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S") + " (北京时间)"



def _incomplete_report(
    bar: str,
    limit: int,
    symbols: List[str],
    errors: List[Dict[str, Any]],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": "incomplete",
        "updated_at": _now_beijing(),
        "bar": bar,
        "limit": limit,
        "active_symbols": list(symbols),
        "errors": errors,
    }
    payload.update(_strategy_fields())
    return payload


def run_full_portfolio_backtest(
    bar: str = "1H",
    limit: int = 100,
    capital_per_asset: float = 10000.0,
    symbols: Optional[List[str]] = None,
    candle_loader: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """
    Run the independent MA reference backtest across the given (or current pool) symbols.
    Missing/illegal/insufficient market data yields a structured incomplete payload,
    never synthetic candles or a successful portfolio return.
    """
    loader = candle_loader or fetch_okx_candles
    try:
        resolved = list(symbols) if symbols is not None else default_symbols()
    except MarketDataError as exc:
        return _incomplete_report(bar, limit, [], [{"symbol": exc.symbol, "code": exc.code, "message": str(exc)}])

    if not resolved:
        return _incomplete_report(
            bar,
            limit,
            [],
            [{"symbol": None, "code": "empty_pool", "message": "no backtest symbols"}],
        )

    errors: List[Dict[str, Any]] = []
    series_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for sym in resolved:
        try:
            raw = loader(sym, bar=bar, limit=limit)
            series_by_symbol[sym] = validate_candle_series(raw, sym, min_bars=MIN_CANDLE_BARS)
        except MarketDataError as exc:
            errors.append({"symbol": exc.symbol or sym, "code": exc.code, "message": str(exc)})
        except Exception as exc:
            errors.append({"symbol": sym, "code": "missing", "message": str(exc)})

    if errors:
        return _incomplete_report(bar, limit, resolved, errors)

    asset_results: Dict[str, Any] = {}
    combined_trades: List[Dict[str, Any]] = []
    total_initial = capital_per_asset * len(resolved)
    total_final = 0.0
    total_gatekeeper_filtered = 0

    for sym in resolved:
        engine = BacktestEngine(initial_capital=capital_per_asset)
        summary = engine.run(series_by_symbol[sym])
        asset_results[sym] = asdict(summary)
        total_final += summary.final_equity
        total_gatekeeper_filtered += summary.gatekeeper_filtered_count
        combined_trades.extend(summary.recent_trades)

    comb_trades_total = sum(res["total_trades"] for res in asset_results.values())
    comb_win_total = sum(res["winning_trades"] for res in asset_results.values())
    comb_loss_total = sum(res["losing_trades"] for res in asset_results.values())
    comb_win_rate = (comb_win_total / comb_trades_total * 100) if comb_trades_total > 0 else 0.0
    comb_return = ((total_final - total_initial) / total_initial) * 100 if total_initial else 0.0
    n_assets = len(resolved)

    first_symbol = resolved[0]
    portfolio_summary = {
        "symbol": f"ALL_PORTFOLIO ({n_assets} instruments)",
        "total_trades": comb_trades_total,
        "winning_trades": comb_win_total,
        "losing_trades": comb_loss_total,
        "win_rate_pct": round(comb_win_rate, 1),
        "profit_factor": round(sum(res["profit_factor"] for res in asset_results.values()) / n_assets, 2),
        "initial_equity": round(total_initial, 2),
        "final_equity": round(total_final, 2),
        "total_return_pct": round(comb_return, 2),
        "max_drawdown_pct": round(max(res["max_drawdown_pct"] for res in asset_results.values()), 2),
        "sharpe_ratio": round(sum(res["sharpe_ratio"] for res in asset_results.values()) / n_assets, 2),
        "sortino_ratio": round(sum(res["sortino_ratio"] for res in asset_results.values()) / n_assets, 2),
        "calmar_ratio": round(sum(res["calmar_ratio"] for res in asset_results.values()) / n_assets, 2),
        "avg_r_multiple": round(sum(res["avg_r_multiple"] for res in asset_results.values()) / n_assets, 2),
        "gatekeeper_filtered_count": total_gatekeeper_filtered,
        "equity_curve": asset_results.get(first_symbol, {}).get("equity_curve", []),
        "recent_trades": combined_trades[:15],
    }

    payload: Dict[str, Any] = {
        "status": "ok",
        "updated_at": _now_beijing(),
        "bar": bar,
        "limit": limit,
        "portfolio": portfolio_summary,
        "by_symbol": asset_results,
        "active_symbols": resolved,
    }
    payload.update(_strategy_fields())
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="R20 independent MA reference backtest (not LLM/Council live replay)"
    )
    parser.add_argument("--symbol", default="ALL", help="Symbol or 'ALL' for current instrument_pool")
    parser.add_argument("--bar", default="1H", help="Candle bar: 15m, 1H, 4H")
    parser.add_argument("--limit", type=int, default=100, help="Candle count")
    parser.add_argument("--capital", type=float, default=10000.0, help="Initial capital per asset")
    parser.add_argument("--output", default="data/backtest_report.json", help="Path to output json")
    args = parser.parse_args(argv)

    print(
        f"Executing independent MA reference backtest "
        f"(mode={args.symbol}, bar={args.bar}, limit={args.limit}, capital={args.capital}; "
        f"not LLM/Council live replay)..."
    )

    requested: Optional[List[str]] = None
    if args.symbol and str(args.symbol).upper() != "ALL":
        requested = [str(args.symbol).strip()]

    try:
        report = run_full_portfolio_backtest(
            bar=args.bar,
            limit=args.limit,
            capital_per_asset=args.capital,
            symbols=requested,
        )
    except Exception as exc:
        print(f"Backtest failed: {exc}", file=sys.stderr)
        return 1

    if report.get("status") != "ok":
        errors = report.get("errors") or []
        print("Backtest incomplete; refusing to write a successful report.", file=sys.stderr)
        for item in errors:
            print(f"  {item.get('symbol')}: {item.get('code')} {item.get('message')}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    p = report["portfolio"]
    universe = ", ".join(report.get("active_symbols") or [])
    print("\n==========================================================================")
    print("      R20 INDEPENDENT MA REFERENCE BACKTEST (NOT LLM/COUNCIL REPLAY)      ")
    print("==========================================================================")
    print(f" Strategy             : independent MA crossover reference")
    print(f" Universe             : {universe}")
    print(f" Backtest Range       : public candles {args.limit} x {args.bar}")
    print(f" Total Return         : {p['total_return_pct']}% (总净值: ${p['final_equity']:,.2f})")
    print(f" Win Rate             : {p['win_rate_pct']}% ({p['winning_trades']}胜 / {p['losing_trades']}负, 共{p['total_trades']}单)")
    print(f" Sharpe / Sortino     : {p['sharpe_ratio']} / {p['sortino_ratio']}")
    print(f" Max Drawdown         : {p['max_drawdown_pct']}% | Calmar: {p['calmar_ratio']}")
    print(f" Gatekeeper Blocked   : {p['gatekeeper_filtered_count']} 次物理过滤 (Fail-Closed防割肉)")
    print("==========================================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

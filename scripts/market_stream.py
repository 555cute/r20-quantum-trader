"""公共行情 WebSocket 流（**只读**；不参与任何交易决策/下单路径）。

## 定位（先读这段，避免误用）

本模块是 roadmap「从轮询迈向流式」的**基础层**，本刀只交付：

1. **帧解析**：把三所公共行情的原始帧归一成 tick；
2. **有界缓冲**：每个标的只留最近 N 条 tick（内存有界，绝不攒无界表）；
3. **健康账本**：帧数/tick 数/解析失败/连接错误/**陈旧度**；
4. **探测 CLI**：`python -m scripts.market_stream --probe`（只读、按需跑、不常驻）。

**它不**：不常驻、不接决策路径、不改任何下单行为。现有 REST 取数（
`market_data_service`）一字未动 —— 流式是**并存**的观测与未来取数面，不是替换。

## 三条被真实端点教出来的规则（本机实跑核对，2026-09-20）

1. **Gate 期货流要连专用域**：`wss://fx-ws.gateio.ws/v4/ws/usdt`。
   往现货域 `wss://api.gateio.ws/ws/v4/` 发 `futures.tickers` 会收到
   `error: Unknown channel futures.tickers` —— **连接成功、订阅被拒**，
   只看"连上了"会以为一切正常。
2. **Binance 用路径式订阅**：`wss://fstream.binance.com/ws/btcusdt@trade`。
   JSON `{"method":"SUBSCRIBE","params":["btcusdt@ticker"]}` 会回
   `{"result":null,"id":1}`（**订阅成功应答**）但**一条数据都不推**；
   `markPrice@1s`、`/stream?streams=` 同样静默。⇒ 用路径式，且必须把
   "已订阅但长时间无数据"当成**故障**（这正是陈旧度账本存在的理由）。
3. **OKX**：`wss://ws.okx.com:8443/ws/v5/public`，`{"op":"subscribe",...}` 正常，
   帧里 `data[].ts` 是**交易所毫秒时间戳**，与本地接收时间分开记
   （两者之差才是链路延迟，混在一起就永远看不出延迟）。

## 与既有可观测性的关系

健康账本按既有跨进程手法落盘（worker/探测进程写、后端 `/metrics` 读）：
`data/market_stream_health.json`（带 `schema_version`，版本不认识一律当"没有数据"）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

#: 快照格式版本（跨进程契约；加字段就升版本）
SCHEMA_VERSION = 1
#: 每标的保留的最近 tick 数（有界内存；探测进程短命，但常驻模式也一样有界）
_BUFFER_MAXLEN = 256

VENUE_ENDPOINTS: Dict[str, str] = {
    "okx": "wss://ws.okx.com:8443/ws/v5/public",
    # ⚠️ 期货专用域：现货域不接受 futures.tickers（实测 error code=2 "Unknown channel"）
    "gate": "wss://fx-ws.gateio.ws/v4/ws/usdt",
    "binance": "wss://fstream.binance.com/ws",
}
#: 路径式订阅场所（订阅写在 URL 里，不发 JSON 订阅帧）
_PATH_SUBSCRIBE = frozenset({"binance"})

__all__ = [
    "SCHEMA_VERSION",
    "VENUE_ENDPOINTS",
    "TickBuffer",
    "StreamHealth",
    "parse_frame",
    "stream_url",
    "venue_symbol",
    "subscribe_payload",
    "write_snapshot",
    "load_snapshot",
    "probe",
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def venue_symbol(venue: str, symbol: str) -> str:
    """把**规范写法**（OKX 形态，如 `BTC-USDT-SWAP`）翻成该所原生合约名。

    第一版直接 `symbol.replace("-", "_")`，实测两个后果**都很难看**：
    - Gate 收到 `BTC_USDT_SWAP` → 订阅被拒 `code=2 unknown currency pair`；
    - Binance 收到 `btcusdtswap` → 连接成功、订阅"成功"、**永远不推数据**
      （路径式订阅对不存在的符号不报错，就是静默）。
    ⇒ 合约名必须按所生成，且"静默零帧"要能被账本抓到（见 probe 的零帧留痕）。
    """
    core = str(symbol or "").upper().replace("/", "-").replace("_", "-")
    core = core.replace("-SWAP", "").replace("-PERP", "")
    parts = [p for p in core.split("-") if p]
    base = parts[0] if parts else "BTC"
    quote = parts[1] if len(parts) > 1 else "USDT"
    if venue == "okx":
        return f"{base}-{quote}-SWAP"
    if venue == "gate":
        return f"{base}_{quote}"
    if venue == "binance":
        return f"{base}{quote}"
    raise KeyError(f"未知场所：{venue}")


def _binance_stream_name(symbol: str) -> str:
    return f"{str(symbol).replace('-', '').replace('_', '').replace('/', '').lower()}@trade"


def stream_url(venue: str, symbol: str) -> str:
    """该所在**路径式**订阅下的连接地址（非路径式场所返回裸地址）。"""
    base = VENUE_ENDPOINTS[venue]
    if venue in _PATH_SUBSCRIBE:
        return f"{base}/{_binance_stream_name(symbol)}"
    return base


def subscribe_payload(venue: str, symbol: str) -> Optional[Dict[str, Any]]:
    """非路径式场所的订阅帧；路径式场所返回 None（不发订阅帧）。"""
    if venue in _PATH_SUBSCRIBE:
        return None
    if venue == "okx":
        return {"op": "subscribe", "args": [{"channel": "tickers", "instId": symbol}]}
    if venue == "gate":
        return {"time": int(time.time()), "channel": "futures.tickers",
                "event": "subscribe", "payload": [symbol]}
    raise KeyError(f"未知场所：{venue}")


def _tick(venue: str, symbol: str, price: Any, *, kind: str,
          exchange_ms: Any = None, now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    value = _num(price)
    if value is None or not symbol:
        return None
    local = int(now_ms if now_ms is not None else _now_ms())
    ts = _num(exchange_ms)
    return {
        "venue": venue,
        "symbol": str(symbol),
        "price": value,
        "kind": kind,
        "exchange_ms": None if ts is None else int(ts),
        "local_ms": local,
        # 链路延迟：**只在交易所给了时间戳时**才算，不给就是 None（不臆造 0）
        "latency_ms": None if ts is None else max(0, local - int(ts)),
    }


def parse_frame(venue: str, raw: Any, *, now_ms: Optional[int] = None) -> Dict[str, Any]:
    """把一帧原始数据归一成 `{"ticks": [...], "control": str|None, "error": str|None}`。

    **永不抛异常**：解析失败一律记成 `error`（第 137 刀的纪律：静默吞掉不可接受的
    是"没人知道"，而不是"函数不抛"）。
    """
    out: Dict[str, Any] = {"ticks": [], "control": None, "error": None}
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(payload, dict):
            return {"ticks": [], "control": None, "error": f"非对象帧：{type(payload).__name__}"}
    except Exception as exc:
        return {"ticks": [], "control": None, "error": f"{type(exc).__name__}: {exc}"}

    try:
        if venue == "okx":
            event = payload.get("event")
            if event == "error":
                return {"ticks": [], "control": None,
                        "error": f"okx error code={payload.get('code')} msg={payload.get('msg')}"}
            if event:
                out["control"] = f"okx {event}"
            arg = payload.get("arg") if isinstance(payload.get("arg"), dict) else {}
            for row in payload.get("data") or []:
                if not isinstance(row, dict):
                    continue
                tick = _tick("okx", row.get("instId") or arg.get("instId"), row.get("last"),
                             kind="ticker", exchange_ms=row.get("ts"), now_ms=now_ms)
                if tick is None:
                    out["error"] = "okx tickers 帧里没有可解析的 last"
                else:
                    tick["bid"] = _num(row.get("bidPx"))
                    tick["ask"] = _num(row.get("askPx"))
                    out["ticks"].append(tick)

        elif venue == "gate":
            event = payload.get("event")
            result = payload.get("result")
            if event == "subscribe":
                status = (result or {}).get("status") if isinstance(result, dict) else None
                if status == "fail" or payload.get("error"):
                    err = payload.get("error") or {}
                    return {"ticks": [], "control": None,
                            "error": f"gate 订阅被拒 code={err.get('code')} msg={err.get('message')}"}
                # 订阅应答**不是** tick：必须直接返回，不能落进下面的行解析。
                # 第一版就栽在这里：应答帧被当成 tickers 帧 ⇒ 每连一次多一条假解析错误
                # （本机实跑看到 "gate futures.tickers 帧里没有可解析的 last" 才发现）。
                return {"ticks": [], "control": f"gate subscribe {status}", "error": None}
            if event == "error" or payload.get("error"):
                err = payload.get("error") or {}
                return {"ticks": [], "control": None,
                        "error": f"gate error code={err.get('code')} msg={err.get('message')}"}
            out["control"] = f"gate {event}"
            rows = result if isinstance(result, list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                tick = _tick("gate", row.get("contract"), row.get("last"),
                             kind="ticker", exchange_ms=row.get("time_ms") or row.get("t"),
                             now_ms=now_ms)
                if tick is None:
                    out["error"] = "gate futures.tickers 帧里没有可解析的 last"
                else:
                    tick["mark_price"] = _num(row.get("mark_price"))
                    out["ticks"].append(tick)

        elif venue == "binance":
            if isinstance(payload.get("stream"), str) and isinstance(payload.get("data"), dict):
                return parse_frame("binance", payload["data"], now_ms=now_ms)   # 组合流解包
            if "result" in payload and "id" in payload:
                out["control"] = f"binance ack id={payload.get('id')}"
            if payload.get("code") is not None and payload.get("msg"):
                return {"ticks": [], "control": None,
                        "error": f"binance error code={payload.get('code')} msg={payload.get('msg')}"}
            event = payload.get("e")
            if event == "trade":
                tick = _tick("binance", payload.get("s"), payload.get("p"), kind="trade",
                             exchange_ms=payload.get("T") or payload.get("E"), now_ms=now_ms)
            elif event == "markPriceUpdate":
                tick = _tick("binance", payload.get("s"), payload.get("p"), kind="mark",
                             exchange_ms=payload.get("E"), now_ms=now_ms)
            elif event == "24hrTicker":
                tick = _tick("binance", payload.get("s"), payload.get("c"), kind="ticker",
                             exchange_ms=payload.get("E"), now_ms=now_ms)
            elif event:
                return {"ticks": [], "control": f"binance {event}", "error": None}
            else:
                tick = None
            if tick is not None:
                tick["qty"] = _num(payload.get("q"))
                out["ticks"].append(tick)

        else:
            return {"ticks": [], "control": None, "error": f"未知场所：{venue}"}
    except Exception as exc:          # 解析器自身异常也必须留痕，绝不上抛
        return {"ticks": [], "control": out.get("control"),
                "error": f"parse {type(exc).__name__}: {exc}"}
    return out


class TickBuffer:
    """每标的最近 N 条 tick 的有界环形缓冲（线程安全）。"""

    def __init__(self, maxlen: int = _BUFFER_MAXLEN) -> None:
        self._lock = threading.Lock()
        self._maxlen = int(maxlen)
        self._ticks: Dict[str, Deque[Dict[str, Any]]] = {}

    @staticmethod
    def key(venue: str, symbol: str) -> str:
        return f"{str(venue).lower()}:{symbol}"

    def put(self, tick: Dict[str, Any]) -> None:
        key = self.key(tick.get("venue", ""), tick.get("symbol", ""))
        with self._lock:
            buf = self._ticks.get(key)
            if buf is None:
                buf = self._ticks[key] = deque(maxlen=self._maxlen)
            buf.append(tick)

    def latest(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            buf = self._ticks.get(key)
            return dict(buf[-1]) if buf else None

    def age_s(self, key: str, *, now_ms: Optional[int] = None) -> Optional[float]:
        latest = self.latest(key)
        if latest is None:
            return None
        return max(0.0, (int(now_ms if now_ms is not None else _now_ms())
                         - int(latest["local_ms"])) / 1000.0)

    def keys(self) -> List[str]:
        with self._lock:
            return sorted(self._ticks)

    def sizes(self) -> Dict[str, int]:
        with self._lock:
            return {k: len(v) for k, v in self._ticks.items()}


class StreamHealth:
    """流健康账本：帧/tick/解析失败/连接错误/最近消息与最近 tick 时刻（按场所）。

    "已连接但没数据"是本模块最要防的形态（Binance JSON 订阅实测就是这个行为），
    故 `last_tick_ms` 与 `last_msg_ms` **分开**记：只连上、只收到订阅应答，
    在账本里必须表现为 tick 陈旧，而不是"健康"。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames: Dict[str, int] = {}
        self._ticks: Dict[str, int] = {}
        self._parse_errors: Dict[str, int] = {}
        self._errors: Dict[str, int] = {}
        self._reconnects: Dict[str, int] = {}
        self._last_msg_ms: Dict[str, int] = {}
        self._last_tick_ms: Dict[str, int] = {}
        self._last_error: Dict[str, str] = {}

    def note_frame(self, venue: str, *, ticks: int = 0, error: Optional[str] = None,
                   now_ms: Optional[int] = None) -> None:
        stamp = int(now_ms if now_ms is not None else _now_ms())
        with self._lock:
            self._frames[venue] = self._frames.get(venue, 0) + 1
            self._last_msg_ms[venue] = stamp
            if ticks:
                self._ticks[venue] = self._ticks.get(venue, 0) + ticks
                self._last_tick_ms[venue] = stamp
            if error:
                self._parse_errors[venue] = self._parse_errors.get(venue, 0) + 1
                self._last_error[venue] = str(error)[:200]

    def note_error(self, venue: str, detail: str, *, now_ms: Optional[int] = None) -> None:
        with self._lock:
            self._errors[venue] = self._errors.get(venue, 0) + 1
            self._last_error[venue] = str(detail)[:200]

    def note_reconnect(self, venue: str) -> None:
        with self._lock:
            self._reconnects[venue] = self._reconnects.get(venue, 0) + 1

    def staleness_s(self, venue: str, *, now_ms: Optional[int] = None) -> Optional[float]:
        """距上次**收到 tick** 的秒数；从未收到过返回 None（不是 0 —— 不可判定≠安全）。"""
        with self._lock:
            last = self._last_tick_ms.get(venue)
        if last is None:
            return None
        return max(0.0, (int(now_ms if now_ms is not None else _now_ms()) - last) / 1000.0)

    def snapshot(self, *, now_ms: Optional[int] = None) -> Dict[str, Any]:
        stamp = int(now_ms if now_ms is not None else _now_ms())
        with self._lock:
            venues = sorted(set(self._frames) | set(self._ticks) | set(self._errors))
            return {
                "schema_version": SCHEMA_VERSION,
                "written_at_ms": stamp,
                "venues": {
                    v: {
                        "frames": self._frames.get(v, 0),
                        "ticks": self._ticks.get(v, 0),
                        "parse_errors": self._parse_errors.get(v, 0),
                        "errors": self._errors.get(v, 0),
                        "reconnects": self._reconnects.get(v, 0),
                        "last_msg_ms": self._last_msg_ms.get(v),
                        "last_tick_ms": self._last_tick_ms.get(v),
                        "tick_age_s": (None if self._last_tick_ms.get(v) is None
                                       else round((stamp - self._last_tick_ms[v]) / 1000.0, 3)),
                        "last_error": self._last_error.get(v),
                    }
                    for v in venues
                },
            }


def write_snapshot(path: str, payload: Dict[str, Any]) -> bool:
    """原子写快照（tmp + `os.replace`）；**绝不抛异常**，失败返回 False。"""
    try:
        target = os.fspath(path)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent or ".", prefix=".ms-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
        return True
    except Exception:
        return False


def load_snapshot(path: str) -> Dict[str, Any]:
    """读快照；缺失/损坏/**版本不认识**一律返回 `{}`（调用方据此标 source_ok=0）。"""
    try:
        with open(os.fspath(path), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return {}
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return {}
    return payload


def probe(*, venues: Sequence[str] = ("okx", "gate", "binance"),
          symbol: str = "BTC-USDT-SWAP", symbol_by_venue: Optional[Dict[str, str]] = None,
          seconds: float = 10.0, snapshot_path: Optional[str] = None,
          connect_factory: Any = None, now: Any = _now_ms) -> Dict[str, Any]:
    """只读探测：连上各所公共行情流各收若干秒，返回健康快照（可选落盘）。

    `seconds` 是**每所**的窗口（总耗时 ≈ seconds × 场所数）—— 第一版把 deadline 设成
    全场共享，结果 OKX 收满 8 秒后 Gate/Binance 的窗口已经是负数、一轮都没跑
    （本机实跑才发现：快照里只有 okx）。

    `connect_factory(url, **kw)` 可注入（测试用假传输，绝不出网）。
    每一所独立 try：**一个所挂了不许影响另一个所**；
    "连上了但一条数据都没来"也要**留痕**（Binance JSON 订阅实测就是这个形态）——
    静默的失败正是第 137 刀事故里最贵的那种。
    """
    if connect_factory is None:                     # 延迟导入：本模块被 import 时不拉网络栈
        from websockets.sync.client import connect as connect_factory   # type: ignore[no-redef]

    health = StreamHealth()
    buffer = TickBuffer()
    for venue in venues:
        sym = (symbol_by_venue or {}).get(venue) or venue_symbol(venue, symbol)
        url = stream_url(venue, sym)
        frames_before = 0
        deadline = time.time() + max(0.5, float(seconds))
        try:
            with connect_factory(url, open_timeout=8, close_timeout=3) as ws:
                payload = subscribe_payload(venue, sym)
                if payload is not None:
                    ws.send(json.dumps(payload))
                while time.time() < deadline:
                    try:
                        raw = ws.recv(timeout=max(0.2, min(2.0, deadline - time.time())))
                    except Exception:
                        break                             # 本轮该所安静：下面统一留痕
                    parsed = parse_frame(venue, raw, now_ms=now())
                    for tick in parsed["ticks"]:
                        buffer.put(tick)
                    frames_before += 1
                    health.note_frame(venue, ticks=len(parsed["ticks"]),
                                      error=parsed["error"], now_ms=now())
                if frames_before == 0:
                    health.note_error(venue, "已连接但窗口内零数据帧（订阅被静默/流被限流）",
                                      now_ms=now())
        except Exception as exc:
            health.note_error(venue, f"{type(exc).__name__}: {exc}", now_ms=now())
    snap = health.snapshot(now_ms=now())
    snap["buffers"] = buffer.sizes()
    if snapshot_path:
        snap["written"] = write_snapshot(snapshot_path, snap)
    return snap


def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="R20 公共行情流只读探测（不常驻、不下单）")
    parser.add_argument("--probe", action="store_true", help="连接三所公共流收若干秒")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--symbol", default="BTC-USDT-SWAP")
    parser.add_argument("--venues", default="okx,gate,binance")
    parser.add_argument("--snapshot", default="")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.probe:
        parser.print_help()
        return 0
    snap = probe(venues=[v.strip() for v in args.venues.split(",") if v.strip()],
                 symbol=args.symbol, seconds=args.seconds,
                 snapshot_path=args.snapshot or None)
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":       # pragma: no cover - 手动探测入口
    sys.exit(_main())

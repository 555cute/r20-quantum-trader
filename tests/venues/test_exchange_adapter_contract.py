"""三所适配器契约全绿套件（审计② 2026-09-13 · 防复发核心）。

背景：D4 族（orders_pending / list_algo_orders / frozen-kwargs / sz=）与 ②#2/#3/#5
（net 方向反读 / 幻影 kwarg / Gate 盘口形态）共同证明——适配器私有包装层**从未被
任何测试穿透**，全靠人工审计成批捞。本套件用假响应离线打穿三家（零出网、假凭证），
断言的是「契约」而非某次修复：

  词表   positions 行 side∈{long,short} 且 size_signed 符号与 side 一致（全仓约定
         "long" if size_signed>0——routers/close_intent/跨所核验共用这把尺）
  形态   fetch_orderbook 三家统一 bids/asks = [[price, qty], ...]（归一壳必归一核）
  绑定   包装层→scripts.okx_rest 的 kwargs 必须过真实签名 bind（sz= 即 TypeError 当场炸）
  身份   cancel_order id 族分流（数字→orderId / 句柄→origClientOrderId）
  语义   fast_close 双向同存拒盲平（closed:False 词汇）

另含全仓 okx_rest/market_data_service 属性存在性静态扫描：把「调了不存在的函数」
这一整族 bug 变成测试期红，而不是生产 AttributeError。
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.okx_rest as okx_rest  # noqa: E402
from r20_backend.exchanges.okx import OKXAdapter  # noqa: E402
from r20_backend.exchanges.binance import BinanceAdapter  # noqa: E402
from r20_backend.exchanges.gate import GateAdapter  # noqa: E402


class _Recorder:
    """捕获 kwargs 并按**真实签名**绑定——幻影 kwarg（sz= 族）在测试期即 TypeError。"""

    def __init__(self, fn):
        self.sig = inspect.signature(fn)
        self.calls = []

    def __call__(self, *a, **k):
        self.sig.bind(*a, **k)
        self.calls.append((a, k))
        return [{"sCode": "0", "ordId": "12345678", "clOrdId": k.get("cl_ord_id") or "c1"}]


# ------------------------------------------------------------------ 词表：positions


class TestPositionsVocabulary(unittest.TestCase):
    def _assert_rows(self, rows, venue):
        self.assertTrue(rows, "fixture 必须产出非空持仓")
        for p in rows:
            self.assertIn(p["side"], ("long", "short"), f"{venue}: side 越出词表 → {p!r}")
            self.assertEqual((float(p["size_signed"]) > 0), p["side"] == "long",
                             f"{venue}: size_signed 符号与 side 矛盾 → {p!r}")
            self.assertEqual(p["venue"], venue)
        return rows

    def test_okx_net_and_hedge_modes(self):
        ad = OKXAdapter(api_key="k", secret_key="s", passphrase="p", environment="demo")
        raw = [
            {"instId": "BTC-USDT-SWAP", "pos": "3.5", "posSide": "net", "upl": "1", "avgPx": "70000", "markPx": "70100", "lever": "3", "mgnMode": "cross"},
            {"instId": "ETH-USDT-SWAP", "pos": "-2", "posSide": "net", "upl": "0", "avgPx": "3000", "markPx": "3001", "lever": "3", "mgnMode": "cross"},
            {"instId": "SOL-USDT-SWAP", "pos": "10", "posSide": "long", "upl": "0", "avgPx": "150", "markPx": "151", "lever": "5", "mgnMode": "cross"},
            {"instId": "XRP-USDT-SWAP", "pos": "-8", "posSide": "short", "upl": "0", "avgPx": "0.5", "markPx": "0.5", "lever": "5", "mgnMode": "cross"},
        ]
        with patch.object(okx_rest, "positions", lambda **kw: raw):
            rows = self._assert_rows(ad.positions(), "okx")
        by = {r["instId"]: r for r in rows}
        self.assertEqual(by["BTC-USDT-SWAP"]["side"], "long")     # net 多头不得反读
        self.assertGreater(by["BTC-USDT-SWAP"]["size_signed"], 0)
        self.assertEqual(by["ETH-USDT-SWAP"]["side"], "short")
        self.assertLess(by["ETH-USDT-SWAP"]["size_signed"], 0)
        self.assertEqual(by["SOL-USDT-SWAP"]["side"], "long")     # hedge 明示照用

    def test_binance_positions(self):
        ad = BinanceAdapter(environment="demo")
        raw = [
            {"symbol": "BTCUSDT", "positionAmt": "1.2", "entryPrice": "70000", "markPrice": "70001",
             "leverage": "3", "unRealizedProfit": "0", "marginType": "cross", "positionSide": "LONG",
             "isolatedMargin": "10", "liquidationPrice": "0"},
            {"symbol": "ETHUSDT", "positionAmt": "-0.5", "entryPrice": "3000", "markPrice": "3000",
             "leverage": "3", "unRealizedProfit": "0", "marginType": "cross", "positionSide": "BOTH",
             "isolatedMargin": "5", "liquidationPrice": "0"},
            {"symbol": "SOLUSDT", "positionAmt": "0", "entryPrice": "0", "markPrice": "0",
             "leverage": "1", "unRealizedProfit": "0", "marginType": "cross", "positionSide": "BOTH",
             "isolatedMargin": "0", "liquidationPrice": "0"},
        ]
        with patch.object(ad, "signed_request", lambda *a, **k: raw):
            rows = self._assert_rows(ad.positions(), "binance")
        self.assertEqual({r["inst_id"] for r in rows}, {"BTCUSDT", "ETHUSDT"})  # 零仓行剔除

    def test_gate_positions(self):
        ad = GateAdapter(environment="sandbox")
        raw = [
            {"contract": "BTC_USDT", "size": 6, "entry_price": "70000", "mark_price": "70000",
             "leverage": "3", "margin": "10", "margin_mode": "cross", "unrealised_pnl": 0, "liq_price": "0"},
            {"contract": "ETH_USDT", "size": -3, "entry_price": "3000", "mark_price": "3000",
             "leverage": "3", "margin": "5", "margin_mode": "cross", "unrealised_pnl": 0, "liq_price": "0"},
        ]
        with patch.object(ad, "signed_request", lambda *a, **k: raw):
            self._assert_rows(ad.positions(), "gate")


# ------------------------------------------------------------------ 形态：orderbook


class TestOrderbookUniformShape(unittest.TestCase):
    """三家 fetch_orderbook 必须吐 bids/asks = [[price, qty], ...]。"""

    def _check(self, ob, venue):
        self.assertIsNotNone(ob)
        self.assertEqual(ob["venue"], venue)
        for side in ("bids", "asks"):
            self.assertIsInstance(ob[side], list)
            for lv in ob[side]:
                self.assertIsInstance(lv, list, f"{venue}.{side} 出现 dict 行（未归一）: {lv!r}")
                self.assertGreaterEqual(len(lv), 2)
                float(lv[0]); float(lv[1])
        return ob

    def test_okx_native_arrays(self):
        ad = OKXAdapter()
        payload = [{"bids": [["70000.1", "3", 0, 0], ["69999", "1", 0, 0]],
                    "asks": [["70001", "2", 0, 0]]}]
        with patch.object(type(ad).__mro__[1], "_get", lambda self, url, params=None: payload):
            self._check(ad.fetch_orderbook("BTC"), "okx")

    def test_binance_native_arrays(self):
        ad = BinanceAdapter(environment="demo")
        payload = {"bids": [["100.0", "5"], ["99.9", "1"]], "asks": [["100.1", "2"]]}
        with patch.object(ad, "_public_get", lambda *a, **k: payload):
            self._check(ad.fetch_orderbook("BTC"), "binance")

    def test_gate_object_array_normalized(self):
        # ②#5 实锤形态：Gate 官方 FuturesOrderBookItem = [{"p":价,"s":量}]
        ad = GateAdapter(environment="sandbox")
        payload = {"bids": [{"p": "48977.6", "s": "100"}, {"p": "48977.5", "s": "7"}],
                   "asks": [{"p": "48977.7", "s": "33"}]}
        with patch.object(ad, "_public_get", lambda *a, **k: payload):
            ob = self._check(ad.fetch_orderbook("BTC"), "gate")
            # 自家消费写法（binance.py: bids[0][0]）在 Gate 形态上必须可行
            self.assertAlmostEqual(float(ob["bids"][0][0]), 48977.6)


# ------------------------------------------------------------------ 绑定：okx_rest 签名


class TestOkxWrapperSignatureBinding(unittest.TestCase):
    def setUp(self):
        self.ad = OKXAdapter(api_key="k", secret_key="s", passphrase="p", environment="demo")

    def _place(self, rec, **kw):
        with patch.object(okx_rest, "place_order", rec):
            self.ad.place_order("BTC", "buy", 3.5, price=70000, **kw)
        return rec.calls[-1][1]

    def test_size_kwarg_not_sz(self):
        rec = _Recorder(okx_rest.place_order)
        kwargs = self._place(rec)
        self.assertIn("size", kwargs)          # 旧 sz= 在此当场 TypeError（真实签名 bind）
        self.assertEqual(kwargs["size"], "3.5")

    def test_net_mode_omits_pos_side(self):
        rec = _Recorder(okx_rest.place_order)
        kwargs = self._place(rec)
        self.assertTrue(kwargs.get("pos_side") in (None, ""),
                        "净模式默认不得发 posSide=long/short（旧 buy→long 恒发被 51006 拒）")

    def test_explicit_hedge_pos_side_passes_through(self):
        rec = _Recorder(okx_rest.place_order)
        kwargs = self._place(rec, pos_side="long")
        self.assertEqual(kwargs.get("pos_side"), "long")

    def test_net_token_maps_to_none(self):
        rec = _Recorder(okx_rest.place_order)
        kwargs = self._place(rec, pos_side="net")
        self.assertTrue(kwargs.get("pos_side") in (None, ""))

    def test_garbage_pos_side_rejected(self):
        with self.assertRaises(ValueError):
            self._place(_Recorder(okx_rest.place_order), pos_side="up")

    def test_close_long_semantics_not_inverted(self):
        # 双向模式平多 = sell + posSide=long（旧默认 sell→short = 反向开空事故形态）
        rec = _Recorder(okx_rest.place_order)
        with patch.object(okx_rest, "place_order", rec):
            self.ad.place_order("BTC", "sell", 3, price=None, pos_side="long", reduce_only=True)
        kwargs = rec.calls[-1][1]
        self.assertEqual((kwargs["side"], kwargs.get("pos_side")), ("sell", "long"))
        self.assertTrue(kwargs.get("reduce_only"))


# ------------------------------------------------------------------ 身份：cancel id 族分流


class TestCancelIdFamilies(unittest.TestCase):
    def test_binance_digit_vs_client_handle(self):
        ad = BinanceAdapter(environment="demo")
        seen = []

        def fake_signed(method, path, params=None, **kw):
            seen.append(params)
            return {"status": "CANCELED"}
        with patch.object(ad, "signed_request", fake_signed):
            ad.cancel_order("BTC", order_id="123456789")
            ad.cancel_order("BTC", order_id="t-r20e1712345")
        self.assertEqual(str(seen[0].get("orderId")), "123456789")
        self.assertNotIn("origClientOrderId", seen[0])
        self.assertEqual(seen[1].get("origClientOrderId"), "t-r20e1712345")
        self.assertNotIn("orderId", seen[1])


# ------------------------------------------------------------------ 语义：fast_close 拒盲平


class TestFastCloseRefusalVocabulary(unittest.TestCase):
    def test_binance_hedge_dual_refuses(self):
        ad = BinanceAdapter(environment="demo")
        raw = [
            {"symbol": "BTCUSDT", "positionAmt": "1", "entryPrice": "1", "markPrice": "1",
             "leverage": "3", "unRealizedProfit": "0", "marginType": "cross", "positionSide": "LONG",
             "isolatedMargin": "1", "liquidationPrice": "0"},
            {"symbol": "BTCUSDT", "positionAmt": "-1", "entryPrice": "1", "markPrice": "1",
             "leverage": "3", "unRealizedProfit": "0", "marginType": "cross", "positionSide": "SHORT",
             "isolatedMargin": "1", "liquidationPrice": "0"},
        ]
        placed = []
        with patch.object(ad, "signed_request", lambda *a, **k: raw), \
             patch.object(ad, "place_order", lambda *a, **k: placed.append(k) or {"closed": True}):
            out = ad.fast_close_position("BTC")
        self.assertIs(out["closed"], False)
        self.assertEqual(placed, [], "拒盲平时绝不发单")

    def test_gate_fractional_size_refuses(self):
        ad = GateAdapter(environment="sandbox")
        sent = []
        # positions() 直接喂归一形态行（B1 语义面，不再测 positions 本身）
        rows = [{"venue": "gate", "inst_id": "BTC_USDT", "base": "BTC",
                 "side": "long", "size_signed": 2.5}]
        with patch.object(ad, "signed_request", lambda *a, **k: sent.append((a, k)) or {}), \
             patch.object(ad, "positions", lambda: rows):
            out = ad.fast_close_position("BTC")
        self.assertIs(out["closed"], False)
        self.assertIn("非整数", out["reason"])
        self.assertEqual(sent, [], "拒截断时绝不发单")


# ------------------------------------------------------------------ 静态：D4 族全仓扫描


_IMPORT_TARGETS = ("okx_rest", "market_data_service")


class TestNoPhantomModuleAttributes(unittest.TestCase):
    """把「调了不存在的函数」整族变测试期红：全仓 X.y( 调用点 hasattr(X) 核对。"""

    def _iter_py(self):
        for d in ("r20_backend", "scripts", "r20_gateway"):
            for f in sorted((ROOT / d).rglob("*.py")):
                if "test" in f.name.lower():
                    continue
                yield f

    def test_attribute_calls_exist(self):
        import scripts.market_data_service as mds
        targets = {"okx_rest": okx_rest, "market_data_service": mds}
        pat = re.compile(r"\b(" + "|".join(_IMPORT_TARGETS) + r")\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
        missing = []
        for f in self._iter_py():
            try:
                src = f.read_text(encoding="utf-8")
            except OSError:
                continue
            for m in pat.finditer(src):
                mod, attr = m.group(1), m.group(2)
                if not hasattr(targets[mod], attr):
                    line = src[:m.start()].count("\n") + 1
                    missing.append(f"{f}:{line} {mod}.{attr}(")
        self.assertEqual(missing, [], "幻影属性调用（D4 族）:\n" + "\n".join(missing))


class TestCloseCannotOpenOppositeTest(unittest.TestCase):
    """平仓路径**结构上不可能反向开仓**（第一百三十三刀审计的契约化）。

    三所三种机制，各自钉住 —— 任一处被"顺手改统一"都会出事：

    | 场所/路径 | 机制 | 后果若有误 |
    |---|---|---|
    | OKX 整仓平 | 专用端点 `/api/v5/trade/close-position`（语义即平仓，**不经** place_order）| 若改走 place_order 且漏 `reduceOnly` ⇒ 反手开新仓 |
    | Binance 对冲模式平 | 只发 `positionSide`、**绝不发** `reduceOnly` | 发了会被交易所 -1106 拒单（平不掉） |
    | Binance 单仓模式平 | `reduceOnly=true` 且**不带** positionSide | 漏了 ⇒ 反手开新仓 |
    | 两者同时传 | 参数构建器**前置抛 ValueError** | 把矛盾参数发给交易所 = 语义不清 |
    """

    def test_okx_whole_close_uses_dedicated_endpoint(self):
        from scripts import okx_rest
        seen = []

        def _rec(method, path, body=None, **kw):
            seen.append((method, path, dict(body or {})))
            return []

        with patch.object(okx_rest, "request", _rec), \
                patch.object(okx_rest, "place_order",
                             lambda *a, **k: (_ for _ in ()).throw(
                                 AssertionError("整仓平不得走 place_order"))):
            okx_rest.close_position("BTC-USDT-SWAP", "long", td_mode="cross")

        self.assertEqual(len(seen), 1)
        _m, _p, _body = seen[0]
        self.assertEqual(_p, "/api/v5/trade/close-position", "整仓平必须走专用平仓端点")
        self.assertEqual((_body.get("instId"), _body.get("posSide")),
                         ("BTC-USDT-SWAP", "long"))

    def _spec(self):
        import types
        return types.SimpleNamespace(step_size=0.1, tick_size=0.1)

    def _params(self, **over):
        from r20_backend.exchanges.binance_orders import build_order_params
        kw = dict(inst="BTCUSDT", position_side=None, price=None, qty=1.0,
                  reduce_only=False, s="SELL", spec=self._spec(), text="", tif="gtc")
        kw.update(over)
        return build_order_params(**kw)

    def test_binance_hedge_close_never_sends_reduce_only(self):
        p = self._params(position_side="LONG", reduce_only=False)
        self.assertEqual(p.get("positionSide"), "LONG")
        self.assertNotIn("reduceOnly", p,
                         "对冲模式发 reduceOnly 会被交易所 -1106 拒单（平不掉仓）")

    def test_binance_one_way_close_sets_reduce_only(self):
        p = self._params(reduce_only=True)
        self.assertEqual(p.get("reduceOnly"), "true")
        self.assertNotIn("positionSide", p)

    def test_binance_contradictory_combination_fails_fast(self):
        with self.assertRaises(ValueError):
            self._params(position_side="LONG", reduce_only=True)

if __name__ == "__main__":
    unittest.main(verbosity=2)
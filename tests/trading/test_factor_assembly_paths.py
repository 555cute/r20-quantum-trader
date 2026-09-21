"""因子装配的剩余路径（第二百五十六刀）。

`fetch_single_instrument_data` 每个标的每周期跑一次，四个依赖（`fetch_candles_direct` /
`news_sentiment_file` / `instrument_profile` / `load_adaptive_config`）由门面注入
—— 正因为注入了，才能在这里安全地造 K 线序列。

本刀覆盖四类此前没走到的路径：

| 路径 | 语义 |
|---|---|
| 持仓快照 | 只认**同 instId 且非零**的仓（零仓不算持仓）|
| `structure_1h` | 近 5 根 vs 前 10 根：`HH_HL` / `LH_LL` / 其余 `CHOP` |
| `market_regime` | 1H 与 15M **必须同向**才叫趋势；1H 空但 15M 反弹 ⇒ 锁 `CHOP`（防惯性误判）|
| `sz` 兜底 | `ctVal`/ATR 不可用时退回 `base_sz * 乘数`；行情不完整 ⇒ **归零**（不放大成 1 张）|
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from scripts.trader.factors import fetch_single_instrument_data

INST = "BTC-USDT-SWAP"


def _candle(close, *, high=None, low=None, open_=None, vol=1.0):
    """[ts, open, high, low, close, vol] —— 索引 2/3/4/5 与生产代码一致。"""
    return [0, open_ if open_ is not None else close,
            high if high is not None else close,
            low if low is not None else close,
            close, vol]


def _rising(n, start=100.0, step=0.5):
    return [_candle(start + i * step) for i in range(n)]


def _falling(n, start=200.0, step=0.5):
    return [_candle(start - i * step) for i in range(n)]


class _Base(unittest.TestCase):
    def setUp(self):
        # ⚠️ 生产代码在 15M 分支里会**真的发 HTTPS 请求**取 BBO 盘口价
        # （urllib → okx.com）⇒ 测试必须打桩，否则既慢又依赖网络。
        self._net = patch("urllib.request.urlopen", side_effect=RuntimeError("测试内不出网"))
        self._net.start()
        self.addCleanup(self._net.stop)
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.tmp.write(json.dumps({"score": 0.0}))
        self.tmp.close()
        self.addCleanup(lambda: os.unlink(self.tmp.name))

    def _call(self, *, candles_15m=None, candles_1h=None, candles_4h=None,
              positions=(), ctVal=1.0, adaptive=None, news_file=None):
        """返回装配好的因子字典 `f`。K 线按**由旧到新**传入，内部自动翻转成交易所的
        「最新在前」顺序（生产代码会 `reversed()`）。"""
        books = {"15m": list(reversed(candles_15m if candles_15m is not None else _rising(45))),
                 "1H": list(reversed(candles_1h if candles_1h is not None else _rising(35))),
                 "4H": list(reversed(candles_4h if candles_4h is not None else _rising(25)))}
        item = {"instId": INST, "name": "BTC", "type": "crypto", "base_sz": 2.0,
                "precision": 2, "ctVal": ctVal, "minSz": 0.01}
        return fetch_single_instrument_data(
            item, list(positions), 1000.0,
            news_sentiment_file=news_file or self.tmp.name,
            fetch_candles_direct=lambda inst, bar, n: books.get(bar, []),
            instrument_profile=lambda f, asset_type: {"sl_atr_mult": 1.3},
            load_adaptive_config=lambda: (adaptive or {}))


class BboTickerTest(_Base):
    """盘口取价（BBO）：成功 ⇒ 用**真实 bid/ask**（限价精度靠它）。"""

    class _Resp:
        def __init__(self, payload):
            self._body = json.dumps(payload).encode("utf-8")

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_successful_ticker_uses_real_bid_ask(self):
        self._net.stop()          # 放开 setUp 的「不出网」桩，换成受控响应
        payload = {"code": "0", "data": [{"bidPx": "79999.5", "askPx": "80000.5"}]}
        with patch("urllib.request.urlopen", return_value=self._Resp(payload)):
            f = self._call()
        self.assertEqual(f["bidPx"], 79999.5, "盘口价取到就用它，而不是退回最新价")
        self.assertEqual(f["askPx"], 80000.5)
        self.assertGreaterEqual(f["askPx"], f["bidPx"], "行情有效性判定要求 ask ≥ bid")

    def test_non_zero_code_keeps_fallback_prices(self):
        """`code != "0"`（交易所侧异常）⇒ 退回最新价，**不抛**（降级但已留痕）。"""
        self._net.stop()
        payload = {"code": "51001", "data": []}
        with patch("urllib.request.urlopen", return_value=self._Resp(payload)):
            f = self._call()
        self.assertEqual(f["bidPx"], f["price"])
        self.assertEqual(f["askPx"], f["price"])


class SentimentTest(_Base):
    """舆情：**「情绪=0（真中性）」与「没读到」必须分开**（本仓红线「缺失 ≠ 0」）。"""

    def _news_file(self, payload):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        fh.write(payload)
        fh.close()
        self.addCleanup(lambda: os.unlink(fh.name))
        return fh.name

    def test_score_is_picked_up_and_marked_available(self):
        path = self._news_file(json.dumps(
            {"coins_sentiment": {"BTC": {"sentiment_factor_score": 0.7}}}))
        f = self._call(news_file=path)
        self.assertEqual(f["sentiment_score"], 0.7)
        self.assertIs(f["sentiment_available"], True)

    def test_true_neutral_is_available_not_missing(self):
        """真的读到 0 分 ⇒ `available=True`（有数据、就是中性），不得与「没读到」混淆。"""
        path = self._news_file(json.dumps(
            {"coins_sentiment": {"BTC": {"sentiment_factor_score": 0.0}}}))
        f = self._call(news_file=path)
        self.assertEqual(f["sentiment_score"], 0.0)
        self.assertIs(f["sentiment_available"], True, "0 分是**中性**，不是缺失")

    def test_unreadable_file_marks_unavailable_and_warns(self):
        """文件在、但读不出来 ⇒ `available=False` **且必须告警**（不许静默当 0）。"""
        path = self._news_file("{不是 json")
        import warnings as _w
        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            f = self._call(news_file=path)
        self.assertIs(f["sentiment_available"], False)
        self.assertEqual(f["sentiment_score"], 0.0)
        self.assertTrue(any(issubclass(w.category, RuntimeWarning) for w in caught),
                        "读失败必须留痕（原先静默 pass 会把「没数据」当「中性」）")


class CalculusTest(_Base):
    """多周期动力学：**引擎成功时用它给的整包**（不是只挑几个字段抄）。"""

    def test_engine_result_is_adopted_wholesale(self):
        import types
        fake = types.ModuleType("calculus_engine")
        fake.calculate_multi_timeframe = lambda books: {
            "valid": True, "regime": "BULL_ACCELERATING", "velocity": 0.4,
            "acceleration": 0.3, "impulse": 0.2, "max_abs_jerk": 0.1, "quality": 0.9}
        with patch.dict("sys.modules", {"calculus_engine": fake}):
            f = self._call()
        self.assertEqual(f["calculus"]["regime"], "BULL_ACCELERATING")
        self.assertEqual(f["calculus"]["velocity"], 0.4)
        self.assertTrue(f["calculus"]["valid"])

    def test_engine_failure_keeps_honest_zero_shape_with_reason(self):
        """引擎炸了 ⇒ 保留「零动力学」的**诚实形状**（`valid=False`）+ 原因 + 告警。

        ⚠️ 关键：不许静默退化 —— 主脑会照着 v=a=0 推理，等于拿"没有动力学"当"动力学为零"。
        """
        import types
        import warnings as _w

        def _boom(books):
            raise RuntimeError("引擎炸了")

        fake = types.ModuleType("calculus_engine")
        fake.calculate_multi_timeframe = _boom
        with patch.dict("sys.modules", {"calculus_engine": fake}):
            with _w.catch_warnings(record=True) as caught:
                _w.simplefilter("always")
                f = self._call()
        self.assertFalse(f["calculus"]["valid"])
        self.assertEqual(f["calculus"]["regime"], "RANGE_LOW_VELOCITY")
        self.assertIn("引擎炸了", f["calculus"]["error"])
        self.assertTrue(any(issubclass(w.category, RuntimeWarning) for w in caught))


class PositionSnapshotTest(_Base):
    """持仓快照：**只认同 instId 且非零**的仓（零仓不是持仓）。"""

    def test_non_zero_position_of_this_instrument_is_snapshotted(self):
        f = self._call(positions=[{"instId": INST, "pos": 5.0, "posSide": "long",
                                   "avgPx": 80000.0, "markPx": 81000.0, "upl": 100.0,
                                   "uplRatio": 0.01, "lever": "3"}])
        self.assertIsNotNone(f["position"])
        self.assertEqual(f["position"]["pos"], 5.0)
        self.assertEqual(f["position"]["side"], "long")
        self.assertEqual(f["position"]["avgPx"], 80000.0)

    def test_zero_position_is_not_a_position(self):
        """平掉的仓（`pos=0`）**不得**被当成持仓 —— 否则后续会按"有仓"做移损等动作。"""
        f = self._call(positions=[{"instId": INST, "pos": 0.0, "posSide": "long"}])
        self.assertIsNone(f["position"])

    def test_other_instrument_is_ignored(self):
        f = self._call(positions=[{"instId": "ETH-USDT-SWAP", "pos": 9.0}])
        self.assertIsNone(f["position"])


class StructureAndRegimeTest(_Base):
    """结构（近 5 根 vs 前 10 根）与 regime（1H+15M 必须同向）。"""

    # ⚠️ 序列长度要**足够算 EMA21**：实测 15 根时 `calc_ema(closes, 21)` 会退化成末位
    # 收盘价，于是 `e9_1h >= e21_1h` 判成 False、趋势方向**整个反过来**（我第一版就踩了：
    # 上涨序列被判成空头）。生产传的是 35 根 ⇒ 夹具也用 35 根（30 根基底 + 5 根新段）。
    _BASE = 30

    def _hh_hl_1h(self):
        # 前 30 根低位窄幅，后 5 根整体抬高 ⇒ 近 5 根高点更高、低点也更高（HH_HL）
        old = [_candle(100.0, high=101.0, low=99.0) for _ in range(self._BASE)]
        new = [_candle(110.0 + i, high=112.0 + i, low=108.0 + i) for i in range(5)]
        return old + new

    def _lh_ll_1h(self):
        old = [_candle(200.0, high=201.0, low=199.0) for _ in range(self._BASE)]
        new = [_candle(190.0 - i, high=192.0 - i, low=188.0 - i) for i in range(5)]
        return old + new

    def test_structure_hh_hl(self):
        f = self._call(candles_1h=self._hh_hl_1h())
        self.assertEqual(f["structure_1h"], "HH_HL")

    def test_structure_lh_ll(self):
        f = self._call(candles_1h=self._lh_ll_1h(), candles_15m=_falling(45, 200.0))
        self.assertEqual(f["structure_1h"], "LH_LL")

    def test_structure_chop_when_neither_direction(self):
        """高不成低不就（近 5 根与前面重叠）⇒ `CHOP`。"""
        flat = [_candle(100.0, high=101.0, low=99.0) for _ in range(35)]
        f = self._call(candles_1h=flat)
        self.assertEqual(f["structure_1h"], "CHOP")

    def test_bull_trend_requires_15m_and_1h_aligned(self):
        f = self._call(candles_1h=self._hh_hl_1h(), candles_15m=_rising(45, 100.0))
        self.assertEqual(f["market_regime"], "BULL_TREND")

    def test_bearish_1h_with_rebounding_15m_is_locked_to_chop(self):
        """★ 防惯性误判：1H 说空、但 15M 正在反弹 ⇒ **锁 `CHOP`**，不许叫 BEAR_TREND。

        （否则会在 V 型反转里按"空头趋势"给反向信号。）
        """
        f = self._call(candles_1h=self._lh_ll_1h(), candles_15m=_rising(45, 100.0))
        self.assertEqual(f["market_regime"], "CHOP")


class SizeFallbackTest(_Base):
    def test_size_falls_back_to_base_multiplier_without_ctval(self):
        """`ctVal<=0`（或 ATR 不可用）⇒ 退回 `base_sz * 位置乘数`，而不是按风险额硬算。"""
        f = self._call(ctVal=0.0, adaptive={"position_size_multipliers": {"BTC": 2.0}})
        self.assertEqual(f["sz"], 4.0, "base_sz 2.0 × 乘数 2.0")

    def test_size_multiplier_zero_means_no_trade(self):
        f = self._call(ctVal=0.0, adaptive={"position_size_multipliers": {"BTC": 0.0}})
        self.assertEqual(f["sz"], 0.0)

    def test_incomplete_market_data_forces_size_to_zero(self):
        """行情不完整 ⇒ 张数**归零**（上层跳过），**绝不放大成 1 张**。"""
        f = self._call(candles_15m=[], candles_1h=[], candles_4h=[])
        self.assertFalse(f["market_data_valid"])
        self.assertEqual(f["sz"], 0.0)


if __name__ == "__main__":
    unittest.main()

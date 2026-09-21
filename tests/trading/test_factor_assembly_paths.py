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
              positions=(), ctVal=1.0, adaptive=None):
        """返回装配好的因子字典 `f`。K 线按**由旧到新**传入，内部自动翻转成交易所的
        「最新在前」顺序（生产代码会 `reversed()`）。"""
        books = {"15m": list(reversed(candles_15m if candles_15m is not None else _rising(45))),
                 "1H": list(reversed(candles_1h if candles_1h is not None else _rising(35))),
                 "4H": list(reversed(candles_4h if candles_4h is not None else _rising(25)))}
        item = {"instId": INST, "name": "BTC", "type": "crypto", "base_sz": 2.0,
                "precision": 2, "ctVal": ctVal, "minSz": 0.01}
        return fetch_single_instrument_data(
            item, list(positions), 1000.0,
            news_sentiment_file=self.tmp.name,
            fetch_candles_direct=lambda inst, bar, n: books.get(bar, []),
            instrument_profile=lambda f, asset_type: {"sl_atr_mult": 1.3},
            load_adaptive_config=lambda: (adaptive or {}))


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

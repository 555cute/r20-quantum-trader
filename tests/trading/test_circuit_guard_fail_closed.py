"""黑天鹅哨兵的 **fail-closed 行为**用例（第二百一十一刀）。

`scripts/trader/circuit_guard.py::check_black_swan_sentinel` 的判据是
「**不可判定 = 不放松**」：行情取不到、样本不足、数据形态不可判定、情绪文件损坏
—— 每一种都必须**触发熔断**，绝不带着盲区继续开新仓。

⚠️ 本刀之前，这个模块只有**静态**用例（`tests/audit/test_circuit_breaker_twin_parity.py`
是 AST 层面的双实现比对、`tests/extraction/...` 是抽取等价性），
**没有任何用例真的把哨兵跑起来**看过它的返回 —— 于是"判据写对了吗"从未被验证过。
"""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.trader.circuit_guard import check_black_swan_sentinel


def _candle(open_px, low, close):
    """OKX K 线形态：[ts, open, high, low, close, ...]。"""
    return ["1700000000000", str(open_px), str(max(open_px, close)), str(low), str(close), "0"]


def _pair(open_px, low, close):
    """**两根** K 线：哨兵要求 `len(candles) >= 2`，只给一根会走"无有效数据"分支
    （我第一版就是这么写的，于是所有行情用例都撞在同一条兜底上）。第一根是待判场景。"""
    return [_candle(open_px, low, close), _candle(open_px, low, close)]


class BlackSwanSentinelFailClosedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20-sentinel-")
        self.addCleanup(self.tmp.cleanup)
        self.news = Path(self.tmp.name) / "news.json"

    def _sentinel(self, candles, *, raises=None):
        def fetch(_inst, _bar, _limit):
            if raises is not None:
                raise raises
            return candles
        return check_black_swan_sentinel(fetch_candles_direct=fetch,
                                        news_sentiment_file=str(self.news))

    # ── 1. 行情侧：不可判定一律熔断 ─────────────────────────────────────
    def test_fetch_failure_trips_the_breaker(self):
        tripped, why = self._sentinel(None, raises=RuntimeError("feed down"))
        self.assertTrue(tripped, "统一行情通道异常 ⇒ 不可判定 ⇒ 必须熔断（不放松）")
        self.assertIn("统一行情通道异常", why)

    def test_empty_or_insufficient_candles_trip_the_breaker(self):
        for candles in ([], [_candle(70000, 69900, 70000)]):
            with self.subTest(n=len(candles)):
                tripped, why = self._sentinel(candles)
                self.assertTrue(tripped, f"样本不足 ⇒ 必须熔断：{candles}")
                self.assertIn("无有效数据", why)

    def test_malformed_candle_rows_trip_the_breaker(self):
        tripped, why = self._sentinel([["1", "不是数字", "x", "y", "z"], ["2"]])
        self.assertTrue(tripped, "行情形态不可判定 ⇒ 必须熔断")
        self.assertIn("格式异常", why)

    def test_extreme_plunge_trips_the_breaker(self):
        tripped, why = self._sentinel(_pair(70000, 67000, 67000))   # 收跌 -4.3%
        self.assertTrue(tripped, "15M 断崖 ⇒ 必须熔断")
        self.assertIn("断崖式暴跌", why)

    def test_long_lower_wick_trips_the_breaker(self):
        # 收盘只跌 1%，但下影插到 -5% ⇒ 同样算插针
        tripped, why = self._sentinel(_pair(70000, 66500, 69300))
        self.assertTrue(tripped, "下影插针 ⇒ 必须熔断")
        self.assertIn("断崖式暴跌", why)

    def test_normal_market_does_not_trip(self):
        tripped, why = self._sentinel(_pair(70000, 69800, 70200))
        self.assertFalse(tripped, f"正常行情不得误熔断：{why!r}")
        self.assertEqual(why, "")

    # ── 2. 情绪侧：缺失 ≠ 中性，只有显式极端值才熔断 ────────────────────
    def test_missing_news_file_does_not_trip_by_itself(self):
        self.assertFalse(self.news.exists())
        tripped, why = self._sentinel(_pair(70000, 69800, 70200))
        self.assertFalse(tripped, "情绪文件缺失只是「没有这一路信号」，不因此熔断（但也不冒充中性 50）")

    def test_extreme_negative_sentiment_trips_the_breaker(self):
        self.news.write_text(json.dumps({"overall_score": 12.5}), encoding="utf-8")
        tripped, why = self._sentinel(_pair(70000, 69800, 70200))
        self.assertTrue(tripped, "情绪指数 ≤20 ⇒ 必须熔断")
        self.assertIn("极度恶性利空", why)

    def test_non_extreme_sentiment_does_not_trip(self):
        for score in (21.0, 50.0, 95.0, None):
            with self.subTest(score=score):
                self.news.write_text(json.dumps({"overall_score": score}), encoding="utf-8")
                tripped, why = self._sentinel(_pair(70000, 69800, 70200))
                self.assertFalse(tripped, f"score={score} 不该触发熔断：{why!r}")

    def test_corrupt_news_file_trips_the_breaker(self):
        self.news.write_text("{ 这不是合法 JSON", encoding="utf-8")
        tripped, why = self._sentinel(_pair(70000, 69800, 70200))
        self.assertTrue(tripped, "情绪缓存损坏 ⇒ 不可判定 ⇒ 必须熔断（不许因读不到而放行）")
        self.assertIn("不可判定", why)

    def test_non_numeric_score_trips_the_breaker(self):
        self.news.write_text(json.dumps({"overall_score": "很糟"}), encoding="utf-8")
        tripped, why = self._sentinel(_pair(70000, 69800, 70200))
        self.assertTrue(tripped, "分数不是数字 ⇒ 不可判定 ⇒ 必须熔断")
        self.assertIn("不可判定", why)


if __name__ == "__main__":
    unittest.main()

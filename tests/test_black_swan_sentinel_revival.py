"""US-014 前置收尾：黑天鹅熔断信号缺失化后的保守语义（缺失≠放宽）。

归因：3137c40/09fba6f 将 smartmoney/news CLI 信号面缺失化后，依赖新闻流的熔断
检测一度休眠。本文件钉住复活后的契约：
1) 熔断由统一 V5 REST 公共行情（market_data_service，零凭证）驱动；
2) 行情不可判定（取不到/异常/样本不足/格式坏）→ 触发熔断，绝不盲行（不可判定=不放松）；
3) 新闻情绪只认显式极端值——旧实现缺省 overall_score=50 的假中性放行已删除，
   文件损坏同样按不可判定保守处理；正常行情+无新闻数据不误伤。

约定：patch 模块内绑定名 fetch_candles_direct（US-001 Learning 3），零网络。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.ai_factor_trader as aft


_OLDER = [["1699999100000", "99.0", "99.8", "98.6", "99.4", "8", "1", "0"]]


def _calm_candles():
    # [ts, open, high, low, close, ...]：15M 正常微涨，无暴跌（最新一根在前）
    return [["1700000000000", "100.0", "101.0", "99.5", "100.4", "10", "1", "0"]] + _OLDER


def _plunge_candles():
    return [["1700000000000", "100.0", "100.5", "95.0", "96.5", "10", "1", "0"]] + _OLDER


class BlackSwanSentinelRevivalTests(unittest.TestCase):
    def setUp(self):
        # 默认指向不存在的情绪文件，隔离本机 data/
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.news_file = os.path.join(self._tmp.name, "news_sentiment.json")
        self._p_news = patch.object(aft, "NEWS_SENTIMENT_FILE", self.news_file)
        self._p_news.start()
        self.addCleanup(self._p_news.stop)

    def _sentinel(self, candles_ret, raises=False):
        with patch.object(aft, "fetch_candles_direct") as fc:
            if raises:
                fc.side_effect = RuntimeError("all domains down")
            else:
                fc.return_value = candles_ret
            return aft.check_black_swan_sentinel()

    # ---- 行情缺失/不可判定 → 保守触发（缺失≠放宽） ----
    def test_missing_market_data_does_not_relax_breaker(self):
        active, reason = self._sentinel(None)
        self.assertTrue(active, "行情缺失必须触发熔断，不得静默放行")
        self.assertIn("不可判定=不放松", reason)

    def test_market_channel_exception_does_not_relax_breaker(self):
        active, reason = self._sentinel(None, raises=True)
        self.assertTrue(active, "统一行情通道异常必须触发熔断")
        self.assertIn("不可判定=不放松", reason)

    def test_insufficient_candles_treated_as_undecidable(self):
        active, _ = self._sentinel([])
        self.assertTrue(active)
        active2, _ = self._sentinel(_calm_candles()[:1])
        self.assertTrue(active2, "样本不足以内视为不可判定，不得放宽")

    def test_malformed_candles_fail_closed(self):
        active, reason = self._sentinel([["x", "bad", "data"], ["y"]])
        self.assertTrue(active, "行情格式异常不可判定 → 保守熔断")
        self.assertIn("不可判定=不放松", reason)

    # ---- 可判定路径语义保留 ----
    def test_plunge_still_triggers(self):
        active, reason = self._sentinel(_plunge_candles())
        self.assertTrue(active)
        self.assertIn("断崖式暴跌", reason)

    def test_calm_market_without_news_is_clear(self):
        active, reason = self._sentinel(_calm_candles())
        self.assertFalse(active, "行情可判定且正常、无新闻数据 → 正常放行（不误伤）")
        self.assertEqual(reason, "")

    def test_news_extreme_score_still_triggers(self):
        with open(self.news_file, "w", encoding="utf-8") as f:
            json.dump({"source_available": True, "overall_score": 12.5}, f)
        active, reason = self._sentinel(_calm_candles())
        self.assertTrue(active)
        self.assertIn("情绪指数", reason)

    def test_news_source_missing_not_faked_neutral(self):
        # harvester 现行载荷：source_available=False、无 overall_score。
        # 旧实现会缺省 50 冒充中性；新实现不得凭空造分（依赖行情路径判定），
        # 且不得抛错。
        with open(self.news_file, "w", encoding="utf-8") as f:
            json.dump({"source_available": False, "macro_sentiment": "🚨 避险熔断中"}, f)
        active, reason = self._sentinel(_calm_candles())
        self.assertFalse(active, "新闻缺失按不可判定跳过，不造假分也不凭空放宽（行情本身可判定）")

    def test_corrupt_news_file_fail_closed(self):
        with open(self.news_file, "w", encoding="utf-8") as f:
            f.write("{not json!!")
        active, reason = self._sentinel(_calm_candles())
        self.assertTrue(active, "情绪文件损坏=不可判定 → 保守熔断（对齐熔断文件损坏语义）")
        self.assertIn("不可判定=不放松", reason)


if __name__ == "__main__":
    unittest.main()

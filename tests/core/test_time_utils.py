"""北京日/时间戳契约（第一百九十八刀）—— 时区与秒·毫秒判据的钱路相邻语义。

| 语义 | 纪律 |
|---|---|
| ★ 秒/毫秒分界 | 唯一常量 `EPOCH_MS_THRESHOLD = 1e11`。**必须是 1e11**：epoch 秒本身已 ~1.79e9，
用 1e9 当分界会把「秒」误判成「毫秒」再除以 1000 ⇒ **「还剩 7 天」被算成「已过期」**（本仓真实踩过）|
| 朴素时间 | `naive` 输入按**北京**解释（`naive_tz` 缺省即 BJ），**不是 UTC**；且**先**补时区**再**换算 |
| 跨日 | 北京时间 00:00–08:00 对应 **UTC 前一日** ⇒ 判「今日」必须用北京日 |
| 脏输入 | `None`/`""`/坏字符串/溢出 ⇒ `None`（不抛），`beijing_day`/`beijing_text` 对应给 `""` |
"""

import re
import unittest
from datetime import datetime, timezone

from r20_backend.time_utils import (BJ_TZ, EPOCH_MS_THRESHOLD, beijing_day, beijing_text,
                                    parse_beijing, to_millis, to_seconds)

_SEVEN_DAYS_MS = 7 * 24 * 3600 * 1000


class ThresholdTest(unittest.TestCase):
    def test_epoch_seconds_are_not_mistaken_for_milliseconds(self):
        """★ 这条就是那个真实事故的回归：秒被当毫秒 ⇒ 7 天后被算成已过期。"""
        now_s = 1_790_000_000                      # ~2026-09，秒
        self.assertEqual(to_seconds(now_s), now_s, "秒必须原样保留")
        self.assertGreater(now_s * 1000, EPOCH_MS_THRESHOLD, "毫秒量级应在阈值之上")
        self.assertLess(now_s, EPOCH_MS_THRESHOLD, "秒量级应在阈值之下")
        # 反证：若阈值误用 1e9，则 to_seconds(now_s) 会变成 now_s/1000 ⇒ 早退约 56 年
        self.assertEqual(to_seconds(now_s * 1000), float(now_s), "毫秒归一成秒")

    def test_threshold_is_the_documented_value(self):
        self.assertEqual(EPOCH_MS_THRESHOLD, 1e11)

    def test_none_passes_through_both_ways(self):
        self.assertIsNone(to_seconds(None))
        self.assertIsNone(to_millis(None))

    def test_to_millis_keeps_milliseconds_and_scales_seconds_and_zero(self):
        self.assertEqual(to_millis(1_790_000_000_000), 1_790_000_000_000)
        self.assertEqual(to_millis(1_790_000_000), 1_790_000_000_000)
        self.assertEqual(to_millis(0), 0, "0 既不是秒也不是毫秒量级 ⇒ 原样")


class ParseBeijingTest(unittest.TestCase):
    def test_missing_and_invalid_are_none(self):
        for value in (None, "", "  ", "不是时间", "2026-13-45 99:99:99", float("nan")):
            with self.subTest(value=value):
                self.assertIsNone(parse_beijing(value))

    def test_naive_input_is_read_as_beijing_not_utc(self):
        """★ 朴素时间按**北京**解释：晚 8 小时才是 UTC，弄反就是把「今天」错一整天。"""
        dt = parse_beijing("2026-09-21 00:30:00")
        self.assertEqual(dt.tzinfo, BJ_TZ)
        self.assertEqual(dt.utcoffset().total_seconds(), 8 * 3600)
        self.assertEqual(beijing_day("2026-09-21 00:30:00"), "2026-09-21")

    def test_utc_cross_day_same_instant_same_beijing_day(self):
        """★ 同一瞬时、两种写法（北京日 vs UTC 前一日）⇒ **必须得到同一个北京日**。"""
        bj_spelling = "2026-09-21 00:30:00 北京时间"
        utc_spelling = "2026-09-20 16:30:00 UTC"
        self.assertEqual(parse_beijing(bj_spelling), parse_beijing(utc_spelling))
        self.assertEqual(beijing_day(bj_spelling), beijing_day(utc_spelling))
        self.assertEqual(beijing_day(utc_spelling), "2026-09-21",
                         "UTC 20 日 16:30 在北京已是 21 日 ⇒ 判今日不能取 UTC 日")
        self.assertEqual(parse_beijing("2026-09-20T16:30:00Z"),
                         parse_beijing(utc_spelling), "Z 后缀同样按 UTC 解析")

    def test_epoch_seconds_and_millis_agree_on_the_same_instant(self):
        sec = 1_790_000_000
        self.assertEqual(parse_beijing(sec), parse_beijing(sec * 1000),
                         "秒与毫秒必须落到同一时刻（分界判错就会差 1000 倍）")
        self.assertEqual(parse_beijing(str(sec)), parse_beijing(sec), "数值字符串同义")

    def test_aware_datetime_is_converted_not_relabelled(self):
        dt = datetime(2026, 9, 20, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(beijing_day(dt), "2026-09-21")
        self.assertEqual(parse_beijing(dt), parse_beijing("2026-09-20 16:30:00 UTC"))


class DisplayTest(unittest.TestCase):
    def test_text_and_day_formatting(self):
        self.assertEqual(beijing_text("2026-09-21 00:30:00"), "2026-09-21 00:30:00")
        self.assertEqual(beijing_day("2026-09-21 00:30:00"), "2026-09-21")
        self.assertEqual(beijing_text(None), "", "读不到 ⇒ 空串（不是假时间、也不是异常）")
        self.assertEqual(beijing_day(None), "")
        self.assertTrue(re.match(r"^\d{4}-\d{2}-\d{2}$", beijing_day("2026-09-21")))


if __name__ == "__main__":
    unittest.main()

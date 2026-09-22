"""降级路径的**时钟与时间语义**（第二百三十九刀）—— 把上一刀的静态发现升级为断言。

第 197 刀之后，失败周期不再用 0 覆盖上次成功载荷，而是走两支：

| 分支 | 触发 | 要点 |
|---|---|---|
| **陈旧** | 核心查询失败但**上次载荷还有意义** | 拷贝旧载荷 ⇒ `is_stale=True`；`last_success_at` 是**旧载荷的时间**、`attempted_at` 是**本次**；`cache_age_seconds` 用**更新前**的时钟算（即**数据**的年龄）|
| **诚实骨架** | 失败且**没有可用缓存** | `account`/`today_stats`/`performance` 给**空对象**（不是 0 填充）；`max_positions` 取 `len(load_instruments())` |

★ 两处**容易被误解的时钟行为**（本刀断言）：

1. **两支都会刷新 `LAST_CACHE_TIME`** ⇒ 交易所一直读失败时，TTL 仍然生效，
   3 秒内**不会重复打交易所**（把"失败"也当作一次周期完成）；
2. `cache_age_seconds` 用**刷新前**的时钟计算 ⇒ 它报告的是**数据**有多旧，而不是"距上次尝试多久"；
   且 `LAST_CACHE_TIME == 0`（从未成功过）⇒ **`None`**，**不填 0**（不可判定 ≠ 0 秒）。
"""

import time
import unittest
from unittest import mock

from r20_backend import dashboard_cache as DC


def _core_tuple(*, positions_ok=True, balance_ok=True, not_ready=False):
    return (not_ready, 100.0, balance_ok, 100.0, 1, [], [],
            [{"instId": "BTC"}], positions_ok, 0, 100.0, 0.0, {}, 0.0)


class StalePathClockTest(unittest.TestCase):
    def setUp(self):
        self.injected = []
        self.enriched = []
        self.old_cache = DC.CACHE_DATA
        self.old_clock = DC.LAST_CACHE_TIME
        self.addCleanup(self._restore)

    def _restore(self):
        DC.CACHE_DATA, DC.LAST_CACHE_TIME = self.old_cache, self.old_clock

    def _run(self, *, meaningful=True, not_ready=False, core=None, clock=None,
             cache=None):
        DC.CACHE_DATA = cache if cache is not None else {
            "timestamp": "2026-09-21 10:00:00 (北京时间)", "keep": "me",
            "positions_summary": {"items": []}}
        DC.LAST_CACHE_TIME = clock if clock is not None else 0
        patches = {
            "collect_core_account_state": mock.Mock(
                return_value=core or _core_tuple(positions_ok=False, not_ready=not_ready)),
            "_is_meaningful_dashboard_snapshot": mock.Mock(return_value=meaningful),
            "enrich_position_risk_fields": mock.Mock(
                side_effect=lambda rows, trackers: self.enriched.append(list(rows))),
            "_inject_local_data_into_stale": mock.Mock(
                side_effect=lambda stale, rows, ts: self.injected.append((stale, ts))),
            "load_instruments": mock.Mock(return_value=[{"instId": "A"}, {"instId": "B"}]),
        }
        for name, stub in patches.items():
            p = mock.patch.object(DC, name, stub)
            p.start()
            self.addCleanup(p.stop)
        DC.update_cache_cycle()
        return DC.CACHE_DATA

    def test_stale_branch_marks_stale_and_keeps_the_old_keys(self):
        out = self._run()
        self.assertIs(out["is_stale"], True, "第 197 刀：拷贝会带 is_stale=False ⇒ 必须显式改真")
        self.assertEqual(out["keep"], "me", "旧载荷的其它键保留")
        self.assertEqual(out["data_health"]["status"], "STALE")
        self.assertIsNone(out["data_health"]["message"], "非 NOT_READY 时不给提示语")

    def test_stale_branch_refreshes_the_clock_so_failures_do_not_hammer(self):
        """★ 失败也刷新 `LAST_CACHE_TIME` ⇒ TTL 生效，3 秒内不重复打交易所。"""
        before = time.time()
        self._run(clock=0)
        self.assertGreaterEqual(DC.LAST_CACHE_TIME, before, "陈旧分支必须刷新时钟")

    def test_stale_branch_measures_the_age_of_the_data_not_of_the_attempt(self):
        """★ `cache_age_seconds` 用**刷新前**的时钟 ⇒ 报告**数据**年龄。"""
        out = self._run(clock=time.time() - 10.0)
        age = out["data_health"]["cache_age_seconds"]
        self.assertGreaterEqual(age, 9.5)
        self.assertLessEqual(age, 10.5)

    def test_never_succeeded_means_unknown_age_not_zero(self):
        """★ 从未成功过（时钟为 0）⇒ `cache_age_seconds` 为 **None**，不填 0。"""
        out = self._run(clock=0)
        self.assertIsNone(out["data_health"]["cache_age_seconds"],
                          "不可判定 ⇒ None（0 会被读成「刚刚还在」）")

    def test_last_success_and_attempted_at_are_distinguishable(self):
        out = self._run(clock=time.time() - 5.0)
        health = out["data_health"]
        self.assertEqual(health["last_success_at"], "2026-09-21 10:00:00 (北京时间)")
        self.assertNotEqual(health["attempted_at"], health["last_success_at"])
        self.assertTrue(health["attempted_at"].endswith("(北京时间)"))

    def test_not_ready_variant_carries_the_human_message(self):
        out = self._run(not_ready=True)
        self.assertEqual(out["data_health"]["status"], "NOT_READY")
        self.assertEqual(out["data_health"]["message"], DC._NOT_READY_TEXT)

    def test_local_data_is_still_injected_into_the_stale_payload(self):
        """★ 陈旧 ≠ 停止更新：只依赖本地文件的那部分（因子库/新闻/日志）仍要刷新。"""
        out = self._run()
        self.assertEqual(len(self.injected), 1, "本地注入必须发生")
        stale_arg, ts_arg = self.injected[0]
        self.assertIs(stale_arg, out, "注入的就是最终要交给前端的那个载荷")
        # ⚠️ 我原以为传进去的是载荷里的 timestamp —— 错：陈旧载荷的 `timestamp` 是**旧载荷**的
        # （这正是 last_success_at/attempted_at 要区分的），传进去的是**本次**周期时间。
        self.assertEqual(ts_arg, out["data_health"]["attempted_at"])
        self.assertNotEqual(ts_arg, out["timestamp"])


class SkeletonPathTest(unittest.TestCase):
    def setUp(self):
        self.old_cache = DC.CACHE_DATA
        self.old_clock = DC.LAST_CACHE_TIME
        self.addCleanup(self._restore)

    def _restore(self):
        DC.CACHE_DATA, DC.LAST_CACHE_TIME = self.old_cache, self.old_clock

    def test_skeleton_uses_empty_objects_instead_of_zero_filled_numbers(self):
        DC.CACHE_DATA = {}
        DC.LAST_CACHE_TIME = 0
        for name, stub in {
            "collect_core_account_state": mock.Mock(
                return_value=_core_tuple(positions_ok=False)),
            "_is_meaningful_dashboard_snapshot": mock.Mock(return_value=False),
            "load_instruments": mock.Mock(return_value=[{"instId": "A"}]),
        }.items():
            p = mock.patch.object(DC, name, stub)
            p.start()
            self.addCleanup(p.stop)
        DC.update_cache_cycle()
        # ⚠️ 该函数**没有返回值**（把结果放进模块级 `CACHE_DATA`）—— 我第一版去接返回值而红。
        out = DC.CACHE_DATA
        self.assertEqual(out["data_health"]["status"], "OFFLINE")
        self.assertEqual(out["account"], {}, "空对象，不是 0 填充")
        self.assertEqual(out["today_stats"], {})
        self.assertEqual(out["positions_summary"]["items"], [])
        self.assertEqual(out["positions_summary"]["max_positions"], 1)
        self.assertGreater(DC.LAST_CACHE_TIME, 0, "骨架分支同样刷新时钟")


if __name__ == "__main__":
    unittest.main()

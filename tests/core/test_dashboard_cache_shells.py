"""看板门面薄壳的**路径注入契约**（第一百九十一刀）。

`dashboard_cache.py` 的每个薄壳都只做一件事：把**模块级路径常量**（或整套依赖束）传给
`dashboard_payload/*` 的实现。这条契约值得钉住，因为它是「**路径只有一个来源**」的落点 ——
实现不在别处重新猜路径，看板与运维看到的就是同一份文件。

`_inject_local_data_into_stale` 尤其重要：它是**接线点**，一次性把七个路径常量与六个函数注入
下游；注入错一个，看板就会静默读错文件（而不是报错）。
"""

import asyncio
import unittest
from unittest.mock import patch

import r20_backend.dashboard_cache as dc


class PathInjectionTest(unittest.TestCase):
    def test_position_trackers(self):
        with patch.object(dc, "_core_load_position_trackers", return_value=[]) as m:
            dc.load_position_trackers()
        self.assertEqual(m.call_args.args, (dc.POSITION_TRACKER_FILE,))

    def test_enrich_position_risk_fields_passes_positions_and_trackers(self):
        with patch.object(dc, "_core_enrich_position_risk_fields", return_value=[]) as m:
            dc.enrich_position_risk_fields(["p"], ["t"])
        self.assertEqual(m.call_args.args, (dc.POSITION_TRACKER_FILE, ["p"], ["t"]),
                         "路径在前，业务参数照原样透传")

    def test_declared_optional_default_is_preserved(self):
        with patch.object(dc, "_core_enrich_position_risk_fields", return_value=[]) as m:
            dc.enrich_position_risk_fields(["p"])
        self.assertEqual(m.call_args.args, (dc.POSITION_TRACKER_FILE, ["p"], None),
                         "缺省 trackers ⇒ 传 None，不编造空列表")

    def test_factor_library_and_factors(self):
        with patch.object(dc, "_core__load_local_factor_library", return_value={}) as m:
            dc._load_local_factor_library()
        self.assertEqual(m.call_args.args, (dc.FACTOR_LIBRARY_FILE,))
        with patch.object(dc, "_core__build_factors_from_local_files", return_value={}) as m2:
            dc._build_factors_from_local_files(["p"], "2026-09-21 12:00:00")
        self.assertEqual(m2.call_args.args,
                         (dc.FACTOR_LIBRARY_FILE, dc.AI_DECISIONS_FILE, dc.STATE_JSON_FILE,
                          ["p"], "2026-09-21 12:00:00"),
                         "三个路径常量 + 业务参数，顺序即契约")

    def test_ai_health_and_persist(self):
        with patch.object(dc, "_core_build_ai_health", return_value={}) as m:
            dc.build_ai_health(["h"])
        self.assertEqual(m.call_args.args, (dc.DATA_DIR, ["h"]))
        with patch.object(dc, "_core_persist_dashboard_cache", return_value=True) as m2:
            dc.persist_dashboard_cache({"k": 1})
        self.assertEqual(m2.call_args.args, (dc.DASHBOARD_CACHE_FILE, dc.DATA_DIR, {"k": 1}))

    def test_injection_bundle_carries_all_paths_and_functions(self):
        """★ **接线点**：六个函数 + 六个路径常量 + 三个业务参数一次注入；漏/换一个，
        看板会**静默读错文件**（不是报错）—— 故顺序即契约。"""
        with patch.object(dc, "_core__inject_local_data_into_stale", return_value={}) as m:
            dc._inject_local_data_into_stale("stale", ["p"], "ts")
        args = m.call_args.args
        self.assertEqual(len(args), 15, "6 函数 + 6 路径 + 3 业务参数")
        self.assertEqual(args[0:6], (dc._load_local_factor_library, dc._load_cross_venue_data,
                                     dc._load_portfolio_risk_data,
                                     dc._build_factors_from_local_files, dc.build_ai_health,
                                     dc.load_trading_memory_md), "先注入函数")
        self.assertEqual(args[6:12], (dc.NEWS_SENTIMENT_FILE, dc.AI_HISTORY_FILE,
                                      dc.REPORT_JSON_FILE, dc.AI_LAST_PROMPT_FILE,
                                      dc.LOG_FILE, dc.LEDGER_JSON_FILE), "再注入路径")
        self.assertEqual(args[12:], ("stale", ["p"], "ts"), "最后是业务参数")


class CacheLockTest(unittest.TestCase):
    def test_lock_is_a_singleton(self):
        with patch.object(dc, "CACHE_LOCK", None):
            first = dc.get_cache_lock()
            self.assertIsInstance(first, asyncio.Lock)
            self.assertIs(first, dc.get_cache_lock(), "同一把锁（否则看板并发写会撕裂缓存）")


if __name__ == "__main__":
    unittest.main()

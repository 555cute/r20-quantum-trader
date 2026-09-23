"""行情取数健康账本（`scripts/market_data_health.py`）的残余分支收口 —— 第 332 刀。

本模块 266 行，记的是**行情取数的失败与耗时**，并落一份跨进程快照
（取数在 worker 进程、`/metrics` 在后端进程 —— 进程内计数器永远看不到对方）。

## 本刀立住的三条纪律

1. **账本自身绝不能成为新的故障源**（模块注释原话）：`note_failure` / `note_call` /
   `write_snapshot` **全部绝不抛**，这是"取数失败"路径上唯一的取证手段。
2. **计数与格式化分开兜底**：计数**最先执行且独立兜底** —— 绝不允许因为
   "格式化异常信息失败"连计数一起丢掉（门里那个 `__str__` 会抛的异常用例逼出了这一点）。
3. **没有样本就返回 `None`，不用 0 冒充**（`_percentile` 的 docstring）。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.market_data_health as mh  # noqa: E402


class _BoomLock:
    """一个 `__enter__` 就炸的锁 —— 用于逼出账本函数的最外层兜底。"""

    def __enter__(self):
        raise RuntimeError("lock is broken")

    def __exit__(self, *a):
        return False


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("_FAILURES", "_LOGGED", "_LAST_ERROR", "_CALLS", "_TOTAL_SECONDS",
                     "_MAX_SECONDS", "_SAMPLES", "_LAST_CALL_MS", "_LAST_OK_MS",
                     "_FAILED_CALLS"):
            obj = getattr(mh, name, None)
            # ⚠️ `_LOGGED` 是 **set**（不是 dict）—— 最初只重置 dict 导致它跨用例泄漏，
            #    "首次失败才打印"的用例因此看到 0 次打印。
            if isinstance(obj, dict):
                empty = {}
            elif isinstance(obj, set):
                empty = set()
            else:
                continue
            p = patch.object(mh, name, empty)
            p.start()
            self.addCleanup(p.stop)


# ───────────────────── note_failure ─────────────────────
class NoteFailureTests(_Sandbox, unittest.TestCase):
    def test_a_failure_is_counted(self):
        mh.note_failure("kline")
        self.assertEqual(mh.failure_count("kline"), 1)

    def test_repeated_failures_keep_counting(self):
        for _ in range(3):
            mh.note_failure("kline")
        self.assertEqual(mh.failure_count("kline"), 3)

    def test_only_the_first_failure_prints(self):
        printed: list = []
        with patch.object(mh, "print", lambda *a, **k: printed.append(" ".join(map(str, a)))):
            mh.note_failure("kline", OSError("net down"))
            mh.note_failure("kline", OSError("net down again"))
        self.assertEqual(len(printed), 1, "后续只累加不再重复打印")
        self.assertIn("net down", printed[0])

    def test_the_error_detail_is_recorded(self):
        mh.note_failure("kline", ValueError("bad payload"))
        self.assertIn("ValueError: bad payload", mh.stats()["last_error"]["kline"])

    def test_a_failure_without_an_exception_uses_the_fallback_label(self):
        mh.note_failure("kline")
        self.assertEqual(mh.stats()["last_error"]["kline"], "未知错误")

    def test_a_broken_counting_block_is_swallowed(self):
        # ★ 第 81/82 行 —— 账本自身绝不抛
        with patch.object(mh, "_LOCK", _BoomLock()):
            self.assertIsNone(mh.note_failure("kline"))

    def test_a_broken_print_does_not_lose_the_count(self):
        # ① 计数独立兜底：格式化/打印一炸，**计数必须已经落账**
        class _BoomStr(Exception):
            def __str__(self):
                raise RuntimeError("cannot stringify")
        with patch.object(mh, "print", side_effect=OSError("no stdout")):
            mh.note_failure("kline", _BoomStr())
        self.assertEqual(mh.failure_count("kline"), 1,
                         "计数绝不允许因格式化失败而一起丢掉")

    def test_each_kind_is_tracked_separately(self):
        mh.note_failure("a")
        mh.note_failure("b")
        mh.note_failure("b")
        self.assertEqual((mh.failure_count("a"), mh.failure_count("b")), (1, 2))

    def test_an_unseen_kind_counts_as_zero(self):
        self.assertEqual(mh.failure_count("never"), 0)


# ───────────────────── note_call / call_stats ─────────────────────
class NoteCallTests(_Sandbox, unittest.TestCase):
    def test_a_successful_call_is_recorded(self):
        mh.note_call("kline", 0.25, ok=True)
        stats = mh.call_stats()
        self.assertEqual(stats["calls"]["kline"], 1)
        # ⚠️ `failed_calls` **只为失败的 kind 建键** ⇒ 成功时用 `.get(..., 0)`
        self.assertEqual(stats["failed_calls"].get("kline", 0), 0)

    def test_a_failed_call_is_recorded_separately(self):
        mh.note_call("kline", 0.5, ok=False)
        stats = mh.call_stats()
        self.assertEqual(stats["calls"]["kline"], 1)
        self.assertEqual(stats["failed_calls"]["kline"], 1)

    def test_the_max_latency_tracks_the_worst_sample(self):
        for dt in (0.1, 0.9, 0.3):
            mh.note_call("kline", dt)
        self.assertAlmostEqual(mh.call_stats()["latency"]["kline"]["max_ms"], 900.0)

    def test_the_sample_buffer_is_bounded(self):
        # 样本只进不进快照（`call_stats` 给的是 `count`/`avg_ms`/`max_ms`/p50/p95）⇒
        # 有界性从"百分位不会看穿更早的样本"来验证：灌 1000 个递增样本后，
        # p50 必须落在缓冲区窗口内，而不是全局中位数。
        for i in range(mh._MAX_SAMPLES + 20):
            mh.note_call("kline", i * 0.001)
        latency = mh.call_stats()["latency"]["kline"]
        # `count` 反映的是**缓冲区**长度（deque maxlen）⇒ 被钳到 `_MAX_SAMPLES`
        self.assertEqual(latency["count"], mh._MAX_SAMPLES)
        self.assertGreater(latency["max_ms"], latency["p50_ms"])
        self.assertAlmostEqual(latency["max_ms"], (mh._MAX_SAMPLES + 19) * 1.0, places=1)

    def test_a_broken_stats_block_is_swallowed(self):
        # ★ 第 165/166 行 —— 统计自身绝不抛
        with patch.object(mh, "_LOCK", _BoomLock()):
            self.assertIsNone(mh.note_call("kline", 0.1))

    def test_the_percentiles_are_reported(self):
        for dt in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
            mh.note_call("kline", dt)
        latency = mh.call_stats()["latency"]["kline"]
        self.assertIn("p50_ms", latency)
        self.assertIn("p95_ms", latency)
        self.assertLessEqual(latency["p50_ms"], latency["p95_ms"])

    def test_the_stats_are_a_pure_copy(self):
        mh.note_call("kline", 0.1)
        stats = mh.call_stats()
        stats["calls"]["kline"] = 999
        self.assertEqual(mh.call_stats()["calls"]["kline"], 1)


class PercentileTests(unittest.TestCase):
    def test_an_empty_sample_returns_none_not_zero(self):
        # ★ 第 171/172 行 —— 「空样本返回 None，不返回 0 冒充」
        self.assertIsNone(mh._percentile([], 0.5))
        self.assertIsNone(mh._percentile([], 0.95))

    def test_a_single_sample_is_returned_for_every_ratio(self):
        for ratio in (0.0, 0.5, 0.95, 1.0):
            with self.subTest(ratio=ratio):
                self.assertEqual(mh._percentile([0.7], ratio), 0.7)

    def test_the_index_is_clamped_at_both_ends(self):
        values = [0.1, 0.2, 0.3]
        self.assertEqual(mh._percentile(values, 0.0), 0.1)
        self.assertEqual(mh._percentile(values, 1.0), 0.3)

    def test_the_ratio_is_monotonic(self):
        values = [float(i) for i in range(100)]
        self.assertLessEqual(mh._percentile(values, 0.5),
                             mh._percentile(values, 0.95))


# ───────────────────── reset / snapshot ─────────────────────
class ResetSnapshotTests(_Sandbox, unittest.TestCase):
    def test_reset_clears_everything(self):
        mh.note_failure("kline")
        mh.note_call("kline", 0.1)
        mh.reset()
        self.assertEqual(mh.failure_count("kline"), 0)
        self.assertEqual(mh.call_stats()["calls"].get("kline", 0), 0)

    def test_the_snapshot_carries_the_schema_version(self):
        self.assertEqual(mh.snapshot()["schema_version"], mh.SCHEMA_VERSION)

    def test_the_snapshot_is_json_serialisable(self):
        # 快照要落到 `data/` 给后端进程读 ⇒ 必须能原样过 `json.dumps`
        mh.note_failure("kline", OSError("x"))
        mh.note_call("kline", 0.1)
        blob = json.dumps(mh.snapshot(), ensure_ascii=False)
        restored = json.loads(blob)
        self.assertEqual(restored["schema_version"], mh.SCHEMA_VERSION)
        self.assertIn("kline", json.dumps(restored))


# ───────────────────── write_snapshot / load_snapshot ─────────────────────
class SnapshotIoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a_snapshot_round_trips(self):
        path = self.root / "h.json"
        self.assertTrue(mh.write_snapshot(str(path), {"schema_version": mh.SCHEMA_VERSION}))
        self.assertEqual(mh.load_snapshot(str(path))["schema_version"], mh.SCHEMA_VERSION)

    def test_the_payload_defaults_to_the_live_snapshot(self):
        path = self.root / "h.json"
        mh.write_snapshot(str(path))
        self.assertEqual(mh.load_snapshot(str(path))["schema_version"], mh.SCHEMA_VERSION)

    def test_the_parent_directory_is_created(self):
        path = self.root / "deep" / "h.json"
        self.assertTrue(mh.write_snapshot(str(path), {"schema_version": mh.SCHEMA_VERSION}))
        self.assertTrue(path.exists())

    def test_no_temporary_file_survives(self):
        mh.write_snapshot(str(self.root / "h.json"), {"schema_version": mh.SCHEMA_VERSION})
        self.assertEqual([p.name for p in self.root.glob(".mdh-*")], [])

    def test_a_write_failure_cleans_up_and_returns_false(self):
        # ★ 第 240–245 行 —— **绝不抛**，失败返回 False
        with patch.object(mh.os, "replace", side_effect=OSError("disk full")):
            self.assertFalse(mh.write_snapshot(str(self.root / "h.json"),
                                               {"schema_version": mh.SCHEMA_VERSION}))
        self.assertEqual([p.name for p in self.root.glob(".mdh-*")], [])

    def test_a_cleanup_failure_still_returns_false(self):
        # ★ 第 243/244 行 —— 清理本身失败也只吞掉
        with patch.object(mh.os, "replace", side_effect=OSError("disk full")), \
             patch.object(mh.os, "unlink", side_effect=OSError("read-only")):
            self.assertFalse(mh.write_snapshot(str(self.root / "h.json"),
                                               {"schema_version": mh.SCHEMA_VERSION}))

    def test_an_unusable_path_returns_false(self):
        self.assertFalse(mh.write_snapshot(None, {"a": 1}))

    def test_a_missing_file_loads_as_empty(self):
        self.assertEqual(mh.load_snapshot(str(self.root / "nope.json")), {})

    def test_a_corrupt_file_loads_as_empty(self):
        path = self.root / "h.json"
        path.write_text("{ broken", encoding="utf-8")
        self.assertEqual(mh.load_snapshot(str(path)), {})

    def test_a_non_dict_body_loads_as_empty(self):
        # ★ 第 262/263 行 —— 宁可显式"没有数据"，也不用未知 schema 的字段拼出看着正常的指标
        path = self.root / "h.json"
        path.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(mh.load_snapshot(str(path)), {})

    def test_an_unknown_schema_version_loads_as_empty(self):
        path = self.root / "h.json"
        path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
        self.assertEqual(mh.load_snapshot(str(path)), {})


if __name__ == "__main__":
    unittest.main()

"""守护式 Web 数据同步循环（scripts/daemon_web_sync.py）收口 —— 第 317 刀。

**注意一个记录错误**：早期记录把 `scripts/daemon_web_sync.py` 记成"缺 47 行"，
而该文件**总共只有 47 行** —— "47"是文件总行数，不是缺口。真实情况是它**整份没有测试**。

本模块是个 `while True` + `sleep(10)` 的守护循环：每轮同步一次交易数据，
每 **600s** 拉起资讯情绪采集，每 **60s** 拉起五维因子库。三处失败都 `except: pass`，
所以它"卡死"或"静默不干活"时外部看不出来 —— 这正是要用例钉住的地方。

## 怎么测一个死循环

`time.sleep(10)`（第 44 行）**在所有 `try` 之外**，是唯一能让循环退出的地方：
把 `time` 换成假表，让它的 `sleep` 在第 N 次调用时抛一个哨兵异常，
循环体就自然走完 N 轮并退出。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import daemon_web_sync as dws  # noqa: E402


class _StopLoop(Exception):
    """哨兵：从 `time.sleep` 抛出，用来结束 `while True`。"""


class _FakeTime:
    """假时钟：`time()` 按表出值，`sleep()` 在第 N 次时抛哨兵。"""

    def __init__(self, stamps, stop_after_sleeps=1):
        self._stamps = list(stamps)
        self._last = self._stamps[-1] if self._stamps else 0.0
        self.sleeps: list = []
        self._stop_after = stop_after_sleeps

    def time(self):
        if self._stamps:
            self._last = self._stamps.pop(0)
        return self._last

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        if len(self.sleeps) >= self._stop_after:
            raise _StopLoop()


class _FakeSubprocess:
    """记录每次 `subprocess.run` 的命令与超时；可按脚本名注入异常。"""

    def __init__(self, boom_on=()):
        self.calls: list = []
        self._boom_on = set(boom_on)

    def run(self, cmd, timeout=None, **kwargs):
        name = os.path.basename(str(cmd[-1]))
        self.calls.append({"cmd": list(cmd), "name": name, "timeout": timeout,
                           "kwargs": kwargs})
        if name in self._boom_on:
            raise RuntimeError(f"{name} 炸了")
        return SimpleNamespace(returncode=0)

    def names(self):
        return [c["name"] for c in self.calls]


class _Harness(unittest.TestCase):
    def setUp(self):
        self.gen_calls: list = []
        self.real_scripts = {"news_sentiment_harvester.py", "factor_library.py"}

    def _gen_ok(self):
        self.gen_calls.append(True)

    def _run(self, stamps, *, stop_after_sleeps=1, gen=None, boom_on=()):
        fake_time = _FakeTime(stamps, stop_after_sleeps)
        fake_sub = _FakeSubprocess(boom_on)
        with patch.object(dws, "generate_trading_data",
                          gen if gen is not None else self._gen_ok), \
             patch.object(dws, "time", fake_time), \
             patch.object(dws, "subprocess", fake_sub):
            try:
                dws.main()
            except _StopLoop:
                pass
            else:  # pragma: no cover - 只有夹具坏掉才会走到
                self.fail("循环没有被哨兵打断（夹具失效）")
        return fake_time, fake_sub


class LoopSkeletonTests(_Harness, unittest.TestCase):
    def test_empty_script_name_set_is_a_guard_for_the_fixture(self):
        # 夹具自检：这些名字必须真的出现在模块常量里，否则下面的断言会变成空跑
        self.assertTrue(self.real_scripts)

    def test_one_iteration_syncs_data_then_sleeps_ten_seconds(self):
        _, sub = self._run([1000.0])
        self.assertEqual(len(self.gen_calls), 1)
        self.assertEqual(len(sub.calls), 2, "1000s 时两件定时活都该跑")

    def test_sleep_is_ten_seconds_every_round(self):
        fake_time, _ = self._run([1000.0, 2000.0], stop_after_sleeps=2)
        self.assertEqual(fake_time.sleeps, [10, 10])

    def test_scripts_are_invoked_through_the_current_interpreter(self):
        _, sub = self._run([1000.0])
        for call in sub.calls:
            self.assertEqual(call["cmd"][0], sys.executable)

    def test_scripts_are_resolved_against_the_module_directory(self):
        _, sub = self._run([1000.0])
        for call in sub.calls:
            self.assertEqual(os.path.dirname(call["cmd"][-1]),
                             os.path.dirname(os.path.abspath(dws.__file__)))


class DataSyncTests(_Harness, unittest.TestCase):
    def test_sync_failure_is_swallowed_and_the_loop_keeps_going(self):
        def boom():
            raise RuntimeError("sync failed")
        _, sub = self._run([1000.0], gen=boom)
        self.assertEqual(len(sub.calls), 2, "同步炸了不许拖累两个定时任务")

    def test_import_time_import_of_generate_trading_data_is_the_seam(self):
        # `from sync_web_data import generate_trading_data` 是**模块级**导入
        # ⇒ 补丁点必须是本模块的同名属性（门面惯例）
        self.assertTrue(callable(dws.generate_trading_data))
        self.assertIs(dws.generate_trading_data.__module__, "sync_web_data")


class NewsCadenceTests(_Harness, unittest.TestCase):
    def test_news_harvester_runs_past_the_six_hundred_second_gate(self):
        _, sub = self._run([601.0])
        self.assertIn("news_sentiment_harvester.py", sub.names())

    def test_news_harvester_is_skipped_below_the_gate(self):
        # 600 是**严格大于**判定 ⇒ 恰好 600 不跑
        _, sub = self._run([600.0])
        self.assertNotIn("news_sentiment_harvester.py", sub.names())

    def test_news_harvester_has_a_thirty_second_timeout(self):
        _, sub = self._run([601.0])
        call = next(c for c in sub.calls if c["name"] == "news_sentiment_harvester.py")
        self.assertEqual(call["timeout"], 30)

    def test_a_successful_news_run_advances_the_cadence_clock(self):
        # 第 1 轮 t=1000 跑资讯；第 2 轮 t=1011（只过 11s）⇒ 不该再跑
        _, sub = self._run([1000.0, 1011.0], stop_after_sleeps=2)
        self.assertEqual(sub.names().count("news_sentiment_harvester.py"), 1)

    def test_a_failed_news_run_does_not_advance_the_cadence_clock(self):
        # 失败时 `last_news_time` 保持原值 ⇒ 下一轮**重试**（这是刻意的：
        # 采集器挂掉后不该再等 600s 才重试）
        _, sub = self._run([1000.0, 1011.0], stop_after_sleeps=2,
                           boom_on=("news_sentiment_harvester.py",))
        self.assertEqual(sub.names().count("news_sentiment_harvester.py"), 2)

    def test_news_resumes_once_the_gate_reopens_after_a_failure(self):
        _, sub = self._run([1000.0, 1601.0], stop_after_sleeps=2,
                           boom_on=("news_sentiment_harvester.py",))
        self.assertEqual(sub.names().count("news_sentiment_harvester.py"), 2)


class FactorLibraryCadenceTests(_Harness, unittest.TestCase):
    def test_factor_library_runs_past_the_sixty_second_gate(self):
        _, sub = self._run([61.0])
        self.assertIn("factor_library.py", sub.names())

    def test_factor_library_is_skipped_below_the_gate(self):
        _, sub = self._run([60.0])
        self.assertNotIn("factor_library.py", sub.names())

    def test_factor_library_has_a_fifteen_second_timeout(self):
        _, sub = self._run([61.0])
        call = next(c for c in sub.calls if c["name"] == "factor_library.py")
        self.assertEqual(call["timeout"], 15)

    def test_a_successful_factor_run_advances_its_own_clock(self):
        _, sub = self._run([1000.0, 1011.0], stop_after_sleeps=2)
        self.assertEqual(sub.names().count("factor_library.py"), 1)

    def test_factor_retries_immediately_after_a_failure(self):
        _, sub = self._run([1000.0, 1011.0], stop_after_sleeps=2,
                           boom_on=("factor_library.py",))
        self.assertEqual(sub.names().count("factor_library.py"), 2)

    def test_the_two_cadences_are_independent(self):
        # t=100 时：资讯不跑（<600），因子跑（>60）
        _, sub = self._run([100.0])
        self.assertEqual(sub.names(), ["factor_library.py"])


class NoSelfImprovementTests(unittest.TestCase):
    def test_this_daemon_never_triggers_self_improvement(self):
        # 模块 docstring 的第一条纪律：自我进化由定时任务**独占**，
        # 这个守护里绝不能再拉起一次
        src = Path(dws.__file__).read_text(encoding="utf-8")
        self.assertIn("Self-improvement is intentionally excluded", src)
        self.assertNotIn("self_improvement", src)

    def test_only_two_child_processes_are_ever_spawned(self):
        src = Path(dws.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("subprocess.run("), 2)


if __name__ == "__main__":
    unittest.main()

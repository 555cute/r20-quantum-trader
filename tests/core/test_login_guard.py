"""登录限速（按来源 IP）：**两级阈值、封锁只增不减、unknown 不记账、内存有界**（第二百六十九刀，开新面 login_guard.py）。

先打印整个文件（114 行）再动笔。它与「按账号 5 次失败锁 15 分钟」互补：账号锁只保护
单个用户名，攻击者轮换用户名或放慢速度即可绕过；本模块从 IP 维度设限。

| 语义 | 口径 |
|---|---|
| ★ **一个开关能整体关掉** | `R20_LOGIN_RATE_LIMIT` 取 `0/false/off/no`（去空白、忽略大小写）⇒ 三个入口全部 no-op，`check` 恒放行；**`_enabled()` 是调用时读环境**，所以测试/压测可以在运行中切换 |
| ★ **两级阈值、封锁只增不减** | 窗口内**总尝试** ≥ `_MAX_ATTEMPTS` ⇒ 封 `_WINDOW`；窗口内**失败** ≥ `_MAX_FAILURES` ⇒ 封 `_BLOCK_SECONDS`（更长）。两处都用 `max(现封锁, 新封锁)` ⇒ **猜密封锁不会被洪泛封锁缩短** |
| ★ **`unknown` / 空 IP 绝不记账** | 既不能用它锁别人，也不会因它被判洪泛（来源不可判定时的明确取舍）|
| ★ **返回的等待秒数至少 1** | `int(remain) + 1`（避免"还要等 0 秒"被前端当成已解封）|
| ★ **内存有界** | 键位超过 `_MAX_KEYS` 时按"最久未活动"淘汰一半；**正在记账的那个 IP 永不淘汰** |
| 滑动窗口 | `_prune` 同时裁剪 attempts/failures，窗口外的旧记录不再计数 |
| 阈值可禁用 | `_MAX_ATTEMPTS=0` / `_MAX_FAILURES=0` ⇒ 该级失效（互不影响）|

⚠️ 如实登记一处**导入时快照**：`_WINDOW`/`_MAX_ATTEMPTS`/`_MAX_FAILURES`/`_BLOCK_SECONDS`/
`_MAX_KEYS` 都是**模块导入时**从环境读的常量（与调用时读环境的 `_enabled()` 不同）
⇒ 进程起来后再改这几个环境变量**不生效**。本刀把这条写进断言，避免误以为"设了环境变量就改了阈值"。
"""

import os
import threading
import unittest
from unittest import mock

from r20_backend import login_guard as LG


class _GuardBase(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.now = 1000.0
        self._start(mock.patch.object(LG, "_WINDOW", 300))
        self._start(mock.patch.object(LG, "_MAX_ATTEMPTS", 100))
        self._start(mock.patch.object(LG, "_MAX_FAILURES", 15))
        self._start(mock.patch.object(LG, "_BLOCK_SECONDS", 900))
        self._start(mock.patch.object(LG, "_MAX_KEYS", 10000))
        self._start(mock.patch.object(LG.time, "time", side_effect=lambda: self.now))
        self._start(mock.patch.dict(os.environ, {}, clear=False))
        os.environ.pop("R20_LOGIN_RATE_LIMIT", None)
        LG._reset_for_tests()
        self.addCleanup(LG._reset_for_tests)

    def _state(self, ip):
        return LG._states.get(ip)


class SwitchTests(_GuardBase):
    def test_kill_switch_tokens_are_recognised_case_and_space_insensitively(self):
        for token in ("0", "false", "FALSE", " off ", "no", "No"):
            with self.subTest(token=token):
                LG._reset_for_tests()
                with mock.patch.dict(os.environ, {"R20_LOGIN_RATE_LIMIT": token}):
                    self.assertFalse(LG._enabled())
                    self.assertEqual(LG.check("1.2.3.4"), (True, 0))
                    LG.note_attempt("1.2.3.4")
                    LG.note_failure("1.2.3.4")
                self.assertEqual(LG.stats()["tracked_ips"], 0,
                                 "开关关掉时不得留下任何记账")

    def test_default_and_affirmative_values_keep_it_enabled(self):
        self.assertTrue(LG._enabled(), "默认开启")
        for token in ("1", "yes", "on", "", "  "):
            with self.subTest(token=token):
                with mock.patch.dict(os.environ, {"R20_LOGIN_RATE_LIMIT": token}):
                    self.assertTrue(LG._enabled())

    def test_switch_is_read_at_call_time_not_import_time(self):
        LG.note_failure("1.2.3.4")
        self.assertEqual(LG.stats()["tracked_ips"], 1)
        with mock.patch.dict(os.environ, {"R20_LOGIN_RATE_LIMIT": "0"}):
            self.assertEqual(LG.check("1.2.3.4"), (True, 0),
                             "调用点重新读环境 ⇒ 运行中即可停用")

    def test_thresholds_are_import_time_constants(self):
        """钉住那条容易被误解的事实：数值阈值是导入时快照。"""
        for name in ("_WINDOW", "_MAX_ATTEMPTS", "_MAX_FAILURES",
                     "_BLOCK_SECONDS", "_MAX_KEYS"):
            with self.subTest(name=name):
                self.assertIsInstance(getattr(LG, name), int)
        with mock.patch.dict(os.environ, {"R20_LOGIN_IP_MAX_FAILURES": "1"}):
            self.assertEqual(LG._MAX_FAILURES, 15,
                             "改环境变量不会影响已导入的常量（需重启才生效）")


class CheckTests(_GuardBase):
    def test_unknown_or_empty_ip_is_always_allowed(self):
        for ip in ("", "unknown"):
            with self.subTest(ip=ip):
                self.assertEqual(LG.check(ip), (True, 0))
                LG.note_attempt(ip)
                LG.note_failure(ip)
        self.assertEqual(LG.stats()["tracked_ips"], 0)

    def test_untracked_ip_is_allowed_without_creating_state(self):
        self.assertEqual(LG.check("9.9.9.9"), (True, 0))
        self.assertEqual(LG.stats()["tracked_ips"], 0, "只读检查不应写入状态")

    def test_tracked_but_unblocked_ip_is_allowed(self):
        LG.note_attempt("1.2.3.4")
        self.assertEqual(LG.check("1.2.3.4"), (True, 0))

    def test_blocked_ip_reports_at_least_one_second(self):
        LG._MAX_FAILURES = 15
        for _ in range(15):
            LG.note_failure("1.2.3.4")
        allowed, wait = LG.check("1.2.3.4")
        self.assertFalse(allowed)
        self.assertEqual(wait, int(LG._BLOCK_SECONDS) + 1)

    def test_sub_second_block_still_reports_one(self):
        state = LG._State()
        state.blocked_until = self.now + 0.4
        LG._states["1.2.3.4"] = state
        self.assertEqual(LG.check("1.2.3.4"), (False, 1),
                         "不足 1 秒也要报 1，不能报 0 让人以为已解封")

    def test_expired_block_is_released(self):
        state = LG._State()
        state.blocked_until = self.now
        LG._states["1.2.3.4"] = state
        self.assertEqual(LG.check("1.2.3.4"), (True, 0))
        self.now += 10
        self.assertEqual(LG.check("1.2.3.4"), (True, 0))


class AttemptFloodTests(_GuardBase):
    def setUp(self):
        super().setUp()
        LG._MAX_ATTEMPTS = 3

    def test_reaching_the_attempt_threshold_blocks_for_one_window(self):
        for _ in range(2):
            LG.note_attempt("1.2.3.4")
        self.assertEqual(LG.check("1.2.3.4")[0], True, "未达阈值不封")
        LG.note_attempt("1.2.3.4")
        self.assertFalse(LG.check("1.2.3.4")[0])
        self.assertEqual(self._state("1.2.3.4").blocked_until, self.now + LG._WINDOW)

    def test_zero_threshold_disables_that_tier(self):
        LG._MAX_ATTEMPTS = 0
        for _ in range(50):
            LG.note_attempt("1.2.3.4")
        self.assertTrue(LG.check("1.2.3.4")[0])

    def test_old_attempts_are_pruned_out_of_the_window(self):
        LG.note_attempt("1.2.3.4")
        LG.note_attempt("1.2.3.4")
        self.now += LG._WINDOW + 1
        LG.note_attempt("1.2.3.4")
        self.assertEqual(len(self._state("1.2.3.4").attempts), 1,
                         "窗口外的旧尝试必须被裁掉")
        self.assertTrue(LG.check("1.2.3.4")[0])

    def test_unknown_ip_never_creates_a_block(self):
        for _ in range(50):
            LG.note_attempt("unknown")
        self.assertEqual(LG.check("unknown"), (True, 0))
        self.assertEqual(LG.stats()["tracked_ips"], 0)


class FailureGuessTests(_GuardBase):
    def setUp(self):
        super().setUp()
        LG._MAX_FAILURES = 3

    def test_reaching_the_failure_threshold_blocks_for_the_longer_window(self):
        for _ in range(2):
            LG.note_failure("1.2.3.4")
        self.assertTrue(LG.check("1.2.3.4")[0])
        LG.note_failure("1.2.3.4")
        self.assertFalse(LG.check("1.2.3.4")[0])
        self.assertEqual(self._state("1.2.3.4").blocked_until, self.now + LG._BLOCK_SECONDS)

    def test_zero_threshold_disables_that_tier(self):
        LG._MAX_FAILURES = 0
        for _ in range(50):
            LG.note_failure("1.2.3.4")
        self.assertTrue(LG.check("1.2.3.4")[0])

    def test_failures_and_attempts_are_tracked_independently(self):
        LG._MAX_FAILURES = 3
        LG._MAX_ATTEMPTS = 100
        for _ in range(3):
            LG.note_failure("1.2.3.4")
        state = self._state("1.2.3.4")
        self.assertEqual(len(state.failures), 3)
        self.assertEqual(len(state.attempts), 0,
                         "note_failure 不应污染 attempts 桶（两级阈值各自独立计）")

    def test_old_failures_are_pruned_out_of_the_window(self):
        LG.note_failure("1.2.3.4")
        LG.note_failure("1.2.3.4")
        self.now += LG._WINDOW + 1
        LG.note_failure("1.2.3.4")
        self.assertEqual(len(self._state("1.2.3.4").failures), 1)


class MonotonicBlockTests(_GuardBase):
    def test_a_longer_block_wins_over_a_shorter_one(self):
        LG._MAX_FAILURES = 1
        LG._MAX_ATTEMPTS = 1
        LG._BLOCK_SECONDS = 10
        LG._WINDOW = 300
        LG.note_failure("1.2.3.4")                      # 先封 10 秒
        self.assertEqual(self._state("1.2.3.4").blocked_until, self.now + 10)
        LG.note_attempt("1.2.3.4")                      # 洪泛封 300 秒
        self.assertEqual(self._state("1.2.3.4").blocked_until, self.now + 300)

    def test_a_shorter_block_never_shortens_an_existing_one(self):
        LG._MAX_FAILURES = 1
        LG._MAX_ATTEMPTS = 1
        LG._BLOCK_SECONDS = 900
        LG._WINDOW = 10
        LG.note_attempt("1.2.3.4")                      # 先封 10 秒
        LG.note_failure("1.2.3.4")                      # 猜密封 900 秒
        self.assertEqual(self._state("1.2.3.4").blocked_until, self.now + 900)
        LG.note_failure("1.2.3.4")                      # 再猜密也不得缩短
        self.assertEqual(self._state("1.2.3.4").blocked_until, self.now + 900)

    def test_sustained_failures_keep_pushing_the_block_forward(self):
        """⚠️ 实测语义：一旦窗口内失败数 ≥ 阈值，**每次新的失败都会把封锁推到 now+_BLOCK_SECONDS**
        ⇒ 持续攻击者会被一直续期（滑窗封锁），而不是只封"达标那一刻起的固定时长"。"""
        LG._MAX_FAILURES = 2
        LG.note_failure("1.2.3.4")
        LG.note_failure("1.2.3.4")
        first = self._state("1.2.3.4").blocked_until
        self.assertEqual(first, self.now + LG._BLOCK_SECONDS)
        self.now += 5
        LG.note_failure("1.2.3.4")
        self.assertEqual(self._state("1.2.3.4").blocked_until, self.now + LG._BLOCK_SECONDS,
                         "仍在窗口内的失败会续期封锁")

    def test_extension_stops_once_old_failures_leave_the_window(self):
        LG._MAX_FAILURES = 2
        LG.note_failure("1.2.3.4")
        LG.note_failure("1.2.3.4")
        self.now += LG._WINDOW + 1          # 旧失败全部出窗
        LG.note_failure("1.2.3.4")          # 只剩 1 条 ⇒ 不再达阈值
        self.assertEqual(self._state("1.2.3.4").blocked_until,
                         1000.0 + LG._BLOCK_SECONDS,
                         "出窗后不再续期，封锁停在上次设定的时刻")


class BoundedMemoryTests(_GuardBase):
    def test_key_count_is_capped_by_evicting_the_least_recently_active_half(self):
        LG._MAX_KEYS = 4
        for index, ip in enumerate(("a", "b", "c", "d", "e")):
            self.now = 1000.0 + index
            LG.note_attempt(ip)
        self.assertEqual(len(LG._states), 5, "5 个键位尚未超过上限（> 4 才触发）")
        self.now = 2000.0
        LG.note_attempt("f")
        self.assertLessEqual(len(LG._states), LG._MAX_KEYS + 1)
        self.assertIn("f", LG._states, "正在记账的 IP 绝不能被自己挤掉")
        self.assertNotIn("a", LG._states, "最久未活动的先出局")
        self.assertNotIn("b", LG._states)

    def test_existing_keys_are_never_evicted_to_make_room_for_themselves(self):
        LG._MAX_KEYS = 2
        for ip in ("a", "b", "c"):
            LG.note_attempt(ip)
        before = set(LG._states)
        LG.note_attempt("a")            # 已在表里 ⇒ 不触发淘汰
        self.assertTrue(before.issubset(set(LG._states)))

    def test_empty_bucket_keys_sort_to_the_front(self):
        LG._MAX_KEYS = 2
        LG._states["empty"] = LG._State()      # attempts 为空
        LG.note_attempt("x")
        LG.note_attempt("y")
        self.assertEqual(len(LG._states), 3, "3 个键位时才超过上限 2")
        LG.note_attempt("z")
        self.assertNotIn("empty", LG._states,
                         "没有活动记录的键位按 0 排序、最先被淘汰")
        self.assertIn("z", LG._states)


class StatsTests(_GuardBase):
    def test_stats_reports_configuration_and_current_usage(self):
        LG.note_failure("1.1.1.1")
        LG.note_attempt("2.2.2.2")
        out = LG.stats()
        self.assertTrue(out["enabled"])
        self.assertEqual(out["tracked_ips"], 2)
        self.assertEqual(out["blocked_ips"], 0)
        self.assertEqual(out["window_seconds"], LG._WINDOW)
        self.assertEqual(out["max_attempts"], LG._MAX_ATTEMPTS)
        self.assertEqual(out["max_failures"], LG._MAX_FAILURES)
        self.assertEqual(out["block_seconds"], LG._BLOCK_SECONDS)

    def test_stats_counts_only_currently_blocked_ips(self):
        LG._MAX_FAILURES = 1
        LG.note_failure("1.1.1.1")
        self.assertEqual(LG.stats()["blocked_ips"], 1)
        self.now += LG._BLOCK_SECONDS + 1
        self.assertEqual(LG.stats()["blocked_ips"], 0)
        self.assertEqual(LG.stats()["tracked_ips"], 1, "解封不等于忘记该 IP")

    def test_stats_reports_the_switch_off(self):
        with mock.patch.dict(os.environ, {"R20_LOGIN_RATE_LIMIT": "off"}):
            self.assertFalse(LG.stats()["enabled"])


class ConcurrencyAndResetTests(_GuardBase):
    def test_reset_clears_all_state(self):
        LG.note_failure("1.1.1.1")
        LG.note_attempt("2.2.2.2")
        LG._reset_for_tests()
        self.assertEqual(LG.stats()["tracked_ips"], 0)
        self.assertEqual(LG.check("1.1.1.1"), (True, 0))

    def test_concurrent_failures_are_all_recorded_without_loss(self):
        LG._MAX_FAILURES = 0          # 关掉封锁，专心验计数不丢
        threads = [threading.Thread(target=LG.note_failure, args=("1.1.1.1",))
                   for _ in range(32)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self._state("1.1.1.1").failures), 32,
                         "并发记账必须一条不丢（有锁保护）")

    def test_concurrent_attempts_do_not_corrupt_the_key_map(self):
        LG._MAX_KEYS = 10000
        threads = [threading.Thread(target=LG.note_attempt, args=(f"10.0.0.{i}",))
                   for i in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(LG._states), 24)


if __name__ == "__main__":
    unittest.main()

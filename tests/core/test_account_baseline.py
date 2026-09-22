"""账户本金基线：**文件优先于环境、环境演进时刻优先于文件、非法值一律回落**（第二百七十七刀，开新面 account_baseline.py）。

先打印整个文件（117 行）再动笔。它被 admin 面与看板共用（初始本金 / 复盘起点），
所以本刀钉的是"**改一笔本金绝不会把复盘起点或自定义键抹掉**"这条不变量。

⚠️ 真实 `BASELINE_FILE` 指向 `data/account_initial_state.json` **且该文件已存在**
（内含线上初始本金）。本文件的用例基类一律把它重定向到临时目录，并自检生产文件 mtime 未变。

| 语义 | 口径 |
|---|---|
| ★ **文件优先于环境** | `INITIAL_CAPITAL` 只是**兜底**：文件里有值就用文件；文件里缺失/非法/非正 ⇒ 才回落到环境，环境再非法 ⇒ `DEFAULT_CAPITAL` |
| ★ **非正数一律视为无效** | `_number` 对 `0`/负数/`"0"`/`"-5"` 全部回落默认（本金为 0 会让所有收益率除零）|
| ★ **演进起点三级回落** | `R20_EVOLUTION_START_TIME`(环境) → 文件 `evolution_start_time` → 文件 `reset_time` → `"2026-09-01 00:00:00"`；**空串算缺失**（用 `or` 链，不是 `get` 的 None 判断）|
| ★ **`reset_time` 缺省是 1970 纪元** | 缺失或空串 ⇒ `"1970-01-01 00:00:00"`（与演进起点缺省**不同**，两者不是同一个兜底）|
| ★ **非 dict 载荷不炸** | JSON 合法但是数组/字符串 ⇒ 当空档处理（`isinstance` 守卫），绝不 `AttributeError` |
| ★ **更新是锁内 RMW** | `update_initial_capital` / `update_evolution_start_time` 都在 `file_lock(BASELINE_FILE)` 内 load→merge→原子替换 |
| ★ **只改一个键** | 返回体带 `previous_initial_capital`；merge 语义保证**其它键逐字保留**（自定义键、复盘起点、本金更新时刻互不覆盖）|
| ★ **落盘 0600 + 原子** | `mkstemp`→`fsync`→`chmod 0600`→`os.replace`→再 `chmod 0600`；失败路径清临时文件 |
| ★ **金额边界显式** | 本金必须落在 `[1.0, 1e9]`；**`NaN`/`inf` 同样被拒**（比较链为假 ⇒ 抛 ValueError），不接受"静默写入一个坏数字" |
"""

import contextlib
import json
import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from r20_backend import account_baseline as AB
from r20_backend import file_locks


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-baseline-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.file = self.tmp / "data" / "account_initial_state.json"
        self._start(mock.patch.object(AB, "BASELINE_FILE", self.file))
        # 本地 import 的 seam：必须打在**源模块**上
        self.lock = self._start(mock.patch.object(file_locks, "file_lock"))
        self._env()

    def _env(self, **values):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("INITIAL_CAPITAL", "R20_EVOLUTION_START_TIME")}
        clean.update(values)
        return self._start(mock.patch.dict(os.environ, clean, clear=True))

    def _write(self, payload, raw=None):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(raw if raw is not None else json.dumps(payload),
                             encoding="utf-8")


class ConstantTests(unittest.TestCase):
    def test_capital_bounds(self):
        self.assertEqual(AB.DEFAULT_CAPITAL, 10_000.0)
        self.assertEqual(AB.MIN_CAPITAL, 1.0)
        self.assertEqual(AB.MAX_CAPITAL, 1_000_000_000.0)
        self.assertLess(AB.MIN_CAPITAL, AB.DEFAULT_CAPITAL)
        self.assertLess(AB.DEFAULT_CAPITAL, AB.MAX_CAPITAL)

    def test_baseline_file_lives_under_data(self):
        self.assertEqual(AB.BASELINE_FILE.name, "account_initial_state.json")
        self.assertEqual(AB.BASELINE_FILE.parent.name, "data")

    def test_timezone_is_beijing(self):
        self.assertEqual(AB.BJ_TZ.utcoffset(None), timedelta(hours=8))


class NumberTests(unittest.TestCase):
    def test_valid_values(self):
        self.assertEqual(AB._number(12, 1.0), 12.0)
        self.assertEqual(AB._number("12.5", 1.0), 12.5)
        self.assertEqual(AB._number(1e9, 1.0), 1e9)

    def test_uncoercible_values_fall_back(self):
        for bad in (None, "abc", object(), [], {}):
            with self.subTest(value=repr(bad)[:20]):
                self.assertEqual(AB._number(bad, 7.0), 7.0)

    def test_non_positive_values_fall_back(self):
        for bad in (0, 0.0, -1, "-5", "-0.0"):
            with self.subTest(value=bad):
                self.assertEqual(AB._number(bad, 7.0), 7.0,
                                 "非正本金必须回落（否则收益率除零）")

    def test_default_is_returned_verbatim(self):
        sentinel = object()
        self.assertIs(AB._number("nope", sentinel), sentinel)


class LoadBaselineTests(_Base):
    def test_missing_file_yields_the_documented_defaults(self):
        loaded = AB.load_account_baseline()
        self.assertEqual(loaded, {"initial_capital": 10_000.0,
                                  "reset_time": "1970-01-01 00:00:00",
                                  "evolution_start_time": "2026-09-01 00:00:00"})

    def test_file_values_win_and_capital_is_rounded(self):
        self._write({"initial_capital": 1234.567, "reset_time": "2026-05-05 08:00:00",
                     "evolution_start_time": "2026-06-06 00:00:00"})
        loaded = AB.load_account_baseline()
        self.assertEqual(loaded["initial_capital"], 1234.57)
        self.assertEqual(loaded["reset_time"], "2026-05-05 08:00:00")
        self.assertEqual(loaded["evolution_start_time"], "2026-06-06 00:00:00")

    def test_corrupt_json_falls_back_without_raising(self):
        self._write(None, raw="{not json")
        self.assertEqual(AB.load_account_baseline()["initial_capital"], 10_000.0)

    def test_non_dict_payload_is_treated_as_empty(self):
        for payload in ([1, 2, 3], "a string", 42, None):
            with self.subTest(payload=payload):
                self._write(payload)
                self.assertEqual(AB.load_account_baseline()["initial_capital"], 10_000.0)

    def test_unreadable_file_falls_back(self):
        self.file.mkdir(parents=True, exist_ok=True)
        self.assertEqual(AB.load_account_baseline()["initial_capital"], 10_000.0)

    def test_unknown_keys_are_preserved(self):
        self._write({"initial_capital": 500.0, "custom_flag": {"a": 1}})
        loaded = AB.load_account_baseline()
        self.assertEqual(loaded["custom_flag"], {"a": 1})

    def test_env_is_the_bootstrap_when_the_file_is_missing(self):
        self._env(INITIAL_CAPITAL="2500")
        self.assertEqual(AB.load_account_baseline()["initial_capital"], 2500.0)

    def test_the_file_beats_the_env(self):
        self._env(INITIAL_CAPITAL="2500")
        self._write({"initial_capital": 777.0})
        self.assertEqual(AB.load_account_baseline()["initial_capital"], 777.0)

    def test_invalid_file_capital_falls_back_to_the_env(self):
        self._env(INITIAL_CAPITAL="2500")
        for bad in ("abc", 0, -3):
            with self.subTest(value=bad):
                self._write({"initial_capital": bad})
                self.assertEqual(AB.load_account_baseline()["initial_capital"], 2500.0)

    def test_invalid_env_also_falls_back_to_the_constant(self):
        self._env(INITIAL_CAPITAL="nope")
        self.assertEqual(AB.load_account_baseline()["initial_capital"],
                         AB.DEFAULT_CAPITAL)

    def test_env_evolution_start_beats_the_file(self):
        self._env(R20_EVOLUTION_START_TIME="2026-08-08 12:00:00")
        self._write({"evolution_start_time": "2026-06-06 00:00:00"})
        self.assertEqual(AB.load_account_baseline()["evolution_start_time"],
                         "2026-08-08 12:00:00")

    def test_blank_env_evolution_start_falls_through_to_the_file(self):
        self._env(R20_EVOLUTION_START_TIME="   ")
        self._write({"evolution_start_time": "2026-06-06 00:00:00"})
        self.assertEqual(AB.load_account_baseline()["evolution_start_time"],
                         "2026-06-06 00:00:00")

    def test_reset_time_is_the_second_fallback_for_the_evolution_start(self):
        self._write({"reset_time": "2026-07-07 00:00:00"})
        self.assertEqual(AB.load_account_baseline()["evolution_start_time"],
                         "2026-07-07 00:00:00")

    def test_blank_evolution_and_reset_fall_through_to_the_default(self):
        self._write({"evolution_start_time": "", "reset_time": ""})
        loaded = AB.load_account_baseline()
        self.assertEqual(loaded["evolution_start_time"], "2026-09-01 00:00:00")
        self.assertEqual(loaded["reset_time"], "1970-01-01 00:00:00",
                         "reset_time 的兜底与演进起点**不同**（纪元 vs 2026-09-01）")

    def test_reset_time_defaults_to_the_epoch(self):
        self._write({"initial_capital": 100.0})
        self.assertEqual(AB.load_account_baseline()["reset_time"],
                         "1970-01-01 00:00:00")


class UpdateInitialCapitalTests(_Base):
    def test_below_minimum_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            AB.update_initial_capital(0.5)
        self.assertIn("初始本金必须在", str(ctx.exception))
        self.assertFalse(self.file.exists(), "被拒的更新不得落盘")

    def test_zero_and_negative_are_rejected(self):
        for bad in (0, -1, -100.0):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    AB.update_initial_capital(bad)

    def test_above_maximum_is_rejected(self):
        with self.assertRaises(ValueError):
            AB.update_initial_capital(AB.MAX_CAPITAL + 1)

    def test_nan_and_infinity_are_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    AB.update_initial_capital(bad)

    def test_the_bounds_themselves_are_accepted(self):
        self.assertEqual(AB.update_initial_capital(AB.MIN_CAPITAL)["initial_capital"],
                         AB.MIN_CAPITAL)
        self.assertEqual(AB.update_initial_capital(AB.MAX_CAPITAL)["initial_capital"],
                         AB.MAX_CAPITAL)

    def test_numeric_strings_are_accepted(self):
        self.assertEqual(AB.update_initial_capital("5000")["initial_capital"], 5000.0)

    def test_value_is_rounded_to_two_decimals(self):
        out = AB.update_initial_capital(1234.5678)
        self.assertEqual(out["initial_capital"], 1234.57)

    def test_the_lock_is_taken_on_the_baseline_file(self):
        AB.update_initial_capital(5000)
        self.lock.assert_called_once_with(AB.BASELINE_FILE)

    def test_previous_capital_is_reported_and_persisted(self):
        self._write({"initial_capital": 1000.0})
        out = AB.update_initial_capital(2500.0)
        self.assertEqual(out["previous_initial_capital"], 1000.0)
        self.assertEqual(out["initial_capital"], 2500.0)
        self.assertEqual(AB.load_account_baseline()["initial_capital"], 2500.0)

    def test_repeated_updates_chain_the_previous_value(self):
        AB.update_initial_capital(1000)
        out = AB.update_initial_capital(2000)
        self.assertEqual(out["previous_initial_capital"], 1000.0)

    def test_other_keys_survive_the_update(self):
        self._write({"initial_capital": 1000.0, "reset_time": "2026-03-03 00:00:00",
                     "evolution_start_time": "2026-04-04 00:00:00",
                     "custom_flag": [1, 2]})
        AB.update_initial_capital(2000.0)
        loaded = AB.load_account_baseline()
        self.assertEqual(loaded["reset_time"], "2026-03-03 00:00:00")
        self.assertEqual(loaded["evolution_start_time"], "2026-04-04 00:00:00")
        self.assertEqual(loaded["custom_flag"], [1, 2])

    def test_timestamp_and_on_disk_format(self):
        out = AB.update_initial_capital(1500.0)
        self.assertRegex(out["capital_updated_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        text = self.file.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(json.loads(text)["initial_capital"], 1500.0)

    def test_file_is_owner_only(self):
        AB.update_initial_capital(1500.0)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)

    def test_no_temp_files_are_left_behind(self):
        AB.update_initial_capital(1500.0)
        self.assertEqual(list(self.file.parent.glob(".account-baseline-*")), [])

    def test_failed_replace_cleans_the_temp_file(self):
        with mock.patch.object(AB.os, "replace", side_effect=OSError("磁盘满了")):
            with self.assertRaises(OSError):
                AB.update_initial_capital(1500.0)
        self.assertEqual(list(self.file.parent.glob(".account-baseline-*")), [])

    def test_parent_directory_is_created(self):
        self.assertFalse(self.file.parent.exists())
        AB.update_initial_capital(1500.0)
        self.assertTrue(self.file.exists())


class UpdateEvolutionStartTests(_Base):
    def test_blank_input_uses_the_default(self):
        for blank in ("", "   ", None):
            with self.subTest(value=repr(blank)):
                out = AB.update_evolution_start_time(blank)
                self.assertEqual(out["evolution_start_time"], "2026-09-01 00:00:00")

    def test_date_only_input_gets_a_midnight_time(self):
        out = AB.update_evolution_start_time("2026-07-01")
        self.assertEqual(out["evolution_start_time"], "2026-07-01 00:00:00")

    def test_full_timestamps_are_kept_verbatim(self):
        out = AB.update_evolution_start_time("2026-07-01 09:30:15")
        self.assertEqual(out["evolution_start_time"], "2026-07-01 09:30:15")

    def test_input_is_stripped(self):
        out = AB.update_evolution_start_time("  2026-07-01  ")
        self.assertEqual(out["evolution_start_time"], "2026-07-01 00:00:00")

    def test_the_lock_is_taken(self):
        AB.update_evolution_start_time("2026-07-01")
        self.lock.assert_called_once_with(AB.BASELINE_FILE)

    def test_capital_and_other_keys_survive(self):
        self._write({"initial_capital": 4321.0, "custom": "keep"})
        out = AB.update_evolution_start_time("2026-07-01")
        self.assertEqual(out["initial_capital"], 4321.0)
        self.assertEqual(out["custom"], "keep")
        loaded = AB.load_account_baseline()
        self.assertEqual(loaded["initial_capital"], 4321.0)
        self.assertEqual(loaded["evolution_start_time"], "2026-07-01 00:00:00")

    def test_timestamp_written_and_file_is_owner_only(self):
        out = AB.update_evolution_start_time("2026-07-01")
        self.assertRegex(out["evolution_start_updated_at"],
                         r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)

    def test_no_temp_files_are_left_behind(self):
        AB.update_evolution_start_time("2026-07-01")
        self.assertEqual(list(self.file.parent.glob(".account-baseline-*")), [])

    def test_failed_replace_cleans_the_temp_file(self):
        with mock.patch.object(AB.os, "replace", side_effect=OSError("磁盘满了")):
            with self.assertRaises(OSError):
                AB.update_evolution_start_time("2026-07-01")
        self.assertEqual(list(self.file.parent.glob(".account-baseline-*")), [])

    def test_env_override_still_wins_when_reading_back(self):
        AB.update_evolution_start_time("2026-07-01")
        self._env(R20_EVOLUTION_START_TIME="2026-09-09 09:09:09")
        self.assertEqual(AB.load_account_baseline()["evolution_start_time"],
                         "2026-09-09 09:09:09",
                         "写入文件的只是回落值；环境变量仍然优先")


class InteractionTests(_Base):
    def test_the_two_updates_do_not_clobber_each_other(self):
        AB.update_initial_capital(3000.0)
        AB.update_evolution_start_time("2026-07-01")
        loaded = AB.load_account_baseline()
        self.assertEqual(loaded["initial_capital"], 3000.0)
        self.assertEqual(loaded["evolution_start_time"], "2026-07-01 00:00:00")

    def test_reverse_order_also_holds(self):
        AB.update_evolution_start_time("2026-07-01")
        AB.update_initial_capital(3000.0)
        loaded = AB.load_account_baseline()
        self.assertEqual(loaded["initial_capital"], 3000.0)
        self.assertEqual(loaded["evolution_start_time"], "2026-07-01 00:00:00")

    def test_empty_file_starting_point(self):
        """空目录起步：第一次写本金时应写入默认兜底值，而不是崩。"""
        out = AB.update_initial_capital(2000.0)
        self.assertEqual(out["reset_time"], "1970-01-01 00:00:00")
        self.assertEqual(out["evolution_start_time"], "2026-09-01 00:00:00")


if __name__ == "__main__":
    unittest.main()

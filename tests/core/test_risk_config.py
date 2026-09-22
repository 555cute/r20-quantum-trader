"""风控参数 schema / 校验 / 派生口径：**未知键拒收、越界拒收、跨字段矛盾拒收、缺失不代填**（第二百六十三刀，开新面 risk_config.py）。

先打印整个文件（451 行）再动笔。本模块是后台风控页的**服务端校验单一入口**，
也是 `routers/risk.py` 的核心依赖（那一刀把本模块整体 mock 掉了，真正的校验逻辑在此收口）。

| 语义 | 口径 |
|---|---|
| ★ **未知键拒收** | `normalize` 只认 `_INDEX` 里的键，多一个未知键就整批拒绝（不静默丢弃）|
| ★ **越界拒收** | `_coerce` 按 `type` 取整/取 6 位小数后按 `[min,max]` 校验，报错文案给**展示单位**（乘 `display_scale`）|
| ★ **跨字段矛盾拒收** | 同向 ≤ 总仓、杠杆下限 ≤ 上限、R:R 底线 ≤ 上限；三条错误**合并**成一条 `；` 消息（不是只报第一条）|
| ★ **缺失 ≠ 0** | `effective_engine_values` 在权益缺失时 `usdt_available_used=None`、池容量读不到时 `max_positions=None`，**绝不用 0 代填** |
| 套件 | `suite_values` 未知 id ⇒ `ValueError`，且返回**副本**（改动不污染全局 `SUITES`）|
| 极端值 | `high_risk_changes` 只对**跨线且在 schema 内**的键出报告；非数值静默跳过（不是崩）|
| 进程 vs 文件 | `process_freshness` 比对 `_LOADED_AT` 与 `.env` mtime，`stale` 与解释文案一起给；`file_vs_process_diff` 只数列**两边都是数字且不等**的键 |
"""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from r20_backend import risk_config as RC
from scripts.risk_constants import DEFAULTS, RISK_ENV_KEYS


class SuiteTests(unittest.TestCase):
    def test_known_suite_is_returned_as_a_copy(self):
        values = RC.suite_values("conservative")
        self.assertEqual(set(values), set(RC.SUITES[0]["values"]))
        values["R20_MAX_LEVERAGE"] = 999
        self.assertEqual(RC.suite_values("conservative")["R20_MAX_LEVERAGE"], 3.0,
                         "必须返回副本，改动不得污染全局套件")

    def test_unknown_suite_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            RC.suite_values("nope")
        self.assertIn("未知风控预设套件", str(ctx.exception))

    def test_all_shipped_suites_stay_inside_the_schema(self):
        normalized = [RC.normalize(suite["values"]) for suite in RC.SUITES]
        self.assertEqual(len(normalized), len(RC.SUITES))
        for out in normalized:
            self.assertTrue(out, "每个内置套件都必须规整出非空映射")
            self.assertTrue(all(isinstance(v, str) for v in out.values()),
                            "规整结果必须是可直接写 .env 的字符串映射")


class HighRiskTests(unittest.TestCase):
    def test_below_threshold_is_not_flagged(self):
        self.assertEqual(RC.high_risk_changes({"R20_MAX_LEVERAGE": 5.0}), [])

    def test_at_or_above_threshold_is_flagged_with_label_and_threshold(self):
        out = RC.high_risk_changes({"R20_MAX_LEVERAGE": 10.0,
                                    "R20_DAILY_LOSS_EQUITY_RATIO": 0.30})
        keys = {item["key"]: item for item in out}
        self.assertEqual(keys["R20_MAX_LEVERAGE"]["threshold"], 10.0)
        self.assertEqual(keys["R20_MAX_LEVERAGE"]["label"], "单笔杠杆上限")
        self.assertEqual(keys["R20_DAILY_LOSS_EQUITY_RATIO"]["value"], 0.30)

    def test_non_numeric_and_unknown_keys_are_skipped_not_crashed(self):
        self.assertEqual(RC.high_risk_changes({"R20_MAX_LEVERAGE": "abc"}), [])
        self.assertEqual(RC.high_risk_changes({"R20_NOT_A_PARAM": 999}), [])


class SchemaTests(unittest.TestCase):
    def test_schema_exposes_groups_params_defaults_and_high_risk_line(self):
        s = RC.schema()
        self.assertEqual(len(s["groups"]), 5)
        self.assertEqual(s["high_risk_phrase"], "HIGH RISK")
        params = {p["key"]: p for p in s["params"]}
        self.assertEqual(set(params), set(DEFAULTS))
        for key, param in params.items():
            self.assertEqual(param["default"], DEFAULTS[key],
                             f"{key} 的 default 必须来自单一事实源")
        self.assertEqual(params["R20_MAX_LEVERAGE"]["high_risk_at"], 10.0)
        self.assertIsNone(params["R20_TIME_STOP_HOURS"]["high_risk_at"])


class CurrentValuesTests(unittest.TestCase):
    def test_defaults_are_used_when_env_is_clean(self):
        clean = {k: v for k, v in os.environ.items() if k not in RISK_ENV_KEYS}
        with mock.patch.dict(os.environ, clean, clear=True):
            values = RC.current_values()
        self.assertEqual(set(values), set(DEFAULTS))
        self.assertEqual(values["R20_MAX_LEVERAGE"], DEFAULTS["R20_MAX_LEVERAGE"])

    def test_env_overrides_are_parsed_by_declared_type(self):
        with mock.patch.dict(os.environ, {"R20_MAX_LEVERAGE": "7.5",
                                          "R20_MAX_CONCURRENT_POSITIONS": "6.0"}, clear=False):
            values = RC.current_values()
        self.assertEqual(values["R20_MAX_LEVERAGE"], 7.5)
        self.assertIsInstance(values["R20_MAX_CONCURRENT_POSITIONS"], int)
        self.assertEqual(values["R20_MAX_CONCURRENT_POSITIONS"], 6)

    def test_unparsable_env_falls_back_to_the_default(self):
        with mock.patch.dict(os.environ, {"R20_MAX_LEVERAGE": "not-a-number"}, clear=False):
            self.assertEqual(RC.current_values()["R20_MAX_LEVERAGE"],
                             DEFAULTS["R20_MAX_LEVERAGE"])

    def test_reset_keys_are_exactly_the_managed_env_keys(self):
        self.assertEqual(RC.reset_keys(), list(RISK_ENV_KEYS))


class ProcessViewTests(unittest.TestCase):
    def test_process_values_cover_every_managed_key(self):
        values = RC.process_values()
        self.assertEqual(set(values), set(RISK_ENV_KEYS))
        self.assertIn(values["R20_SCALE_OUT_ENABLED"], (0, 1),
                      "布尔量必须以 0/1 暴露，不能是 True/False 混入数值列")

    def test_reload_refreshes_the_snapshot_timestamp(self):
        original = RC._LOADED_AT
        self.addCleanup(setattr, RC, "_LOADED_AT", original)
        with mock.patch.object(RC.time, "time", return_value=4_000_000_000.0):
            RC.reload_risk_constants()
        self.assertEqual(RC._LOADED_AT, 4_000_000_000.0)

    def test_freshness_is_stale_when_env_is_newer_than_the_process_snapshot(self):
        tmp = Path(tempfile.mkdtemp(prefix="r20-riskcfg-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        env_file = tmp / ".env"
        env_file.write_text("R20_MAX_LEVERAGE=5\n", encoding="utf-8")
        os.utime(env_file, (time.time(), time.time()))
        with mock.patch("r20_backend.settings_store.ENV_FILE", env_file), \
                mock.patch.object(RC, "_LOADED_AT", 0.0):
            out = RC.process_freshness()
        self.assertIsNotNone(out["env_file_mtime"])
        self.assertTrue(out["stale"])
        self.assertIn("重启", out["note"])

    def test_freshness_is_not_stale_for_an_old_env_file(self):
        tmp = Path(tempfile.mkdtemp(prefix="r20-riskcfg-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        env_file = tmp / ".env"
        env_file.write_text("x=1\n", encoding="utf-8")
        os.utime(env_file, (time.time() - 1000, time.time() - 1000))
        with mock.patch("r20_backend.settings_store.ENV_FILE", env_file), \
                mock.patch.object(RC, "_LOADED_AT", time.time()):
            out = RC.process_freshness()
        self.assertFalse(out["stale"])
        self.assertNotIn("重启", out["note"])

    def test_freshness_tolerates_every_lookup_failure(self):
        with mock.patch("r20_backend.settings_store.ENV_FILE", object()):
            out = RC.process_freshness()
        self.assertIsNone(out["env_file_mtime"])
        self.assertFalse(out["stale"])

    def test_file_vs_process_diff_lists_only_differing_numeric_keys(self):
        file_values = {key: 1.0 for key in RISK_ENV_KEYS}
        proc_values = {key: 1.0 for key in RISK_ENV_KEYS}
        proc_values["R20_MAX_LEVERAGE"] = 9.0
        with mock.patch.object(RC, "current_values", return_value=file_values), \
                mock.patch.object(RC, "process_values", return_value=proc_values):
            out = RC.file_vs_process_diff()
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["differing"]["R20_MAX_LEVERAGE"],
                         {"file": 1.0, "process": 9.0})


class EffectiveEngineValuesTests(unittest.TestCase):
    def test_pool_capacity_is_read_and_positions_are_derived(self):
        with mock.patch("scripts.instrument_pool.load_instruments",
                        return_value=[{"instId": "BTC-USDT-SWAP"},
                                      {"instId": "ETH-USDT-SWAP"}]):
            out = RC.effective_engine_values(usdt_available=None)
        self.assertEqual(out["pool_size_used"], 2)
        self.assertIsNotNone(out["max_positions"])
        self.assertLessEqual(out["max_same_direction"], out["max_positions"])
        self.assertIsNone(out["usdt_available_used"], "权益缺失绝不用 0 代填")

    def test_empty_pool_leaves_position_caps_unknown(self):
        with mock.patch("scripts.instrument_pool.load_instruments", return_value=[]):
            out = RC.effective_engine_values(usdt_available=1000.0)
        self.assertEqual(out["pool_size_used"], 0)
        self.assertIsNone(out["max_positions"], "池容量为 0 ⇒ 不臆造持仓上限")
        self.assertIsNone(out["max_same_direction"])
        self.assertEqual(out["usdt_available_used"], 1000.0)

    def test_unreadable_pool_is_not_faked(self):
        with mock.patch("scripts.instrument_pool.load_instruments",
                        side_effect=RuntimeError("池文件坏了")):
            out = RC.effective_engine_values()
        self.assertIsNone(out["pool_size_used"])
        self.assertIsNone(out["max_positions"])

    def test_explicit_pool_size_skips_the_file_read(self):
        reader = mock.Mock()
        with mock.patch("scripts.instrument_pool.load_instruments", reader):
            out = RC.effective_engine_values(usdt_available=500.0, pool_size=3)
        reader.assert_not_called()
        self.assertEqual(out["pool_size_used"], 3)

    def test_confidence_band_has_a_hard_floor_of_78(self):
        with mock.patch.object(RC, "MIN_ENTRY_CONFIDENCE", 60.0):
            out = RC.effective_engine_values(pool_size=1)
        self.assertEqual(out["confidence_band"], [78.0, 86.0],
                         "低于 78 的配置被硬地板抬回 78（宽度固定 8）")
        default_band = RC.effective_engine_values(pool_size=1)["confidence_band"]
        self.assertGreaterEqual(default_band[0], 78.0)
        self.assertEqual(default_band[1] - default_band[0], 8.0)
        self.assertGreaterEqual(out["target_rr"], 2.2)


class NormalizeTests(unittest.TestCase):
    def test_unknown_keys_are_rejected_as_a_batch(self):
        with self.assertRaises(ValueError) as ctx:
            RC.normalize({"R20_NOT_A_PARAM": 1, "R20_MAX_LEVERAGE": 5})
        self.assertIn("未知风控参数", str(ctx.exception))
        self.assertIn("R20_NOT_A_PARAM", str(ctx.exception))

    def test_non_numeric_values_are_rejected_with_the_label(self):
        with self.assertRaises(ValueError) as ctx:
            RC.normalize({"R20_MAX_LEVERAGE": "abc"})
        self.assertIn("必须是数字", str(ctx.exception))

    def test_out_of_range_reports_display_units(self):
        # R20_MAX_MARGIN_EQUITY_RATIO 的 display_scale=100（原生 0.01~1.0 显示为 1~100 %）
        with self.assertRaises(ValueError) as ctx:
            RC.normalize({"R20_MAX_MARGIN_EQUITY_RATIO": 1.5})
        self.assertIn("100", str(ctx.exception))

    def test_success_returns_string_mapping_for_update_env(self):
        out = RC.normalize({"R20_MAX_LEVERAGE": 7.5,
                            "R20_MAX_CONCURRENT_POSITIONS": 6.6})
        self.assertEqual(out["R20_MAX_LEVERAGE"], "7.5")
        self.assertEqual(out["R20_MAX_CONCURRENT_POSITIONS"], "7",
                         "int 类型必须四舍五入成整数再转字符串")

    def test_same_direction_may_not_exceed_total_positions(self):
        with self.assertRaises(ValueError) as ctx:
            RC.normalize({"R20_MAX_CONCURRENT_POSITIONS": 3,
                          "R20_MAX_SAME_DIRECTION_POSITIONS": 5})
        self.assertIn("同向持仓上限", str(ctx.exception))

    def test_leverage_and_rr_intervals_must_not_invert(self):
        with self.assertRaises(ValueError) as ctx:
            RC.normalize({"R20_MIN_LEVERAGE": 8, "R20_MAX_LEVERAGE": 3})
        self.assertIn("杠杆下限", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            RC.normalize({"R20_MIN_RISK_REWARD": 5.0, "R20_MAX_RISK_REWARD": 3.0})
        self.assertIn("最小盈亏比底线", str(ctx.exception))

    def test_multiple_errors_are_merged_into_one_message(self):
        with self.assertRaises(ValueError) as ctx:
            RC.normalize({"R20_MIN_LEVERAGE": 8, "R20_MAX_LEVERAGE": 3,
                          "R20_MIN_RISK_REWARD": 5.0, "R20_MAX_RISK_REWARD": 3.0})
        message = str(ctx.exception)
        self.assertIn("；", message)
        self.assertIn("杠杆下限", message)
        self.assertIn("最小盈亏比底线", message)

    def test_cross_field_checks_compare_against_current_values_when_absent(self):
        # 只提交同向上限：与「当前生效的总仓上限」比较
        with mock.patch.dict(os.environ, {"R20_MAX_CONCURRENT_POSITIONS": "2"},
                             clear=False):
            with self.assertRaises(ValueError) as ctx:
                RC.normalize({"R20_MAX_SAME_DIRECTION_POSITIONS": 4})
        self.assertIn("同向持仓上限", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

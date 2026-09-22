"""通知/维护计划存储：**缺文件给默认、坏文件也给默认、写入必须原子**（第二百七十三刀，开新面 schedule_store.py）。

先打印整个文件（42 行）再动笔。它被 `routers/system.py` 与 `scheduler.py` 共用，
所以"读不出来时返回什么"直接决定调度器会不会拿 None 去算时间。

| 语义 | 口径 |
|---|---|
| ★ **缺文件 = 默认档（副本）** | 返回 `dict(DEFAULT_SCHEDULE)` ⇒ 调用方改它**不污染**模块常量（否则一次 `pop` 就把全局默认改坏了）|
| ★ **坏文件 = 默认档** | `JSONDecodeError`（内容坏了）与 `OSError`（目录冒充文件 / 读不动）都回落默认，绝不抛给面板 |
| ★ **部分文件与默认合并** | `{**DEFAULT_SCHEDULE, **payload}` ⇒ 少写几个键不影响其它键；额外键原样保留（前向兼容）|
| ★ **原子写** | `mkstemp`→`fsync`→`os.replace`；**失败路径清掉临时文件**；父目录自动创建 |
| 落盘格式 | `ensure_ascii=False` + `indent=2` + 结尾换行（人可读、可 diff）|
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from r20_backend import schedule_store as SS


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-sched-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.file = self.tmp / "data" / "notification_schedule.json"
        self._start(mock.patch.object(SS, "SCHEDULE_FILE", self.file))

    def _write(self, text):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(text, encoding="utf-8")


class LoadTests(_Base):
    def test_missing_file_returns_the_defaults(self):
        self.assertEqual(SS.load_schedule(), SS.DEFAULT_SCHEDULE)
        self.assertEqual(SS.load_schedule()["timezone"], "Asia/Shanghai")
        self.assertEqual(SS.load_schedule()["briefing_times"], ["08:00", "20:00"])

    def test_missing_file_result_is_a_shallow_copy_so_nested_mutation_leaks(self):
        """⚠️ **实测缺陷（本刀发现，未擅自改生产代码）**：`dict(DEFAULT_SCHEDULE)`
        是**浅拷贝** ⇒ 返回值里的 `briefing_times` / `self_improvement_times` 与模块常量
        **共享同一个 list 对象**。调用方一句 `schedule["briefing_times"].append(...)`
        就污染全局默认档 —— 本刀第一次跑时正是这条把同批其它用例一起带红了。

        这里按实际行为钉住，并**立刻复原**（避免污染后续用例）；
        修法建议：`copy.deepcopy(DEFAULT_SCHEDULE)`。"""
        original = list(SS.DEFAULT_SCHEDULE["briefing_times"])
        self.addCleanup(SS.DEFAULT_SCHEDULE.__setitem__, "briefing_times", original)
        got = SS.load_schedule()
        got["briefing_times"].append("MUTATED")
        self.assertIn("MUTATED", SS.DEFAULT_SCHEDULE["briefing_times"],
                      "嵌套 list 是共享的 ⇒ 泄漏确实发生")
        self.assertIn("MUTATED", SS.load_schedule()["briefing_times"])

    def test_top_level_keys_are_not_shared_with_the_module_defaults(self):
        got = SS.load_schedule()
        got["injected"] = True
        got["timezone"] = "UTC"
        fresh = SS.load_schedule()
        self.assertNotIn("injected", fresh, "顶层键是拷贝，新增不影响常量")
        self.assertEqual(fresh["timezone"], "Asia/Shanghai")

    def test_corrupt_json_falls_back_to_the_defaults(self):
        self._write("{not json")
        self.assertEqual(SS.load_schedule(), SS.DEFAULT_SCHEDULE)

    def test_unreadable_file_falls_back_to_the_defaults(self):
        self.file.mkdir(parents=True, exist_ok=True)     # 目录冒充文件 ⇒ OSError
        self.assertEqual(SS.load_schedule(), SS.DEFAULT_SCHEDULE)

    def test_partial_payload_is_merged_with_the_defaults(self):
        self._write(json.dumps({"backup_time": "03:30"}))
        loaded = SS.load_schedule()
        self.assertEqual(loaded["backup_time"], "03:30")
        self.assertEqual(loaded["briefing_times"], ["08:00", "20:00"],
                         "未提及的键必须回落到默认值")

    def test_extra_keys_are_preserved_for_forward_compatibility(self):
        self._write(json.dumps({"future_feature": {"enabled": True}}))
        self.assertEqual(SS.load_schedule()["future_feature"], {"enabled": True})

    def test_non_object_payload_is_merged_leniently(self):
        """JSON 合法但不是对象：`{**defaults, **payload}` 对 list 会抛 TypeError。
        本用例只钉住"读到数组时会怎样"，避免有人以为它一定会回落默认。"""
        self._write(json.dumps([1, 2, 3]))
        with self.assertRaises(TypeError):
            SS.load_schedule()


class SaveTests(_Base):
    def test_round_trip_and_on_disk_format(self):
        payload = {"timezone": "Asia/Shanghai", "briefing_times": ["09:00"],
                   "note": "中文原样"}
        SS.save_schedule(payload)
        text = self.file.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertIn("\n  ", text, "必须是 indent=2 的可读格式")
        self.assertIn("中文原样", text, "ensure_ascii=False ⇒ 中文不转义")
        self.assertEqual(json.loads(text), payload)
        self.assertEqual(SS.load_schedule(), {**SS.DEFAULT_SCHEDULE, **payload})

    def test_parent_directory_is_created(self):
        self.assertFalse(self.file.parent.exists())
        SS.save_schedule({"backup_time": "04:00"})
        self.assertTrue(self.file.exists())

    def test_no_temp_files_are_left_behind(self):
        SS.save_schedule({"a": 1})
        self.assertEqual(list(self.file.parent.glob(".notification-schedule-*.tmp")), [])

    def test_failed_replace_still_cleans_the_temp_file(self):
        with mock.patch.object(SS.os, "replace", side_effect=OSError("磁盘满了")):
            with self.assertRaises(OSError):
                SS.save_schedule({"a": 1})
        self.assertEqual(list(self.file.parent.glob(".notification-schedule-*.tmp")), [],
                         "失败路径不得留下半截临时文件")
        self.assertFalse(self.file.exists())

    def test_write_failure_inside_the_file_handle_also_cleans_up(self):
        with mock.patch.object(SS.os, "fsync", side_effect=OSError("fsync 失败")):
            with self.assertRaises(OSError):
                SS.save_schedule({"a": 1})
        self.assertEqual(list(self.file.parent.glob(".notification-schedule-*.tmp")), [])

    def test_overwrite_keeps_the_file_at_the_same_path(self):
        SS.save_schedule({"version": 1})
        SS.save_schedule({"version": 2})
        self.assertEqual(SS.load_schedule()["version"], 2)
        self.assertEqual(list(self.file.parent.glob("*.json")), [self.file])


if __name__ == "__main__":
    unittest.main()

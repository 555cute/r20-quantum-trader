"""审计日志：**路径调用时解析、只追加、倒序读、坏行不炸**（第二百八十三刀，开新面 audit.py）。

先打印整个文件（57 行）再动笔。它是所有已鉴权管理动作的**只追加**审计流。

| 语义 | 口径 |
|---|---|
| ★ **路径必须"调用时"解析** | 审计卫生修复：模块级绑定路径不可重定向，历史上全部 TestClient 后台测试把伪造记录**直写生产** `logs/r20_admin_audit.jsonl`（实测 >2200 条污染）。现在每次调用读 `R20_AUDIT_FILE`，空值回落生产默认 |
| ★ **只追加、不重写** | 用 `open("a")`，已有内容一字不动（审计流的根本要求：不能丢历史）|
| ★ **+08:00 业务时区** | `datetime.now(timezone(timedelta(hours=8)))` 固定东八区，**不随宿主 TZ 漂移**（审计时间必须可对账）|
| ★ **来源字段是截断不是拒绝** | `actor_ip` 截 64、`user_agent` 截 200；**空值不写键**（不是写空串），避免"字段在场但没值"的歧义 |
| ★ **中文不转义** | `ensure_ascii=False` + 紧凑分隔符（`(",", ":")`）⇒ 一条一行、体积小、可读 |
| ★ **倒序读最近 N 条** | `recent` 返回**最新在前**；`limit` 夹到 `[1, 200]`（上限防一次拉爆内存）|
| ★ **坏行静默跳过** | 单行 JSON 坏了只 `continue`（审计流被截断/半行写入时不能让整个查询接口 500）|
| ★ **文件不存在返回空表** | 且**不创建**它（读操作不应有副作用）|
"""

import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from r20_backend import audit as AU


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "nested" / "audit.jsonl"
        patcher = mock.patch.dict(os.environ, {"R20_AUDIT_FILE": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _lines(self):
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x]


class AuditFilePathTests(unittest.TestCase):
    def test_default_is_the_production_path(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("R20_AUDIT_FILE", None)
            self.assertEqual(AU._audit_file(), AU.AUDIT_FILE)

    def test_the_env_var_overrides_it(self):
        with mock.patch.dict(os.environ, {"R20_AUDIT_FILE": "/tmp/other.jsonl"}):
            self.assertEqual(AU._audit_file(), Path("/tmp/other.jsonl"))

    def test_an_empty_override_falls_back_to_the_default(self):
        """空串按"没配"处理（`if override` 为假）——不会解析成当前目录。"""
        with mock.patch.dict(os.environ, {"R20_AUDIT_FILE": ""}):
            self.assertEqual(AU._audit_file(), AU.AUDIT_FILE)

    def test_the_path_is_resolved_on_every_call(self):
        with mock.patch.dict(os.environ, {"R20_AUDIT_FILE": "/tmp/a.jsonl"}):
            self.assertEqual(AU._audit_file(), Path("/tmp/a.jsonl"))
        with mock.patch.dict(os.environ, {"R20_AUDIT_FILE": "/tmp/b.jsonl"}):
            self.assertEqual(AU._audit_file(), Path("/tmp/b.jsonl"))

    def test_the_default_path_lives_under_logs(self):
        self.assertEqual(AU.AUDIT_FILE, AU.ROOT / "logs" / "r20_admin_audit.jsonl")


class RecordTests(_Base):
    def test_one_call_writes_one_line(self):
        AU.record("login", "success")
        self.assertEqual(len(self._lines()), 1)

    def test_missing_parent_directories_are_created(self):
        self.assertFalse(self.path.parent.exists())
        AU.record("login", "success")
        self.assertTrue(self.path.parent.is_dir())

    def test_the_payload_shape(self):
        AU.record("login", "success")
        row = self._lines()[0]
        self.assertEqual(row["action"], "login")
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["detail"], {})
        self.assertEqual(set(row), {"timestamp", "action", "status", "detail"})

    def test_the_timestamp_is_beijing_time(self):
        AU.record("login", "success")
        stamp = self._lines()[0]["timestamp"]
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        parsed = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
        expected = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        self.assertLess(abs((parsed - expected).total_seconds()), 10,
                        "必须是东八区墙上时间，不随宿主 TZ 漂移")

    def test_the_timestamp_is_not_utc(self):
        """宿主在 UTC 时这一步会差 8 小时 —— 用固定偏移的断言把它钉死。"""
        with mock.patch.object(AU, "datetime") as fake:
            fake.now.return_value = datetime(2026, 9, 22, 16, 30, 0,
                                             tzinfo=timezone(timedelta(hours=8)))
            AU.record("x", "y")
        self.assertEqual(self._lines()[0]["timestamp"], "2026-09-22 16:30:00")
        tz = fake.now.call_args[0][0]
        self.assertEqual(tz.utcoffset(None), timedelta(hours=8))

    def test_the_detail_is_preserved(self):
        AU.record("cfg.update", "success", {"actor": "alice", "count": 3})
        self.assertEqual(self._lines()[0]["detail"], {"actor": "alice", "count": 3})

    def test_a_none_detail_becomes_an_empty_dict(self):
        AU.record("x", "y", None)
        self.assertEqual(self._lines()[0]["detail"], {})

    def test_an_empty_detail_stays_an_empty_dict(self):
        AU.record("x", "y", {})
        self.assertEqual(self._lines()[0]["detail"], {})

    def test_ip_is_recorded_when_given(self):
        AU.record("login", "success", ip="203.0.113.9")
        self.assertEqual(self._lines()[0]["actor_ip"], "203.0.113.9")

    def test_a_long_ip_is_truncated_to_64(self):
        AU.record("login", "success", ip="9" * 100)
        self.assertEqual(len(self._lines()[0]["actor_ip"]), 64)

    def test_an_empty_ip_omits_the_key(self):
        AU.record("login", "success", ip="")
        self.assertNotIn("actor_ip", self._lines()[0])

    def test_a_none_ip_omits_the_key(self):
        AU.record("login", "success", ip=None)
        self.assertNotIn("actor_ip", self._lines()[0])

    def test_a_non_string_ip_is_coerced(self):
        AU.record("login", "success", ip=12345)
        self.assertEqual(self._lines()[0]["actor_ip"], "12345")

    def test_user_agent_is_recorded_and_truncated_to_200(self):
        AU.record("login", "success", user_agent="M" * 500)
        self.assertEqual(len(self._lines()[0]["user_agent"]), 200)

    def test_an_empty_user_agent_omits_the_key(self):
        AU.record("login", "success", user_agent="")
        self.assertNotIn("user_agent", self._lines()[0])

    def test_both_source_fields_together(self):
        AU.record("login", "success", ip="1.2.3.4", user_agent="curl/8")
        row = self._lines()[0]
        self.assertEqual(row["actor_ip"], "1.2.3.4")
        self.assertEqual(row["user_agent"], "curl/8")

    def test_appends_do_not_touch_existing_content(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"existing":true}\n', encoding="utf-8")
        AU.record("login", "success")
        raw = self.path.read_text(encoding="utf-8")
        self.assertTrue(raw.startswith('{"existing":true}\n'))
        self.assertEqual(len(raw.splitlines()), 2)

    def test_chinese_is_not_escaped(self):
        AU.record("心法切换", "成功", {"备注": "中文"})
        raw = self.path.read_text(encoding="utf-8")
        self.assertIn("心法切换", raw)
        self.assertIn("备注", raw)
        self.assertNotIn("\\u", raw)

    def test_the_line_is_compact(self):
        AU.record("login", "success", {"a": 1})
        raw = self.path.read_text(encoding="utf-8")
        self.assertEqual(raw.count("\n"), 1)
        self.assertNotIn(", ", raw)
        self.assertNotIn(": ", raw.split('"timestamp"')[0] or raw)

    def test_it_returns_none(self):
        self.assertIsNone(AU.record("login", "success"))

    def test_each_call_is_its_own_line(self):
        for i in range(5):
            AU.record(f"action-{i}", "success")
        self.assertEqual([r["action"] for r in self._lines()],
                         [f"action-{i}" for i in range(5)])

    def test_the_file_is_utf8(self):
        AU.record("心法", "成功")
        raw = self.path.read_bytes()
        self.assertIn("心法", raw.decode("utf-8"), "必须是 UTF-8 字节，不是转义后的 ASCII")

    def test_write_failure_propagates(self):
        """审计写不进去必须炸出来（不能静默丢掉一条审计）。"""
        with mock.patch.object(AU.Path, "open", side_effect=OSError("磁盘满")):
            with self.assertRaises(OSError):
                AU.record("login", "success")


class RecentTests(_Base):
    def _seed(self, count, action="a"):
        for i in range(count):
            AU.record(f"{action}-{i}", "success")

    def test_a_missing_file_returns_an_empty_list(self):
        self.assertEqual(AU.recent(), [])

    def test_reading_does_not_create_the_file(self):
        AU.recent()
        self.assertFalse(self.path.exists())

    def test_an_empty_file_returns_an_empty_list(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("", encoding="utf-8")
        self.assertEqual(AU.recent(), [])

    def test_newest_first(self):
        self._seed(3)
        self.assertEqual([r["action"] for r in AU.recent()],
                         ["a-2", "a-1", "a-0"])

    def test_the_default_limit_is_fifty(self):
        self._seed(60)
        self.assertEqual(len(AU.recent()), 50)

    def test_an_explicit_limit_is_honoured(self):
        self._seed(10)
        self.assertEqual(len(AU.recent(3)), 3)
        self.assertEqual([r["action"] for r in AU.recent(3)], ["a-9", "a-8", "a-7"])

    def test_the_limit_is_capped_at_two_hundred(self):
        self._seed(210)
        self.assertEqual(len(AU.recent(1000)), 200)

    def test_a_zero_limit_still_returns_one(self):
        self._seed(5)
        self.assertEqual(len(AU.recent(0)), 1)

    def test_a_negative_limit_still_returns_one(self):
        self._seed(5)
        self.assertEqual(len(AU.recent(-7)), 1)

    def test_broken_lines_are_skipped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"action":"good-1","status":"s","detail":{},"timestamp":"t"}\n'
                             'not json at all\n'
                             '{"action":"good-2","status":"s","detail":{},"timestamp":"t"}\n',
                             encoding="utf-8")
        rows = AU.recent()
        self.assertEqual([r["action"] for r in rows], ["good-2", "good-1"])

    def test_a_truncated_half_line_is_skipped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"action":"good","status":"s","detail":{}}\n{"action":"hal',
                             encoding="utf-8")
        self.assertEqual([r["action"] for r in AU.recent()], ["good"])

    def test_blank_lines_are_skipped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"action":"a","status":"s","detail":{}}\n'
                             '\n'
                             '   \n', encoding="utf-8")
        self.assertEqual([r["action"] for r in AU.recent()], ["a"])

    def test_only_the_tail_is_read(self):
        self._seed(300)
        rows = AU.recent(2)
        self.assertEqual([r["action"] for r in rows], ["a-299", "a-298"])

    def test_it_honours_the_env_override(self):
        AU.record("here", "success")
        other = Path(self.tmp.name) / "elsewhere.jsonl"
        with mock.patch.dict(os.environ, {"R20_AUDIT_FILE": str(other)}):
            self.assertEqual(AU.recent(), [])
        self.assertEqual([r["action"] for r in AU.recent()], ["here"])

    def test_the_records_are_plain_dicts(self):
        self._seed(1)
        row = AU.recent()[0]
        self.assertIs(type(row), dict)
        self.assertIn("timestamp", row)


class RoundTripTests(_Base):
    def test_record_then_recent(self):
        AU.record("cfg.update", "success", {"actor": "alice"}, ip="1.2.3.4",
                  user_agent="curl/8")
        row = AU.recent()[0]
        self.assertEqual(row["action"], "cfg.update")
        self.assertEqual(row["detail"], {"actor": "alice"})
        self.assertEqual(row["actor_ip"], "1.2.3.4")
        self.assertEqual(row["user_agent"], "curl/8")

    def test_a_failed_action_is_recorded_too(self):
        """审计要能记失败（只记成功就查不出暴力破解）。"""
        AU.record("login", "failed", {"reason": "bad token"}, ip="9.9.9.9")
        row = AU.recent()[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["detail"], {"reason": "bad token"})

    def test_the_serialised_line_is_valid_json(self):
        AU.record("x", "y", {"n": 1.5, "b": True, "nested": {"k": [1, 2]}})
        rows = [json.loads(line)
                for line in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1, "一次调用必须恰好一行、且整行是合法 JSON")
        self.assertEqual(rows[0]["detail"]["nested"], {"k": [1, 2]})
        self.assertIs(rows[0]["detail"]["b"], True)


if __name__ == "__main__":
    unittest.main()

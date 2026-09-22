"""交易台账分片归档的安全性契约（审计「未完成清单」#2）。

这个脚本动的是**实盘台账**，所以它的价值不在"能归档"，而在"绝不会弄丢一行"：
默认 dry-run、先归档再截断、回读校验、行集合多重集校验、不可解析时间绝不猜。
本文件把这些红线逐条钉死。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BJ = timezone(timedelta(hours=8))

_spec = importlib.util.spec_from_file_location("archive_ledger", ROOT / "scripts" / "archive_ledger.py")
assert _spec and _spec.loader
al = importlib.util.module_from_spec(_spec)
sys.modules["archive_ledger"] = al
_spec.loader.exec_module(al)

NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=BJ)


def row(row_id: int, close_time: str, **extra) -> dict:
    return {"id": row_id, "inst": "BTC-USDT-SWAP", "close_time": close_time, "net_pnl": 1.0, **extra}


class PlanArchiveTests(unittest.TestCase):
    def test_recent_rows_stay_hot(self):
        rows = [row(1, "2026-09-01 10:00:00"), row(2, "2026-08-01 10:00:00")]
        hot, cold = al.plan_archive(rows, keep_days=180, now=NOW)
        self.assertEqual([r["id"] for r in hot], [1, 2])
        self.assertEqual(cold, [])

    def test_old_rows_are_archived(self):
        rows = [row(1, "2025-01-01 10:00:00"), row(2, "2026-09-01 10:00:00")]
        hot, cold = al.plan_archive(rows, keep_days=180, now=NOW)
        self.assertEqual([r["id"] for r in hot], [2])
        self.assertEqual([r["id"] for r in cold], [1])

    def test_unparsable_time_never_guessed(self):
        """「持仓中…」这类占位与缺字段一律留在热台账。"""
        rows = [row(1, "持仓中..."), row(2, ""), {"id": 3}, row(4, "2020-01-01 00:00:00")]
        hot, cold = al.plan_archive(rows, keep_days=180, now=NOW)
        self.assertEqual(sorted(r.get("id") for r in hot), [1, 2, 3])
        self.assertEqual([r["id"] for r in cold], [4])

    def test_non_dict_rows_stay_hot(self):
        """结构异常的行（历史脏数据）不参与归档判定，原样留在热台账。"""
        hot, cold = al.plan_archive(["junk", None, row(9, "2019-01-01 00:00:00")], now=NOW)
        self.assertEqual(cold, [row(9, "2019-01-01 00:00:00")])
        self.assertEqual([h for h in hot if not isinstance(h, dict)], ["junk", None])

    def test_cutoff_boundary_excludes_exact_cutoff(self):
        edge = (NOW - timedelta(days=180)).strftime("%Y-%m-%d %H:%M:%S")
        hot, cold = al.plan_archive([row(1, edge)], keep_days=180, now=NOW)
        self.assertEqual([r["id"] for r in hot], [1], "正好等于 cut-off 的行不算过期")


class ArchiveSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-ledger-archive-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ledger = self.tmp / "trading_ledger.json"
        self.archive = self.tmp / "archive"
        self.rows = [row(1, "2025-03-01 10:00:00"), row(2, "2025-03-02 10:00:00"),
                     row(3, "2026-09-01 10:00:00"), row(4, "持仓中...")]
        self._write(self.ledger, self.rows)

    def _write(self, path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_dry_run_writes_nothing(self):
        before = self.ledger.read_bytes()
        result = al.run(self.ledger, self.archive, keep_days=180, apply=False, now=NOW)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["to_archive"], 2)
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertFalse(self.archive.exists(), "dry-run 不得创建任何归档文件")

    def test_apply_archives_and_keeps_recent(self):
        result = al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        self.assertEqual(result["status"], "applied")
        hot = json.loads(self.ledger.read_text(encoding="utf-8"))
        self.assertEqual(sorted(r["id"] for r in hot), [3, 4], "近期行与不可解析行必须留在热台账")
        archived = json.loads((self.archive / "ledger_2025.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(r["id"] for r in archived), [1, 2])

    def test_no_row_is_lost(self):
        al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        hot = json.loads(self.ledger.read_text(encoding="utf-8"))
        archived = json.loads((self.archive / "ledger_2025.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(r["id"] for r in hot + archived), [1, 2, 3, 4])

    def test_idempotent(self):
        al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        first = (self.archive / "ledger_2025.json").read_text(encoding="utf-8")
        again = al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        self.assertEqual(again["status"], "noop", "已归档过的热台账不应再产生新归档")
        self.assertEqual((self.archive / "ledger_2025.json").read_text(encoding="utf-8"), first)

    def test_merges_into_existing_shard_without_overwriting(self):
        self.archive.mkdir(parents=True, exist_ok=True)
        self._write(self.archive / "ledger_2025.json", [row(99, "2025-01-01 00:00:00")])
        al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        merged = json.loads((self.archive / "ledger_2025.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(r["id"] for r in merged), [1, 2, 99], "已有分片必须合并而不是覆盖")

    def test_hot_ledger_untouched_when_archive_verification_fails(self):
        """回读校验失败 → 热台账必须保持原样（fail-closed，先归档后截断）。"""
        before = self.ledger.read_bytes()
        with mock.patch.object(al, "_load_rows", side_effect=[self.rows, []]):
            with self.assertRaises(RuntimeError):
                al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_hot_ledger_untouched_when_signature_mismatch(self):
        before = self.ledger.read_bytes()
        original_signature = al._signature
        calls = {"n": 0}

        def fake_signature(rows):
            calls["n"] += 1
            result = original_signature(rows)
            if calls["n"] == 2:  # 第二次是"归档后"的校验：伪造不一致
                result = dict(result)
                result["forged"] = 1
            return result

        with mock.patch.object(al, "_signature", side_effect=fake_signature):
            with self.assertRaises(RuntimeError):
                al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_malformed_ledger_is_refused(self):
        self._write(self.ledger, {"not": "a list"})
        with self.assertRaises(ValueError):
            al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)

    def test_cli_defaults_to_dry_run(self):
        before = self.ledger.read_bytes()
        code = al.main(["--ledger", str(self.ledger), "--archive-dir", str(self.archive), "--keep-days", "180"])
        self.assertEqual(code, 0)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_live_ledger_plan_is_conservative(self):
        """对**真实**台账演算一次：不得改动文件，且当前不该归档任何行（31 笔全在热窗口）。"""
        live = ROOT / "data" / "trading_ledger.json"
        if not live.exists():
            self.skipTest("无实盘台账")
        before = live.read_bytes()
        result = al.run(live, ROOT / "data" / "archive", keep_days=180, apply=False, now=NOW)
        self.assertEqual(live.read_bytes(), before)
        self.assertIn(result["status"], {"dry_run", "noop"})


if __name__ == "__main__":
    unittest.main()


# =====================================================================
# 第三百刀：把 fail-closed 的最后四行补齐 + 时间格式/夹取/分片
# =====================================================================

class ParseCloseTimeTests(unittest.TestCase):
    """`_TIME_FORMATS` 里四种拼写都要真的认（实测台账里都出现过）。"""

    def test_the_second_precision_format(self):
        self.assertEqual(al.parse_close_time("2026-09-14 12:30:45"),
                         datetime(2026, 9, 14, 12, 30, 45, tzinfo=BJ))

    def test_the_minute_precision_format(self):
        self.assertEqual(al.parse_close_time("2026-09-14 12:30"),
                         datetime(2026, 9, 14, 12, 30, tzinfo=BJ))

    def test_the_iso_t_separator_format(self):
        self.assertEqual(al.parse_close_time("2026-09-14T12:30:45"),
                         datetime(2026, 9, 14, 12, 30, 45, tzinfo=BJ))

    def test_the_slash_separated_format(self):
        self.assertEqual(al.parse_close_time("2026/09/14 12:30:45"),
                         datetime(2026, 9, 14, 12, 30, 45, tzinfo=BJ))

    def test_the_result_carries_the_beijing_timezone(self):
        parsed = al.parse_close_time("2026-09-14 12:30:45")
        self.assertEqual(parsed.utcoffset(), timedelta(hours=8))

    def test_the_placeholder_text_is_refused(self):
        for text in ("持仓中...", "持仓中", "--", "N/A"):
            with self.subTest(text=text):
                self.assertIsNone(al.parse_close_time(text))

    def test_blank_and_missing_are_refused(self):
        for value in (None, "", "   ", 0):
            with self.subTest(value=value):
                self.assertIsNone(al.parse_close_time(value))

    def test_whitespace_is_trimmed(self):
        self.assertIsNotNone(al.parse_close_time("  2026-09-14 12:30:45  "))

    def test_a_numeric_epoch_is_not_silently_reinterpreted(self):
        """绝不猜：Unix 时间戳这种"看着像数字"的输入一律拒绝。"""
        self.assertIsNone(al.parse_close_time(1757800000))


class KeepDaysClampTests(unittest.TestCase):
    def test_zero_days_is_clamped_to_one(self):
        """★ `max(int(keep_days), 1)` —— 0 天等于"归档所有能解析的行"，是个危险的误输入。"""
        rows = [row(1, "2026-09-14 11:00:00"), row(2, "2026-09-13 00:00:00")]
        hot, cold = al.plan_archive(rows, keep_days=0, now=NOW)
        self.assertEqual([r["id"] for r in hot], [1])
        self.assertEqual([r["id"] for r in cold], [2])

    def test_a_negative_window_is_clamped_to_one(self):
        rows = [row(1, "2026-09-14 11:00:00"), row(2, "2026-09-13 00:00:00")]
        hot, cold = al.plan_archive(rows, keep_days=-30, now=NOW)
        self.assertEqual([r["id"] for r in cold], [2])

    def test_an_empty_row_set_yields_two_empty_lists(self):
        self.assertEqual(al.plan_archive([], now=NOW), ([], []))


class ArchiveShardsTests(unittest.TestCase):
    def test_rows_are_grouped_by_close_year(self):
        cold = [row(1, "2024-05-01 10:00:00"), row(2, "2025-01-01 10:00:00"),
                row(3, "2025-12-31 23:59:59")]
        shards = al.archive_shards(cold)
        self.assertEqual(sorted(shards), ["2024", "2025"])
        self.assertEqual(len(shards["2025"]), 2)

    def test_an_unparsable_row_is_dropped_here_never_guessed_into_a_shard(self):
        """★ 本函数的防御分支：`plan_archive` 保证 cold 一定可解析，但**别处可能直接调用**。

        而这里是**唯一**会按年份分片的地方 —— 若它把不可解析的行猜进某个分片，
        台账就真的错位了。故宁可丢掉也不猜。
        """
        shards = al.archive_shards([{"id": 1, "close_time": "持仓中..."},
                                    {"id": 2}])
        self.assertEqual(shards, {})

    def test_a_mixed_batch_keeps_only_the_parsable_rows(self):
        shards = al.archive_shards([row(1, "2025-01-01 00:00:00"),
                                    {"id": 2, "close_time": "坏"}])
        self.assertEqual([r["id"] for r in shards["2025"]], [1])

    def test_an_empty_input_is_an_empty_mapping(self):
        self.assertEqual(al.archive_shards([]), {})


class ReadbackIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-ledger-readback-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ledger = self.tmp / "trading_ledger.json"
        self.archive = self.tmp / "archive"
        self.rows = [row(1, "2025-03-01 10:00:00"), row(2, "2026-09-01 10:00:00")]
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(json.dumps(self.rows, ensure_ascii=False), encoding="utf-8")

    def test_a_sha256_mismatch_aborts_before_touching_the_hot_ledger(self):
        """★ 第二道闸：条数对了但**字节不一致**（写盘被截断/磁盘撒谎）也要停。

        伪造手法：让 `_atomic_write_json` 返回一个不可能匹配的摘要。
        """
        before = self.ledger.read_bytes()
        real = al._atomic_write_json

        def _lying_write(path, payload):
            real(path, payload)
            return "0" * 64

        with mock.patch.object(al, "_atomic_write_json", _lying_write):
            with self.assertRaises(RuntimeError) as ctx:
                al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        self.assertIn("sha256 不一致", str(ctx.exception))
        self.assertEqual(self.ledger.read_bytes(), before, "校验失败必须 fail-closed")

    def test_the_reported_status_is_noop_when_nothing_qualifies(self):
        self.ledger.write_text(json.dumps([row(1, "2026-09-13 00:00:00")]), encoding="utf-8")
        result = al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        self.assertEqual(result["status"], "noop")
        self.assertFalse(self.archive.exists())

    def test_the_report_counts_unparsable_rows(self):
        self.ledger.write_text(json.dumps(
            [row(1, "2025-01-01 00:00:00"), row(2, "持仓中..."), {"id": 3}]),
            encoding="utf-8")
        result = al.run(self.ledger, self.archive, now=NOW)
        self.assertEqual(result["unparsable_kept_hot"], 2)

    def test_the_report_lists_the_shard_sizes(self):
        result = al.run(self.ledger, self.archive, now=NOW)
        self.assertEqual(result["shards"], {"2025": 1})

    def test_the_report_echoes_the_inputs(self):
        result = al.run(self.ledger, self.archive, keep_days=42, apply=False, now=NOW)
        self.assertEqual(result["keep_days"], 42)
        self.assertIs(result["apply"], False)
        self.assertEqual(result["total"], 2)
        self.assertEqual((result["keep_hot"], result["to_archive"]), (1, 1))

    def test_a_missing_ledger_raises_before_any_write(self):
        missing = self.tmp / "nope.json"
        with self.assertRaises(OSError):
            al.run(missing, self.archive, apply=True, now=NOW)
        self.assertFalse(self.archive.exists())

    def test_a_top_level_object_is_refused(self):
        self.ledger.write_text('{"rows": []}', encoding="utf-8")
        with self.assertRaises(ValueError):
            al.run(self.ledger, self.archive, apply=True, now=NOW)

    def test_the_shard_file_is_a_plain_array(self):
        al.run(self.ledger, self.archive, apply=True, now=NOW)
        self.assertIsInstance(json.loads((self.archive / "ledger_2025.json").read_text("utf-8")),
                              list)

    def test_multiple_years_produce_multiple_shards(self):
        self.ledger.write_text(json.dumps([row(1, "2024-01-01 00:00:00"),
                                           row(2, "2025-01-01 00:00:00"),
                                           row(3, "2026-09-01 00:00:00")]), encoding="utf-8")
        result = al.run(self.ledger, self.archive, apply=True, now=NOW)
        self.assertEqual(sorted(result["archived_files"]), ["ledger_2024.json", "ledger_2025.json"])
        self.assertEqual(result["hot_after"], 1)

    def test_the_atomic_writer_leaves_no_temp_files(self):
        al.run(self.ledger, self.archive, apply=True, now=NOW)
        leftovers = [p.name for p in list(self.tmp.rglob("*")) + list(self.archive.rglob("*"))
                     if p.is_file() and p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class CliFailClosedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-ledger-cli-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ledger = self.tmp / "trading_ledger.json"
        self.archive = self.tmp / "archive"

    def test_a_broken_ledger_yields_exit_code_one(self):
        """★ 闸门：任何异常都只报告、**绝不写盘**，退出码必须是 1（调度器要能看出失败）。"""
        self.ledger.write_text('{"not": "a list"}', encoding="utf-8")
        code = al.main(["--ledger", str(self.ledger), "--archive-dir", str(self.archive)])
        self.assertEqual(code, 1)
        self.assertFalse(self.archive.exists())

    def test_the_failure_report_is_machine_readable(self):
        import io
        from contextlib import redirect_stdout
        self.ledger.write_text('{"not": "a list"}', encoding="utf-8")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            al.main(["--ledger", str(self.ledger), "--archive-dir", str(self.archive)])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["status"], "failed")
        self.assertIn("ValueError", payload["reason"])

    def test_a_missing_ledger_also_yields_exit_code_one(self):
        code = al.main(["--ledger", str(self.tmp / "nope.json"),
                        "--archive-dir", str(self.archive)])
        self.assertEqual(code, 1)

    def test_apply_through_the_cli_actually_writes(self):
        self.ledger.write_text(json.dumps([row(1, "2025-01-01 00:00:00"),
                                           row(2, "2026-09-01 00:00:00")], ensure_ascii=False),
                               encoding="utf-8")
        code = al.main(["--ledger", str(self.ledger), "--archive-dir", str(self.archive),
                        "--apply"])
        self.assertEqual(code, 0)
        self.assertTrue((self.archive / "ledger_2025.json").exists())

    def test_the_main_guard_runs_the_cli(self):
        """覆盖 `if __name__ == "__main__": raise SystemExit(main())`。

        调度器/运维真的就是 `python scripts/archive_ledger.py` 这样调用它 ——
        `SystemExit` 必须带着 `main()` 的退出码冒出来。用**隔离命名空间 exec** 触发，
        并把 `sys.argv` 指到临时路径（默认不带 `--apply` ⇒ 仍然一个字节都不写）。
        """
        self.ledger.write_text(json.dumps([row(1, "2025-01-01 00:00:00")],
                                          ensure_ascii=False), encoding="utf-8")
        source = (ROOT / "scripts" / "archive_ledger.py").read_text(encoding="utf-8")
        namespace = {"__name__": "__main__",
                     "__file__": str(ROOT / "scripts" / "archive_ledger.py")}
        import io
        from contextlib import redirect_stdout
        argv = ["archive_ledger.py", "--ledger", str(self.ledger),
                "--archive-dir", str(self.archive)]
        buffer = io.StringIO()
        with mock.patch.object(sys, "argv", argv):
            with redirect_stdout(buffer):
                with self.assertRaises(SystemExit) as ctx:
                    exec(compile(source, str(ROOT / "scripts" / "archive_ledger.py"), "exec"),
                         namespace)  # noqa: S102
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("dry_run", buffer.getvalue())
        self.assertFalse(self.archive.exists(), "默认必须是 dry-run")

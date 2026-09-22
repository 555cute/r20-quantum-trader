"""自定义灾备作业 CLI（nightly_backup_and_clean.py）收口 —— 第 299 刀。

本模块是调度器实际拉起的那一层（`job_runs` 里那条命令就是它），所以它的
退出码与 stdout 是**机器可读契约**，不是给人看的日志：

- `0` = 全部成功 / 无启用的作业（后者是 `skipped`，不是失败）；
- `2` = 加载任务失败、或**任何一个**作业没成功。

本刀钉三件事：
1. **退出码语义**（调度器据此判 `success`/`failed`）；
2. **单作业异常不许拖垮整批** —— 捕获后转成一个 `failed` 结果继续跑其余作业；
3. **通知策略按作业的 `notify_on_success` / `notify_on_failure` 分流**，
   且 `notify` 自身**吞掉所有异常**（通知失败绝不能让备份作业报失败）。
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.nightly_backup_and_clean as nbc


class NotifyTests(unittest.TestCase):
    """`notify` 是纯症状输出层：绝不允许它把上层作业搞挂。"""

    def setUp(self):
        import qq_notifier
        self.qq = qq_notifier
        self.sent: list[str] = []

        def fake_send(text):
            self.sent.append(text)

        patcher = patch.object(self.qq, "send_qq_message", fake_send)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _text(self) -> str:
        self.assertEqual(len(self.sent), 1)
        return self.sent[0]

    def test_success_icon_and_fields(self):
        nbc.notify({"job_name": "夜间全量", "status": "success",
                    "started_at": "2026-09-22 02:00:00",
                    "finished_at": "2026-09-22 02:03:11",
                    "sha256": "a" * 64, "targets": [1, 2], "sqlite": [1]})
        text = self._text()
        self.assertTrue(text.startswith("✅ 【R20 自定义灾备】夜间全量"))
        self.assertIn("状态：success", text)
        self.assertIn("2026-09-22 02:00:00 - 2026-09-22 02:03:11", text)
        # sha256 只暴露前 16 位（摘要本身不是秘密，但没必要整串刷屏）
        self.assertIn("SHA256：" + "a" * 16 + "...", text)
        self.assertNotIn("a" * 17, text)
        self.assertIn("目标：2，SQLite：1", text)

    def test_partial_and_failed_icons(self):
        nbc.notify({"job_name": "A", "status": "partial"})
        self.assertTrue(self._text().startswith("⚠️"))
        self.sent.clear()
        nbc.notify({"job_name": "B", "status": "failed"})
        self.assertTrue(self._text().startswith("❌"))
        self.sent.clear()
        nbc.notify({"job_name": "C"})
        # 未知状态既不是 success 也不是 partial ⇒ 落到 ❌，且状态显示 unknown
        self.assertTrue(self._text().startswith("❌"))
        self.assertIn("状态：unknown", self._text())

    def test_missing_sha256_line_is_omitted(self):
        nbc.notify({"job_name": "A", "status": "success"})
        self.assertNotIn("SHA256", self._text())

    def test_errors_are_joined_and_truncated_to_600(self):
        errors = ["E" * 400, "F" * 400]
        nbc.notify({"job_name": "A", "status": "failed", "errors": errors})
        text = self._text()
        self.assertIn("错误：", text)
        payload = text.split("错误：", 1)[1]
        self.assertEqual(len(payload), 600)

    def test_send_failure_is_swallowed(self):
        with patch.object(self.qq, "send_qq_message",
                          side_effect=RuntimeError("qq down")):
            nbc.notify({"job_name": "A", "status": "success"})  # 不许抛
        self.assertEqual(self.sent, [])

    def test_missing_qq_notifier_is_swallowed(self):
        with patch.dict(sys.modules, {"qq_notifier": None}):
            nbc.notify({"job_name": "A", "status": "success"})  # 不许抛
        self.assertEqual(self.sent, [])


class MainTests(unittest.TestCase):
    """`main()` 的退出码 / stdout 是调度器读的契约。"""

    def setUp(self):
        self.jobs_calls: list = []
        self.ran: list = []
        self.notified: list = []
        self.staging_calls: list = []

        def _noop_staging():
            self.staging_calls.append(True)

        def _notify(result):
            self.notified.append(result)

        for name, value in (("clean_stale_staging", _noop_staging),
                            ("notify", _notify)):
            patcher = patch.object(nbc, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _patch(self, name, value):
        patcher = patch.object(nbc, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def _run(self, argv, *, jobs=None, job=None, runner=None):
        def list_jobs():
            self.jobs_calls.append("list")
            return jobs if jobs is not None else []

        def get_job(job_id):
            self.jobs_calls.append(("get", job_id))
            if isinstance(job, Exception):
                raise job
            return job

        def run_backup_job(j):
            self.ran.append(j)
            if isinstance(runner, Exception):
                raise runner
            return runner(j) if callable(runner) else runner

        self._patch("list_jobs", list_jobs)
        self._patch("get_job", get_job)
        self._patch("run_backup_job", run_backup_job)
        buf = io.StringIO()
        with patch.object(sys, "argv", ["nightly_backup_and_clean.py"] + argv), \
             contextlib.redirect_stdout(buf):
            rc = nbc.main()
        return rc, buf.getvalue()

    def test_single_job_success_returns_zero(self):
        rc, out = self._run(["--job-id", "7"], job={"id": 7, "name": "n"},
                            runner={"job_id": 7, "job_name": "n", "status": "success"})
        self.assertEqual(rc, 0)
        self.assertEqual(self.jobs_calls, [("get", "7")])
        self.assertEqual(json.loads(out.strip())["status"], "success")

    def test_all_enabled_filters_disabled_jobs(self):
        jobs = [{"id": 1, "enabled": True}, {"id": 2, "enabled": False},
                {"id": 3, "enabled": True}]
        rc, _ = self._run([], jobs=jobs,
                          runner=lambda j: {"job_id": j["id"], "status": "success"})
        self.assertEqual(rc, 0)
        self.assertEqual([j["id"] for j in self.ran], [1, 3])

    def test_all_enabled_flag_is_accepted(self):
        # ⚠️ 实测：`--all-enabled` 被 argparse 接受但**从未被读取**（死参数）——
        # 不带 --job-id 时本来就会列全部 enabled 作业，故该开关不改变任何行为。
        rc, _ = self._run(["--all-enabled"], jobs=[{"id": 1, "enabled": True}],
                          runner={"status": "success"})
        self.assertEqual(rc, 0)
        self.assertEqual([j["id"] for j in self.ran], [1])

    def test_no_enabled_jobs_is_skipped_not_failed(self):
        rc, out = self._run([], jobs=[])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.strip())["status"], "skipped")
        self.assertEqual(self.ran, [])

    def test_missing_job_id_reports_value_error_and_exits_2(self):
        rc, out = self._run(["--job-id", "999"], job=ValueError("no such job"))
        payload = json.loads(out.strip())
        self.assertEqual(rc, 2)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("灾备任务不存在", payload["reason"])
        self.assertEqual(self.ran, [])

    def test_load_failure_reports_exception_type_and_exits_2(self):
        rc, out = self._run([], jobs=[])
        self.assertEqual(rc, 0)  # 空列表不是失败
        self._patch("list_jobs", lambda: (_ for _ in ()).throw(OSError("db locked")))
        buf = io.StringIO()
        with patch.object(sys, "argv", ["x"]), contextlib.redirect_stdout(buf):
            rc = nbc.main()
        payload = json.loads(buf.getvalue().strip())
        self.assertEqual(rc, 2)
        self.assertIn("加载灾备任务失败", payload["reason"])
        self.assertIn("OSError", payload["reason"])

    def test_job_exception_becomes_failed_result_and_batch_continues(self):
        jobs = [{"id": 1, "enabled": True, "name": "boom"},
                {"id": 2, "enabled": True, "name": "ok"}]

        def runner(j):
            if j["id"] == 1:
                raise RuntimeError("archive failed")
            return {"job_id": 2, "status": "success"}

        rc, out = self._run([], jobs=jobs, runner=runner)
        lines = [json.loads(l) for l in out.strip().splitlines() if l.strip()]
        self.assertEqual(rc, 2)                      # 有一个没成功 ⇒ 2
        self.assertEqual(len(lines), 2)              # 两个作业都跑了
        self.assertEqual(lines[0]["status"], "failed")
        self.assertIn("未捕获任务异常", lines[0]["errors"][0])
        self.assertIn("RuntimeError: archive failed", lines[0]["errors"][0])
        # 失败结果也必须带齐契约字段（下游/通知都按这些键取）
        self.assertEqual(lines[0]["targets"], [])
        self.assertEqual(lines[0]["sqlite"], [])
        self.assertEqual(lines[0]["job_id"], 1)
        self.assertEqual(lines[0]["job_name"], "boom")
        self.assertEqual(lines[1]["status"], "success")

    def test_all_success_returns_zero(self):
        jobs = [{"id": 1, "enabled": True}, {"id": 2, "enabled": True}]
        rc, out = self._run([], jobs=jobs,
                            runner=lambda j: {"job_id": j["id"], "status": "success"})
        self.assertEqual(rc, 0)
        self.assertEqual(len(out.strip().splitlines()), 2)

    def test_notify_only_when_job_opts_in(self):
        jobs = [
            {"id": 1, "enabled": True, "notify_on_success": True},
            {"id": 2, "enabled": True, "notify_on_success": False},
            {"id": 3, "enabled": True, "notify_on_failure": True},
            {"id": 4, "enabled": True, "notify_on_failure": False},
        ]

        def runner(j):
            return {"job_id": j["id"],
                    "status": "success" if j["id"] in (1, 2) else "failed"}

        self._run([], jobs=jobs, runner=runner)
        self.assertEqual([r["job_id"] for r in self.notified], [1, 3])

    def test_stale_staging_failure_does_not_stop_the_batch(self):
        jobs = [{"id": 1, "enabled": True}]
        self._patch("clean_stale_staging",
                    lambda: (_ for _ in ()).throw(OSError("no perm")))
        rc, out = self._run([], jobs=jobs, runner={"status": "success"})
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.strip())["status"], "success")

    def test_staging_is_cleaned_before_jobs_run(self):
        order = []
        self._patch("clean_stale_staging", lambda: order.append("clean"))
        jobs = [{"id": 1, "enabled": True}]

        def runner(j):
            order.append("run")
            return {"status": "success"}

        self._run([], jobs=jobs, runner=runner)
        self.assertEqual(order, ["clean", "run"])


class MainGuardTests(unittest.TestCase):
    """`if __name__ == "__main__"` 必须以 `main()` 的退出码结束进程。"""

    def _run_guard(self, rc):
        src = Path(nbc.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        node = next(n for n in tree.body
                    if isinstance(n, ast.If) and n.lineno == 85)
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {"__name__": "__main__", "main": lambda: rc}
        with self.assertRaises(SystemExit) as ctx:
            exec(compile(module, nbc.__file__, "exec"), namespace)  # noqa: S102
        return ctx.exception.code

    def test_success_code_propagates(self):
        self.assertEqual(self._run_guard(0), 0)

    def test_failure_code_propagates(self):
        self.assertEqual(self._run_guard(2), 2)


if __name__ == "__main__":
    unittest.main()

"""受管 Agent 只读登记表（第二百九十七刀，开新面 agents.py）。

先打印整个文件（33 行）再动笔。它把「作业运行记录 + 产物文件新鲜度」折算成一张
给人看的健康表。

| 语义 | 口径 |
|---|---|
| ★ **取每个作业的"最新一条"靠的是输入顺序** | `setdefault` 只记**第一次遇到**的 job_name，所以调用方给的 `job_runs` 必须是**新的在前**。这个前提不在函数签名里，故专门钉一条 |
| ★ **三档健康度的判定次序** | 先默认 `healthy`；`status == "failed"` ⇒ `degraded`（**覆盖** healthy）；再判「有产物但文件不存在」⇒ `cold`（**覆盖**前面两者，优先级最高）。三条各一测，含相互覆盖 |
| ★ **`not-run` 与空串是两种"没跑过"** | 没有运行记录时 `last_run_status` 是字面量 `not-run`，而 `last_run_at` 是**空串** —— 前端要能区分"没跑过"与"跑了但没记开始时间" |
| ★ **产物年龄非负** | `max(0, ...)` —— 时钟回拨/未来 mtime 不许报出负数年龄 |
| ★ **无产物作业永不 `cold`** | `backup_agent` 的 `output` 为空串，既不 stat 也不判冷 |
| ★ **`health` 字段会被 `{**agent, ...}` 覆盖而非并存** | 登记表本身没有 `health` 键，故不会出现两套健康度 |

## 封闭性

`ROOT / "data" / agent["output"]` 默认指向**生产 `data/`**（只读 stat，但仍要钉住环境），
故 `setUp` 把 `agents.ROOT` 改写到临时目录，`time.time()` 打桩为固定值以精确断言年龄。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from r20_gateway import agents as AG


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self._start(mock.patch.object(AG, "ROOT", self.root))
        self.now = 1_800_000_000.0
        self._start(mock.patch.object(AG.time, "time", return_value=self.now))

    def _artifact(self, name, age_seconds=0):
        path = self.data / name
        path.write_text("x", encoding="utf-8")
        import os
        os.utime(path, (self.now - age_seconds, self.now - age_seconds))
        return path

    def _agent(self, payload, agent_id):
        return next(a for a in payload if a["id"] == agent_id)

    def _statuses(self, runs=()):
        return AG.agent_statuses(list(runs))


class RegistryShapeTests(_Base):
    def test_every_agent_is_returned(self):
        self.assertEqual(len(self._statuses()), len(AG.AGENTS))

    def test_the_registry_fields_are_passed_through(self):
        row = self._agent(self._statuses(), "trading_brain")
        self.assertEqual(row["name"], "交易主脑")
        self.assertEqual(row["role"], "全市场决策与持仓裁决")
        self.assertEqual(row["job"], "trader")
        self.assertEqual(row["prompt_transport"], "python-direct")

    def test_the_display_fields_are_appended(self):
        row = self._agent(self._statuses(), "trading_brain")
        for key in ("health", "output_age_seconds", "last_run_status", "last_run_at"):
            self.assertIn(key, row)

    def test_health_does_not_shadow_an_existing_registry_key(self):
        for agent in AG.AGENTS:
            self.assertNotIn("health", agent)
        self.assertEqual(len([a for a in self._statuses()]),
                         len(AG.AGENTS))

    def test_every_agent_has_a_unique_id_and_a_job(self):
        ids = [a["id"] for a in AG.AGENTS]
        jobs = [a["job"] for a in AG.AGENTS]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(len(set(jobs)), len(jobs))


class NoOutputTests(_Base):
    def test_an_agent_without_an_output_is_never_cold(self):
        row = self._agent(self._statuses(), "backup_agent")
        self.assertIsNone(row["output_age_seconds"])
        self.assertEqual(row["health"], "healthy")

    def test_an_agent_without_an_output_is_not_degraded_by_a_missing_file(self):
        self.assertNotEqual(self._agent(self._statuses(), "backup_agent")["health"], "cold")


class OutputAgeTests(_Base):
    def test_a_fresh_artifact_reports_its_age(self):
        self._artifact("ai_brain_decisions.json", age_seconds=120)
        row = self._agent(self._statuses(), "trading_brain")
        self.assertEqual(row["output_age_seconds"], 120)
        self.assertEqual(row["health"], "healthy")

    def test_a_missing_artifact_is_cold(self):
        row = self._agent(self._statuses(), "trading_brain")
        self.assertIsNone(row["output_age_seconds"])
        self.assertEqual(row["health"], "cold")

    def test_a_future_mtime_never_yields_a_negative_age(self):
        """★ 时钟回拨（或 NTP 校正）不许让年龄变成负数。"""
        self._artifact("ai_brain_decisions.json", age_seconds=-500)
        self.assertEqual(self._agent(self._statuses(), "trading_brain")["output_age_seconds"],
                         0)

    def test_the_age_is_truncated_to_whole_seconds(self):
        self._artifact("ai_brain_decisions.json", age_seconds=10.7)
        self.assertEqual(self._agent(self._statuses(), "trading_brain")["output_age_seconds"],
                         10)

    def test_each_agent_reads_its_own_artifact(self):
        self._artifact("factor_library_snapshot.json", age_seconds=60)
        payload = self._statuses()
        self.assertEqual(self._agent(payload, "factor_engine")["output_age_seconds"], 60)
        self.assertEqual(self._agent(payload, "trading_brain")["health"], "cold")

    def test_a_directory_in_place_of_an_artifact_still_reports_an_age(self):
        (self.data / "news_sentiment.json").mkdir()
        row = self._agent(self._statuses(), "news_engine")
        self.assertEqual(row["health"], "healthy")


class HealthPrecedenceTests(_Base):
    def test_a_failed_last_run_is_degraded(self):
        """产物必须在场 —— 否则 `cold` 会盖住 `degraded`，这条就测不到 degraded。"""
        self._artifact("ai_brain_decisions.json", age_seconds=5)
        row = self._agent(self._statuses([{"job_name": "trader", "status": "failed"}]),
                          "trading_brain")
        self.assertEqual(row["health"], "degraded")

    def test_a_successful_run_without_an_artifact_is_still_cold(self):
        row = self._agent(self._statuses([{"job_name": "trader", "status": "success"}]),
                          "trading_brain")
        self.assertEqual(row["health"], "cold",
                         "产物文件不存在 ⇒ cold 覆盖 healthy")

    def test_a_missing_artifact_outranks_a_failure(self):
        """★ 优先级：`cold`（有产物但文件不在）**覆盖** `degraded`。"""
        runs = [{"job_name": "trader", "status": "failed"}]
        self.assertEqual(self._agent(self._statuses(runs), "trading_brain")["health"], "cold")

    def test_a_failure_outranks_a_fresh_artifact(self):
        self._artifact("ai_brain_decisions.json", age_seconds=5)
        runs = [{"job_name": "trader", "status": "failed"}]
        self.assertEqual(self._agent(self._statuses(runs), "trading_brain")["health"],
                         "degraded")

    def test_a_fresh_artifact_and_a_successful_run_is_healthy(self):
        self._artifact("ai_brain_decisions.json", age_seconds=5)
        runs = [{"job_name": "trader", "status": "success"}]
        self.assertEqual(self._agent(self._statuses(runs), "trading_brain")["health"],
                         "healthy")

    def test_an_unrelated_job_failure_does_not_leak_into_another_agent(self):
        self._artifact("news_sentiment.json", age_seconds=5)
        runs = [{"job_name": "trader", "status": "failed"}]
        payload = self._statuses(runs)
        self.assertEqual(self._agent(payload, "news_engine")["health"], "healthy")
        self.assertEqual(self._agent(payload, "trading_brain")["health"], "cold")

    def test_an_unknown_status_is_treated_as_healthy(self):
        self._artifact("ai_brain_decisions.json", age_seconds=5)
        runs = [{"job_name": "trader", "status": "running"}]
        self.assertEqual(self._agent(self._statuses(runs), "trading_brain")["health"],
                         "healthy")


class LatestRunTests(_Base):
    def test_the_first_entry_for_a_job_wins(self):
        """★ `setdefault` ⇒ 调用方必须**新的在前**。这个前提不在签名里。"""
        runs = [{"job_name": "trader", "status": "running", "started_at": "新"},
                {"job_name": "trader", "status": "failed", "started_at": "旧"}]
        row = self._agent(self._statuses(runs), "trading_brain")
        self.assertEqual(row["last_run_status"], "running")
        self.assertEqual(row["last_run_at"], "新")

    def test_an_agent_without_any_run_reports_not_run(self):
        row = self._agent(self._statuses([{"job_name": "news", "status": "failed"}]),
                          "trading_brain")
        self.assertEqual(row["last_run_status"], "not-run")

    def test_an_agent_without_any_run_has_an_empty_start_time(self):
        row = self._agent(self._statuses(), "trading_brain")
        self.assertEqual(row["last_run_at"], "")

    def test_an_empty_run_list_is_tolerated(self):
        self.assertEqual(len(self._statuses()), len(AG.AGENTS))

    def test_a_run_without_a_started_at_yields_an_empty_start_time(self):
        runs = [{"job_name": "trader", "status": "success"}]
        self.assertEqual(self._agent(self._statuses(runs), "trading_brain")["last_run_at"], "")

    def test_a_run_missing_a_job_name_does_not_break_lookup(self):
        runs = [{"status": "failed"}, {"job_name": "trader", "status": "success"}]
        self.assertEqual(self._agent(self._statuses(runs), "trading_brain")["last_run_status"],
                         "success")

    def test_each_agent_reads_its_own_job_history(self):
        runs = [{"job_name": "news", "status": "failed"},
                {"job_name": "factor_library", "status": "success"}]
        payload = self._statuses(runs)
        self.assertEqual(self._agent(payload, "news_engine")["last_run_status"], "failed")
        self.assertEqual(self._agent(payload, "factor_engine")["last_run_status"], "success")


class RealRootContractTests(_Base):
    def test_the_real_registry_root_is_the_repo_root(self):
        """反证：临时 ROOT 只是测试脚手架，真登记表的 ROOT 仍是仓库根。"""
        real_root = Path(AG.__file__).resolve().parents[1]
        self.assertTrue((real_root / "r20_gateway" / "agents.py").exists())
        self.assertNotEqual(real_root, self.root)

    def test_agent_statuses_does_not_mutate_the_registry(self):
        before = [dict(a) for a in AG.AGENTS]
        self._statuses([{"job_name": "trader", "status": "failed"}])
        self.assertEqual(AG.AGENTS, tuple(before))


if __name__ == "__main__":
    unittest.main()

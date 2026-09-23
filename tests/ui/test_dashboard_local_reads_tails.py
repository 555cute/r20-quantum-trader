"""本地快照与历史读取装配（`r20_backend/dashboard_payload/local_reads.py`）残余分支收口测试 —— 第 359 刀。

本模块 108 行，负责操盘看板本地快照曲线、复盘情报、长提示词历史收敛与磁盘信息装配：
- 快照文件损坏自愈（`snapshots_file` 读取/解析异常静默忽略，只追加当前实时快照点）；
- 历史周期长提示词收敛（`history_file` 历史第 2 条起长于 500 字符的 `ai_last_prompt` 自动截断为 200 字符并追加 `...(历史已收敛)`）；
- 历史文件损坏容错（`history_file` 读取抛异常安全自愈返回空列表）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from r20_backend.dashboard_payload.local_reads import load_local_reads


class DashboardLocalReadsTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_dashboard_local_reads_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.dummy_file = self.tmp_path / "dummy.json"
        self.dummy_file.write_text("{}", encoding="utf-8")

    # -------------------------------------------------------------------------
    # 1. 快照文件读取容错 (snapshots_file)
    # -------------------------------------------------------------------------
    def test_load_local_reads_corrupt_snapshots_file_graceful(self):
        # 快照文件损坏时忽略异常，仅保留当前实时快照点 (lines 55-56)
        corrupt_snap = self.tmp_path / "corrupt_snapshots.json"
        corrupt_snap.write_text("{ corrupt json", encoding="utf-8")

        res = load_local_reads(
            report_file=self.dummy_file,
            snapshots_file=corrupt_snap,
            news_file=self.dummy_file,
            last_prompt_file=self.dummy_file,
            history_file=self.dummy_file,
            factor_file=self.dummy_file,
            load_memory_md=lambda: "",
            reset_time_str="2026-09-01 00:00:00",
            initial_capital_val=1000.0,
            total_eq=1500.0,
            timestamp_full="2026-09-22 12:00:00",
        )
        self.assertIn("snapshots_list", res)
        self.assertEqual(len(res["snapshots_list"]), 1)
        self.assertEqual(res["snapshots_list"][0]["total_eq"], 1500.0)

    # -------------------------------------------------------------------------
    # 2. 决策历史长提示词收敛与坏文件容错 (history_file)
    # -------------------------------------------------------------------------
    def test_load_local_reads_history_long_prompt_trimmed(self):
        # 历史第 2 条起（idx > 0）长提示词（> 500 字符）自动截断 (lines 79-80)
        history_file = self.tmp_path / "ai_brain_history.json"
        history_records = [
            {"time": "2026-09-22 10:00:00", "ai_last_prompt": "Prompt 0: " + ("X" * 600)},  # idx=0 不收敛
            {"time": "2026-09-22 09:00:00", "ai_last_prompt": "Prompt 1: " + ("Y" * 600)},  # idx=1 收敛
        ]
        history_file.write_text(json.dumps(history_records), encoding="utf-8")

        res = load_local_reads(
            report_file=self.dummy_file,
            snapshots_file=self.dummy_file,
            news_file=self.dummy_file,
            last_prompt_file=self.dummy_file,
            history_file=history_file,
            factor_file=self.dummy_file,
            load_memory_md=lambda: "",
            reset_time_str="2026-09-01 00:00:00",
            initial_capital_val=1000.0,
            total_eq=1500.0,
            timestamp_full="2026-09-22 12:00:00",
        )
        hist = res["ai_history_list"]
        self.assertEqual(len(hist), 2)
        # 首条不收敛
        self.assertGreater(len(hist[0]["ai_last_prompt"]), 500)
        # 次条截断为 200 + 后缀
        self.assertTrue(hist[1]["ai_last_prompt"].endswith("...(历史已收敛)"))
        self.assertEqual(len(hist[1]["ai_last_prompt"]), 200 + len("...(历史已收敛)"))

    def test_load_local_reads_corrupt_history_file_graceful(self):
        # 历史文件损坏时安全返回空历史列表 (lines 82-83)
        corrupt_hist = self.tmp_path / "corrupt_history.json"
        corrupt_hist.write_text("{ corrupt json", encoding="utf-8")

        res = load_local_reads(
            report_file=self.dummy_file,
            snapshots_file=self.dummy_file,
            news_file=self.dummy_file,
            last_prompt_file=self.dummy_file,
            history_file=corrupt_hist,
            factor_file=self.dummy_file,
            load_memory_md=lambda: "",
            reset_time_str="2026-09-01 00:00:00",
            initial_capital_val=1000.0,
            total_eq=1500.0,
            timestamp_full="2026-09-22 12:00:00",
        )
        self.assertEqual(res["ai_history_list"], [])


if __name__ == "__main__":
    unittest.main()

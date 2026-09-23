"""看板健康度与记忆渲染（`r20_backend/dashboard_payload/health.py`）残余分支收口测试 —— 第 348 刀。

本模块 178 行，负责心法渲染、记忆新鲜度标注、AI 决策健康度与跨所协调数据装配：
- 心法降级读取（`load_trading_memory_md`）：渲染异常降级、传统 Markdown 读取与读取失败自愈；
- 记忆新鲜度标注（`_memory_freshness_note`）：非标准时间戳安全截断、无时间戳条目格式化、坏 JSON 自愈；
- AI 决策健康度（`build_ai_health`）：决策年龄计算、委员会状态及历史周期对齐；
- 跨所协调快照（`_load_cross_venue_data`）：非法空资产跳过、缺失健位补齐与基差计算。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r20_backend.dashboard_payload.health import (
    _load_cross_venue_data,
    _memory_freshness_note,
    build_ai_health,
    load_trading_memory_md,
)


class DashboardHealthTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_dashboard_health_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.mem_file = self.tmp_path / "trading_memory.md"
        self.struct_file = self.tmp_path / "structured_trading_memory.json"
        self.dec_file = self.tmp_path / "ai_brain_decisions.json"
        self.council_file = self.tmp_path / "council_config.json"
        self.venue_file = self.tmp_path / "venue_health.json"

    # -------------------------------------------------------------------------
    # 1. 心法渲染与降级 (load_trading_memory_md)
    # -------------------------------------------------------------------------
    def test_load_trading_memory_md_render_exception_falls_back_to_markdown_file(self):
        # evolution_shield.render_trading_memory 抛异常时降级读取传统 markdown 文件 (lines 31-38)
        self.mem_file.write_text("# Fallback Markdown Content", encoding="utf-8")
        with patch("scripts.evolution_shield.render_trading_memory", side_effect=RuntimeError("render fail")):
            res = load_trading_memory_md(self.mem_file, self.tmp_path)
            self.assertEqual(res, "# Fallback Markdown Content")

    def test_load_trading_memory_md_file_read_error_returns_empty(self):
        # 降级读取传统文件时发生异常返回空字符串 (lines 39-40)
        self.mem_file.write_text("content", encoding="utf-8")
        with patch("scripts.evolution_shield.render_trading_memory", return_value=""):
            with patch("builtins.open", side_effect=OSError("permission denied")):
                res = load_trading_memory_md(self.mem_file, self.tmp_path)
                self.assertEqual(res, "")

    def test_load_trading_memory_md_non_existent_file_returns_empty(self):
        # 文件不存在时返回空字符串 (line 41)
        with patch("scripts.evolution_shield.render_trading_memory", return_value=""):
            res = load_trading_memory_md(self.tmp_path / "not_exist.md", self.tmp_path)
            self.assertEqual(res, "")

    # -------------------------------------------------------------------------
    # 2. 记忆新鲜度标注 (_memory_freshness_note)
    # -------------------------------------------------------------------------
    def test_memory_freshness_note_invalid_iso_date_truncated(self):
        # 时间戳非标准 ISO 格式时异常截断取前 19 位 (line 59)
        self.struct_file.write_text(
            json.dumps({"lessons": [{"created_at": "invalid-timestamp-2026-09-22"}]}),
            encoding="utf-8",
        )
        res = _memory_freshness_note(self.tmp_path)
        self.assertIn("最近更新 invalid-timestamp-2 (UTC+8)", res)

    def test_memory_freshness_note_no_timestamps_renders_count(self):
        # 心法条目无 created_at 时间戳时仅渲染条目数 (line 65)
        self.struct_file.write_text(
            json.dumps({"lessons": [{"rule_text": "no stamp rule"}]}),
            encoding="utf-8",
        )
        res = _memory_freshness_note(self.tmp_path)
        self.assertIn("共 1 条心法", res)
        self.assertNotIn("最近更新", res)

    def test_memory_freshness_note_corrupt_json_returns_empty(self):
        # 结构化记忆文件损坏时安全返回空字符串 (line 67)
        self.struct_file.write_text("{ corrupt json", encoding="utf-8")
        self.assertEqual(_memory_freshness_note(self.tmp_path), "")

    # -------------------------------------------------------------------------
    # 3. AI 决策健康度 (build_ai_health)
    # -------------------------------------------------------------------------
    def test_build_ai_health_full_assembly(self):
        # 正常组装决策文件年龄、委员会配置与周期状态 (lines 81, 86-88, 92-94)
        self.dec_file.write_text(
            json.dumps({"BTC-USDT": {"timestamp": 1720000000}}),
            encoding="utf-8",
        )
        self.council_file.write_text(
            json.dumps({"enabled": True, "timeout_seconds": 45}),
            encoding="utf-8",
        )
        history = [{"time": "2026-09-22 12:00:00", "council_status": {"ok": True, "passed": 3}}]
        health = build_ai_health(self.tmp_path, history)

        self.assertGreaterEqual(health["decision_age_seconds"], 0)
        self.assertTrue(health["council_enabled"])
        self.assertEqual(health["council_timeout"], 45)
        self.assertEqual(health["last_cycle_time"], "2026-09-22 12:00:00")
        self.assertEqual(health["last_council_status"], {"ok": True, "passed": 3})

    def test_build_ai_health_decisions_read_exception_handled(self):
        # 决策文件读取异常安全跳过 (line 83)
        self.dec_file.write_text("bad-json", encoding="utf-8")
        health = build_ai_health(self.tmp_path, [])
        self.assertNotIn("decision_age_seconds", health)

    # -------------------------------------------------------------------------
    # 4. 跨所协调快照 (_load_cross_venue_data)
    # -------------------------------------------------------------------------
    def test_load_cross_venue_data_empty_asset_name_skipped(self):
        # 决策条目中无法提取有效 asset 时跳过 (line 147)
        self.dec_file.write_text(
            json.dumps({"-SWAP": {"name": "", "xvenue": {}}}),
            encoding="utf-8",
        )
        res = _load_cross_venue_data(self.tmp_path, self.dec_file)
        self.assertEqual(res["by_asset"], {})


if __name__ == "__main__":
    unittest.main()

"""投委会预设对齐 + 配置导入/导出回归测试(2026-09-09)。

覆盖：
1. 新预设与提示词工坊宪法对齐(无"6大"硬编码、无写死保证金比例、全员引用风险预算)；
2. 未改动的旧出厂提示词按哈希迁移为新预设，用户定制一律保留；
3. export/import 往返、裸格式兼容、CIO 仲裁席强制、字段清洗与导入前自动备份。
所有用例在临时目录运行，绝不触碰生产 data/council_config.json。
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from r20_backend import council_manager as cm


class CouncilPresetAlignmentTests(unittest.TestCase):
    def test_presets_free_of_legacy_hardcodes(self):
        for rid, tpl in cm.DEFAULT_PRESET_TEMPLATES.items():
            prompt = tpl["prompt"]
            self.assertNotIn("6 大", prompt, rid)
            self.assertNotIn("6大", prompt, rid)
            self.assertIn("风险预算", prompt, rid)
        self.assertNotIn("5%~15%", cm.DEFAULT_PRESET_TEMPLATES["trader_trend"]["prompt"])
        self.assertNotIn("8%~15%", cm.DEFAULT_PRESET_TEMPLATES["trader_momentum"]["prompt"])
        self.assertNotIn("2.5R", cm.DEFAULT_PRESET_TEMPLATES["trader_momentum"]["prompt"])

    def test_presets_no_template_literal_leaks(self):
        for rid, tpl in cm.DEFAULT_PRESET_TEMPLATES.items():
            self.assertNotIn("if False else", tpl["prompt"], rid)
            self.assertNotIn("{'", tpl["prompt"], rid)

    def test_cio_contract_keywords(self):
        cio = cm.DEFAULT_PRESET_TEMPLATES["cio"]["prompt"]
        for kw in ("裁决优先级", "position_management", "pending_orders_management",
                   "adopted_role", "REJECT_ALL", "4H Fail-Closed"):
            self.assertIn(kw, cio)

    def test_runtime_prompts_enforce_quote_sheet_format(self):
        """参谋报价单格式强制在运行时模板(代码层)，任何定制/导入的角色提示词都绕不开。"""
        src = Path(cm.__file__).read_text(encoding="utf-8")
        self.assertIn("提案输出格式（强制）", src)
        self.assertIn("标的 | 倾向 | 限价 | 止损 | 止盈 | 拟用保证金(USDT) | 置信度(0-100) | 一句话依据", src)
        self.assertIn("标准报价单", src)


class CouncilPresetMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_file = cm.COUNCIL_CONFIG_FILE
        self._orig_dir = cm.DATA_DIR
        self._orig_hashes = cm._LEGACY_PRESET_PROMPT_HASHES
        cm.DATA_DIR = Path(self._tmp.name)
        cm.COUNCIL_CONFIG_FILE = Path(self._tmp.name) / "council_config.json"

    def tearDown(self):
        cm.COUNCIL_CONFIG_FILE = self._orig_file
        cm.DATA_DIR = self._orig_dir
        cm._LEGACY_PRESET_PROMPT_HASHES = self._orig_hashes
        self._tmp.cleanup()

    def _seed(self, prompt_text: str) -> None:
        config = {
            "enabled": False,
            "consensus_mode": "standard",
            "timeout_seconds": 60.0,
            "roles": {"trader_trend": {
                "id": "trader_trend", "name": "T", "prompt": prompt_text,
                "weight": 0.35, "enabled": True, "is_arbitrator": False, "model_id": "keep-me",
            }, "cio": dict(cm.DEFAULT_PRESET_TEMPLATES["cio"])},
        }
        cm.COUNCIL_CONFIG_FILE.write_text(json.dumps(config), encoding="utf-8")

    def test_untouched_legacy_prompt_is_migrated(self):
        legacy_text = "【旧出厂文案】模拟内容"
        digest = hashlib.sha256(legacy_text.encode("utf-8")).hexdigest()[:16]
        cm._LEGACY_PRESET_PROMPT_HASHES = {"trader_trend": digest}
        self._seed(legacy_text)
        loaded = cm.load_council_config()
        self.assertEqual(loaded["roles"]["trader_trend"]["prompt"],
                         cm.DEFAULT_PRESET_TEMPLATES["trader_trend"]["prompt"])
        self.assertEqual(loaded["roles"]["trader_trend"]["model_id"], "keep-me")
        on_disk = json.loads(cm.COUNCIL_CONFIG_FILE.read_text(encoding="utf-8"))
        self.assertIn("风险预算", on_disk["roles"]["trader_trend"]["prompt"])

    def test_customized_prompt_preserved(self):
        custom = "【用户自定义策略】我的独家审查纪律"
        cm._LEGACY_PRESET_PROMPT_HASHES = {"trader_trend": "0000000000000000"}
        self._seed(custom)
        loaded = cm.load_council_config()
        self.assertEqual(loaded["roles"]["trader_trend"]["prompt"], custom)


class CouncilImportExportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_file = cm.COUNCIL_CONFIG_FILE
        self._orig_dir = cm.DATA_DIR
        cm.DATA_DIR = Path(self._tmp.name)
        cm.COUNCIL_CONFIG_FILE = Path(self._tmp.name) / "council_config.json"

    def tearDown(self):
        cm.COUNCIL_CONFIG_FILE = self._orig_file
        cm.DATA_DIR = self._orig_dir
        self._tmp.cleanup()

    def test_export_import_roundtrip_with_backup(self):
        cm.save_council_config({
            "enabled": True, "consensus_mode": "cross_examination", "timeout_seconds": 90,
            "roles": cm.DEFAULT_PRESET_TEMPLATES,
        })
        pkg = cm.export_council_config()
        self.assertEqual(pkg["format"], "r20-council-config")
        self.assertEqual(set(pkg["config"]["roles"]), set(cm.DEFAULT_PRESET_TEMPLATES))

        cm.save_council_config({
            "enabled": False, "consensus_mode": "standard", "timeout_seconds": 60,
            "roles": {"cio": dict(cm.DEFAULT_PRESET_TEMPLATES["cio"])},
        })
        result = cm.import_council_config(pkg)
        self.assertEqual(result["consensus_mode"], "cross_examination")
        self.assertTrue(result["backup_file"].startswith("council_config_backup_"))
        self.assertTrue((cm.DATA_DIR / result["backup_file"]).is_file())
        restored = cm.load_council_config()
        self.assertEqual(set(restored["roles"]), set(cm.DEFAULT_PRESET_TEMPLATES))

    def test_import_bare_roles_format_and_sanitization(self):
        result = cm.import_council_config({"roles": {
            "trader_x": {"prompt": "x" * 10, "weight": 99, "temperature": -3,
                         "reasoning_effort": "warp-speed", "model_id": "m" * 300},
            "cio": {"prompt": "仲裁契约", "is_arbitrator": True},
        }})
        cfg = cm.load_council_config()
        role = cfg["roles"]["trader_x"]
        self.assertEqual(role["weight"], 1.0)
        self.assertEqual(role["temperature"], 0.0)
        self.assertEqual(role["reasoning_effort"], "medium")
        self.assertLessEqual(len(role["model_id"]), 80)
        self.assertIn("cio", result["roles"])

    def test_import_rejects_missing_arbitrator_and_bad_payloads(self):
        with self.assertRaises(ValueError):
            cm.import_council_config({"roles": {"trader_a": {"prompt": "只有兵没有官"}}})
        with self.assertRaises(ValueError):
            cm.import_council_config({"roles": {}})
        with self.assertRaises(ValueError):
            cm.import_council_config({"roles": {"cio": {"prompt": "   "}}})
        with self.assertRaises(ValueError):
            cm.import_council_config("不是对象")

    def test_import_keeps_at_most_10_backups(self):
        for i in range(12):
            cm.save_council_config({"enabled": False, "consensus_mode": "standard",
                                    "timeout_seconds": 60, "roles": cm.DEFAULT_PRESET_TEMPLATES})
            cm._backup_council_config()
        backups = sorted(cm.DATA_DIR.glob("council_config_backup_*.json"))
        self.assertLessEqual(len(backups), 10)


if __name__ == "__main__":
    unittest.main()

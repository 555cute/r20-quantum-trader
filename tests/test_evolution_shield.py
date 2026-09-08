import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import evolution_shield as shield
from scripts.evolution_shield import (
    load_structured_memory,
    audit_proposed_lesson,
    toggle_lesson,
    rollback_to_baseline,
)


class EvolutionShieldTests(unittest.TestCase):
    def setUp(self):
        network = patch("socket.socket", side_effect=AssertionError("network forbidden"))
        network.start()
        self.addCleanup(network.stop)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, value in {
            "WORKSPACE_DIR": root,
            "DATA_DIR": root / "data",
            "STRUCTURED_MEMORY_FILE": root / "data" / "structured_trading_memory.json",
            "AI_MEMORY_MD_FILE": root / "data" / "AI_TRADING_MEMORY.md",
        }.items():
            patcher = patch.object(shield, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_constitution_red_lines_block_extreme_biases(self):
        # Test 1: Block extreme long bias
        passed, reason = audit_proposed_lesson("【偏见】极端暴跌后未来绝对不要做多，所有买点都是陷阱", sample_size=10)
        self.assertFalse(passed)
        self.assertIn("极端做多偏见阻断", reason)

        # Test 2: Block stop loss cancellation
        passed, reason = audit_proposed_lesson("【抗单】遇到插针可以取消止损，扛单等待解套", sample_size=10)
        self.assertFalse(passed)
        self.assertIn("违规抗单放大止损", reason)

        # Test 3: Block Martingale doubling
        passed, reason = audit_proposed_lesson("【翻倍】亏损后加倍仓位摊平亏损", sample_size=10)
        self.assertFalse(passed)
        self.assertIn("马丁格尔赌徒加仓倾向", reason)

    def test_outlier_single_event_rejection(self):
        # Single event (<2 samples) must be rejected
        passed, reason = audit_proposed_lesson("【合理经验】4H多头回踩均线支撑时开多", sample_size=1)
        self.assertFalse(passed)
        self.assertIn("样本量不足", reason)

        # Sufficient sample size passes
        passed, reason = audit_proposed_lesson("【合理经验】4H多头回踩均线支撑时开多", sample_size=3)
        self.assertTrue(passed)

    def test_white_box_memory_crud_and_rollback(self):
        # Rollback initializes clean golden baseline
        baseline = rollback_to_baseline(expected_version=shield.read_memory_snapshot()["version"])
        self.assertGreaterEqual(len(baseline), 4)

        # Toggle first lesson
        first_id = baseline[0]["id"]
        toggled = toggle_lesson(first_id, expected_version=shield.read_memory_snapshot()["version"])
        self.assertIsNotNone(toggled)
        self.assertFalse(toggled["enabled"])

        # Toggle back
        toggled_back = toggle_lesson(first_id, expected_version=shield.read_memory_snapshot()["version"])
        self.assertTrue(toggled_back["enabled"])

    def _write_structured(self, lessons):
        path = shield.STRUCTURED_MEMORY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": 1,
            "revision": "testhash",
            "lessons": lessons,
        }), encoding="utf-8")
        return path

    def test_public_projection_missing_is_uninitialized(self):
        projection = shield.project_public_trading_memory()
        self.assertEqual(projection["status"], "uninitialized")
        self.assertEqual(projection["markdown"], "")
        self.assertFalse(shield.STRUCTURED_MEMORY_FILE.exists())

    def test_public_projection_legacy_markdown_ready_without_authority(self):
        shield.AI_MEMORY_MD_FILE.parent.mkdir(parents=True, exist_ok=True)
        shield.AI_MEMORY_MD_FILE.write_text("- usable legacy heuristic\n", encoding="utf-8")
        projection = shield.project_public_trading_memory(shield.AI_MEMORY_MD_FILE)
        self.assertEqual(projection["status"], "ready")
        self.assertIn("usable legacy heuristic", projection["markdown"])

    def test_public_projection_empty_authority_does_not_revive_markdown(self):
        self._write_structured([])
        shield.AI_MEMORY_MD_FILE.write_text("- OLD LEGACY MIRROR\n", encoding="utf-8")
        before = shield.STRUCTURED_MEMORY_FILE.read_bytes()
        projection = shield.project_public_trading_memory(shield.AI_MEMORY_MD_FILE)
        self.assertEqual(projection["status"], "empty")
        self.assertEqual(projection["markdown"], "")
        self.assertNotIn("OLD LEGACY", projection["markdown"])
        self.assertEqual(shield.STRUCTURED_MEMORY_FILE.read_bytes(), before)

    def test_public_projection_disabled_and_expired_do_not_revive_markdown(self):
        expired_at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self._write_structured([
            {"id": "lesson_off", "rule_text": "disabled heuristic", "enabled": False},
            {
                "id": "lesson_old",
                "rule_text": "expired heuristic",
                "enabled": True,
                "created_at": expired_at,
                "ttl_days": 1,
                "is_baseline": False,
            },
        ])
        shield.AI_MEMORY_MD_FILE.write_text("- OLD LEGACY MIRROR\n", encoding="utf-8")
        projection = shield.project_public_trading_memory(shield.AI_MEMORY_MD_FILE)
        self.assertEqual(projection["status"], "empty")
        self.assertEqual(projection["markdown"], "")
        self.assertNotIn("OLD LEGACY", projection["markdown"])

    def test_public_projection_corrupt_is_unavailable_without_mutation(self):
        shield.STRUCTURED_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        shield.STRUCTURED_MEMORY_FILE.write_text("{", encoding="utf-8")
        shield.AI_MEMORY_MD_FILE.write_text("- OLD LEGACY MIRROR\n", encoding="utf-8")
        before = shield.STRUCTURED_MEMORY_FILE.read_bytes()
        projection = shield.project_public_trading_memory(shield.AI_MEMORY_MD_FILE)
        self.assertEqual(projection["status"], "unavailable")
        self.assertEqual(projection["markdown"], "")
        self.assertNotIn("damaged", projection["markdown"])
        self.assertEqual(shield.STRUCTURED_MEMORY_FILE.read_bytes(), before)

    def test_public_projection_ready_structured(self):
        self._write_structured([
            {"id": "lesson_live", "rule_text": "active injectable heuristic", "enabled": True},
        ])
        projection = shield.project_public_trading_memory()
        self.assertEqual(projection["status"], "ready")
        self.assertIn("active injectable heuristic", projection["markdown"])

if __name__ == "__main__":
    unittest.main()

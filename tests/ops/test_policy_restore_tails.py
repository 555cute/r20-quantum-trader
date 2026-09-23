"""策略冷恢复（`r20_backend/policy/restore.py`）残余分支收口测试 —— 第 342 刀。

本模块 255 行，是系统策略版本归档回滚的核心原子执行引擎：
- 心法复核门禁（`_review_restored_lessons`）：复核器异常捕获、非字典与空文本过滤、宪法违规改标 `RESTORED_UNREVIEWED`；
- 回滚原子性与防偏门：哈希标识格式校验、心法存储格式（raw_text 列表、信封嵌套、类型异常）自愈与拦截；
- 失败安全回滚（Reverting to pre-restore state）：四单元哈希不匹配回退、规范化投影整包差异回退。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from r20_backend.policy.restore import _review_restored_lessons, restore_archived_policy


class PolicyRestoreTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_restore_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.archive_file = self.tmp_path / "target_policy_hash.json"

    # -------------------------------------------------------------------------
    # 1. 恢复心法宪法复核 (_review_restored_lessons)
    # -------------------------------------------------------------------------
    def test_review_restored_lessons_import_error_handled(self):
        # 复核器不可用（evolution_shield 无法导入）-> 报告 reviewer_error 并返回
        with patch.dict(sys.modules, {"evolution_shield": None}):
            res = _review_restored_lessons([{"rule_text": "Valid rule"}])
            self.assertIn("reviewer_error", res)
            self.assertEqual(res["total"], 0)

    def test_review_restored_lessons_filters_non_dict_and_empty_rules(self):
        # 混入非字典条目以及空 rule_text -> 过滤跳过
        items = [
            "not-a-dict",
            {"rule_text": ""},
            {"rule_text": "   "},
            {"rule_text": "Valid rule", "sample_size": 2},
        ]
        with patch("evolution_shield.audit_proposed_lesson", return_value=(True, "ok")):
            res = _review_restored_lessons(items)
            self.assertEqual(res["total"], 3)  # 3 个 dict 条目
            self.assertEqual(res["marked"], 1)

    def test_review_restored_lessons_audit_exception_marks_unreviewed(self):
        # 复核过程抛异常 -> 判定未通过，标记为 RESTORED_UNREVIEWED
        lesson = {"rule_text": "Crash rule"}
        with patch("evolution_shield.audit_proposed_lesson", side_effect=RuntimeError("eval crash")):
            res = _review_restored_lessons([lesson])
            self.assertEqual(res["marked"], 1)
            self.assertEqual(len(res["flagged"]), 1)
            self.assertEqual(lesson["shield_status"], "RESTORED_UNREVIEWED")
            self.assertIn("复核异常: eval crash", lesson["shield_reason"])

    def test_review_restored_lessons_passes_and_marks_restored(self):
        # 复核通过且原条目无 shield_status -> 标记为 RESTORED
        lesson = {"rule_text": "Compliant rule", "sample_size": 10}
        with patch("evolution_shield.audit_proposed_lesson", return_value=(True, "pass")):
            res = _review_restored_lessons([lesson])
            self.assertEqual(res["marked"], 1)
            self.assertEqual(len(res["flagged"]), 0)
            self.assertEqual(lesson["shield_status"], "RESTORED")

    # -------------------------------------------------------------------------
    # 2. 策略包回滚流程 (restore_archived_policy)
    # -------------------------------------------------------------------------
    def test_restore_archived_policy_invalid_hash_raises_value_error(self):
        for bad_hash in ("", None, "invalid hash with space", "!bad_hash!"):
            with self.subTest(bad_hash=bad_hash):
                with self.assertRaises(ValueError) as ctx:
                    restore_archived_policy(lambda a, h: self.archive_file, self.tmp_path, bad_hash)  # type: ignore
                self.assertIn("无效的策略哈希标识", str(ctx.exception))

    def _prepare_archive_and_restore(self, evo_data: any) -> dict:
        pkg = {"package": {"evolution_memory": evo_data}, "policy_hash": "target_hash"}
        self.archive_file.write_text(json.dumps(pkg), encoding="utf-8")
        with patch("r20_backend.policy.restore.capture_full_strategy_package", return_value={"package": {}}):
            with patch("r20_backend.policy.restore.generate_policy_snapshot", return_value={"policy_hash": "target_hash"}):
                with patch("r20_backend.policy.restore.package_restore_diff", return_value=[]):
                    with patch("evolution_shield.STRUCTURED_MEMORY_FILE", self.tmp_path / "memory.json"):
                        with patch("evolution_shield._validate"):
                            return restore_archived_policy(
                                lambda a, h: self.archive_file,
                                self.tmp_path,
                                "target_hash",
                                archive_dir=self.tmp_path,
                                root_dir=ROOT,
                            )

    def test_restore_archived_policy_evolution_memory_raw_text_list(self):
        # 1) evo_data 为 dict 且带 raw_text（内容为 JSON list）
        raw_list = [{"rule_text": "Lesson 1"}]
        res = self._prepare_archive_and_restore({"raw_text": json.dumps(raw_list)})
        self.assertEqual(res["status"], "restored")
        self.assertEqual(res["target_policy_hash"], "target_hash")

    def test_restore_archived_policy_evolution_memory_raw_text_invalid_lessons_envelope(self):
        # 2) evo_data 为 dict 且带 raw_text，但 parsed["lessons"] 不是 list -> 抛 ValueError 并回滚
        raw_dict = {"lessons": "not-a-list"}
        with self.assertRaises(RuntimeError) as ctx:
            self._prepare_archive_and_restore({"raw_text": json.dumps(raw_dict)})
        self.assertIn("Invalid lessons in envelope: must be a list", str(ctx.exception))

    def test_restore_archived_policy_evolution_memory_invalid_lessons_format(self):
        # 3) evo_data 字典中 lessons 字段不是 list -> 抛 ValueError 并回滚
        with self.assertRaises(RuntimeError) as ctx:
            self._prepare_archive_and_restore({"lessons": 12345})
        self.assertIn("Invalid lessons in evolution memory: must be a list", str(ctx.exception))

    def test_restore_archived_policy_evolution_memory_unsupported_type(self):
        # 4) evo_data 格式不支持（如整型或浮点数）-> 抛 ValueError 并回滚
        with self.assertRaises(RuntimeError) as ctx:
            self._prepare_archive_and_restore(12345)
        self.assertIn("Unsupported evolution memory format", str(ctx.exception))

    def test_restore_archived_policy_sys_path_remove_value_error_suppressed(self):
        # 在 finally 块中，若 sys.path.remove 抛 ValueError，安全忽略 pass
        pkg = {"package": {}, "policy_hash": "target_hash"}
        self.archive_file.write_text(json.dumps(pkg), encoding="utf-8")

        class MockPathList(list):
            def remove(self, item):
                raise ValueError("path already removed")

        with patch("r20_backend.policy.restore.capture_full_strategy_package", return_value={"package": {}}):
            with patch("r20_backend.policy.restore.generate_policy_snapshot", return_value={"policy_hash": "target_hash"}):
                with patch("r20_backend.policy.restore.package_restore_diff", return_value=[]):
                    with patch.object(sys, "path", MockPathList(sys.path)):
                        res = restore_archived_policy(
                            lambda a, h: self.archive_file,
                            self.tmp_path,
                            "target_hash",
                            archive_dir=self.tmp_path,
                            root_dir=self.tmp_path,
                        )
                        self.assertEqual(res["status"], "restored")

    def test_restore_archived_policy_hash_mismatch_triggers_rollback(self):
        # 恢复后快照哈希与目标不一致 -> 触发回滚并报错
        pkg = {"package": {}, "policy_hash": "target_hash"}
        self.archive_file.write_text(json.dumps(pkg), encoding="utf-8")
        with patch("r20_backend.policy.restore.capture_full_strategy_package", return_value={"package": {}}):
            with patch("r20_backend.policy.restore.generate_policy_snapshot", return_value={"policy_hash": "mismatched_hash"}):
                with self.assertRaises(RuntimeError) as ctx:
                    restore_archived_policy(
                        lambda a, h: self.archive_file,
                        self.tmp_path,
                        "target_hash",
                        archive_dir=self.tmp_path,
                    )
                self.assertIn("策略回滚失败且已恢复原状态: 恢复后哈希 mismatched_hash 与目标 target_hash 不一致", str(ctx.exception))

    def test_restore_archived_policy_unit_diff_triggers_rollback(self):
        # 四单元哈希相同，但规范化投影核对发现单元缺失 -> 触发回滚并报错
        pkg = {"package": {}, "policy_hash": "target_hash"}
        self.archive_file.write_text(json.dumps(pkg), encoding="utf-8")
        with patch("r20_backend.policy.restore.capture_full_strategy_package", return_value={"package": {}}):
            with patch("r20_backend.policy.restore.generate_policy_snapshot", return_value={"policy_hash": "target_hash"}):
                with patch("r20_backend.policy.restore.package_restore_diff", return_value=["interceptor_config", "risk_config"]):
                    with self.assertRaises(RuntimeError) as ctx:
                        restore_archived_policy(
                            lambda a, h: self.archive_file,
                            self.tmp_path,
                            "target_hash",
                            archive_dir=self.tmp_path,
                        )
                    self.assertIn("策略回滚失败且已恢复原状态: 以下单元未恢复到归档值 — interceptor_config、risk_config", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

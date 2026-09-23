"""策略归档索引管理与文件解析（`r20_backend/policy/archive.py`）残余分支收口测试 —— 第 357 刀。

本模块 132 行，是策略快照整包归档、索引原子写入与损坏自愈核心：
- 索引加载与损坏重建容错（`load_archive_index`）：
  - 索引文件不存在且写入重建索引异常时静默忽略；
  - 索引文件内容为空触发自愈重建；
  - 索引文件内容非列表（如空字典或非列表 JSON）触发自愈重建；
  - 损坏重建后写盘异常安全 pass。
- 归档文件路径解析（`_resolve_archive_file`）：
  - 直接存在 `policy_{key}.json` 时直接返回命中路径；
  - 索引条目哈希不匹配时继续迭代比对下一条。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r20_backend.policy.archive import _resolve_archive_file, load_archive_index


class PolicyArchiveTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_policy_archive_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    # -------------------------------------------------------------------------
    # 1. 索引加载与损坏重建 (load_archive_index)
    # -------------------------------------------------------------------------
    def test_load_archive_index_missing_save_exception_ignored(self):
        # 索引不存在重建后，写入保存抛异常安全 pass 忽略并返回重建结果 (lines 37-38)
        with patch("r20_backend.policy.archive.save_archive_index", side_effect=OSError("disk read-only")):
            res = load_archive_index(lambda d: [{"policy_hash": "rebuilt_h1"}], archive_dir=self.tmp_path)
            self.assertEqual(res, [{"policy_hash": "rebuilt_h1"}])

    def test_load_archive_index_empty_file_triggers_rebuild(self):
        # 索引文件存在但内容为空时触发重建 (lines 46-47)
        (self.tmp_path / "index.json").write_text("", encoding="utf-8")
        res = load_archive_index(lambda d: [{"policy_hash": "empty_rebuilt"}], archive_dir=self.tmp_path)
        self.assertEqual(res, [{"policy_hash": "empty_rebuilt"}])

    def test_load_archive_index_non_list_json_triggers_rebuild(self):
        # 索引文件存在且为合法 JSON 但顶层非列表（如字典）时触发重建 (lines 50-51)
        (self.tmp_path / "index.json").write_text('{"error": "not a list"}', encoding="utf-8")
        res = load_archive_index(lambda d: [{"policy_hash": "dict_rebuilt"}], archive_dir=self.tmp_path)
        self.assertEqual(res, [{"policy_hash": "dict_rebuilt"}])

    def test_load_archive_index_corrupt_rebuild_save_exception_ignored(self):
        # 坏 JSON 重建后写盘再次抛异常安全 pass (lines 68-69)
        (self.tmp_path / "index.json").write_text("{ corrupt json", encoding="utf-8")
        with patch("r20_backend.policy.archive.save_archive_index", side_effect=OSError("write fail")):
            res = load_archive_index(lambda d: [{"policy_hash": "corrupt_rebuilt"}], archive_dir=self.tmp_path)
            self.assertEqual(res, [{"policy_hash": "corrupt_rebuilt"}])

    # -------------------------------------------------------------------------
    # 2. 归档文件路径解析 (_resolve_archive_file)
    # -------------------------------------------------------------------------
    def test_resolve_archive_file_direct_match(self):
        # 直接存在 policy_{key}.json 时直接返回该文件 (line 95)
        direct_file = self.tmp_path / "policy_direct_hash_123.json"
        direct_file.write_text("{}", encoding="utf-8")
        resolved = _resolve_archive_file(lambda archive_dir: [], self.tmp_path, "direct_hash_123")
        self.assertEqual(resolved, direct_file)

    def test_resolve_archive_file_index_iteration_continue_and_fallback(self):
        # 索引中存在条目但哈希不匹配时通过 continue 跳过，最终回退直接路径 (lines 97-98, 102)
        mock_index = [
            {"policy_hash": "other_hash_1", "package_hash": "other_pkg_1"},
            {"policy_hash": "other_hash_2", "package_hash": "other_pkg_2"},
        ]
        resolved = _resolve_archive_file(lambda archive_dir: mock_index, self.tmp_path, "target_key_456")
        self.assertEqual(resolved, self.tmp_path / "policy_target_key_456.json")


if __name__ == "__main__":
    unittest.main()

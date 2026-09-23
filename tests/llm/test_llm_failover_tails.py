"""大模型故障转移事件读取（`r20_backend/llm/failover.py`）残余分支收口测试 —— 第 365 刀。

本模块 23 行，负责大模型故障转移历史日志读取、限额截断与格式自愈：
- 文件缺失或格式损坏容错（`events_file` 不存在或坏 JSON 时安全返回空列表 `[]`）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from r20_backend.llm.failover import recent_failover_events


class LLMFailoverTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_llm_failover_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    def test_recent_failover_events_corrupt_file_returns_empty(self):
        # 坏 JSON 文件读取抛异常安全 pass 并返回 [] (lines 21-23)
        corrupt_file = self.tmp_path / "corrupt_failover.json"
        corrupt_file.write_text("{ corrupt json", encoding="utf-8")
        res = recent_failover_events(corrupt_file)
        self.assertEqual(res, [])

    def test_recent_failover_events_nonexistent_file_returns_empty(self):
        # 文件不存在直接返回 [] (line 23)
        missing_file = self.tmp_path / "missing_failover.json"
        res = recent_failover_events(missing_file)
        self.assertEqual(res, [])

    def test_recent_failover_events_valid_list_sliced(self):
        # 合法列表读取并切片 (lines 19-20)
        import json
        valid_file = self.tmp_path / "valid_failover.json"
        events = [{"event_id": f"evt_{i}"} for i in range(10)]
        valid_file.write_text(json.dumps(events), encoding="utf-8")
        res = recent_failover_events(valid_file, limit=5)
        self.assertEqual(len(res), 5)
        self.assertEqual(res[0]["event_id"], "evt_0")


if __name__ == "__main__":
    unittest.main()

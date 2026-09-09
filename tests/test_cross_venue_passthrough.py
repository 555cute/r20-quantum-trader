"""US-007：/api/all cross_venue 透传层单测（零网络，读写钉临时目录）。

前会话半成品测试：原断言按中间态形状（顶层 symbols 即全形状）书写，与实现
演进出的 by_asset 契约脱节，且未封闭 AI_DECISIONS_FILE（会隐式读生产决策
缓存）。本版对齐最终契约：透传段 + by_asset 合并段并存，双文件路径都钉 tmp。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dashboard.app as dashboard


def _load_with(data_dir: str):
    with patch.object(dashboard, "DATA_DIR", data_dir), \
         patch.object(dashboard, "AI_DECISIONS_FILE",
                      str(Path(data_dir, "ai_brain_decisions.json"))):
        return dashboard._load_cross_venue_data()


class CrossVenuePassthroughTests(unittest.TestCase):
    EMPTY = {"updated_utc": "", "package_count": 0,
             "venues": {}, "symbols": {}, "by_asset": {}}

    def test_normal_shape(self):
        with tempfile.TemporaryDirectory() as td:
            payload = {"updated_utc": "2026-09-09 19:00:00", "writer_pid": 1,
                       "package_count": 10,
                       "venues": {"binance": {"ok": ["BTC"], "failed": {}, "avg_ms": 40}},
                       "symbols": {"BTC": {"okx": 79000.0, "bin_last": 79020.0,
                                           "bin_basis_pct": 0.02}}}
            Path(td, "venue_health.json").write_text(json.dumps(payload), encoding="utf-8")
            out = _load_with(td)
            self.assertEqual(out["updated_utc"], "2026-09-09 19:00:00")
            self.assertEqual(out["package_count"], 10)
            self.assertIn("binance", out["venues"])
            self.assertEqual(out["symbols"]["BTC"]["okx"], 79000.0)
            # symbols 单源即可填 by_asset（决策缓存此刻无 xvenue 的过渡态）
            self.assertEqual(out["by_asset"]["BTC"]["bin_basis_pct"], 0.02)
            self.assertEqual(out["by_asset"]["BTC"]["gate_last"], "")

    def test_missing_file_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(_load_with(td), self.EMPTY)

    def test_corrupt_json_and_bad_types_degrade(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "venue_health.json").write_text("{ not json", encoding="utf-8")
            self.assertEqual(_load_with(td)["venues"], {})
            Path(td, "venue_health.json").write_text(
                json.dumps({"venues": "not-a-dict", "symbols": None,
                            "package_count": "x"}), encoding="utf-8")
            out = _load_with(td)
            self.assertEqual(out["venues"], {})
            self.assertEqual(out["symbols"], {})
            self.assertEqual(out["package_count"], 0)


if __name__ == "__main__":
    unittest.main()

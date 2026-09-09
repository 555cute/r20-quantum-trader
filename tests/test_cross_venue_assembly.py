"""US-007：/api/all cross_venue 装配契约单测（dashboard._load_cross_venue_data）。

红线自检：零网络（被测函数只 open 本地 JSON）；读写全部钉到 tmp 目录；
不依赖生产 data/ 当前内容（venue_health.symbols / decisions.xvenue 均按
「可能缺席」设计断言——US-009 收尾者落库前后本测试都应为绿）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dashboard.app as dashboard


def _dec_entry(name: str, inst: str, last, xvenue) -> dict:
    entry = {
        "name": name,
        "instId": inst,
        "raw_ticker": {"last": last},
    }
    if xvenue is not None:
        entry["xvenue"] = xvenue
    return entry


class CrossVenueAssemblyTests(unittest.TestCase):
    """装配面：健康段透传 + by_asset 双源合并 + 逐键位/整体 fail-soft。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.vh = self.dir / "venue_health.json"
        self.dec = self.dir / "ai_brain_decisions.json"

    def _load(self):
        with patch.object(dashboard, "DATA_DIR", str(self.dir)), \
             patch.object(dashboard, "AI_DECISIONS_FILE", str(self.dec)):
            return dashboard._load_cross_venue_data()

    def _write(self, path: Path, obj):
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    # ---- ① 齐全：两路数据源都在，symbols 优先、xvenue 补缺 ----
    def test_full_both_sources_symbols_precedence(self):
        self._write(self.vh, {
            "updated_utc": "2026-09-09 19:00:00", "writer_pid": 1, "package_count": 10,
            "venues": {"okx": {"ok": ["BTC"]}, "binance": {"ok": ["BTC"], "avg_ms": 349},
                       "gate": {"ok": ["BTC"], "avg_ms": 206}},
            "symbols": {"BTC": {
                "okx": 79000.0, "bin_last": 79020.0, "bin_basis_pct": 0.025,
                "gate_last": 78990.0, "gate_basis_pct": -0.013,
                "bin_ls": 2.12, "bin_funding_pct": 0.0074,
                # 注意：symbols 缺 gate_ls / gate_funding_pct → 应由 xvenue 补
            }},
        })
        self._write(self.dec, {
            "BTC-USDT-SWAP": _dec_entry("BTC", "BTC-USDT-SWAP", 79000.0, {
                "bin_last": 79020.0, "gate_last": 78990.0,
                "bin_ls": 9.99,  # 与 symbols 值故意不同 → 断言 symbols 优先
                "gate_ls": 1.10, "gate_funding_pct": 0.0482,
                "bin_funding_pct": 0.0074,
            }),
        })
        out = self._load()
        self.assertEqual(out["updated_utc"], "2026-09-09 19:00:00")
        self.assertEqual(out["package_count"], 10)
        self.assertIn("binance", out["venues"])
        self.assertIn("BTC", out["symbols"])
        row = out["by_asset"]["BTC"]
        # AC 键位齐全
        for k in ("bin_last", "gate_last", "bin_ls", "gate_ls",
                  "bin_funding_pct", "gate_funding_pct"):
            self.assertIn(k, row)
        self.assertEqual(row["bin_ls"], 2.12)        # symbols 优先
        self.assertEqual(row["gate_ls"], 1.10)       # symbols 缺 → xvenue 补
        self.assertEqual(row["bin_basis_pct"], 0.025)  # brain 预计算基差直传
        self.assertEqual(row["gate_basis_pct"], -0.013)
        self.assertEqual(row["gate_funding_pct"], 0.0482)
        self.assertEqual(row["okx_last"], 79000.0)

    # ---- ② 仅决策缓存 xvenue（venue_health 无 symbols）→ 基差现算 ----
    def test_xvenue_only_basis_computed(self):
        self._write(self.vh, {"updated_utc": "x", "package_count": 1,
                              "venues": {"gate": {"ok": []}}})
        self._write(self.dec, {
            "ETH-USDT-SWAP": _dec_entry("ETH", "ETH-USDT-SWAP", 2500.0, {
                "bin_last": 2502.5, "gate_last": 2495.0,
                "bin_ls": 1.5, "gate_ls": 1.2,
                "bin_funding_pct": 0.01, "gate_funding_pct": -0.02,
            }),
        })
        out = self._load()
        row = out["by_asset"]["ETH"]
        self.assertEqual(row["bin_basis_pct"], 0.1)        # (2502.5-2500)/2500*100
        self.assertEqual(row["gate_basis_pct"], -0.2)
        self.assertEqual(row["okx_last"], 2500.0)
        self.assertEqual(out["symbols"], {})

    # ---- ③ US-009 前置容错：决策缓存无 xvenue 键 / 只有半成品 xvenue ----
    def test_decisions_missing_or_partial_xvenue(self):
        self._write(self.dec, {
            "BTC-USDT-SWAP": _dec_entry("BTC", "BTC-USDT-SWAP", 100.0, None),   # 整键缺失
            "SOL-USDT-SWAP": _dec_entry("SOL", "SOL-USDT-SWAP", 200.0, {"bin_last": 201.0}),  # 半成品
            "junk": "not-a-dict",                                               # 脏条目
        })
        out = self._load()
        self.assertEqual(out["updated_utc"], "")
        self.assertEqual(out["by_asset"]["BTC"]["bin_last"], "")
        self.assertEqual(out["by_asset"]["BTC"]["bin_basis_pct"], "")
        self.assertEqual(out["by_asset"]["SOL"]["gate_ls"], "")
        self.assertEqual(out["by_asset"]["SOL"]["bin_basis_pct"], 0.5)  # 有价才算
        self.assertNotIn("JUNK", out["by_asset"])

    # ---- ④ symbols 独有资产（决策缓存没这个币）也要出现 ----
    def test_symbols_only_asset_included(self):
        self._write(self.vh, {"updated_utc": "u", "package_count": 2, "venues": {},
                              "symbols": {"PEPE": {"okx": 0.00001, "bin_last": 0.0000102,
                                                   "bin_basis_pct": 0.2}}})
        self._write(self.dec, {})
        out = self._load()
        self.assertEqual(out["by_asset"]["PEPE"]["bin_basis_pct"], 0.2)
        self.assertEqual(out["by_asset"]["PEPE"]["gate_last"], "")

    # ---- ⑤ 损坏：两路全烂 → 空态且不抛（整体 fail-soft 不影响响应其余部分） ----
    def test_both_corrupt_degrade_to_empty(self):
        self.vh.write_text("{ not json", encoding="utf-8")
        self.dec.write_text("[[[ broken", encoding="utf-8")
        out = self._load()
        self.assertEqual(out, {"updated_utc": "", "package_count": 0,
                               "venues": {}, "symbols": {}, "by_asset": {}})

    # ---- ⑥ 垃圾类型：合法 JSON 但结构烂 → 逐段防御 ----
    def test_bad_types_degrade(self):
        self._write(self.vh, {"venues": "not-a-dict", "symbols": [1, 2],
                              "package_count": "x", "updated_utc": None})
        self._write(self.dec, ["not", "a", "dict"])
        out = self._load()
        self.assertEqual(out["venues"], {})
        self.assertEqual(out["symbols"], {})
        self.assertEqual(out["by_asset"], {})
        self.assertEqual(out["package_count"], 0)
        self.assertEqual(out["updated_utc"], "")

    # ---- ⑦ 缺失：两个文件都不存在 → 空态（旧部署/首轮周期） ----
    def test_both_missing_empty_shape(self):
        out = self._load()
        self.assertEqual(out, {"updated_utc": "", "package_count": 0,
                               "venues": {}, "symbols": {}, "by_asset": {}})

    # ---- ⑧ 快照通道注入：STALE 降级路径也带 cross_venue 键 ----
    def test_stale_injection_carries_cross_venue(self):
        self._write(self.vh, {"updated_utc": "2026-09-09 20:00:00", "package_count": 3,
                              "venues": {"gate": {"ok": ["BTC"], "avg_ms": 120}},
                              "symbols": {}})
        self._write(self.dec, {})
        missing = lambda n: str(self.dir / ("__absent__" + n))
        with patch.object(dashboard, "DATA_DIR", str(self.dir)), \
             patch.object(dashboard, "AI_DECISIONS_FILE", str(self.dec)), \
             patch.object(dashboard, "FACTOR_LIBRARY_FILE", missing("fl")), \
             patch.object(dashboard, "NEWS_SENTIMENT_FILE", missing("ns")), \
             patch.object(dashboard, "AI_HISTORY_FILE", missing("ah")), \
             patch.object(dashboard, "REPORT_JSON_FILE", missing("rp")), \
             patch.object(dashboard, "AI_LAST_PROMPT_FILE", missing("ap")), \
             patch.object(dashboard, "LOG_FILE", missing("lg")), \
             patch.object(dashboard, "_build_factors_from_local_files", lambda *a: ([], {})), \
             patch.object(dashboard, "load_trading_memory_md", lambda: ""):
            stale = {"account": {}}
            dashboard._inject_local_data_into_stale(stale, [], "2026-09-09 20:01:00")
        self.assertEqual(stale["cross_venue"]["updated_utc"], "2026-09-09 20:00:00")
        self.assertIn("gate", stale["cross_venue"]["venues"])


if __name__ == "__main__":
    unittest.main()

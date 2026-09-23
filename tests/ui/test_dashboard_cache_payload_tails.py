"""面板缓存持久化与 STALE 兜底注入（`r20_backend/dashboard_payload/cache.py`）全量分支收口测试 —— 第 352 刀。

本模块 135 行，负责操盘看板缓存读取、原子持久化落盘与 STALE 模式本地数据注入：
- 缓存读取容错（`load_persisted_dashboard_cache`）：空路径/不存在文件自愈、非实质快照过滤与合法快照解析；
- 缓存原子落盘（`persist_dashboard_cache`）：非实质快照拒绝落盘、原子写入临时文件、权限设定（0o600）与写盘异常清理保护；
- STALE 模式本地数据注入（`_inject_local_data_into_stale`）：
  - 因子库、跨所快照、组合风险无网络只读注入；
  - 因子列表条件注入（非空才覆盖）；
  - 资讯情报、AI 脑历史、复盘报告、最近提示词、心法记忆、日志条目与交易台账文件的读取容错（存在解析、文件缺失跳过、损坏坏 JSON 自愈、I/O 异常捕获）。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from r20_backend.dashboard_payload.cache import (
    _inject_local_data_into_stale,
    load_persisted_dashboard_cache,
    persist_dashboard_cache,
)


class DashboardCachePayloadTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_dashboard_cache_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.cache_file = self.tmp_path / "dashboard_cache.json"

    # -------------------------------------------------------------------------
    # 1. 缓存读取 (load_persisted_dashboard_cache)
    # -------------------------------------------------------------------------
    def test_load_persisted_dashboard_cache_non_existent_returns_empty(self):
        # 文件不存在时捕获异常并返回空字典 (lines 27-28)
        self.assertEqual(load_persisted_dashboard_cache(self.tmp_path / "missing.json"), {})

    def test_load_persisted_dashboard_cache_not_meaningful_returns_empty(self):
        # 缓存内容不是实质快照（如缺少 account.total_eq）返回空字典 (line 26)
        self.cache_file.write_text(json.dumps({"account": {}}), encoding="utf-8")
        self.assertEqual(load_persisted_dashboard_cache(self.cache_file), {})

    def test_load_persisted_dashboard_cache_valid_returns_data(self):
        # 实质有效快照返回完整字典 (line 26)
        valid = {"account": {"total_eq": 1234.5}}
        self.cache_file.write_text(json.dumps(valid), encoding="utf-8")
        self.assertEqual(load_persisted_dashboard_cache(self.cache_file), valid)

    # -------------------------------------------------------------------------
    # 2. 缓存持久化 (persist_dashboard_cache)
    # -------------------------------------------------------------------------
    def test_persist_dashboard_cache_not_meaningful_early_return(self):
        # 非实质有效快照直接返回，不创建任何文件 (lines 32-33)
        target = self.tmp_path / "not_created.json"
        persist_dashboard_cache(target, self.tmp_path, {})
        self.assertFalse(target.exists())

    def test_persist_dashboard_cache_valid_writes_and_chmods(self):
        # 正常原子落盘 (lines 34-45)
        target = self.tmp_path / "saved.json"
        valid = {"account": {"total_eq": 888.8}}
        persist_dashboard_cache(target, self.tmp_path, valid)
        self.assertTrue(target.exists())
        loaded = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(loaded, valid)

    def test_persist_dashboard_cache_exception_cleans_temp_file(self):
        # 落盘过程中抛异常（如 os.replace 失败）时清理临时文件 (lines 47-48)
        target = self.tmp_path / "fail.json"
        valid = {"account": {"total_eq": 999.9}}
        with patch("os.replace", side_effect=OSError("disk read-only")):
            with self.assertRaises(OSError):
                persist_dashboard_cache(target, self.tmp_path, valid)
            # 确认临时文件均已清理
            temps = list(self.tmp_path.glob(".dashboard-cache-*.json"))
            self.assertEqual(len(temps), 0)

    # -------------------------------------------------------------------------
    # 3. STALE 模式本地数据注入 (_inject_local_data_into_stale)
    # -------------------------------------------------------------------------
    def test_inject_local_data_into_stale_all_valid(self):
        # 准备各类本地文件
        news_f = self.tmp_path / "news.json"
        news_f.write_text(json.dumps({"sentiment": "bullish"}), encoding="utf-8")
        hist_f = self.tmp_path / "history.json"
        hist_f.write_text(json.dumps([{"time": "now"}]), encoding="utf-8")
        rep_f = self.tmp_path / "report.json"
        rep_f.write_text(json.dumps({"summary": "review ok"}), encoding="utf-8")
        prompt_f = self.tmp_path / "prompt.txt"
        prompt_f.write_text("system prompt content", encoding="utf-8")
        log_f = self.tmp_path / "log.txt"
        log_f.write_text("line1\n\nline2\n", encoding="utf-8")
        ledg_f = self.tmp_path / "ledger.json"
        ledg_f.write_text(json.dumps([{"id": 1}, {"id": 2}]), encoding="utf-8")

        stale: dict = {}
        res = _inject_local_data_into_stale(
            load_factor_lib=lambda: {"factor_lib_key": 1},
            load_cross_venue=lambda: {"cross_venue_key": 2},
            load_portfolio_risk=lambda: {"portfolio_risk_key": 3},
            build_factors=lambda pos, ts: ([{"name": "f1"}], {"state": "active"}),
            build_health=lambda h: {"health_status": "good"},
            load_memory_md=lambda: "memory markdown content",
            news_file=news_f,
            history_file=hist_f,
            report_file=rep_f,
            last_prompt_file=prompt_f,
            log_file=log_f,
            ledger_file=ledg_f,
            stale=stale,
            positions=[{"pos": "ok"}],
            timestamp_full="2026-09-22 12:00:00",
        )

        self.assertEqual(res["factor_library"], {"factor_lib_key": 1})
        self.assertEqual(res["cross_venue"], {"cross_venue_key": 2})
        self.assertEqual(res["portfolio_risk"], {"portfolio_risk_key": 3})
        self.assertEqual(res["factors"], [{"name": "f1"}])
        self.assertEqual(res["state_snapshot"], {"state": "active"})
        self.assertEqual(res["news_intelligence"], {"sentiment": "bullish"})
        self.assertEqual(res["ai_brain_history"], [{"time": "now"}])
        self.assertEqual(res["ai_health"], {"health_status": "good"})
        self.assertEqual(res["review"], {"summary": "review ok"})
        self.assertEqual(res["ai_last_prompt"], "system prompt content")
        self.assertEqual(res["ai_trading_memory_md"], "memory markdown content")
        self.assertEqual(res["logs"], ["line1", "line2"])
        self.assertEqual(len(res["trades"]), 2)

    def test_inject_local_data_into_stale_exceptions_handled_gracefully(self):
        # 文件损坏（坏 JSON）与调用函数抛异常时安全捕获并忽略 (lines 79, 87, 93, 101, 109, 115, 124, 132)
        bad_json = self.tmp_path / "bad.json"
        bad_json.write_text("{ corrupt json", encoding="utf-8")

        stale: dict = {}
        res = _inject_local_data_into_stale(
            load_factor_lib=lambda: None,
            load_cross_venue=lambda: None,
            load_portfolio_risk=lambda: None,
            build_factors=lambda pos, ts: ([], {}),  # factors_list 为空，不覆盖 stale["factors"] (line 70)
            build_health=MagicMock(side_effect=RuntimeError("health fail")),
            load_memory_md=MagicMock(side_effect=RuntimeError("memory fail")),
            news_file=bad_json,
            history_file=bad_json,
            report_file=bad_json,
            last_prompt_file=self.tmp_path / "missing_prompt.txt",
            log_file=self.tmp_path / "missing_log.txt",
            ledger_file=bad_json,
            stale=stale,
            positions=[],
            timestamp_full="2026-09-22 12:00:00",
        )

        self.assertNotIn("factors", res)
        self.assertNotIn("news_intelligence", res)
        self.assertNotIn("ai_brain_history", res)
        self.assertNotIn("ai_health", res)
        self.assertNotIn("review", res)
        self.assertNotIn("ai_last_prompt", res)
        self.assertNotIn("ai_trading_memory_md", res)
        self.assertNotIn("logs", res)
        self.assertNotIn("trades", res)

    def test_inject_local_data_into_stale_file_read_io_errors(self):
        # 存在的文件读取抛 I/O 异常时安全捕获 (lines 109, 124)
        prompt_f = self.tmp_path / "prompt.txt"
        prompt_f.write_text("prompt", encoding="utf-8")
        log_f = self.tmp_path / "log.txt"
        log_f.write_text("logs", encoding="utf-8")

        with patch("builtins.open", side_effect=OSError("permission denied")):
            stale: dict = {}
            res = _inject_local_data_into_stale(
                load_factor_lib=lambda: None,
                load_cross_venue=lambda: None,
                load_portfolio_risk=lambda: None,
                build_factors=lambda pos, ts: ([], {}),
                build_health=lambda h: None,
                load_memory_md=lambda: None,
                news_file=self.tmp_path / "missing_news.json",
                history_file=self.tmp_path / "missing_hist.json",
                report_file=self.tmp_path / "missing_rep.json",
                last_prompt_file=prompt_f,
                log_file=log_f,
                ledger_file=self.tmp_path / "missing_ledger.json",
                stale=stale,
                positions=[],
                timestamp_full="2026-09-22 12:00:00",
            )
            self.assertNotIn("ai_last_prompt", res)
            self.assertNotIn("logs", res)


if __name__ == "__main__":
    unittest.main()

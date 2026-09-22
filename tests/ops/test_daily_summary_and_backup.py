"""每日量化研报（daily_summary_and_backup.py）收口 —— 第 301 刀。

这个脚本会在 08:00/20:00 往群里**主动推送**一份「今日战绩」研报。它的历史事故
写在代码注释里（审计③）：旧实现 `except: pass` 之后照发「0 胜 0 负 +0.00U」——
把一次**数据事故**渲染成事实主动推送出去。所以本刀的第一条语义就是：

> **损坏 ≠ 「确实没有交易」**。台账读不动时必须**前置故障警示**，且仍然把
> 其余（账户/持仓/舆情）照常报出来，让读到的人自己判断可信度。

其余钉住的是「三份输入各自坏掉都不许影响另外两份」与"报出来的数就是输入的数"。
"""
from __future__ import annotations

import ast
import contextlib
import datetime
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.daily_summary_and_backup as dsb

_BJ = datetime.timezone(datetime.timedelta(hours=8))


class ModuleLoadFallbackTests(unittest.TestCase):
    """`qq_notifier` 缺失时 `notify_daily_summary` 必须是 `None`，而不是让模块导入失败。"""

    def test_notify_daily_summary_is_none_when_notifier_missing(self):
        src = Path(dsb.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        node = next(n for n in tree.body if isinstance(n, ast.Try) and n.lineno == 24)
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace: dict = {}
        with patch.dict(sys.modules, {"qq_notifier": None}):
            exec(compile(module, dsb.__file__, "exec"), namespace)  # noqa: S102
        self.assertIn("notify_daily_summary", namespace)
        self.assertIsNone(namespace["notify_daily_summary"])


class BriefingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.ledger = self.data / "trading_ledger.json"
        self.notified: list[str] = []
        self.ran_scripts: list = []
        self.printed: list[str] = []

        for name, value in (
            ("DATA_DIR", str(self.data)),
            ("LEDGER_JSON_FILE", str(self.ledger)),
            ("notify_daily_summary", lambda text: self.notified.append(text)),
            ("_run_captured", lambda script, label=None, timeout=15: self.ran_scripts.append(script)),
            ("print", lambda *a, **k: self.printed.append(" ".join(str(x) for x in a))),
        ):
            patcher = patch.object(dsb, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # ── 夹具 ────────────────────────────────────────────────────────────
    def _today_dates(self):
        now = datetime.datetime.now(_BJ)
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M:%S")

    def _trade(self, *, pnl=0.0, inst=None, status="closed", day=None, **extra):
        date_str, stamp = self._today_dates()
        row = {"status": status, "close_time": day if day is not None else stamp,
               "pnl": pnl}
        if inst is not None:
            row["inst"] = inst
        row.update(extra)
        return row

    def _write_ledger(self, payload):
        self.ledger.write_text(json.dumps(payload), encoding="utf-8")

    def _write(self, name, text):
        (self.data / name).write_text(text, encoding="utf-8")

    def _briefing(self):
        return dsb.generate_daily_briefing_and_backup()

    # ── 正常路径 ────────────────────────────────────────────────────────
    def test_healthy_ledger_reports_counts_and_pnl(self):
        self._write_ledger([
            self._trade(pnl=10.0, inst="BTC-USDT-SWAP"),
            self._trade(pnl=-4.0, inst="ETH-USDT-SWAP"),
            self._trade(pnl=0.0, inst="SOL-USDT-SWAP"),
            self._trade(pnl=999.0, inst="DOGE-USDT-SWAP", status="open"),
        ])
        text = self._briefing()
        # ★ pnl 恰好为 0 的行**既不算胜也不算负**（判据是 >0 / <0），
        #   所以 胜+负 可以小于当日平仓总数 —— 分母用的仍是总数（3）
        self.assertIn("1 胜 / 1 负（胜率 33.3%）", text)
        self.assertIn("今日已结净盈亏：+6.00 USDT", text)
        # 最优贡献标的取 pnl 最大者
        self.assertIn("最优贡献标的：BTC-USDT-SWAP (+10.00 U)", text)
        # 未平仓的不计入
        self.assertNotIn("DOGE", text)
        self.assertEqual(self.notified, [text])

    def test_top_asset_falls_back_to_placeholder_when_no_trades(self):
        self._write_ledger([])
        text = self._briefing()
        self.assertIn("最优贡献标的：暂无 (+0.00 U)", text)
        self.assertIn("0 胜 / 0 负", text)

    def test_asset_name_falls_back_to_name_then_other(self):
        self._write_ledger([
            self._trade(pnl=5.0, name="LINK-USDT-SWAP"),
            self._trade(pnl=3.0, inst="None"),      # 字面量 "None" 必须被跳过
            self._trade(pnl=1.0, inst=""),          # 空串 ⇒ OTHER
        ])
        text = self._briefing()
        self.assertIn("LINK-USDT-SWAP (+5.00 U)", text)
        self.assertNotIn("None (+", text)

    def test_fee_line_only_when_fee_present(self):
        self._write_ledger([self._trade(pnl=10.0, inst="BTC-USDT-SWAP", fee=0.5)])
        self.assertIn("(交易手续费: -0.50 U)", self._briefing())
        self.ledger.unlink()
        self._write_ledger([self._trade(pnl=10.0, inst="BTC-USDT-SWAP")])
        self.assertNotIn("交易手续费", self._briefing())

    # ── 台账损坏：宁报故障，不发假 0 ────────────────────────────────────
    def test_non_list_ledger_is_flagged_not_silently_empty(self):
        self._write_ledger({"oops": "it is a dict"})
        text = self._briefing()
        self.assertTrue(text.startswith("⚠️ 台账文件损坏/不可读"))
        self.assertIn("宁报故障，不发假 0", text)
        self.assertIn("请尽快人工检查 data/trading_ledger.json", text)
        # 其余段落仍然照常输出（让人能自己判断可信度）
        self.assertIn("今日平仓战绩：0 胜 / 0 负", text)
        self.assertTrue(any("CRITICAL" in line for line in self.printed))

    def test_corrupt_json_ledger_is_flagged(self):
        self.ledger.write_text("{ not json at all", encoding="utf-8")
        text = self._briefing()
        self.assertTrue(text.startswith("⚠️ 台账文件损坏/不可读"))

    def test_missing_ledger_is_not_flagged(self):
        # 没有台账文件 = 尚未产生交易，**不是**故障
        text = self._briefing()
        self.assertFalse(text.startswith("⚠️"))
        self.assertIn("0 胜 / 0 负", text)

    def test_unreadable_flag_does_not_suppress_notification(self):
        self.ledger.write_text("nope", encoding="utf-8")
        text = self._briefing()
        self.assertEqual(self.notified, [text])

    # ── 三份输入各自坏掉互不影响 ────────────────────────────────────────
    def test_broken_account_init_is_ignored(self):
        self._write("account_initial_state.json", "{ broken")
        self._write_ledger([self._trade(pnl=1.0, inst="BTC-USDT-SWAP")])
        text = self._briefing()
        self.assertIn("+1.00 USDT", text)

    def test_account_init_is_read_without_crashing_when_valid(self):
        self._write("account_initial_state.json",
                    json.dumps({"reset_time": "2026-01-01 00:00:00"}))
        self._write_ledger([])
        self.assertIn("今日平仓战绩", self._briefing())

    def test_broken_news_file_keeps_default_sentiment(self):
        self._write("news_sentiment.json", "][")
        self._write_ledger([])
        self.assertIn("市场舆情环境：偏多震荡", self._briefing())

    def test_news_sentiment_is_echoed_when_readable(self):
        self._write("news_sentiment.json", json.dumps({"macro_sentiment": "偏空急跌"}))
        self._write_ledger([])
        self.assertIn("市场舆情环境：偏空急跌", self._briefing())

    def test_broken_dashboard_file_drops_account_section(self):
        self._write("dashboard_last_good.json", "not json")
        self._write_ledger([])
        text = self._briefing()
        self.assertNotIn("账户总净值", text)

    def test_sync_script_failure_does_not_abort_briefing(self):
        def boom(script, label=None, timeout=15):
            self.ran_scripts.append(script)
            raise RuntimeError("sync exploded")

        with patch.object(dsb, "_run_captured", boom):
            self._write_ledger([])
            text = self._briefing()
        self.assertEqual(len(self.ran_scripts), 1)
        self.assertIn("今日平仓战绩", text)

    # ── 账户与持仓渲染 ──────────────────────────────────────────────────
    def test_account_block_rendered_with_upl_only_when_nonzero(self):
        self._write("dashboard_last_good.json", json.dumps({
            "account": {"total_eq": 1234.567, "avail_eq": 900.0,
                        "pos_upl_total": -12.5, "margin_usage_pct": 27.34},
            "positions": [],
        }))
        self._write_ledger([])
        text = self._briefing()
        self.assertIn("账户总净值：1,234.57 USDT (可用: 900.00 U | 杠杆占用率: 27.3%)", text)
        self.assertIn("实时在管浮盈 (UPL)：-12.50 USDT", text)

    def test_zero_upl_line_is_omitted(self):
        self._write("dashboard_last_good.json", json.dumps({
            "account": {"total_eq": 1.0, "avail_eq": 1.0, "pos_upl_total": 0.0},
            "positions": [],
        }))
        self._write_ledger([])
        self.assertNotIn("实时在管浮盈", self._briefing())

    def test_positions_render_side_venue_and_stop(self):
        self._write("dashboard_last_good.json", json.dumps({
            "account": {"total_eq": 1.0, "avail_eq": 1.0},
            "positions": [
                {"name": "BTC", "venue": "binance", "side": "long",
                 "upl": 3.5, "uplRatio": 1.25, "exchangeSl": "60000"},
                {"name": "ETH", "side": "short", "upl": -1.0, "roi_pct": -0.5},
            ],
        }))
        self._write_ledger([])
        text = self._briefing()
        self.assertIn("当前在管持仓 (2 笔)", text)
        self.assertIn("🟢多 BTC (BINANCE) | 浮盈 +3.50 U (+1.2%) | 止损 60000", text)
        # 无 venue ⇒ 默认 OKX；无止损 ⇒ "--"
        self.assertIn("🔴空 ETH (OKX) | 浮盈 -1.00 U (-0.5%) | 止损 --", text)

    def test_more_than_four_positions_are_summarized(self):
        self._write("dashboard_last_good.json", json.dumps({
            "account": {},
            "positions": [{"name": f"P{i}", "side": "long", "upl": 0} for i in range(6)],
        }))
        self._write_ledger([])
        text = self._briefing()
        self.assertIn("另有 2 笔持仓监控中", text)
        # 只逐条列出前 4 笔
        self.assertIn("• 🟢多 P3", text)
        self.assertNotIn("• 🟢多 P4", text)

    def test_position_name_falls_back_to_instid_base(self):
        self._write("dashboard_last_good.json", json.dumps({
            "account": {},
            "positions": [{"instId": "SOL-USDT-SWAP", "side": "long", "upl": 0}],
        }))
        self._write_ledger([])
        self.assertIn("SOL (OKX)", self._briefing())

    # ── 渲染契约 ────────────────────────────────────────────────────────
    def test_date_line_uses_beijing_clock(self):
        self._write_ledger([])
        date_str, stamp = self._today_dates()
        text = self._briefing()
        self.assertIn(f"📅 日期：{date_str}（北京时间 {stamp[11:16]}）", text)

    def test_no_notifier_means_no_crash(self):
        with patch.object(dsb, "notify_daily_summary", None):
            self._write_ledger([])
            text = self._briefing()
        self.assertEqual(self.notified, [])
        self.assertIn("今日平仓战绩", text)
        self.assertTrue(any("每日量化研报已成功生成并推送" in line for line in self.printed))

    def test_yesterdays_trades_are_excluded(self):
        old = (datetime.datetime.now(_BJ) - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        self._write_ledger([self._trade(pnl=100.0, inst="BTC-USDT-SWAP", day=old)])
        text = self._briefing()
        self.assertIn("0 胜 / 0 负", text)
        self.assertNotIn("+100.00", text)

    def test_unparsable_close_time_is_excluded(self):
        self._write_ledger([self._trade(pnl=100.0, inst="BTC", day="bad")])
        self.assertIn("0 胜 / 0 负", self._briefing())


class MainGuardTests(unittest.TestCase):
    """`__main__` 必须把研报正文再打一遍（运维直接跑时的可见性）。"""

    def test_result_is_printed(self):
        src = Path(dsb.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        node = next(n for n in tree.body if isinstance(n, ast.If) and n.lineno == 164)
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(compile(module, dsb.__file__, "exec"),  # noqa: S102
                 {"__name__": "__main__",
                  "generate_daily_briefing_and_backup": lambda: "BRIEFING-BODY",
                  "print": print})
        self.assertIn("Daily Briefing Result:\nBRIEFING-BODY", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

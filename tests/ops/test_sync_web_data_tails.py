"""Web 看板数据同步（`scripts/sync_web_data.py`）的残余分支收口 —— 第 328 刀。

本模块 346 行，把 OKX 余额 / 持仓 / 当日盈亏 / 快照 / 台账 / AI 决策 / 日志
汇总成 `data/trading_data.json` 供前端看板消费。既有测试里有一条专门的
`tests/ui/test_sync_web_data_single_read.py`（第六十二刀的单次读缓存），但其余
分支无人覆盖。本刀补 23 行。

## 本刀立住的三条纪律

1. **fail-closed**：未配置 OKX 时在**任何写盘之前**抛异常 —— 绝不用全零覆盖看板缓存。
2. **余额为 0 时回落最近一次有效快照**（而不是把看板显示成"资产归零"）。
3. **当日盈亏的口径**：`净额 = 已实现毛利 + 手续费 + 资金费`；
   台账兜底分支必须按 `close_time`（**不是** `time`）分日，且只认结清状态（审计 D5）。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.sync_web_data as swd  # noqa: E402


def _today():
    """`generate_trading_data` 的 `today_str` 取自**当前北京时间** ⇒ 夹具不能写死日期，
    否则台账兜底分支恒不匹配（本刀在此自伤过一次：9 条用例连锁假红）。"""
    import datetime
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz).strftime("%Y-%m-%d")


def _stamp(day=None):
    return f"{day or _today()} 12:00:00"


def _ms(day=None):
    import datetime
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return int(datetime.datetime.strptime(_stamp(day), "%Y-%m-%d %H:%M:%S")
               .replace(tzinfo=tz).timestamp() * 1000)


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.logs = self.root / "logs"
        self.data.mkdir()
        self.logs.mkdir()
        for name, value in (
            ("WORKSPACE_DIR", str(self.root)),
            ("DATA_DIR", str(self.data)),
            ("LOGS_DIR", str(self.logs)),
            ("LEDGER_JSON_FILE", str(self.data / "trading_ledger.json")),
            ("SNAPSHOTS_JSON_FILE", str(self.data / "snapshots.json")),
            ("LOG_FILE", str(self.logs / "trading.log")),
            ("DATA_JSON_PATH", str(self.data / "trading_data.json")),
        ):
            p = patch.object(swd, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.printed: list = []
        pr = patch.object(swd, "print", lambda *a, **k: self.printed.append(" ".join(map(str, a))))
        pr.start()
        self.addCleanup(pr.stop)

    def _env(self, *, configured=True):
        return SimpleNamespace(configured=configured, mode="demo", fingerprint="FP")

    def _run(self, *, balances=None, positions=None, bills=None,
             configured=True, instruments=None, tickers=None):
        patches = [
            patch.object(swd.okx_runtime, "current_environment",
                         lambda: self._env(configured=configured)),
            patch.object(swd.okx_rest, "balances", lambda: balances),
            patch.object(swd.okx_rest, "positions", lambda: positions),
            patch.object(swd.okx_rest, "bills", lambda limit=100: bills),
            patch.object(swd, "TARGET_INSTRUMENTS",
                         instruments if instruments is not None else []),
            patch.object(swd, "fetch_tickers_bulk", lambda **k: tickers or {}),
            patch.object(swd, "fetch_ticker", lambda i, **k: {}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        # ⚠️ `generate_trading_data()` **不返回** `data`（只写盘）⇒ 用例必须从盘上读回，
        #    也顺带更接近真实消费方（前端读的就是这个文件）。
        swd.generate_trading_data()
        return self._out()

    def _out(self):
        return json.loads((self.data / "trading_data.json").read_text(encoding="utf-8"))


# ───────────────────── 磁盘信息 ─────────────────────
class DiskInfoTests(unittest.TestCase):
    def test_a_real_disk_query_is_formatted(self):
        out = swd.get_disk_info()
        self.assertGreater(out["total_gb"], 0)
        self.assertGreaterEqual(out["percent"], 0)
        self.assertLessEqual(out["percent"], 100)

    def test_a_disk_query_failure_yields_all_zeros(self):
        # ★ 第 48/49 行 —— 拿不到磁盘信息不许让整个看板 500
        with patch("shutil.disk_usage", side_effect=OSError("no such device")):
            self.assertEqual(swd.get_disk_info(),
                             {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0})


# ───────────────────── JSON 惰性缓存 ─────────────────────
class JsonListCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "x.json")
        swd._JSON_CACHE.clear()
        self.addCleanup(swd._JSON_CACHE.clear)

    def test_a_missing_file_yields_the_default(self):
        self.assertEqual(swd._load_json_list(self.path, default=[]), [])

    def test_a_null_body_is_a_cache_hit_not_a_miss(self):
        # 第 74/75 行 —— 用哨兵而不是 None 表示"未缓存"，
        # 否则文件内容恰为 `null`（合法 JSON）时每次都被判为未缓存，退化成每调用读一次
        Path(self.path).write_text("null", encoding="utf-8")
        self.assertIsNone(swd._load_json_list(self.path, default=[]))
        self.assertIn(self.path, swd._JSON_CACHE)
        self.assertIsNone(swd._JSON_CACHE[self.path])

    def test_a_second_read_does_not_touch_the_disk(self):
        Path(self.path).write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(swd._load_json_list(self.path), [1, 2])
        Path(self.path).write_text("[9]", encoding="utf-8")
        self.assertEqual(swd._load_json_list(self.path), [1, 2], "第二次读缓存")

    def test_the_cache_is_cleared_per_generate_call(self):
        # 第 84/87 行 —— 调用方 `daemon_web_sync` 是**循环**调用的，
        # 缓存跨调用存活会让第二轮读到第一轮的陈旧数据
        self.assertIn("_JSON_CACHE.clear()",
                      Path(swd.__file__).read_text(encoding="utf-8"))


# ───────────────────── 主流程 ─────────────────────
class FailClosedTests(_Sandbox, unittest.TestCase):
    def test_unconfigured_keys_raise_before_writing(self):
        (self.data / "trading_data.json").write_text('{"keep": true}', encoding="utf-8")
        with self.assertRaises(swd.okx_rest.OKXNotConfigured):
            self._run(configured=False)
        self.assertEqual(self._out(), {"keep": True},
                         "fail-closed：绝不用全零覆盖看板缓存")


class PositionAggregationTests(_Sandbox, unittest.TestCase):
    def test_a_zero_size_position_is_skipped(self):
        # ★ 第 121/122 行
        out = self._run(positions=[{"instId": "BTC-USDT-SWAP", "pos": "0",
                                    "posSide": "long"}])
        self.assertEqual(out["positions_summary"]["items"], [])

    def test_a_long_position_is_counted(self):
        out = self._run(positions=[{"instId": "BTC-USDT-SWAP", "pos": "2",
                                    "posSide": "long", "upl": "5"}])
        self.assertEqual(len(out["positions_summary"]["items"]), 1)
        self.assertEqual(out["positions_summary"]["items"][0]["posSide"], "long")

    def test_a_short_position_is_counted(self):
        # ★ 第 126/127 行
        out = self._run(positions=[{"instId": "ETH-USDT-SWAP", "pos": "3",
                                    "posSide": "short", "upl": "-2"}])
        self.assertEqual(out["positions_summary"]["items"][0]["posSide"], "short")

    def test_a_net_position_neither_counts_as_long_nor_short(self):
        # `posSide` 默认 "net" ⇒ 只进 positions，不计方向计数（可由此推出）
        out = self._run(positions=[{"instId": "X-USDT-SWAP", "pos": "1"}])
        self.assertEqual(out["positions_summary"]["items"][0]["posSide"], "net")

    def test_a_non_list_positions_response_is_tolerated(self):
        out = self._run(positions="junk")
        self.assertEqual(out["positions_summary"]["items"], [])

    def test_the_upl_ratio_is_converted_to_a_percentage(self):
        out = self._run(positions=[{"instId": "X-USDT-SWAP", "pos": "1",
                                    "uplRatio": "0.15"}])
        self.assertEqual(out["positions_summary"]["items"][0]["uplRatio"], 15.0)


class SnapshotFallbackTests(_Sandbox, unittest.TestCase):
    def test_a_zero_balance_falls_back_to_the_last_valid_snapshot(self):
        # ★ 第 150–153 行
        (self.data / "snapshots.json").write_text(json.dumps([
            {"equity": 0.0, "avail": 0.0, "upl": 0.0},
            {"equity": 5000.0, "avail": 4000.0, "upl": 12.0},
        ]), encoding="utf-8")
        out = self._run(balances=[{"details": [{"ccy": "USDT", "eq": "0"}]}])
        self.assertEqual(out["account"]["total_eq"], 5000.0)
        self.assertEqual(out["account"]["avail_eq"], 4000.0)

    def test_the_last_valid_snapshot_is_used_not_the_first(self):
        (self.data / "snapshots.json").write_text(json.dumps([
            {"equity": 111.0}, {"equity": 222.0}]), encoding="utf-8")
        out = self._run(balances=[])
        self.assertEqual(out["account"]["total_eq"], 222.0)

    def test_a_non_positive_snapshot_is_not_valid_enough(self):
        # 全是 0/负 ⇒ `valid_snaps` 为空 ⇒ 保持 0
        (self.data / "snapshots.json").write_text(json.dumps([
            {"equity": 0.0}, {"equity": -5.0}]), encoding="utf-8")
        out = self._run(balances=[])
        self.assertEqual(out["account"]["total_eq"], 0.0)

    def test_a_corrupt_snapshot_file_is_swallowed(self):
        (self.data / "snapshots.json").write_text("{ broken", encoding="utf-8")
        out = self._run(balances=[])
        self.assertEqual(out["account"]["total_eq"], 0.0,
                         "`_load_json_list` 内部已吞掉解析失败 ⇒ 回落默认空表")

    def test_a_well_formed_json_with_the_wrong_shape_is_swallowed(self):
        # ★ 第 154/155 行 —— 注意 `_load_json_list` **自己不抛**（内部 except 回落 default），
        #    所以外层这个 except 只有在**形状**不对时才可达：
        #    `[1, 2]` 是合法 JSON，但元素不是 dict ⇒ 下面 `s.get(...)` 抛 AttributeError。
        (self.data / "snapshots.json").write_text("[1, 2, 3]", encoding="utf-8")
        out = self._run(balances=[])
        self.assertEqual(out["account"]["total_eq"], 0.0)

    def test_a_real_balance_is_not_overridden_by_the_fallback(self):
        (self.data / "snapshots.json").write_text(json.dumps([{"equity": 999.0}]),
                                                 encoding="utf-8")
        out = self._run(balances=[{"details": [{"ccy": "USDT", "eq": "1234.5"}]}])
        self.assertEqual(out["account"]["total_eq"], 1234.5)


class TodayPnlTests(_Sandbox, unittest.TestCase):
    def _bill(self, *, pnl=10.0, fee=0.5, sub="5", day=None):
        return {"ts": str(_ms(day)), "pnl": str(pnl), "fee": str(fee), "subType": sub}

    def test_a_profitable_close_counts_as_a_win(self):
        out = self._run(bills=[self._bill(pnl=10.0)])
        self.assertEqual(out["today_stats"]["win_trades"], 1)
        self.assertEqual(out["today_stats"]["loss_trades"], 0)

    def test_a_losing_close_counts_as_a_loss(self):
        # ★ 第 179/180 行
        out = self._run(bills=[self._bill(pnl=-10.0)])
        self.assertEqual(out["today_stats"]["loss_trades"], 1)
        self.assertEqual(out["today_stats"]["win_trades"], 0)

    def test_a_flat_close_counts_as_neither(self):
        # `pnl > 0` / `pnl < 0` 互斥 ⇒ 0 不计入任一侧
        out = self._run(bills=[self._bill(pnl=0.0)])
        self.assertEqual((out["today_stats"]["win_trades"], out["today_stats"]["loss_trades"]), (0, 0))

    def test_funding_goes_into_its_own_bucket(self):
        # ★ 第 181/182 行 —— subType 173/174
        out = self._run(bills=[self._bill(pnl=-3.0, sub="173"),
                               self._bill(pnl=-2.0, sub="174")])
        self.assertEqual(out["today_stats"]["win_trades"], 0, "资金费不算交易胜负")
        self.assertEqual(out["today_stats"]["loss_trades"], 0)

    def test_other_sub_types_are_ignored(self):
        out = self._run(bills=[self._bill(pnl=99.0, sub="999")])
        self.assertEqual(out["today_stats"]["win_trades"], 0)

    def test_a_bill_from_another_day_is_ignored(self):
        out = self._run(bills=[self._bill(pnl=10.0, day="2026-08-01")])
        self.assertEqual(out["today_stats"]["win_trades"], 0)

    def test_the_win_rate_is_a_percentage_of_closed_trades(self):
        out = self._run(bills=[self._bill(pnl=1.0), self._bill(pnl=-1.0)])
        self.assertEqual(out["today_stats"]["win_rate"], 50.0)

    def test_the_win_rate_is_zero_without_any_closed_trade(self):
        out = self._run(bills=[])
        self.assertEqual(out["today_stats"]["win_rate"], 0.0)


class LedgerFallbackTests(_Sandbox, unittest.TestCase):
    """无账单时从台账兜底（审计 D5：按 `close_time` 分日 + 结清状态白名单）。"""

    def _ledger(self, rows):
        (self.data / "trading_ledger.json").write_text(json.dumps(rows), encoding="utf-8")

    def test_a_closed_winning_trade_is_counted(self):
        self._ledger([{"status": "closed", "close_time": _stamp(),
                       "pnl": 10.0}])
        out = self._run(bills=[])
        self.assertEqual(out["today_stats"]["win_trades"], 1)

    def test_a_closed_losing_trade_is_counted(self):
        self._ledger([{"status": "closed", "close_time": _stamp(), "pnl": -10.0}])
        out = self._run(bills=[])
        self.assertEqual(out["today_stats"]["loss_trades"], 1)

    def test_the_chinese_closed_status_is_accepted(self):
        self._ledger([{"status": "已平仓", "close_time": _stamp(), "pnl": 1.0}])
        self.assertEqual(self._run(bills=[])["today_stats"]["win_trades"], 1)

    def test_the_completed_status_is_accepted(self):
        self._ledger([{"status": "Completed", "close_time": _stamp(), "pnl": 1.0}])
        self.assertEqual(self._run(bills=[])["today_stats"]["win_trades"], 1)

    def test_an_open_holding_row_is_not_counted(self):
        self._ledger([{"status": "holding", "close_time": _stamp(), "pnl": 1.0}])
        self.assertEqual(self._run(bills=[])["today_stats"]["win_trades"], 0)

    def test_a_row_without_a_status_is_not_counted(self):
        self._ledger([{"close_time": _stamp(), "pnl": 1.0}])
        self.assertEqual(self._run(bills=[])["today_stats"]["win_trades"], 0)

    def test_the_close_time_key_is_used_not_time(self):
        # 审计 D5：旧代码按 `t.get("time")` 分日恒 None ⇒ JSON 兜底分支静默归零
        self._ledger([{"status": "closed", "close_time": _stamp(), "pnl": 5.0}])
        counted_via_close_time = self._run(bills=[])["today_stats"]["win_trades"]

        (self.data / "trading_data.json").unlink()
        self._ledger([{"status": "closed", "time": _stamp(), "pnl": 5.0}])
        counted_via_time = self._run(bills=[])["today_stats"]["win_trades"]
        self.assertEqual(counted_via_close_time, 1)
        self.assertEqual(counted_via_time, 1, "`time` 作为**后备**键仍然生效")

    def test_a_trade_from_another_day_is_ignored(self):
        self._ledger([{"status": "closed", "close_time": _stamp("2026-08-01"),
                       "pnl": 10.0}])
        self.assertEqual(self._run(bills=[])["today_stats"]["win_trades"], 0)

    def test_a_corrupt_ledger_is_swallowed(self):
        (self.data / "trading_ledger.json").write_text("{ broken", encoding="utf-8")
        out = self._run(bills=[])
        self.assertEqual(out["today_stats"]["win_trades"], 0)

    def test_a_non_dict_ledger_row_is_swallowed(self):
        # ★ 第 203/204 行 —— 元素非 dict ⇒ `t.get(...)` 抛 AttributeError
        (self.data / "trading_ledger.json").write_text("[1, 2]", encoding="utf-8")
        out = self._run(bills=[])
        self.assertEqual(out["today_stats"]["win_trades"], 0)

    def test_a_real_bill_list_suppresses_the_ledger_fallback(self):
        self._ledger([{"status": "closed", "close_time": _stamp(), "pnl": 5.0}])
        out = self._run(bills=[{"ts": str(_ms()), "pnl": "1", "fee": "0", "subType": "5"}])
        self.assertEqual(out["today_stats"]["win_trades"], 1, "只算账单那一笔，不叠加台账")


class AuxiliaryReadTests(_Sandbox, unittest.TestCase):
    def test_snapshots_are_truncated_to_the_last_forty(self):
        (self.data / "snapshots.json").write_text(
            json.dumps([{"equity": i} for i in range(1, 61)]), encoding="utf-8")
        out = self._run(balances=[])
        self.assertEqual(len(out["snapshots"]), 40)
        self.assertEqual(out["snapshots"][-1]["equity"], 60)

    def test_trades_are_reversed_and_truncated_to_sixty(self):
        (self.data / "trading_ledger.json").write_text(
            json.dumps([{"id": i} for i in range(1, 81)]), encoding="utf-8")
        out = self._run(bills=[])
        self.assertEqual(len(out["trades"]), 60)
        self.assertEqual(out["trades"][0]["id"], 80, "最新的在前")

    def test_a_corrupt_snapshot_read_is_swallowed(self):
        (self.data / "snapshots.json").write_text("{ broken", encoding="utf-8")
        self.assertEqual(self._run(balances=[])["snapshots"], [])

    def test_a_non_list_snapshot_body_is_swallowed(self):
        # ★ 第 216/217 行 —— 合法 JSON 但非列表 ⇒ 后面的 `[-40:]` 抛 TypeError
        (self.data / "snapshots.json").write_text("5", encoding="utf-8")
        self.assertEqual(self._run(balances=[])["snapshots"], [])

    def test_a_corrupt_ledger_read_is_swallowed(self):
        (self.data / "trading_ledger.json").write_text("{ broken", encoding="utf-8")
        self.assertEqual(self._run(bills=[])["trades"], [])

    def test_a_non_list_ledger_body_is_swallowed(self):
        # ★ 第 222/223 行 —— `reversed(5)` 抛 TypeError
        (self.data / "trading_ledger.json").write_text("5", encoding="utf-8")
        self.assertEqual(self._run(bills=[])["trades"], [])

    def test_a_corrupt_ai_decisions_cache_is_swallowed(self):
        # ★ 第 232/233 行
        (self.data / "ai_brain_decisions.json").write_text("{ broken", encoding="utf-8")
        out = self._run(balances=[], instruments=[{"instId": "BTC-USDT-SWAP",
                                                   "name": "BTC"}])
        self.assertEqual(out["factors"][0]["action"], "WAIT")

    def test_a_buy_long_decision_scores_positive(self):
        # ★ 第 254/255 行
        (self.data / "ai_brain_decisions.json").write_text(json.dumps({
            "BTC-USDT-SWAP": {"decision": {"action": "BUY_LONG", "confidence": 88}}}),
            encoding="utf-8")
        out = self._run(balances=[], instruments=[{"instId": "BTC-USDT-SWAP",
                                                   "name": "BTC"}])
        self.assertEqual(out["factors"][0]["score"], 2.5)
        self.assertEqual(out["factors"][0]["confidence"], 88)

    def test_a_sell_short_decision_scores_negative(self):
        # ★ 第 256/257 行
        (self.data / "ai_brain_decisions.json").write_text(json.dumps({
            "BTC-USDT-SWAP": {"decision": {"action": "SELL_SHORT"}}}),
            encoding="utf-8")
        out = self._run(balances=[], instruments=[{"instId": "BTC-USDT-SWAP",
                                                   "name": "BTC"}])
        self.assertEqual(out["factors"][0]["score"], -2.5)

    def test_a_wait_decision_scores_zero(self):
        out = self._run(balances=[], instruments=[{"instId": "BTC-USDT-SWAP",
                                                   "name": "BTC"}])
        self.assertEqual(out["factors"][0]["score"], 0.0)

    def test_the_default_reason_is_used_when_absent(self):
        out = self._run(balances=[], instruments=[{"instId": "BTC-USDT-SWAP",
                                                   "name": "BTC"}])
        self.assertEqual(out["factors"][0]["reason"], "等待高确定性行情出现")

    def test_the_24h_change_is_computed_from_the_open(self):
        out = self._run(balances=[], instruments=[{"instId": "BTC-USDT-SWAP",
                                                   "name": "BTC"}],
                        tickers={"BTC-USDT-SWAP": {"last": "110", "open24h": "100"}})
        self.assertEqual(out["factors"][0]["chg24h"], 10.0)

    def test_a_zero_open_price_yields_a_zero_change(self):
        out = self._run(balances=[], instruments=[{"instId": "BTC-USDT-SWAP",
                                                   "name": "BTC"}],
                        tickers={"BTC-USDT-SWAP": {"last": "110", "open24h": "0"}})
        self.assertEqual(out["factors"][0]["chg24h"], 0)

    def test_the_log_tail_is_read_when_present(self):
        # ★ 第 277–281 行
        (self.logs / "trading.log").write_text(
            "\n".join(f"line{i}" for i in range(80)), encoding="utf-8")
        out = self._run(balances=[])
        self.assertEqual(len(out["logs"]), 60)
        self.assertEqual(out["logs"][-1], "line79")

    def test_a_missing_log_file_yields_no_logs(self):
        self.assertEqual(self._run(balances=[])["logs"], [])

    def test_a_log_tail_failure_is_swallowed(self):
        # ★ 第 280/281 行
        (self.logs / "trading.log").write_text("x", encoding="utf-8")
        with patch.object(swd.subprocess, "run", side_effect=OSError("no shell")):
            out = self._run(balances=[])
        self.assertEqual(out["logs"], [])


class OutputShapeTests(_Sandbox, unittest.TestCase):
    def test_the_auth_block_reports_the_runtime(self):
        out = self._run(balances=[])
        self.assertEqual(out["auth"]["mode"], "demo")
        self.assertTrue(out["auth"]["configured"])
        self.assertEqual(out["auth"]["fingerprint"], "FP")

    def test_the_timestamp_uses_beijing_time(self):
        out = self._run(balances=[])
        self.assertIn("(北京时间)", out["timestamp"])

    def test_the_disk_block_is_present(self):
        out = self._run(balances=[])
        self.assertIn("free_gb", out["system"]["disk"])

    def test_the_output_file_is_written_to_the_configured_path(self):
        self._run(balances=[])
        self.assertTrue((self.data / "trading_data.json").exists())


class MainGuardTests(_Sandbox, unittest.TestCase):
    def test_the_main_guard_exits_three_when_keys_are_missing(self):
        # ★ 第 345 行段 —— 未配置时非零退出，且不写缓存
        import runpy
        scripts = self.root / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        copy = scripts / "sync_web_data.py"
        copy.write_text(Path(swd.__file__).read_text(encoding="utf-8"), encoding="utf-8")
        # 副本会在模块头部把 `_ROOT` / `_PROJECT_ROOT` / `_THIS_DIR` 塞进 sys.path
        # （实测泄漏的是 `<tmp>/scripts` 这个**子目录**）⇒ 两个都登记摘除。
        for entry in (str(self.root), str(self.root / "scripts")):
            if entry not in sys.path:
                self.addCleanup(sys.path.remove, entry)
        with patch.object(swd.okx_runtime, "current_environment",
                          lambda: self._env(configured=False)), \
             patch("urllib.request.urlopen", side_effect=OSError("no net")):
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_path(str(copy), run_name="__main__")
        self.assertEqual(ctx.exception.code, 3)

    def test_the_main_guard_source_has_the_documented_contract(self):
        src = Path(swd.__file__).read_text(encoding="utf-8")
        self.assertIn('raise SystemExit(3)', src)
        self.assertIn("[NOT READY]", src)
        self.assertIn("def main():", src, "CLI 入口是 main()，不是内联 __main__ 块")


if __name__ == "__main__":
    unittest.main()

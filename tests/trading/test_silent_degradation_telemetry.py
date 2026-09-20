# -*- coding: utf-8 -*-
"""四处「静默降级」加告警/遥测的回归门（2026-09-16 用户点名）。

## 这个测试在守什么

本仓红线是「缺失≠0 / UI 不说谎」，但核心链路里有几处 `except Exception: pass`
会让失败**完全静默**（`tests/audit/test_module_free_names.py` 的 docstring 也点名了这个坑）：

| 位置 | 静默降级的后果 |
|---|---|
| `trader/factors.py` BBO 取价失败 | bid/ask 悄悄退回最新价 → 限价精度降级，无人知 |
| `trader/factors.py` 舆情文件读失败 | `sentiment_score` 停在 0.0 → 主脑把"没数据"当"中性" |
| `trader/factors.py` 动力学计算失败 | 退化成全 0 动力学（v=a=j=I=0）→ 主脑照着 0 推理 |
| `ai_factor_trader.py` 持仓追踪读/写失败 | 静默丢在管持仓状态（移动止损/水位），且原先是非原子写 |

本门钉住每一条都必须：① **保留降级路径**（绝不因告警而阻断交易）；
② **留可观测痕迹**（RuntimeWarning / 结构化标记）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.ai_factor_trader as aft
from scripts.trader import factors as factors_mod


def _candles(n: int, base: float = 100.0):
    """OKX 蜡烛形状：[ts, o, h, l, c, vol, ...]（时间递增）。"""
    rows = []
    for i in range(n):
        px = base + i * 0.1
        rows.append([str(1_700_000_000_000 + i * 900_000), str(px), str(px + 0.5),
                     str(px - 0.5), str(px + 0.2), "10", "10", "10", "1"])
    return rows


def _item():
    return {"instId": "BTC-USDT-SWAP", "name": "BTC", "type": "crypto",
            "base_sz": 1, "minSz": 1, "ctVal": 0.01, "precision": 1,
            "risk_per_trade_usd": 15.0, "max_leverage": 5}


def _run_factors(news_file: str):
    """跑一次 fetch_single_instrument_data（全注入、零出网）。"""
    return factors_mod.fetch_single_instrument_data(
        _item(), [], 1000.0,
        news_sentiment_file=news_file,
        fetch_candles_direct=lambda inst_id, bar, limit: _candles(max(limit, 40)),
        instrument_profile=lambda f, t: {"sl_atr_mult": 1.3, "tp_atr_mult": 2.0},
        load_adaptive_config=lambda: {"position_size_multipliers": {}},
    )


class FactorTelemetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tel-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.news = os.path.join(self.tmp, "news_sentiment.json")

    def test_bbo_failure_warns_and_still_degrades(self):
        import urllib.request
        with patch.object(urllib.request, "urlopen", side_effect=OSError("bbo down")):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                f = _run_factors(self.news)
        self.assertIsInstance(f, dict, "告警不得阻断取数")
        msgs = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
        self.assertTrue(any("BBO" in m and "BTC-USDT-SWAP" in m for m in msgs), msgs)

    def test_sentiment_read_failure_marks_unavailable_not_neutral(self):
        with open(self.news, "w", encoding="utf-8") as h:
            h.write("{ 这不是 JSON")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            f = _run_factors(self.news)
        self.assertIs(f.get("sentiment_available"), False,
                      "读失败必须显式标记不可用，不得留下'0.0=中性'的假象")
        msgs = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
        self.assertTrue(any("舆情" in m for m in msgs), msgs)

    def test_sentiment_present_marks_available(self):
        with open(self.news, "w", encoding="utf-8") as h:
            json.dump({"coins_sentiment": {"BTC": {"sentiment_factor_score": 0.0}}}, h)
        f = _run_factors(self.news)
        self.assertIs(f.get("sentiment_available"), True,
                      "文件可读且该币在册 ⇒ 即使分数为 0 也是「真中性」")

    def test_calculus_failure_records_reason_and_warns(self):
        broken = types.ModuleType("calculus_engine")

        def _boom(*a, **k):
            raise RuntimeError("engine exploded")

        broken.calculate_multi_timeframe = _boom
        with patch.dict(sys.modules, {"calculus_engine": broken}):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                f = _run_factors(self.news)
        calc = f.get("calculus") or {}
        self.assertIs(calc.get("valid"), False, "退化形状必须自陈 valid=False")
        self.assertIn("error", calc, "必须把失败原因写进结构化字段，而不是只留在日志")
        self.assertIn("engine exploded", str(calc["error"]))
        msgs = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
        self.assertTrue(any("动力学" in m for m in msgs), msgs)


class TrackerTelemetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tracker-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.path = os.path.join(self.tmp, "position_trackers.json")
        self._patch = patch.object(aft, "POSITION_TRACKER_FILE", self.path)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_load_bad_json_warns_and_returns_empty(self):
        with open(self.path, "w", encoding="utf-8") as h:
            h.write("{半截 JSON")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            got = aft.load_trackers()
        self.assertEqual(got, {}, "调用方语义保持：拿不到追踪就按空继续")
        msgs = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
        self.assertTrue(any("持仓追踪" in m for m in msgs), msgs)

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        aft.save_trackers({"BTC-USDT-SWAP_long": {"trailingStopPx": 1.23}})
        self.assertEqual(aft.load_trackers()["BTC-USDT-SWAP_long"]["trailingStopPx"], 1.23)
        leftovers = [n for n in os.listdir(self.tmp) if n != os.path.basename(self.path)]
        self.assertEqual(leftovers, [], f"原子写不得留下临时文件：{leftovers}")

    def test_save_failure_warns(self):
        with patch.object(aft, "POSITION_TRACKER_FILE", "/proc/nonexistent/pt.json"):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                aft.save_trackers({"x": 1})
        msgs = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
        self.assertTrue(any("追踪状态未落盘" in m for m in msgs), msgs)


class UnreadableTrackersMustNotClobberTest(unittest.TestCase):
    """追踪状态"读不出来" ⇒ 标记身份 + **拒绝覆盖**（第一百三十七刀）。

    缺陷形状（两个后果，都是"读不到当成没有"）：
    ① `pyramiding_gate` 的「每仓最多加仓 N 次」判据是 `scale_count < max`；读失败
       返回 `{}` ⇒ `scale_count=0` ⇒ **上限被静默绕过**（可反复加仓、过度集中）；
    ② 加仓成功后会 `tracker["scale_count"] = 1` 再 `save_trackers(trackers)` ⇒
       用这个**近乎空的字典覆盖整个文件** ⇒ 其它持仓的移动止损水位与挂单 tracker
       归属依据**被永久抹掉**。

    本刀堵②（破坏性那一半）；①仍只告警 —— 入场循环被入口门以**零归一 AST 逐字**
    冻结，改它需先给那道门加"文档化差异"机制（独立一刀，已列入待办）。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tracker-unreadable-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.path = os.path.join(self.tmp, "position_trackers.json")
        self._patch = patch.object(aft, "POSITION_TRACKER_FILE", self.path)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_unreadable_state_is_marked_but_still_a_dict(self):
        with open(self.path, "w", encoding="utf-8") as h:
            h.write("{半截 JSON")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            got = aft.load_trackers()
        self.assertIsInstance(got, aft.UnreadableTrackers, "必须带身份标记，供写入侧判定")
        self.assertEqual(got, {}, "调用方语义不变（它就是个空 dict）")

    def test_missing_file_is_a_legitimate_empty_state(self):
        """文件**不存在**是合法空态（不是标记类型）⇒ 之后照常可落盘。"""
        got = aft.load_trackers()
        self.assertNotIsInstance(got, aft.UnreadableTrackers)
        got["BTC-USDT-SWAP_long"] = {"scale_count": 1}
        aft.save_trackers(got)
        self.assertEqual(sorted(json.loads(Path(self.path).read_text(encoding="utf-8"))),
                         ["BTC-USDT-SWAP_long"])

    def test_save_refuses_to_clobber_when_state_unreadable(self):
        # 现场：文件里有**别的持仓**的水位（此处用"半截 JSON"模拟读不出来的真实文件）
        broken = '{"ETH-USDT-SWAP_long": {"trailingStopPx": 3000.0}}'[:-1]
        with open(self.path, "w", encoding="utf-8") as h:
            h.write(broken)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            state = aft.load_trackers()
        state["BTC-USDT-SWAP_long"] = {"scale_count": 1}      # 模拟加仓记账
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            aft.save_trackers(state)
        self.assertEqual(Path(self.path).read_text(encoding="utf-8"), broken,
                         "读不出来时**不得覆盖**（否则其它持仓水位/归属依据被永久抹掉）")
        msgs = [str(w.message) for w in caught if issubclass(w.category, RuntimeWarning)]
        self.assertTrue(any("拒绝落盘" in m for m in msgs), msgs)
        self.assertEqual([n for n in os.listdir(self.tmp)
                          if n != os.path.basename(self.path)], [],
                         "拒绝路径不得留下临时文件")

if __name__ == "__main__":
    unittest.main()


class SignalJournalIsolationTest(unittest.TestCase):
    """③ 信号日记落盘必须**调用期**解析路径，否则测试会真写生产。

    历史事故：`test_trader_position_exit_extraction` 未替身 `record_signal_snapshot`
    时，模块级 `SIGNAL_JOURNAL_FILE`（导入期绑定）让 `patch.object(aft, "DATA_DIR")`
    失效 ⇒ 249 条夹具（ETH/2500.0/2026-09-07 10:00:00）被写进**生产**日记。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="journal-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self._patch = patch.object(aft, "DATA_DIR", self.tmp)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_data_dir_patch_isolates_the_write(self):
        prod = _ROOT / "data" / "signal_journal.json"
        before = prod.read_bytes() if prod.exists() else None
        aft.record_signal_snapshot({"instId": "TEST-USDT-SWAP", "entryTime": "2026-09-16 00:00:00"})
        written = os.path.join(self.tmp, "signal_journal.json")
        self.assertTrue(os.path.exists(written), "快照必须落在被 patch 的 DATA_DIR 里")
        self.assertEqual(json.load(open(written, encoding="utf-8"))[0]["instId"], "TEST-USDT-SWAP")
        after = prod.read_bytes() if prod.exists() else None
        self.assertEqual(before, after, "生产 signal_journal.json 不得被测试写动一个字节")

    def test_write_is_atomic_and_caps_at_500(self):
        path = os.path.join(self.tmp, "signal_journal.json")
        with open(path, "w", encoding="utf-8") as h:
            json.dump([{"i": i} for i in range(500)], h)
        aft.record_signal_snapshot({"i": 500})
        rows = json.load(open(path, encoding="utf-8"))
        self.assertEqual(len(rows), 500, "保留最近 500 条")
        self.assertEqual(rows[-1]["i"], 500)
        leftovers = [n for n in os.listdir(self.tmp) if n != "signal_journal.json"]
        self.assertEqual(leftovers, [], f"原子写不得留临时文件：{leftovers}")

class CycleDisclosureSummaryTest(unittest.TestCase):
    """周期披露汇总（第 50 刀）：每轮必须留下**一条可检索**的"跳过/未核验"行。

    为什么把它当门禁：披露此前散落在各处 `print`，**重构时最容易静默消失**。
    汇总行把"本轮跳过了什么"固定成日志里的一条 —— 有人删掉某处披露，数字就会变，
    评审看日志即可发现。
    """

    def _sum(self, **kw):
        # 第 51 刀起：payload（结构化）与渲染分离 —— 渲染用例依旧只关心"那一行"
        from scripts.trader.cycle_stages import (cycle_disclosure_payload,
                                                 cycle_disclosure_summary)
        return cycle_disclosure_summary(cycle_disclosure_payload(**kw))

    def test_clean_cycle_says_so(self):
        line = self._sum()
        self.assertTrue(line.startswith("[周期披露] "), line)
        self.assertIn("本轮无跳过/未核验项", line)

    def test_broken_venues_are_listed_sorted_and_deduped(self):
        line = self._sum(broken_venues=["gate", "binance", "gate"])
        self.assertIn("凭证坏所=2(binance,gate)", line)

    def test_entries_blocked_is_disclosed(self):
        self.assertIn("对账失败（禁本轮新开仓）", self._sum(entries_blocked=True))

    def test_shape_violations_are_counted_with_a_head(self):
        line = self._sum(shape_violations=["a", "b", "c", "d", "e"])
        self.assertIn("数据形状违规=5", line)
        self.assertIn("共5条", line)
        self.assertIn("a", line)
        self.assertNotIn("e", line, "只展示前 3 条明细（避免刷屏）")

    def test_watchdog_report_counts_errors_and_critical(self):
        rep = {"errors": [{"stage": "list"}], "critical": [{"venue": "gate"}, {"venue": "x"}]}
        line = self._sum(watchdog_report=rep)
        self.assertIn("跨所保护：错误=1 严重缺口=2", line)

    def test_clean_watchdog_report_adds_no_clause(self):
        line = self._sum(watchdog_report={"errors": [], "critical": []})
        self.assertNotIn("跨所保护", line)

    def test_disabled_watchdog_is_disclosed_as_not_running(self):
        """未开闸的加固层是"没在跑的保护" —— 应当被看见（但不算错误）。"""
        line = self._sum(watchdog_enabled=False)
        self.assertIn("跨所保护巡检未开闸", line)
        self.assertIn("本轮无跳过/未核验项", line)

    def test_reporter_never_raises_on_garbage_input(self):
        """报告器不得成为新的单点故障（本仓固有约束）。"""
        for kw in ({"broken_venues": None, "shape_violations": None},
                   {"watchdog_report": MagicMock()},
                   {"broken_venues": [None, ""], "shape_violations": [None]},
                   {"entries_blocked": None}):
            with self.subTest(kw=sorted(kw)):
                line = self._sum(**kw)
                self.assertTrue(line.startswith("[周期披露] "), line)

    def test_facade_prints_the_summary_every_cycle(self):
        """源码钉：汇总必须在周期收尾被打印（否则整条纪律只是"有个函数没人调"）。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2] / "scripts" / "ai_factor_trader.py"
               ).read_text(encoding="utf-8")
        self.assertIn("print(cycle_disclosure_summary(", src)
        # 同一份载荷还要**落盘**给后端 /metrics（有函数没人调 = 纪律落空）
        self.assertIn("write_cycle_disclosure_snapshot(", src)
        self.assertIn("path=CYCLE_DISCLOSURE_FILE", src)
        for kw in ("broken_venues=_BROKEN_VENUES", "entries_blocked=entries_blocked",
                   "shape_violations=_shape_violations", "watchdog_report=_wd_report",
                   "watchdog_enabled=R20_VENUE_PROTECTION_WATCHDOG"):
            with self.subTest(arg=kw):
                self.assertIn(kw, src, f"汇总缺参数 {kw} ⇒ 该路披露不会被汇总")

class CycleDisclosurePayloadAndSnapshotTest(unittest.TestCase):
    """披露载荷（结构化）+ 快照落盘（第 51 刀）。

    分层的理由：**渲染**（给人看的一行）与**落盘**（给 /metrics 读的结构）必须共用
    同一份判定 —— 否则就是本仓反复吃过的"同一语义两处写 ⇒ 必然漂移"。
    """

    def test_payload_is_structured_and_marks_clean(self):
        from scripts.trader.cycle_stages import cycle_disclosure_payload
        clean = cycle_disclosure_payload()
        self.assertTrue(clean["clean"])
        self.assertEqual(clean["broken_venue_count"], 0)
        self.assertFalse(clean["watchdog_enabled"] is None)
        dirty = cycle_disclosure_payload(broken_venues=["gate"], entries_blocked=True,
                                         shape_violations=["x", "y"])
        self.assertFalse(dirty["clean"])
        self.assertEqual(dirty["broken_venues"], ["gate"])
        self.assertEqual(dirty["shape_violation_count"], 2)
        self.assertEqual(dirty["shape_violation_head"], ["x", "y"])

    def test_payload_tolerates_garbage(self):
        from scripts.trader.cycle_stages import cycle_disclosure_payload
        p = cycle_disclosure_payload(broken_venues=None, shape_violations=[None],
                                     watchdog_report=MagicMock())
        self.assertEqual(p["broken_venue_count"], 0)
        self.assertEqual(p["shape_violation_count"], 1)     # None 也如实计入（不假装没发生）
        self.assertEqual(p["watchdog_errors"], 0)           # 非 dict ⇒ 不猜、不计

    def test_snapshot_is_written_atomically_with_freshness_stamp(self):
        import json as _json
        import tempfile
        from pathlib import Path as _P
        from scripts.trader.cycle_stages import (cycle_disclosure_payload,
                                                 write_cycle_disclosure_snapshot)
        from scripts.ai_factor_trader import _atomic_write_json
        with tempfile.TemporaryDirectory() as td:
            target = _P(td) / "cycle_disclosure.json"
            ok = write_cycle_disclosure_snapshot(
                path=str(target), payload=cycle_disclosure_payload(broken_venues=["gate"]),
                _atomic_write_json=_atomic_write_json)
            self.assertTrue(ok)
            body = _json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(body["broken_venue_count"], 1)
            self.assertIsInstance(body["written_at_ms"], int)
            left = [n for n in __import__("os").listdir(td) if n != "cycle_disclosure.json"]
            self.assertEqual(left, [], "原子写不得留临时文件")

    def test_snapshot_write_failure_returns_false_and_never_raises(self):
        from scripts.trader.cycle_stages import write_cycle_disclosure_snapshot
        def boom(*a, **k):
            raise OSError("磁盘满")
        self.assertFalse(write_cycle_disclosure_snapshot(
            path="/proc/nonexistent/x.json", payload={}, _atomic_write_json=boom))

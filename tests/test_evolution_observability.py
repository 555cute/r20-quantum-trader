"""自进化数理快照可观测性回归钉扎（2026-09-10）。

审计背景：台账 30 笔平仓单的 v/a/j/I、积分、概率、VaR/CVaR 动力学字段全为 null
（09-09 build_signal_snapshot schema 修复之前的历史空壳快照）。复盘系统必须：
1. join 侧禁止未来/过期/反向快照回填成「开仓证据」（倒推伪造通道）；
2. 宿主逐单确定性判定可观测性并前置注入「宿主宪章」，不依赖模型自数 null；
3. 基准心法（is_baseline）宪法级：进化输出省略/删除时宿主补回，NO_CHANGE 永不覆盖。
"""
from __future__ import annotations
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
scripts_dir = str(ROOT / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import self_improvement_engine as sie
from scripts import evolution_shield as shield


def _dyn(**over):
    snap = {k: None for k in sie.DYNAMICS_FIELDS}
    snap.update({"price": 1.0, "atr": 0.01, "adx": 20.0, "rsi": 55.0,
                 "funding_rate": 0.01, "composite_alpha_score": 3.0,
                 "smart_money_net": 100.0})
    for k in sie.DYNAMICS_FIELDS:
        snap.setdefault(k, None)
    snap.update(over)
    return snap


class MatchSnapshotIronRulesTests(unittest.TestCase):
    JOURNAL = {"BTC": [
        {"name": "BTC", "side": "long",  "entryTime": "2026-09-09 23:50:00", "snapshot": _dyn(velocity=9.9)},
        {"name": "BTC", "side": "short", "entryTime": "2026-09-09 10:00:00", "snapshot": _dyn(velocity=-9.9)},
        {"name": "BTC", "side": "long",  "entryTime": "2026-09-09 13:20:00", "snapshot": _dyn(velocity=1.1)},
        {"name": "BTC", "side": "long",  "entryTime": "2026-08-01 13:20:00", "snapshot": _dyn(velocity=7.7)},
    ]}

    def test_picks_nearest_same_side_before_open(self):
        snap = sie._match_snapshot(self.JOURNAL, "BTC", "2026-09-09 13:33:11", "多")
        self.assertEqual(snap["velocity"], 1.1)

    def test_never_joins_future_snapshot(self):
        # 开仓前无任何同向候选 → 必须 None，不得回填之后的快照
        journal = {"X": [{"side": "long", "entryTime": "2026-09-10 00:00:00", "snapshot": _dyn(velocity=1)}]}
        self.assertIsNone(sie._match_snapshot(journal, "X", "2026-09-09 13:33:11", "空"))
        self.assertIsNone(sie._match_snapshot(journal, "X", "2026-09-09 13:33:11", "多"))

    def test_rejects_stale_snapshot_beyond_window(self):
        journal = {"Y": [{"side": "long", "entryTime": "2026-08-01 10:00:00", "snapshot": _dyn(velocity=2)}]}
        self.assertIsNone(sie._match_snapshot(journal, "Y", "2026-09-09 13:33:11", "多"))

    def test_side_mismatch_rejected(self):
        journal = {"Z": [{"side": "short", "entryTime": "2026-09-09 13:00:00", "snapshot": _dyn(velocity=3)}]}
        self.assertIsNone(sie._match_snapshot(journal, "Z", "2026-09-09 13:33:11", "多"))

    def test_unparseable_open_time_yields_none(self):
        self.assertIsNone(sie._match_snapshot(self.JOURNAL, "BTC", "", "多"))
        self.assertIsNone(sie._match_snapshot(self.JOURNAL, "BTC", None, None))


class ObservabilityClassifyTests(unittest.TestCase):
    def test_null_shell_is_price_only(self):
        self.assertEqual(sie.classify_snapshot_observability(_dyn()), "PRICE_ONLY")

    def test_full_chain_is_observed(self):
        full = _dyn(**{k: 0.5 for k in sie.DYNAMICS_FIELDS})
        self.assertEqual(sie.classify_snapshot_observability(full), "DYNAMICS_OBSERVED")

    def test_partial_chain(self):
        partial = _dyn(velocity=0.5, acceleration=0.4, jerk=0.3)
        self.assertEqual(sie.classify_snapshot_observability(partial), "PARTIAL")

    def test_none_and_empty(self):
        self.assertEqual(sie.classify_snapshot_observability(None), "NONE")
        self.assertEqual(sie.classify_snapshot_observability({}), "NONE")

    def test_prune_drops_nulls(self):
        pruned = sie.prune_snapshot(_dyn(velocity=1.0))
        self.assertNotIn("acceleration", pruned)
        self.assertEqual(pruned["velocity"], 1.0)
        # 空壳快照剪掉 null 后仍保留 price/atr 等普通观测（诚实呈现，不伪装也无所谓）
        self.assertNotIn("velocity", sie.prune_snapshot(_dyn()))
        self.assertEqual(len(sie.prune_snapshot(_dyn())), 7)
        self.assertIsNone(sie.prune_snapshot({"velocity": None, "price": None}))
        self.assertIsNone(sie.prune_snapshot(None))

    def test_audit_counts(self):
        trades = [{"snapshot_observability": t} for t in
                  ("DYNAMICS_OBSERVED", "PARTIAL", "PRICE_ONLY", "NONE", {})]
        audit = sie.audit_snapshot_observability(trades)
        self.assertEqual(audit["total"], 5)
        self.assertEqual(audit["math_observable"], 2)
        self.assertEqual(audit["NONE"], 2)  # 标签为 NONE + 完全缺失标签各一


class ConstitutionMergeTests(unittest.TestCase):
    EXISTING = [
        {"rule_text": "【A基准】趋势", "enabled": True, "is_baseline": True},
        {"rule_text": "【B基准】宽止损", "enabled": True, "is_baseline": True},
        {"rule_text": "【C战术】短线", "enabled": True, "is_baseline": False},
    ]

    def test_add_is_pure_append(self):
        final, readded = sie.merge_memory_with_constitution("ADD", ["【D新】经验"], self.EXISTING)
        self.assertEqual(final, ["【A基准】趋势", "【B基准】宽止损", "【C战术】短线", "【D新】经验"])
        self.assertEqual(readded, [])

    def test_add_never_drops_existing_even_if_model_omits(self):
        final, _ = sie.merge_memory_with_constitution("ADD", [], self.EXISTING)
        self.assertIn("【A基准】趋势", final)

    def test_invalidate_cannot_delete_baseline(self):
        final, readded = sie.merge_memory_with_constitution("INVALIDATE", ["【B基准】宽止损"], self.EXISTING)
        self.assertIn("【A基准】趋势", final)   # 被省略的基准由宿主补回
        self.assertEqual(readded, ["【A基准】趋势"])
        self.assertNotIn("【C战术】短线", final)  # 非基准战术层可由模型整理

    def test_revise_keeps_omitted_baselines(self):
        final, readded = sie.merge_memory_with_constitution("REVISE", ["【C战术】改版"], self.EXISTING)
        self.assertIn("【A基准】趋势", final)
        self.assertIn("【B基准】宽止损", final)
        self.assertEqual(len(readded), 2)

    def test_resolve_no_change_preserves(self):
        status, mem, preserve = sie.resolve_memory_update("NO_CHANGE", ["新东西"], ["旧A", "旧B"])
        self.assertTrue(preserve)
        self.assertEqual(mem, ["旧A", "旧B"])
        status, mem, preserve = sie.resolve_memory_update("GARBAGE", [], ["旧A"])
        self.assertEqual(status, "NO_CHANGE")
        self.assertTrue(preserve)


class PromptConstitutionInjectionTests(unittest.TestCase):
    """宪章必须在 apply_module_layout 之后注入——风格档案保存的旧分节拷贝无法覆盖它。"""

    CLOSED = [
        {"inst": "ALGO", "side": "多", "time": "2026-09-09 23:18:21", "open_time": "2026-09-09 13:33:11",
         "strategy": "🌊 顺势做多", "margin": 179.4, "gross_pnl": -7.34, "fee": 0.37, "net_pnl": -7.71,
         "exit_reason": "🛑 触发云端止损", "snapshot_observability": "NONE", "entry_snapshot": None},
        {"inst": "BTC", "side": "多", "time": "2026-09-08 10:00:00", "open_time": "2026-09-08 09:00:00",
         "strategy": "⚡ 趋势", "margin": 100.0, "gross_pnl": 5.0, "fee": 0.2, "net_pnl": 4.8,
         "exit_reason": "🎯 目标止盈达成", "snapshot_observability": "PRICE_ONLY",
         "entry_snapshot": {"price": 111000.0, "atr": 700.0}},
    ]

    def test_prompts_carry_host_constitution(self):
        s, u, ts, audit = sie.compose_evolution_prompts(
            copy.deepcopy(self.CLOSED), existing_memory_md="- 【宽止损抗噪】...", timestamp_str="T")
        for doc in (s, u):
            self.assertIn("宿主宪章·代码层硬约束", doc)
            self.assertIn("非模型推断", doc)
            self.assertIn("NO_CHANGE 永不覆盖或清空长期记忆", doc)
        self.assertIn("完全可观测 0", u)
        self.assertIn("无快照 1", u)
        self.assertIn("仅价格与普通观测 1", u)
        # 空壳 null 不得再进入 prompt；逐单标签必须显式可见
        self.assertNotIn('"velocity": null', u)
        self.assertIn('"snapshot_observability"', u)
        self.assertEqual(audit["total"], 2)


class EngineEndToEndTests(unittest.TestCase):
    """临时目录全链路：LLM 提案删除基准 → 宿主补回；NO_CHANGE → 权威库零变化。"""

    def _env(self, tmp):
        data = Path(tmp) / "data"
        data.mkdir(parents=True, exist_ok=True)
        # 从真实库复制 4 条基准心法（验证的是保护逻辑，不依赖条目内容是否真基准）
        lessons = [
            {"id": "l1", "category": "RISK", "rule_text": "【基准1】宽止损抗噪", "health_score": 95.0,
             "enabled": True, "created_at": "2026-09-01 00:00:00", "ttl_days": 14,
             "sample_size": 38, "is_baseline": True, "shield_status": "PASSED"},
            {"id": "l2", "category": "PORTFOLIO", "rule_text": "【基准2】禁止同向共振", "health_score": 92.0,
             "enabled": True, "created_at": "2026-09-04 12:00:00", "ttl_days": 14,
             "sample_size": 15, "is_baseline": True, "shield_status": "PASSED"},
        ]
        payload = {"schema_version": 1, "revision": "0" * 32, "lessons": lessons}
        mem_file = data / "structured_trading_memory.json"
        mem_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        inst = (sie.TARGET_INSTRUMENTS or ["ALGO"])[0]
        ledger = [
            {"id": f"t{i}", "inst": inst, "side": "多", "open_time": "2026-09-08 09:00:00",
             "close_time": f"2026-09-08 1{i}:00:00", "margin": 100.0, "gross_pnl": pnl,
             "fee": 0.2, "pnl": pnl, "status": "closed", "exit_reason": "🛑 触发云端止损",
             "strategy": "🌊 顺势做多"}
            for i, pnl in enumerate((-3.0, 5.0))
        ]
        (data / "trading_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
        return {
            "data_dir": str(data),
            "report": str(data / "self_improvement_report.json"),
            "memory_json": str(data / "ai_trading_memory.json"),
            "memory_md": str(data / "AI_TRADING_MEMORY.md"),
            "lock": str(data / ".self_improvement.lock"),
            "prompt_dump": str(data / "self_improvement_last_prompt.txt"),
            "asset_mults": str(data / "asset_multipliers.json"),
            "shield_mem": mem_file,
            "shield_md": data / "AI_TRADING_MEMORY.md",
        }

    def _run(self, change_status, ai_memory):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(tmp)

            def fake_review(closed_trades, existing_memory_md="", timestamp_str=""):
                return {"change_status": change_status,
                        "diagnosis_insights": ["同向多单同时止损，支持现有基准"],
                        "evolution_actions": [],
                        "ai_long_term_memory": ai_memory,
                        "memory_overwrites_reason": "test"}

            with patch.object(sie, "DATA_DIR", env["data_dir"]), \
                 patch.object(sie, "LEDGER_JSON_FILE", os.path.join(env["data_dir"], "trading_ledger.json")), \
                 patch.object(sie, "REPORT_JSON_FILE", env["report"]), \
                 patch.object(sie, "AI_MEMORY_FILE", env["memory_json"]), \
                 patch.object(sie, "AI_MEMORY_MD_FILE", env["memory_md"]), \
                 patch.object(sie, "EVOLUTION_LOCK_FILE", env["lock"]), \
                 patch.object(sie, "EVOLUTION_LAST_PROMPT_FILE", env["prompt_dump"]), \
                 patch.object(sie, "LOGS_DIR", os.path.join(env["data_dir"], "logs")), \
                 patch.object(sie, "LOG_FILE", os.path.join(env["data_dir"], "self_improvement_test.log")), \
                 patch.object(sie, "call_llm_evolution_review", fake_review), \
                 patch.object(shield, "STRUCTURED_MEMORY_FILE", env["shield_mem"]), \
                 patch.object(shield, "AI_MEMORY_MD_FILE", env["shield_md"]), \
                 patch.dict(sys.modules, {"qq_notifier": type(sys)("qq_notifier")}):
                sys.modules["qq_notifier"].notify_evolution_report = lambda *a, **k: None
                report = sie.run_self_evolution(force=True)
            final_mem = json.loads(env["shield_mem"].read_text(encoding="utf-8"))
            return report, [l["rule_text"] for l in final_mem["lessons"]], final_mem

    def test_no_change_preserves_all_baseline_memory(self):
        report, texts, _ = self._run("NO_CHANGE", ["【新】不该被采纳的孤证"])
        self.assertEqual(report["change_status"], "NO_CHANGE")
        self.assertTrue(report["memory_preserved"])
        self.assertEqual(texts, ["【基准1】宽止损抗噪", "【基准2】禁止同向共振"])
        self.assertEqual(report["snapshot_audit"]["total"], 2)
        self.assertEqual(report["snapshot_audit"]["math_observable"], 0)

    def test_add_proposal_cannot_delete_baselines(self):
        # 模型 ADD 时只给 1 条新心法、省略全部基准：旧行为会清空权威库，现在必须补回
        report, texts, final_mem = self._run("ADD", ["【新3】多标的共振需单边敞口熔断"])
        self.assertIn("【基准1】宽止损抗噪", texts)
        self.assertIn("【基准2】禁止同向共振", texts)
        self.assertIn("【新3】多标的共振需单边敞口熔断", texts)
        # ADD 为纯追加语义：基准在合并中天然保留，无需「强制补回」计数
        self.assertEqual(report["baseline_memory_protected"], 0)
        # 基准条目保留原始 id/metadata（身份未被重写）
        ids = {l["rule_text"]: l.get("id") for l in final_mem["lessons"]}
        self.assertEqual(ids["【基准1】宽止损抗噪"], "l1")

    def test_invalidate_attempt_on_baseline_is_readded(self):
        report, texts, _ = self._run("INVALIDATE", ["【基准1】宽止损抗噪"])  # 想删掉基准2
        self.assertIn("【基准2】禁止同向共振", texts)
        self.assertEqual(report["baseline_memory_protected"], 1)


if __name__ == "__main__":
    unittest.main()

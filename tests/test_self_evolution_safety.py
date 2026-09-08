"""Offline cycle regressions: synthetic inputs, temporary storage, no app runtime imports."""
import ast
import builtins
import multiprocessing
import socket
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest.mock import Mock, patch

from scripts import evolution_shield as shield
from path_guard import contained



class SelfEvolutionSafetyTests(unittest.TestCase):
    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # Block transport before loading the engine. No trader/runtime is imported.
        self.network = self.start_patch(patch("socket.socket", side_effect=AssertionError("network forbidden")))
        self.urlopen = self.start_patch(patch("urllib.request.urlopen", side_effect=AssertionError("HTTP forbidden")))
        dependencies = {}
        for name, attrs in {
            "r20_backend.config": {"settings": None},
            "instrument_pool": {"load_instruments": Mock(return_value=[{"name": "BTC"}])},
            "prompt_library": {"active_profile": Mock(), "apply_module_layout": Mock()},
            "r20_gateway.telemetry": {"ModelCallTelemetry": Mock()},
            "qq_notifier": {"notify_evolution_report": Mock()},
        }.items():
            module = ModuleType(name)
            module.__dict__.update(attrs)
            dependencies[name] = module
        self.start_patch(patch.dict(sys.modules, dependencies))
        source = Path(__file__).resolve().parents[1] / "scripts" / "self_improvement_engine.py"
        spec = importlib.util.spec_from_file_location("isolated_evolution_engine", source)
        self.engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.engine)
        for name in ("PROJECT_ROOT", "WORKSPACE_DIR", "DATA_DIR", "LOGS_DIR"):
            self.start_patch(patch.object(self.engine, name, str(self.root)))
        for name in ("LEDGER_JSON_FILE", "REPORT_JSON_FILE", "AI_DECISIONS_FILE", "AI_MEMORY_FILE",
                     "AI_MEMORY_MD_FILE", "EVOLUTION_LAST_PROMPT_FILE", "LOG_FILE", "EVOLUTION_LOCK_FILE",
                     "SIGNAL_JOURNAL_FILE"):
            self.start_patch(patch.object(self.engine, name, str(self.root / name)))
        for name in ("WORKSPACE_DIR", "DATA_DIR"):
            self.start_patch(patch.object(shield, name, self.root))
        for name in ("STRUCTURED_MEMORY_FILE", "AI_MEMORY_MD_FILE"):
            self.start_patch(patch.object(shield, name, self.root / ("shield_" + name)))
        self.start_patch(patch.object(self.engine, "log_msg"))
        self.llm = self.start_patch(patch.object(self.engine, "call_llm_evolution_review"))
        self.trades = self.start_patch(patch.object(self.engine, "load_closed_trades", return_value=[
            {"net_pnl": 2, "fee": 0.1}, {"net_pnl": -1, "fee": 0.1}]))
        # All subsequent file reads/writes must stay inside the temporary directory.
        def guarded_open(original):
            def checked(file, *args, **kwargs):
                if not isinstance(file, int):
                    self.assertTrue(contained(file, self.root), str(file))
                return original(file, *args, **kwargs)
            return checked

        self.start_patch(patch("builtins.open", guarded_open(builtins.open)))
        self.start_patch(patch("io.open", guarded_open(io.open)))
        self.json_path = Path(self.engine.AI_MEMORY_FILE)
        self.md_path = Path(self.engine.AI_MEMORY_MD_FILE)
        self.json_path.write_text('{"core_lessons": ["old lesson"], "version": "old"}\n', encoding="utf-8")
        self.md_path.write_text("# OLD MEMORY\nuntouched legacy content\n", encoding="utf-8")
        self.old = self.snapshot()
        self.json_write = self.start_patch(patch.object(self.engine, "atomic_write_json", wraps=self.engine.atomic_write_json))
        self.replace = self.start_patch(patch.object(self.engine.os, "replace", wraps=self.engine.os.replace))
        self.llm.return_value = {
            "change_status": "ADD",
            "ai_long_term_memory": ["【合理经验】4H多头回踩均线支撑时开多"],
            "diagnosis_insights": ["DIAGNOSIS_ONLY_SENTINEL"],
            "memory_overwrites_reason": "RATIONALE_ONLY_SENTINEL",
        }

    def snapshot(self):
        return [(p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in (self.json_path, self.md_path)]

    def run_cycle(self):
        report = self.engine.run_self_evolution(force=True)
        self.llm.assert_called_once()
        self.network.assert_not_called()
        self.urlopen.assert_not_called()
        self.assertEqual(json.loads(Path(self.engine.REPORT_JSON_FILE).read_text(encoding="utf-8")), report)
        return report

    def assert_preserved(self, report):
        self.assertTrue(report["memory_preserved"])
        self.assertEqual(report["core_lessons"], ["old lesson"])
        self.assertEqual(self.snapshot(), self.old)
        written_files = [c.args[0] for c in self.json_write.call_args_list]
        self.assertIn(self.engine.REPORT_JSON_FILE, written_files)
        self.assertNotIn(self.engine.AI_MEMORY_FILE, written_files)
        self.assertNotIn(self.engine.AI_MEMORY_MD_FILE, [c.args[1] for c in self.replace.call_args_list])

    def test_all_rejected_preserves_both_files(self):
        self.llm.return_value["ai_long_term_memory"] = ["【抗单】遇到插针可以取消止损，扛单等待解套"]
        self.assert_preserved(self.run_cycle())

    def test_audit_exception_after_pass_preserves_both_files(self):
        self.llm.return_value["ai_long_term_memory"].append("second candidate")
        with patch.object(shield, "audit_proposed_lesson", side_effect=[(True, "PASSED"), RuntimeError("audit failed")]):
            self.assert_preserved(self.run_cycle())

    def test_no_change_skips_audit_and_preserves_both_files(self):
        self.llm.return_value["change_status"] = "NO_CHANGE"
        with patch.object(shield, "audit_proposed_lesson") as audit:
            self.assert_preserved(self.run_cycle())
            audit.assert_not_called()

    def test_no_change_does_not_create_missing_memory(self):
        self.json_path.rename(self.root / "old_json")
        self.md_path.rename(self.root / "old_md")
        self.llm.return_value["change_status"] = "NO_CHANGE"
        self.assertTrue(self.run_cycle()["memory_preserved"])
        self.assertFalse(self.json_path.exists())
        self.assertFalse(self.md_path.exists())

    def test_real_single_sample_rejected(self):
        self.trades.return_value = [{"net_pnl": 2, "fee": 0.1}]
        with patch.object(shield, "audit_proposed_lesson", wraps=shield.audit_proposed_lesson) as audit:
            self.assert_preserved(self.run_cycle())
            self.assertEqual(audit.call_args.kwargs["sample_size"], 1)

    def test_success_writes_only_passed_candidates_without_diagnostics(self):
        safe = self.llm.return_value["ai_long_term_memory"][0]
        report = self.run_cycle()
        self.assertFalse(report["memory_preserved"])
        self.assertEqual(report["core_lessons"], [safe])
        self.assertEqual([i["rule_text"] for i in shield.load_structured_memory()], [safe])
        self.assertEqual(self.snapshot(), self.old)  # Legacy files never published again.
        markdown = shield.render_trading_memory(self.md_path, self.json_path)
        self.assertIn(safe, markdown)
        self.assertNotIn("取消止损", markdown)
        for marker in ("DIAGNOSIS_ONLY_SENTINEL", "RATIONALE_ONLY_SENTINEL"):
            self.assertNotIn(marker, markdown)
            self.assertIn(marker, json.dumps(report))
        self.assertEqual(report["diagnosis_insights"], ["DIAGNOSIS_ONLY_SENTINEL"])
        self.assertEqual(report["memory_overwrites_reason"], "RATIONALE_ONLY_SENTINEL")

    def test_mixed_pass_and_reject_is_all_or_nothing(self):
        self.llm.return_value["ai_long_term_memory"].append("【抗单】遇到插针可以取消止损，扛单等待解套")
        self.assert_preserved(self.run_cycle())
        self.assertFalse(shield.STRUCTURED_MEMORY_FILE.exists())

    def test_no_change_during_rollback_reports_current_authority(self):
        def review(*args, **kwargs):
            shield.rollback_to_baseline(expected_version=shield.read_memory_snapshot()["version"])
            return {"change_status": "NO_CHANGE"}
        self.llm.side_effect = review
        report = self.run_cycle()
        self.assertTrue(report["memory_preserved"])
        self.assertEqual(report["core_lessons"], [i["rule_text"] for i in shield.BASELINE_LESSONS])

    def test_llm_empty_fallback_preserves_both_files(self):
        self.llm.return_value = {}
        self.assert_preserved(self.run_cycle())

    def test_disabled_candidate_stays_disabled_after_review(self):
        safe = self.llm.return_value["ai_long_term_memory"][0]
        _, _, item = shield.add_safe_lesson(safe)
        shield.toggle_lesson(item["id"], expected_version=shield.read_memory_snapshot()["version"])
        before = shield.STRUCTURED_MEMORY_FILE.read_bytes()
        report = self.run_cycle()
        self.assertTrue(report["memory_preserved"])
        self.assertEqual(report["core_lessons"], [])
        self.assertEqual(shield.STRUCTURED_MEMORY_FILE.read_bytes(), before)
        self.assertEqual(shield.render_trading_memory(self.md_path), "")

    def test_concurrent_toggle_during_review_conflicts(self):
        _, _, item = shield.add_safe_lesson("【已有经验】4H多头回踩均线支撑时开多")
        result = dict(self.llm.return_value)
        def review(*args, **kwargs):
            shield.toggle_lesson(item["id"], expected_version=shield.read_memory_snapshot()["version"])
            return result
        self.llm.side_effect = review
        report = self.run_cycle()
        self.assertTrue(report["memory_preserved"])
        self.assertEqual(report["core_lessons"], [])
        self.assertEqual(len(shield.load_structured_memory()), 1)

    def test_rollback_then_no_change_preserves_authority(self):
        shield.rollback_to_baseline(expected_version=shield.read_memory_snapshot()["version"])
        before = shield.STRUCTURED_MEMORY_FILE.read_bytes()
        self.llm.return_value["change_status"] = "NO_CHANGE"
        report = self.run_cycle()
        self.assertTrue(report["memory_preserved"])
        self.assertEqual(shield.STRUCTURED_MEMORY_FILE.read_bytes(), before)
        self.assertEqual(report["core_lessons"], [i["rule_text"] for i in shield.BASELINE_LESSONS])

    def test_empty_authority_no_change_ignores_legacy(self):
        shield.STRUCTURED_MEMORY_FILE.write_text("[]", encoding="utf-8")
        self.llm.return_value["change_status"] = "NO_CHANGE"
        report = self.run_cycle()
        self.assertEqual(report["core_lessons"], [])
        self.assertEqual(self.llm.call_args.kwargs["existing_memory_md"], "")


class ClosedTradeEvidenceTests(unittest.TestCase):
    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.network = self.start_patch(patch("socket.socket", side_effect=AssertionError("network forbidden")))
        self.urlopen = self.start_patch(patch("urllib.request.urlopen", side_effect=AssertionError("HTTP forbidden")))
        dependencies = {}
        for name, attrs in {
            "r20_backend.config": {"settings": None},
            "instrument_pool": {"load_instruments": Mock(return_value=[{"name": "BTC"}])},
            "prompt_library": {"active_profile": Mock(), "apply_module_layout": Mock()},
            "r20_gateway.telemetry": {"ModelCallTelemetry": Mock()},
            "qq_notifier": {"notify_evolution_report": Mock()},
        }.items():
            module = ModuleType(name)
            module.__dict__.update(attrs)
            dependencies[name] = module
        self.start_patch(patch.dict(sys.modules, dependencies))
        source = Path(__file__).resolve().parents[1] / "scripts" / "self_improvement_engine.py"
        spec = importlib.util.spec_from_file_location("isolated_evolution_evidence", source)
        self.engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.engine)
        for name in ("PROJECT_ROOT", "WORKSPACE_DIR", "DATA_DIR", "LOGS_DIR"):
            self.start_patch(patch.object(self.engine, name, str(self.root)))
        self.ledger_path = self.root / "trading_ledger.json"
        self.journal_path = self.root / "signal_journal.json"
        self.start_patch(patch.object(self.engine, "LEDGER_JSON_FILE", str(self.ledger_path)))
        self.start_patch(patch.object(self.engine, "SIGNAL_JOURNAL_FILE", str(self.journal_path)))
        self.start_patch(patch.object(self.engine, "REPORT_JSON_FILE", str(self.root / "self_improvement_report.json")))
        self.start_patch(patch.object(self.engine, "LOG_FILE", str(self.root / "self_improvement.log")))
        self.start_patch(patch.object(self.engine, "EVOLUTION_LOCK_FILE", str(self.root / ".self_improvement.lock")))
        self.start_patch(patch.object(self.engine, "log_msg"))
        self.start_patch(patch.object(self.engine, "TARGET_INSTRUMENTS", ["BTC"]))

        def guarded_open(original):
            def checked(file, *args, **kwargs):
                if not isinstance(file, int):
                    self.assertTrue(contained(file, self.root), str(file))
                return original(file, *args, **kwargs)
            return checked

        self.start_patch(patch("builtins.open", guarded_open(builtins.open)))
        self.start_patch(patch("io.open", guarded_open(io.open)))

    def _write_json(self, path, payload):
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _closed_row(self, **overrides):
        row = {
            "id": "pos_hist_1_BTC_long_1",
            "inst": "BTC",
            "instId": "BTC-USDT-SWAP",
            "posSide": "long",
            "status": "closed",
            "open_time": "2023-11-15 06:13:20",
            "close_time": "2023-11-15 07:13:20",
            "gross_pnl": 10.0,
            "pnl": 10.0,
            "net_pnl": 9.6,
            "fee": -0.4,
            "strategy": "⚡ 趋势",
            "exit_reason": "止盈",
        }
        row.update(overrides)
        return row

    def _journal(self, **overrides):
        rec = {
            "name": "BTC",
            "instId": "BTC-USDT-SWAP",
            "posSide": "long",
            "order_id": "1001",
            "entryTime": "2023-11-16 00:00:00",
            "snapshot": {"velocity": 1.2, "atr": 10},
            "policy_version": "p1",
            "policy_hash": "h1",
            "strategy": "⚡ 趋势",
            "status": "submitted",
        }
        rec.update(overrides)
        return rec

    def test_incomplete_ledger_returns_no_closed_trades(self):
        self._write_json(self.ledger_path, [self._closed_row()])
        self._write_json(self.root / "ledger_sync_status.json", {"incomplete": True, "status": "unavailable"})
        self.assertEqual(self.engine.load_closed_trades(), [])

    def test_holding_and_non_closed_are_not_learning_evidence(self):
        self._write_json(self.ledger_path, [
            self._closed_row(status="holding", net_pnl=-99),
            self._closed_row(id="open-1", status="open", net_pnl=5),
            self._closed_row(id="missing-status", status=None, net_pnl=5),
            self._closed_row(id="ok", net_pnl=9.6),
        ])
        trades = self.engine.load_closed_trades()
        self.assertEqual([t["net_pnl"] for t in trades], [9.6])

    def test_evolution_uses_net_pnl_not_gross(self):
        self._write_json(self.ledger_path, [self._closed_row(gross_pnl=10.0, pnl=10.0, net_pnl=9.6)])
        trades = self.engine.load_closed_trades()
        self.assertEqual(trades[0]["net_pnl"], 9.6)
        self.assertNotEqual(trades[0]["net_pnl"], trades[0]["gross_pnl"])

    def test_future_or_name_time_journal_is_not_entry_evidence(self):
        self._write_json(self.ledger_path, [self._closed_row()])
        self._write_json(self.journal_path, [self._journal()])
        trades = self.engine.load_closed_trades()
        self.assertEqual(len(trades), 1)
        self.assertIsNone(trades[0]["entry_snapshot"])

    def test_unfilled_submitted_journal_is_not_a_fill(self):
        self._write_json(self.ledger_path, [self._closed_row()])
        self._write_json(self.journal_path, [self._journal(entryTime="2023-11-15 06:13:20")])
        trades = self.engine.load_closed_trades()
        self.assertIsNone(trades[0]["entry_snapshot"])

    def test_wrong_pos_side_or_inst_is_not_evidence(self):
        self._write_json(self.ledger_path, [self._closed_row(entryOrderIds=["1001"])])
        self._write_json(self.journal_path, [
            self._journal(posSide="short"),
            self._journal(order_id="1001", instId="ETH-USDT-SWAP", snapshot={"velocity": 9}),
        ])
        trades = self.engine.load_closed_trades()
        self.assertIsNone(trades[0]["entry_snapshot"])

    def test_first_entry_order_id_joins_account_journal(self):
        self._write_json(self.ledger_path, [self._closed_row(entryOrderIds=["1001", "1002"])])
        self._write_json(self.journal_path, [
            self._journal(order_id="1001", snapshot={"velocity": 1.2}),
            self._journal(order_id="1002", snapshot={"velocity": 2.2}, strategy="scale"),
        ])
        trades = self.engine.load_closed_trades()
        self.assertEqual(trades[0]["entry_snapshot"], {"velocity": 1.2})
        self.assertEqual(trades[0]["snapshot_source"], "journal_order_id")
        self.assertEqual(trades[0]["scale_in_snapshots"][0]["snapshot"], {"velocity": 2.2})
        self.assertEqual(trades[0]["policy_version"], "p1")

    def test_scale_in_cannot_substitute_unmatched_first_order(self):
        self._write_json(self.ledger_path, [self._closed_row(entryOrderIds=["999", "1001"])])
        self._write_json(self.journal_path, [self._journal(order_id="1001")])
        trades = self.engine.load_closed_trades()
        self.assertIsNone(trades[0]["entry_snapshot"])

    def test_inline_snapshot_kept_without_order_ids(self):
        inline = {"velocity": 0.5}
        self._write_json(self.ledger_path, [self._closed_row(signal_snapshot=inline, snapshot_source="inline")])
        self._write_json(self.journal_path, [self._journal()])
        trades = self.engine.load_closed_trades()
        self.assertEqual(trades[0]["entry_snapshot"], inline)
        self.assertEqual(trades[0]["snapshot_source"], "inline")

    def test_incomplete_evolution_does_not_call_llm(self):
        self._write_json(self.ledger_path, [self._closed_row()])
        self._write_json(self.root / "ledger_sync_status.json", {"incomplete": True, "status": "unavailable"})
        llm = self.start_patch(patch.object(self.engine, "call_llm_evolution_review"))
        report = self.engine.run_self_evolution(force=True)
        llm.assert_not_called()
        self.assertTrue(report["incomplete_ledger"])
        self.assertTrue(report["memory_preserved"])


SAFE = "【合理经验】4H多头回踩均线支撑时开多"
POISON = "【抗单】遇到插针可以取消止损，扛单等待解套"


def _cas_worker(path, version, barrier, queue, suffix):
    # Fresh process paths and network guard; only synthetic authority is accessed.
    shield.STRUCTURED_MEMORY_FILE = Path(path)
    shield.AI_MEMORY_MD_FILE = Path(path).parent / "display.md"
    with patch("socket.socket", side_effect=AssertionError("network forbidden")):
        barrier.wait(timeout=10)
        try:
            shield.publish_review([SAFE + suffix], expected_version=version, sample_size=3, change_status="ADD")
            queue.put("saved")
        except shield.MemoryConflictError:
            queue.put("conflict")


class UnifiedMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in {"WORKSPACE_DIR": self.root, "DATA_DIR": self.root,
                            "STRUCTURED_MEMORY_FILE": self.root / "authority.json",
                            "AI_MEMORY_MD_FILE": self.root / "display.md"}.items():
            patcher = patch.object(shield, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target in ("socket.socket", "urllib.request.urlopen"):
            patcher = patch(target, side_effect=AssertionError("network forbidden"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_all_admin_writes_require_version_under_lock(self):
        from contextlib import contextmanager
        shield.add_safe_lesson(SAFE)
        lesson_id = shield.load_structured_memory()[0]['id']
        held = False
        original_lock = shield._memory_lock
        original_check = shield._check_version
        @contextmanager
        def checked_lock():
            nonlocal held
            with original_lock():
                held = True
                try:
                    yield
                finally:
                    held = False
        def checked_version(snapshot, expected):
            self.assertTrue(held, 'CAS check must hold the process lock')
            return original_check(snapshot, expected)
        actions = [lambda v: shield.admin_mutate('add', texts=[SAFE], expected_version=v),
                   lambda v: shield.admin_mutate('replace', texts=[], expected_version=v),
                   lambda v: shield.admin_mutate('delete', lesson_id=lesson_id, expected_version=v),
                   lambda v: shield.toggle_lesson(lesson_id, expected_version=v),
                   lambda v: shield.rollback_to_baseline(expected_version=v)]
        before = shield.STRUCTURED_MEMORY_FILE.read_bytes()
        with patch.object(shield, '_memory_lock', checked_lock), patch.object(shield, '_check_version', side_effect=checked_version) as check:
            for action in actions:
                for version, error in [(None, shield.MemoryVersionRequiredError), ('', shield.MemoryVersionRequiredError), ('stale', shield.MemoryConflictError)]:
                    with self.subTest(action=action, version=version), self.assertRaises(error):
                        action(version)
                    self.assertEqual(shield.STRUCTURED_MEMORY_FILE.read_bytes(), before)
            self.assertEqual(check.call_count, 15)

    def test_missing_and_empty_reads_are_pure(self):
        self.assertEqual(shield.load_structured_memory(), [])
        self.assertEqual(list(self.root.iterdir()), [])
        shield.STRUCTURED_MEMORY_FILE.write_text("[]", encoding="utf-8")
        shield.AI_MEMORY_MD_FILE.write_text("- OLD LEGACY", encoding="utf-8")
        before = shield.STRUCTURED_MEMORY_FILE.stat()
        self.assertEqual(shield.render_trading_memory(), "")
        self.assertEqual(shield.admin_memory_view()["items"], [])
        self.assertEqual(shield.STRUCTURED_MEMORY_FILE.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(len(list(self.root.iterdir())), 2)

    def test_corrupt_reads_and_mutations_preserve_file(self):
        for text in ("{", "{}", '[{"id":"x"}]', "null"):
            with self.subTest(text=text):
                shield.STRUCTURED_MEMORY_FILE.write_text(text, encoding="utf-8")
                before = shield.STRUCTURED_MEMORY_FILE.stat().st_mtime_ns
                for action in (shield.load_structured_memory, shield.render_trading_memory,
                               shield.rollback_to_baseline):
                    with self.assertRaises(shield.MemoryCorruptError):
                        action()
                self.assertEqual(shield.STRUCTURED_MEMORY_FILE.read_text(encoding="utf-8"), text)
                self.assertEqual(shield.STRUCTURED_MEMORY_FILE.stat().st_mtime_ns, before)

    def test_atomic_replace_failure_preserves_authority(self):
        shield.add_safe_lesson(SAFE)
        before = shield.STRUCTURED_MEMORY_FILE.read_bytes()
        version = shield.read_memory_snapshot()["version"]
        with patch.object(shield.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                shield.publish_review([SAFE + "等待确认"], expected_version=version, sample_size=3, change_status="REVISE")
        self.assertEqual(shield.STRUCTURED_MEMORY_FILE.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".memory-*.tmp")), [])

    def test_atomic_fsync_failure_preserves_authority(self):
        shield.add_safe_lesson(SAFE)
        before = shield.STRUCTURED_MEMORY_FILE.read_bytes()
        with patch.object(shield.os, "fsync", side_effect=OSError("flush failed")):
            with self.assertRaises(OSError):
                shield.toggle_lesson(shield.load_structured_memory()[0]["id"], expected_version=shield.read_memory_snapshot()["version"])
        self.assertEqual(shield.STRUCTURED_MEMORY_FILE.read_bytes(), before)

    def test_cross_process_cas_has_one_winner(self):
        shield.add_safe_lesson(SAFE)
        version = shield.read_memory_snapshot()["version"]
        # The worker imports only the shield and explicitly binds temporary paths.
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        queue = ctx.Queue()
        children = [ctx.Process(target=_cas_worker, args=(str(shield.STRUCTURED_MEMORY_FILE), version, barrier, queue, str(n))) for n in range(2)]
        for child in children:
            child.start()
        for child in children:
            child.join(15)
            if child.is_alive():
                child.terminate()
                child.join()
                self.fail("CAS worker timed out")
            self.assertEqual(child.exitcode, 0)
        self.assertEqual(sorted([queue.get(timeout=2), queue.get(timeout=2)]), ["conflict", "saved"])
        queue.close()
        queue.join_thread()
        self.assertEqual(len(shield.load_structured_memory()), 1)

    def test_rollback_revision_rejects_old_snapshot_even_same_content(self):
        shield.rollback_to_baseline(expected_version=shield.read_memory_snapshot()["version"])
        version = shield.read_memory_snapshot()["version"]
        shield.rollback_to_baseline(expected_version=shield.read_memory_snapshot()["version"])
        with self.assertRaises(shield.MemoryConflictError):
            shield.publish_review([SAFE], expected_version=version, sample_size=3, change_status="ADD")

    def test_backend_handlers_audit_crud_without_app_import(self):
        # Compile only reviewed endpoint functions: no app startup/auth/config reads.
        source = Path(__file__).resolve().parents[1] / "r20_backend" / "app.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        names = {"_memory_service_call", "get_admin_memory", "add_admin_memory_item",
                 "delete_admin_memory_item", "update_admin_memory_all",
                 "toggle_admin_memory_lesson", "rollback_admin_memory_lessons"}
        nodes = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                node.decorator_list = []
                nodes.append(node)
        class HTTPError(Exception):
            def __init__(self, status_code, detail):
                self.status_code = status_code
                super().__init__(detail)
        scope = {"Header": lambda **kw: None, "Any": object, "MemoryItemRequest": object,
                 "MemoryUpdateAllRequest": object, "HTTPException": HTTPError,
                 "refresh_settings": Mock(), "require_admin_header": Mock(return_value={}),
                 "audit_record": Mock()}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
        from types import SimpleNamespace
        shield.STRUCTURED_MEMORY_FILE.write_text("[]", encoding="utf-8")
        self.assertEqual(scope["get_admin_memory"]()["structured_lessons"], [])
        for name, payload in (("add_admin_memory_item", SimpleNamespace(text=POISON, expected_version=shield.read_memory_snapshot()["version"])),
                              ("update_admin_memory_all", SimpleNamespace(items=[SAFE, POISON], expected_version=shield.read_memory_snapshot()["version"]))):
            before = shield.STRUCTURED_MEMORY_FILE.read_bytes()
            with self.assertRaises(HTTPError) as cm:
                scope[name](payload)
            self.assertEqual(cm.exception.status_code, 422)
            self.assertEqual(shield.STRUCTURED_MEMORY_FILE.read_bytes(), before)
        scope["add_admin_memory_item"](SimpleNamespace(text=SAFE, expected_version=shield.read_memory_snapshot()["version"]))
        item = shield.load_structured_memory()[0]
        scope["toggle_admin_memory_lesson"](item["id"], expected_version=shield.read_memory_snapshot()["version"])
        scope["update_admin_memory_all"](SimpleNamespace(items=[SAFE], expected_version=shield.read_memory_snapshot()["version"]))
        self.assertFalse(shield.load_structured_memory()[0]["enabled"])
        scope["toggle_admin_memory_lesson"](item["id"], expected_version=shield.read_memory_snapshot()["version"])
        self.assertEqual(scope["delete_admin_memory_item"](0, lesson_id=item["id"], expected_version=shield.read_memory_snapshot()["version"])["items"], [])
        scope["rollback_admin_memory_lessons"](expected_version=shield.read_memory_snapshot()["version"])
        self.assertEqual(len(scope["get_admin_memory"]()["structured_lessons"]), len(shield.BASELINE_LESSONS))
        scope["require_admin_header"].assert_called()

    def test_delete_id_does_not_use_active_list_index(self):
        _, _, first = shield.add_safe_lesson(SAFE)
        _, _, second = shield.add_safe_lesson(SAFE + "等待量能确认")
        shield.toggle_lesson(first["id"], expected_version=shield.read_memory_snapshot()["version"])
        shield.admin_mutate("delete", index=0, lesson_id=first["id"], expected_version=shield.read_memory_snapshot()["version"])
        self.assertEqual(shield.admin_memory_view()["items"], [second["rule_text"]])

    def test_empty_bulk_update_is_valid_and_does_not_read_legacy(self):
        shield.add_safe_lesson(SAFE)
        shield.AI_MEMORY_MD_FILE.write_text("- old legacy", encoding="utf-8")
        shield.admin_mutate("replace", texts=[], expected_version=shield.read_memory_snapshot()["version"])
        self.assertEqual(shield.load_structured_memory(), [])
        self.assertEqual(shield.render_trading_memory(), "")

    def test_read_render_entrypoints_without_runtime_imports(self):
        root = Path(__file__).resolve().parents[1]
        trader = ast.parse((root / "scripts" / "ai_brain_trader.py").read_text(encoding="utf-8"))
        # Execute only the actual consumer import and assignment, not production functions.
        nodes = [node for node in ast.walk(trader) if
                 (isinstance(node, ast.ImportFrom) and node.module == "scripts.evolution_shield") or
                 (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "memory_lessons" for t in node.targets))]
        self.assertEqual(len(nodes), 2)
        shield.STRUCTURED_MEMORY_FILE.write_text("[]", encoding="utf-8")
        shield.AI_MEMORY_MD_FILE.write_text("- stale legacy", encoding="utf-8")
        scope = {"AI_MEMORY_MD_FILE": shield.AI_MEMORY_MD_FILE, "AI_MEMORY_FILE": self.root / "legacy.json"}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "isolated_trader_memory", "exec"), scope)
        self.assertEqual(scope["memory_lessons"], "")
        shield.STRUCTURED_MEMORY_FILE.write_text("{", encoding="utf-8")
        with self.assertRaises(shield.MemoryCorruptError):
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "isolated_trader_memory", "exec"), scope)

    def test_legacy_backend_is_read_only_without_initialization(self):
        shield.AI_MEMORY_MD_FILE.write_text("- legacy lesson", encoding="utf-8")
        self.assertTrue(shield.admin_memory_view()["legacy_read_only"])
        with self.assertRaises(shield.MemoryConflictError):
            shield.admin_mutate("add", texts=[SAFE], expected_version=shield.read_memory_snapshot()["version"])
        self.assertFalse(shield.STRUCTURED_MEMORY_FILE.exists())


if __name__ == "__main__":
    unittest.main()

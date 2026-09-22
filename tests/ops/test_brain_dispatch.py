"""主脑批次的 LLM 派发与决策落盘（brain/dispatch.py）收口 —— 第 312 刀。

本模块是 `execute_batch_ai_brain_cycle` 的**尾块**（173 行 `try`，纯搬家）：
LLM 请求 → 决策归一 → 缓存/历史/持仓指令三份落盘 → 周期健康记录。
它的全部 37 个自由名都是 kw-only 入参（门面调用期解析），所以本刀全部走注入，
不碰任何真实网络或磁盘。

## 本刀最重要的产出：一个**真实生产缺陷**

详见 `CouncilSuccessPathBugTests` —— 投委会**开启且辩论成功**时，`content` 变量从未被赋值，
而第 230 行要用 `len(content)`，于是 `UnboundLocalError` 被外层 `except` 吞掉：

  ⇒ 决策**确实写盘了**，但函数**返回 `None`**、周期健康被记成 **`failed`**、成功日志不打印。

按本战役纪律：**只记录、不擅自修**（改它会改实盘行为）。已用 `assertRaises` 的反面钉法
把现状锁住，避免后人以为这条路径"是好的"。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import r20_backend.council_manager as council_manager  # noqa: E402
import r20_backend.file_locks as file_locks  # noqa: E402
from scripts.brain import dispatch  # noqa: E402


class _Telemetry:
    def __init__(self):
        self.calls: list = []

    def finish(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class _Resp:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@contextmanager
def _no_lock(path):
    yield


class _Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paths = {"cache": str(self.root / "cache.json"),
                      "history": str(self.root / "history.json"),
                      "pos": str(self.root / "pos_mgmt.json")}
        self.written: dict = {}
        self.health: list = []
        self.cancels: list = []
        self.normalized: list = []
        self.locked: list = []
        self.telemetry = _Telemetry()
        self.brain_output = {"decisions": {}, "macro_assessment": "宏观中性震荡"}
        self.council_cfg = {"enabled": False}
        self.council_result = None
        self.council_error = None
        self.debate_fn = None
        self.llm_result = ("{}", None, {"total_tokens": 7}, None)

    # ---- 注入面 -----------------------------------------------------------
    def _atomic_write(self, path, payload):
        self.written[str(path)] = payload

    def _normalize(self, lst, active_inst_ids, safe_float=None):
        self.normalized.append((lst, active_inst_ids))
        return list(lst)

    def _health(self, *args):
        self.health.append(args)

    def _cancel(self, lst):
        self.cancels.append(lst)

    def _lock(self, path):
        self.locked.append(str(path))
        return _no_lock(path)

    def _debate(self, **kwargs):
        # 用例可通过 `debate_fn=` 注入自己的替身（**不能**在外面再 patch ——
        # `_patch_council` 会盖掉它，`patch.start()` 后启动的赢）
        if self.debate_fn is not None:
            return self.debate_fn(**kwargs)
        if self.council_error is not None:
            raise self.council_error
        return self.council_result

    def _llm(self, **kwargs):
        self.llm_kwargs = kwargs
        return self.llm_result

    def _patch_council(self):
        stack = [
            patch.object(council_manager, "load_council_config", lambda: dict(self.council_cfg)),
            patch.object(council_manager, "execute_council_debate", self._debate),
            patch.object(file_locks, "file_lock", self._lock),
        ]
        for p in stack:
            p.start()
            self.addCleanup(p.stop)

    _HARNESS_FIELDS = ("brain_output", "council_cfg", "council_result", "council_error",
                       "llm_result", "debate_fn")

    def _run(self, **over):
        # 先摘掉 harness 级字段（它们不是被测算函数的参数）——
        # 直接混进去会 `TypeError: unexpected keyword argument`
        for field in self._HARNESS_FIELDS:
            if field in over:
                setattr(self, field, over.pop(field))
        self._patch_council()
        kw = {
            "AI_DECISION_CACHE_FILE": self.paths["cache"],
            "AI_DECISION_HISTORY_FILE": self.paths["history"],
            "AI_POSITION_MANAGEMENT_FILE": self.paths["pos"],
            "Any": object, "Dict": dict,
            "_build_effective_prompt_text": lambda **k: "FULL_PROMPT",
            "_build_history_record": lambda **k: {"history": True, "kw": k},
            "_normalize_position_management": self._normalize,
            "_record_cycle_health": self._health,
            "active_inst_ids": {"BTC-USDT-SWAP"}, "active_position_sides": {},
            "api_format": "openai", "api_key": "SECRET",
            "assemble_decision_cache": lambda **k: {"assembled": True, "kw": k},
            "atomic_write_json": self._atomic_write,
            "base_url": "https://api.example.com", "effective_system_prompt": "SYS",
            "effort": "high",
            "execute_brain_pending_cancels": self._cancel,
            "execute_llm_request": self._llm,
            "json": json, "model_name": "m1", "os": os, "packages": [],
            "policy_hash": "H", "policy_snapshot": {}, "policy_summary": "PS",
            "policy_version": "V", "prompt": "USER_PROMPT", "runtime_context": {},
            "safe_float": lambda v: float(v or 0), "telemetry": self.telemetry,
            "thinking_timeout": 30.0, "time": time, "time_str": "T",
            "urllib": urllib,
        }
        if "llm_result" not in over and self.brain_output is not None:
            self.llm_result = (json.dumps(self.brain_output), None,
                               {"total_tokens": 7}, None)
        kw.update(over)
        self.kw = kw
        return dispatch.dispatch_llm_and_persist_decisions(**kw)


class CouncilDisabledTests(_Harness, unittest.TestCase):
    def test_default_status_says_the_backend_switch_is_off(self):
        out = self._run()
        self.assertTrue(out["assembled"])
        self.assertFalse(out["kw"]["council_status"]["ran"])

    def test_council_off_records_an_explicit_reason(self):
        captured = {}

        def assemble(**kw):
            captured.update(kw)
            return {"ok": True}
        self._run(assemble_decision_cache=assemble)
        status = captured["council_status"]
        self.assertFalse(status["ran"])
        self.assertIn("未启用", status["reason"])
        self.assertIn("投委会开关关闭", status["reason"])

    def test_council_off_never_calls_the_debate(self):
        calls = []
        self._run(debate_fn=lambda **k: calls.append(k))
        self.assertEqual(calls, [])

    def test_unreachable_council_module_degrades_to_off(self):
        with patch.dict(sys.modules, {"r20_backend.council_manager": None}):
            out = self._run()
        self.assertEqual(out["kw"]["council_status"]["ran"], False)


class CouncilEnabledTests(_Harness, unittest.TestCase):
    def test_successful_debate_reports_transparency_metrics(self):
        self.council_cfg = {"enabled": True, "timeout_seconds": 120}
        self.council_result = ({"decisions": {}, "macro_assessment": "M"},
                               {"total_duration_ms": 1234, "consensus_mode": "majority",
                                "advisors": {"a": {"status": "ok"}, "b": {"status": "ok"},
                                             "c": {"status": "error"}}})
        captured = {}

        def assemble(**kw):
            captured.update(kw)
            return {"ok": True}
        self._run(assemble_decision_cache=assemble)
        status = captured["council_status"]
        self.assertTrue(status["ran"])
        self.assertEqual(status["duration_ms"], 1234)
        self.assertEqual(status["consensus_mode"], "majority")
        # 参谋计数：只有 status != "error" 的 dict 才算有效
        self.assertEqual(status["advisors_ok"], 2)
        self.assertEqual(status["advisors_total"], 3)

    def test_debate_timeout_is_taken_from_the_config(self):
        self.council_cfg = {"enabled": True, "timeout_seconds": 77}
        self.council_result = ({"decisions": {}}, {"total_duration_ms": 1})
        seen = {}

        def debate(**kw):
            seen.update(kw)
            return self.council_result
        self._run(debate_fn=debate)
        self.assertEqual(seen["timeout"], 77.0)

    def test_missing_timeout_falls_back_to_240_seconds(self):
        self.council_cfg = {"enabled": True}
        self.council_result = ({"decisions": {}}, {"total_duration_ms": 1})
        seen = {}

        def debate(**kw):
            seen.update(kw)
            return self.council_result
        self._run(debate_fn=debate)
        self.assertEqual(seen["timeout"], 240.0)

    def test_runtime_context_and_prompts_are_forwarded_to_the_desk_prompts(self):
        # 审计 P1-4d：席位提示词的占位符此前从不渲染 ⇒ 必须把运行上下文交进去
        self.council_cfg = {"enabled": True}
        self.council_result = ({"decisions": {}}, {"total_duration_ms": 1})
        seen = {}

        def debate(**kw):
            seen.update(kw)
            return self.council_result
        self._run(debate_fn=debate, runtime_context={"account_balance": "100 USDT"})
        self.assertEqual(seen["market_prompt"], "USER_PROMPT")
        self.assertEqual(seen["original_system_prompt"], "SYS")
        self.assertEqual(seen["runtime_context"], {"account_balance": "100 USDT"})

    def test_failed_debate_degrades_to_single_model_with_the_reason_recorded(self):
        self.council_cfg = {"enabled": True}
        self.council_error = RuntimeError("仲裁超时")
        captured = {}

        def assemble(**kw):
            captured.update(kw)
            return {"ok": True}
        # 降级后走单模型路径 ⇒ 必须让 execute_llm_request 可用
        self._run(assemble_decision_cache=assemble)
        status = captured["council_status"]
        self.assertFalse(status["ran"])
        self.assertIn("RuntimeError", status["reason"])
        self.assertIn("仲裁超时", status["reason"])

    def test_failed_debate_reason_is_truncated_to_300_chars(self):
        self.council_cfg = {"enabled": True}
        self.council_error = RuntimeError("x" * 500)
        captured = {}

        def assemble(**kw):
            captured.update(kw)
            return {"ok": True}
        self._run(assemble_decision_cache=assemble)
        reason = captured["council_status"]["reason"]
        self.assertLessEqual(len(reason) - len("RuntimeError: "), 300)

    def test_debate_returning_none_falls_back_to_single_model(self):
        self.council_cfg = {"enabled": True}
        self.council_result = (None, {"total_duration_ms": 1})
        seen = []
        self._run(execute_llm_request=lambda **k: seen.append(k) or self.llm_result)
        self.assertEqual(len(seen), 1, "brain_output 为 None ⇒ 必须走单模型")


class LlmRequestTests(_Harness, unittest.TestCase):
    def test_injected_request_helper_is_preferred(self):
        called = []

        def llm(**kw):
            called.append(kw)
            return ('{"decisions": {}}', None, {"total_tokens": 5}, None)
        out = self._run(execute_llm_request=llm)
        self.assertEqual(len(called), 1)
        self.assertEqual(called[0]["model"], "m1")
        self.assertEqual(called[0]["api_format"], "openai")
        self.assertEqual(called[0]["reasoning_effort"], "high")
        self.assertEqual(called[0]["response_format"], {"type": "json_object"})
        self.assertEqual([m["role"] for m in called[0]["messages"]], ["system", "user"])

    def test_raw_res_carries_the_usage_dict_when_one_was_returned(self):
        self._run()
        self.assertEqual(self.telemetry.calls[0][0], ("success", {"usage": {"total_tokens": 7}}))

    def test_non_dict_usage_leaves_raw_res_without_a_usage_key(self):
        self._run(execute_llm_request=lambda **k: ('{"decisions": {}}', None, None, None))
        self.assertEqual(self.telemetry.calls[0][0], ("success", {}))
        self.assertEqual(self.telemetry.calls[0][1]["output_chars"],
                         len('{"decisions": {}}'))

    def test_urllib_path_is_used_when_no_request_helper_is_available(self):
        seen = {}

        def opener(req, timeout=None):
            seen["url"] = req.full_url
            seen["headers"] = dict(req.headers)
            seen["data"] = json.loads(req.data.decode("utf-8"))
            seen["timeout"] = timeout
            return _Resp({"choices": [{"message": {"content": "  {\"decisions\": {}}  "}}]})

        with patch.object(urllib.request, "urlopen", opener):
            out = self._run(execute_llm_request=None)
        self.assertEqual(seen["url"], "https://api.example.com/chat/completions")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer SECRET")
        self.assertEqual(seen["timeout"], 30.0)
        self.assertIn("assembled", out)

    def test_reasoning_effort_is_omitted_for_none_and_auto(self):
        for effort in ("none", "auto"):
            with self.subTest(effort=effort):
                seen = {}

                def opener(req, timeout=None):
                    seen["data"] = json.loads(req.data.decode("utf-8"))
                    return _Resp({"choices": [{"message": {"content": "{}"}}]})
                with patch.object(urllib.request, "urlopen", opener):
                    self._run(execute_llm_request=None, effort=effort)
                self.assertNotIn("reasoning_effort", seen["data"])

    def test_reasoning_effort_is_included_for_a_real_effort(self):
        seen = {}

        def opener(req, timeout=None):
            seen["data"] = json.loads(req.data.decode("utf-8"))
            return _Resp({"choices": [{"message": {"content": "{}"}}]})
        with patch.object(urllib.request, "urlopen", opener):
            self._run(execute_llm_request=None, effort="medium")
        self.assertEqual(seen["data"]["reasoning_effort"], "medium")

    def test_markdown_fences_are_stripped(self):
        for wrapped in ('```json\n{"decisions": {}}\n```',
                        '```{"decisions": {}}```',
                        '{"decisions": {}}'):
            with self.subTest(wrapped=wrapped):
                out = self._run(execute_llm_request=lambda **k: (
                    wrapped, None, {"total_tokens": 1}, None))
                self.assertEqual(out["kw"]["decisions_dict"], {})

    def test_non_object_root_is_rejected_and_becomes_a_failed_cycle(self):
        # `raise ValueError("LLM response root must be an object")` ⇒ 外层吞掉 ⇒ 返回 None
        self.assertIsNone(self._run(execute_llm_request=lambda **k: (
            "[1, 2, 3]", None, {"total_tokens": 1}, None)))
        self.assertEqual(self.telemetry.calls[0][0], ("failed",))

    def test_decision_shapes_are_coerced(self):
        captured = {}

        def assemble(**kw):
            captured.update(kw)
            return {"ok": True}
        self._run(brain_output={"decisions": ["bad"], "position_management": "bad",
                                "macro_assessment": "M"},
                  assemble_decision_cache=assemble)
        self.assertEqual(captured["decisions_dict"], {})
        self.assertEqual(self.normalized[0][0], [])

    def test_macro_summary_is_truncated_to_120(self):
        captured = {}

        def assemble(**kw):
            captured.update(kw)
            return {"ok": True}
        self._run(brain_output={"decisions": {}, "macro_assessment": "长" * 400},
                  assemble_decision_cache=assemble)
        self.assertEqual(len(captured["macro_summary"]), 120)

    def test_default_macro_summary_is_used_when_absent(self):
        captured = {}

        def assemble(**kw):
            captured.update(kw)
            return {"ok": True}
        self._run(brain_output={"decisions": {}}, assemble_decision_cache=assemble)
        self.assertEqual(captured["macro_summary"], "宏观中性震荡")

    def test_pending_cancels_run_only_for_a_list(self):
        self._run(brain_output={"decisions": {}, "pending_orders_management": [{"a": 1}]})
        self.assertEqual(self.cancels, [[{"a": 1}]])
        self.cancels.clear()
        self._run(brain_output={"decisions": {}, "pending_orders_management": "junk"})
        self.assertEqual(self.cancels, [])


class PersistenceTests(_Harness, unittest.TestCase):
    def test_cache_write_holds_the_cross_process_lock(self):
        # 审计③(2026-09-13)：整档覆盖必须与 trader 的读-改-写互斥
        self._run()
        self.assertEqual(self.locked, [self.paths["cache"]])
        self.assertIn(self.paths["cache"], self.written)

    def test_position_management_payload_shape(self):
        self._run()
        payload = self.written[self.paths["pos"]]
        self.assertEqual(payload["time_str"], "T")
        self.assertEqual(payload["policy_version"], "V")
        self.assertEqual(payload["policy_hash"], "H")
        self.assertEqual(payload["instructions"], [])
        self.assertIsInstance(payload["timestamp"], int)

    def test_history_entry_is_prepended_and_written(self):
        self._run()
        history = self.written[self.paths["history"]]
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]["history"])

    def test_history_is_capped_at_fifty_rounds(self):
        existing = [{"old": i} for i in range(80)]
        Path(self.paths["history"]).write_text(json.dumps(existing),
                                                             encoding="utf-8")
        self._run()
        history = self.written[self.paths["history"]]
        self.assertEqual(len(history), 50)
        self.assertTrue(history[0]["history"], "新记录必须在最前")

    def test_corrupt_history_file_is_treated_as_empty(self):
        Path(self.paths["history"]).write_text("{ not json", encoding="utf-8")
        self._run()
        self.assertEqual(len(self.written[self.paths["history"]]), 1)

    def test_full_prompt_text_is_built_from_the_injected_helper(self):
        seen = {}
        self._run(_build_effective_prompt_text=lambda **k: seen.update(k) or "FULL")
        self.assertEqual(seen["effective_system_prompt"], "SYS")
        self.assertEqual(seen["prompt"], "USER_PROMPT")
        self.assertEqual(seen["policy_version"], "V")

    def test_history_record_receives_the_cache_and_packages(self):
        seen = {}
        self._run(_build_history_record=lambda **k: seen.update(k) or {"h": 1})
        self.assertIn("standard_cache", seen)
        self.assertIn("packages", seen)
        self.assertIn("council_status", seen)


class HealthAndReturnTests(_Harness, unittest.TestCase):
    def test_success_marks_health_ok_and_returns_the_cache(self):
        out = self._run()
        self.assertIn("assembled", out)
        self.assertEqual(self.health, [("ok",)])
        self.assertEqual(self.telemetry.calls[0][0],
                         ("success", {"usage": {"total_tokens": 7}}))
        self.assertIn("output_chars", self.telemetry.calls[0][1])

    def test_failure_marks_health_failed_with_the_message(self):
        def boom(**kw):
            raise RuntimeError("上游 500")
        out = self._run(execute_llm_request=boom)
        self.assertIsNone(out)
        self.assertEqual(self.health, [("failed", "上游 500")])
        self.assertEqual(self.telemetry.calls[0][0], ("failed",))
        self.assertIsInstance(self.telemetry.calls[0][1]["error"], RuntimeError)

    def test_any_exception_is_swallowed_into_a_none_return(self):
        # 该函数**从不抛**给调用方 —— 主脑批次失败不许把整轮交易循环打挂
        def boom(**kw):
            raise ValueError("whatever")
        self.assertIsNone(self._run(assemble_decision_cache=boom))

    def test_write_failure_also_returns_none(self):
        def boom(path, payload):
            raise OSError("disk full")
        self.assertIsNone(self._run(atomic_write_json=boom))
        self.assertEqual(self.health[0][0], "failed")


class CouncilSuccessPathBugTests(_Harness, unittest.TestCase):
    """★★ 本刀实测到的**真实生产缺陷**（只记录，未改）。

    投委会**开启且辩论成功**时，`brain_output` 由委员会给出，
    于是 `if brain_output is None:` 那段（**唯一**给 `content` 赋值的地方）被跳过；
    而第 230 行要 `output_chars=len(content)` ⇒ `UnboundLocalError`。

    后果链条（三条都已实测）：

    1. 决策缓存 / 持仓指令 / 历史 **确实写盘了**（它们在第 230 行之前完成）；
    2. 但函数 **`return None`** —— 调用方拿到的是失败信号；
    3. `_record_cycle_health("failed", ...)` ⇒ **成功的周期被记成失败**。

    即：**开启投委会会让每一轮都被记成 failed**（而投委会本身是可用功能）。
    修它属于改实盘行为，按本战役纪律只钉现状、留给单独决策。
    """

    def _enable_council(self):
        self.council_cfg = {"enabled": True, "timeout_seconds": 60}
        self.council_result = ({"decisions": {"BTC-USDT-SWAP": {"action": "WAIT"}},
                                "macro_assessment": "由委员会给出"},
                               {"total_duration_ms": 42, "consensus_mode": "unanimous",
                                "advisors": {"a": {"status": "ok"}}})

    def test_council_success_returns_none_instead_of_the_cache(self):
        self._enable_council()
        self.assertIsNone(self._run())

    def test_council_success_is_recorded_as_a_failed_cycle(self):
        self._enable_council()
        self._run()
        self.assertEqual(self.health[0][0], "failed")
        self.assertIn("content", self.health[0][1])
        self.assertEqual(self.telemetry.calls[0][0], ("failed",))
        self.assertIsInstance(self.telemetry.calls[0][1]["error"], UnboundLocalError)

    def test_the_cache_and_history_still_land_despite_the_crash(self):
        # 三份落盘都发生在第 230 行之前 ⇒ 副作用已产生，只有**返回值与健康记录**是错的
        self._enable_council()
        self._run()
        self.assertIn(self.paths["cache"], self.written)
        self.assertIn(self.paths["pos"], self.written)
        self.assertIn(self.paths["history"], self.written)

    def test_the_single_model_path_is_unaffected(self):
        # 对照组：投委会关闭时 `content` 有值 ⇒ 同一份夹具下返回缓存、健康记 ok
        self.council_cfg = {"enabled": False}
        out = self._run()
        self.assertIn("assembled", out)
        self.assertEqual(self.health, [("ok",)])

    def test_the_crash_is_purely_the_missing_content_binding(self):
        # 反证：源码里 `content` 的**每一处赋值都只在单模型分支之内**，
        # 而使用点在外层 —— 这就是根因（不是委员会结果本身有什么问题）
        src = Path(dispatch.__file__).read_text(encoding="utf-8")
        self.assertIn("output_chars=len(content)", src)
        assign_lines = [i + 1 for i, line in enumerate(src.splitlines())
                        if line.strip().startswith(("content =", "content,", "content.startswith"))]
        self.assertTrue(assign_lines, "应能找到 content 的绑定点")
        guard = next(i + 1 for i, line in enumerate(src.splitlines())
                     if "if brain_output is None:" in line)
        use = next(i + 1 for i, line in enumerate(src.splitlines())
                   if "output_chars=len(content)" in line)
        self.assertTrue(all(guard < ln < use for ln in assign_lines),
                        f"content 的绑定点 {assign_lines} 应全部落在守卫 {guard} 与使用点 {use} 之间")
        self.assertLess(guard, use)


if __name__ == "__main__":
    unittest.main()

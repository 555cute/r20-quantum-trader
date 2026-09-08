"""Isolated LLM runtime regressions: output limits, protocol diagnostics, strict JSON."""
from __future__ import annotations

import io
import json
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import r20_backend.llm_manager as llm_manager


def _runtime(**overrides):
    base = {
        "model": "glm-5.3",
        "base_url": "https://example.test/v1",
        "api_key": "sk-test",
        "api_format": "openai_responses",
        "reasoning_effort": "high",
        "reasoning_type": "auto",
        "thinking_timeout": 15.0,
        "max_output_tokens": 2048,
    }
    base.update(overrides)
    return base


class _HttpBody:
    def __init__(self, payload, status=200):
        self._payload = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode("utf-8")
        self.status = status

    def getcode(self):
        return self.status

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class LlmRuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.orig_models_file = llm_manager.LLM_CONFIG_FILE
        self.orig_legacy_file = llm_manager.LEGACY_PROVIDERS_FILE
        test_file = Path(self.temp.name) / "llm_models.json"
        llm_manager.LLM_CONFIG_FILE = test_file
        llm_manager.LLM_PROVIDERS_FILE = test_file
        llm_manager.LEGACY_PROVIDERS_FILE = Path(self.temp.name) / "missing-legacy.json"
        self.env_patch = patch("r20_backend.settings_store.update_env")
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        llm_manager.LLM_CONFIG_FILE = self.orig_models_file
        llm_manager.LLM_PROVIDERS_FILE = self.orig_models_file
        llm_manager.LEGACY_PROVIDERS_FILE = self.orig_legacy_file
        self.temp.cleanup()

    def test_responses_payload_includes_explicit_max_output_tokens(self):
        _, _, payload = llm_manager.build_request_spec(
            model="glm-5.3",
            messages=[{"role": "user", "content": "hi"}],
            base_url="https://example.test/v1",
            api_format="openai_responses",
            max_tokens=2048,
        )
        self.assertEqual(payload["max_output_tokens"], 2048)
        self.assertNotIn("messages", payload)
        self.assertIn("input", payload)

    def test_chat_and_claude_use_native_output_limit_fields(self):
        _, _, chat = llm_manager.build_request_spec(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "hi"}],
            base_url="https://api.openai.com/v1",
            api_format="openai_chat",
            max_tokens=1024,
        )
        self.assertEqual(chat["max_tokens"], 1024)
        self.assertNotIn("max_completion_tokens", chat)

        _, _, o3 = llm_manager.build_request_spec(
            model="o3-mini",
            messages=[{"role": "user", "content": "hi"}],
            base_url="https://api.openai.com/v1",
            api_format="openai_chat",
            max_tokens=1024,
        )
        self.assertEqual(o3["max_completion_tokens"], 1024)
        self.assertNotIn("max_tokens", o3)

        _, _, claude = llm_manager.build_request_spec(
            model="claude-3-7-sonnet-20250219",
            messages=[{"role": "user", "content": "hi"}],
            base_url="https://api.anthropic.com/v1",
            api_format="claude_messages",
            reasoning_effort="none",
            max_tokens=1024,
        )
        self.assertEqual(claude["max_tokens"], 1024)

    def test_settings_round_trip_max_output_tokens_into_runtime(self):
        llm_manager.upsert_model("custom", {
            "id": "probe-model",
            "name": "probe",
            "base_url": "https://example.test/v1",
            "api_key": "sk-test",
            "api_format": "openai_responses",
        })
        llm_manager.activate_provider_model("custom", "probe-model", reasoning_effort="high")
        updated = llm_manager.update_llm_settings(max_output_tokens=8192)
        self.assertEqual(updated["max_output_tokens"], 8192)
        loaded = llm_manager.load_llm_config()
        self.assertEqual(loaded["max_output_tokens"], 8192)
        runtime = llm_manager.get_active_llm_runtime()
        self.assertEqual(runtime["max_output_tokens"], 8192)
        self.assertEqual(runtime["model"], "probe-model")
        self.assertIn("provider_name", runtime)
        self.assertIn("reasoning_effort", runtime)
        self.assertIn("api_format", runtime)
        self.assertNotIn("active_model", runtime)

    def test_admin_model_cap_can_clear_override_without_changing_global(self):
        from r20_backend import app as backend

        llm_manager.upsert_provider({"id": "fixture", "name": "Fixture", "base_url": "https://example.test/v1"})
        row = {"id": "probe-model", "max_output_tokens": 8192, "api_format": "openai_responses"}
        llm_manager.upsert_model("fixture", row)
        llm_manager.activate_provider_model("fixture", "probe-model")
        llm_manager.update_llm_settings(max_output_tokens=4096)
        with patch.object(backend, "require_superadmin", return_value={"username": "fixture"}), patch.object(backend, "audit_record"):
            backend.admin_upsert_llm_model(backend.LLMModelUpsertRequest(id="probe-model"), "fixture")
            self.assertEqual(llm_manager.get_active_llm_runtime()["max_output_tokens"], 8192)
            backend.admin_upsert_llm_model(
                backend.LLMModelUpsertRequest(id="probe-model", max_output_tokens=None), "fixture",
            )
        self.assertEqual(llm_manager.get_active_llm_runtime()["max_output_tokens"], 4096)
        persisted = llm_manager.load_llm_config()
        self.assertEqual(persisted["max_output_tokens"], 4096)
        provider_model = next(p for p in persisted["providers"] if p["id"] == "fixture")["models"][0]
        self.assertIsNone(provider_model.get("max_output_tokens"))

    def test_council_uses_each_role_limit_instead_of_active_model_limit(self):
        from r20_backend import council_manager as council

        catalog = {"max_output_tokens": 4096, "models": [
            {"id": "advisor", "api_format": "openai_responses", "max_output_tokens": 8192},
            {"id": "arbiter", "api_format": "openai_responses"},
        ]}
        roles = {
            "trader_a": {"enabled": True, "model_id": "advisor", "name": "Advisor", "prompt": "Review."},
            "cio": {"enabled": True, "model_id": "arbiter", "is_arbitrator": True, "name": "CIO"},
        }
        sent = []

        def respond(request, timeout=None):
            body = json.loads(request.data)
            sent.append((body["model"], body["max_output_tokens"]))
            return _HttpBody({"status": "completed", "output_text": '{"decisions": {}, "position_management": []}'})

        with patch.object(council, "load_council_config", return_value={"roles": roles, "consensus_mode": "cross_examination"}), \
             patch.object(llm_manager, "load_llm_config", return_value=catalog), \
             patch.object(llm_manager, "get_active_llm_runtime", return_value=_runtime()), \
             patch("urllib.request.urlopen", side_effect=respond):
            council.execute_council_debate("Synthetic market.", "Do not trade.", timeout=30)
        self.assertEqual(sent, [("advisor", 8192), ("advisor", 8192), ("arbiter", 4096)])

    @patch("r20_backend.llm_manager.get_active_llm_runtime", return_value=_runtime())
    @patch("urllib.request.urlopen")
    def test_execute_sends_runtime_limit_on_responses(self, mock_urlopen, _runtime_mock):
        mock_urlopen.return_value = _HttpBody({
            "status": "completed",
            "output_text": '{"action":"WAIT"}',
            "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        })
        content, _, usage, _ = llm_manager.execute_llm_request(
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(content, '{"action":"WAIT"}')
        self.assertEqual(usage["total_tokens"], 14)
        sent = json.loads(mock_urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent["max_output_tokens"], 2048)
        self.assertTrue(str(mock_urlopen.call_args[0][0].full_url).endswith("/responses"))

    @patch("r20_backend.llm_manager.get_active_llm_runtime", return_value=_runtime())
    @patch("urllib.request.urlopen")
    def test_incomplete_responses_cannot_become_content(self, mock_urlopen, _runtime_mock):
        mock_urlopen.return_value = _HttpBody({
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output_text": '{"action":"BUY_LONG"',
            "usage": {"output_tokens": 80, "total_tokens": 120},
        })
        with self.assertRaises(llm_manager.LlmIncompleteError) as ctx:
            llm_manager.execute_llm_request(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.stage, "incomplete")
        self.assertEqual(ctx.exception.output_chars, len('{"action":"BUY_LONG"'))
        self.assertEqual(ctx.exception.usage.get("total_tokens"), 120)
        self.assertNotIn("BUY_LONG", str(ctx.exception))

    @patch("r20_backend.llm_manager.get_active_llm_runtime", return_value=_runtime(api_format="openai_chat", model="gpt-4o-mini"))
    @patch("urllib.request.urlopen")
    def test_chat_refusal_and_empty_are_typed(self, mock_urlopen, _runtime_mock):
        mock_urlopen.return_value = _HttpBody({
            "choices": [{"finish_reason": "content_filter", "message": {"content": "", "refusal": "blocked"}}],
            "usage": {"total_tokens": 9},
        })
        with self.assertRaises(llm_manager.LlmRefusalError) as refused:
            llm_manager.execute_llm_request(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(refused.exception.stage, "refusal")

        mock_urlopen.return_value = _HttpBody({
            "choices": [{"finish_reason": "stop", "message": {"content": "   "}}],
            "usage": {"total_tokens": 3},
        })
        with self.assertRaises(llm_manager.LlmEmptyContentError) as empty:
            llm_manager.execute_llm_request(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(empty.exception.stage, "empty_content")
        self.assertEqual(empty.exception.output_chars, 0)

    @patch("r20_backend.llm_manager.get_active_llm_runtime", return_value=_runtime())
    @patch("urllib.request.urlopen")
    def test_malformed_envelope_is_not_business_json(self, mock_urlopen, _runtime_mock):
        mock_urlopen.return_value = _HttpBody(b'{"id":')
        with self.assertRaises(llm_manager.LlmEnvelopeJsonError) as ctx:
            llm_manager.execute_llm_request(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.stage, "envelope_json")
        self.assertEqual(ctx.exception.output_chars, 6)
        self.assertNotIn('{"id":', str(ctx.exception))

    @patch("r20_backend.llm_manager.get_active_llm_runtime", return_value=_runtime())
    @patch("urllib.request.urlopen")
    def test_rejected_parameter_does_not_drop_constraints(self, mock_urlopen, _runtime_mock):
        def _boom(_req, timeout=None):
            raise urllib.error.HTTPError(
                "https://example.test/v1/responses",
                400,
                "Bad Request",
                None,
                io.BytesIO(b'{"error":{"message":"invalid parameter"}}'),
            )

        mock_urlopen.side_effect = _boom
        with self.assertRaises(llm_manager.LlmHttpError) as ctx:
            llm_manager.execute_llm_request(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(mock_urlopen.call_count, 1)
        sent = json.loads(mock_urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent["max_output_tokens"], 2048)
        self.assertIn("input", sent)
        self.assertNotIn("messages", sent)
        self.assertEqual(ctx.exception.stage, "http")
        self.assertIn("invalid parameter", str(ctx.exception))
        self.assertNotIn("sk-test", str(ctx.exception))

    def test_parse_model_json_object_allows_fence_not_repairs(self):
        self.assertEqual(
            llm_manager.parse_model_json_object('```json\n{"action": "WAIT"}\n```'),
            {"action": "WAIT"},
        )
        with self.assertRaises(llm_manager.LlmBusinessJsonError) as truncated:
            llm_manager.parse_model_json_object('{"action": "WAIT"')
        self.assertEqual(truncated.exception.stage, "business_json")
        self.assertGreater(truncated.exception.output_chars, 0)
        with self.assertRaises(llm_manager.LlmBusinessJsonError):
            llm_manager.parse_model_json_object('["WAIT"]')
        with self.assertRaises(llm_manager.LlmBusinessJsonError):
            llm_manager.parse_model_json_object("not json")

    def test_telemetry_keeps_failure_output_chars_from_protocol_error(self):
        from r20_gateway.telemetry import ModelCallTelemetry

        captured = {}

        class _Store:
            def __init__(self, _path):
                pass

            def record_model_call(self, record):
                captured.update(record)
                return 1

        err = llm_manager.LlmIncompleteError(
            "LLM response incomplete (reason=max_output_tokens, output_chars=41)",
            output_chars=41,
            usage={"output_tokens": 80, "total_tokens": 120},
            reason="max_output_tokens",
        )
        with patch("r20_gateway.telemetry.GatewayStore", _Store):
            call = ModelCallTelemetry("trading_brain", "glm-5.3", "high", "sys", "user")
            call.finish("failed", error=err)
        self.assertEqual(captured["output_chars"], 41)
        self.assertEqual(captured["output_tokens"], 80)
        self.assertEqual(captured["total_tokens"], 120)
        self.assertIn("incomplete", captured["error_type"])
        self.assertNotIn("sys", json.dumps(captured))
        self.assertNotIn("user", json.dumps(captured))

    def test_timeout_retries_share_one_budget(self):
        now = [0.0]
        timeouts = []

        def fake_perf_counter():
            return now[0]

        def fake_urlopen(_req, timeout=None):
            timeouts.append(timeout)
            now[0] += float(timeout or 0)
            raise socket.timeout()

        def fake_sleep(seconds):
            now[0] += seconds

        with patch.object(llm_manager, "get_active_llm_runtime", return_value=_runtime()), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("r20_backend.llm_manager.time.perf_counter", side_effect=fake_perf_counter), \
             patch("r20_backend.llm_manager.time.sleep", side_effect=fake_sleep):
            with self.assertRaises(TimeoutError) as ctx:
                llm_manager.execute_llm_request(
                    messages=[{"role": "user", "content": "hi"}],
                    timeout=5.0,
                )
        self.assertEqual(len(timeouts), 1)
        self.assertAlmostEqual(timeouts[0], 5.0, places=2)
        self.assertIn("5s", str(ctx.exception))



if __name__ == "__main__":
    unittest.main()

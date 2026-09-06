"""Unit tests for Unified LLM Management, Multi-Format API Support (OpenAI Chat, OpenAI Responses, Claude Messages),
standard reasoning effort adaptation, and connection testing."""
from __future__ import annotations
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import r20_backend.app as app_module
import r20_backend.llm_manager as llm_manager
from r20_backend.admin_auth import AdminAuthStore


class LLMMultiProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)

        # Isolate LLM config files across both modules
        self.orig_models_file = llm_manager.LLM_CONFIG_FILE
        self.orig_legacy_file = llm_manager.LEGACY_PROVIDERS_FILE
        self.orig_app_providers_file = app_module.LLM_PROVIDERS_FILE
        test_file = self.temp_path / "llm_models.json"
        llm_manager.LLM_CONFIG_FILE = test_file
        llm_manager.LLM_PROVIDERS_FILE = test_file
        llm_manager.LEGACY_PROVIDERS_FILE = self.temp_path / "non_existent_legacy.json"
        app_module.LLM_PROVIDERS_FILE = test_file

        # Isolate admin auth
        self.orig_auth = app_module.admin_auth
        app_module.admin_auth = AdminAuthStore(self.temp_path / "admin.db")
        app_module.admin_auth.initialize_from_legacy("TestAdminPass123456")

        # Isolate production environment and secrets from test mutations
        self.patcher_env = patch("r20_backend.settings_store.update_env")
        self.patcher_sec = patch("r20_gateway.secrets.save_secrets")
        self.mock_update_env = self.patcher_env.start()
        self.mock_save_secrets = self.patcher_sec.start()

        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.patcher_env.stop()
        self.patcher_sec.stop()
        llm_manager.LLM_CONFIG_FILE = self.orig_models_file
        llm_manager.LLM_PROVIDERS_FILE = self.orig_models_file
        llm_manager.LEGACY_PROVIDERS_FILE = self.orig_legacy_file
        app_module.LLM_PROVIDERS_FILE = self.orig_app_providers_file
        app_module.admin_auth = self.orig_auth
        self.temp.cleanup()

    def login(self) -> dict[str, str]:
        resp = self.client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "TestAdminPass123456"})
        self.assertEqual(resp.status_code, 200, resp.text)
        return {"X-R20-Session": resp.json()["session_token"]}

    def test_init_and_load_models_clean_no_bloat(self):
        config = llm_manager.load_llm_config(mask_keys=True)
        self.assertIn("models", config)
        self.assertTrue(len(config["models"]) >= 1)
        # Should not contain bloated hardcoded preset list
        model_ids = [m["id"] for m in config["models"]]
        self.assertIn(config["active_model_id"], model_ids)

        for m in config["models"]:
            self.assertNotIn("api_key", m)
            self.assertIn("has_key", m)
            self.assertIn("api_format", m)

    def test_build_request_spec_all_protocols(self):
        # 1. OpenAI Chat Completions Protocol
        url_chat, headers_chat, payload_chat = llm_manager.build_request_spec(
            model="o3-mini",
            messages=[{"role": "user", "content": "hi"}],
            base_url="https://api.openai.com/v1",
            api_key="sk-test-chat",
            api_format="openai_chat",
            reasoning_effort="high",
            temperature=0.2,
        )
        self.assertTrue(url_chat.endswith("/chat/completions"))
        self.assertEqual(headers_chat["Authorization"], "Bearer sk-test-chat")
        self.assertEqual(payload_chat["reasoning_effort"], "high")
        self.assertNotIn("temperature", payload_chat)  # Omitted for o3-mini reasoning model

        # 2. OpenAI Responses Protocol (Complete Responses)
        url_resp, headers_resp, payload_resp = llm_manager.build_request_spec(
            model="gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
            base_url="https://api.openai.com/v1",
            api_key="sk-test-resp",
            api_format="openai_responses",
            reasoning_effort="medium",
            response_format={"type": "json_object"},
        )
        self.assertTrue(url_resp.endswith("/responses"))
        self.assertEqual(headers_resp["Authorization"], "Bearer sk-test-resp")
        self.assertIn("input", payload_resp)
        self.assertEqual(payload_resp["text"]["format"]["type"], "json_object")
        self.assertEqual(payload_resp["reasoning"]["effort"], "medium")

        # 3. Anthropic Claude Messages Protocol
        url_claude, headers_claude, payload_claude = llm_manager.build_request_spec(
            model="claude-3-7-sonnet-20250219",
            messages=[
                {"role": "system", "content": "System directive"},
                {"role": "user", "content": "User question"}
            ],
            base_url="https://api.anthropic.com/v1",
            api_key="sk-ant-test",
            api_format="claude_messages",
            reasoning_effort="high",
        )
        self.assertTrue(url_claude.endswith("/messages"))
        self.assertEqual(headers_claude["x-api-key"], "sk-ant-test")
        self.assertEqual(headers_claude["anthropic-version"], "2023-06-01")
        self.assertEqual(payload_claude["system"], "System directive")
        self.assertEqual(len(payload_claude["messages"]), 1)
        self.assertEqual(payload_claude["thinking"]["type"], "enabled")
        self.assertEqual(payload_claude["thinking"]["budget_tokens"], 16000)

    def test_model_crud_and_activation(self):
        # 1. Add custom model with claude_messages format
        m = llm_manager.upsert_model("custom", {
            "id": "claude-3-7-custom",
            "name": "Claude 3.7 生产主脑",
            "provider_name": "Anthropic Direct",
            "base_url": "https://api.anthropic.com/v1",
            "api_key": "sk-ant-prod-key",
            "api_format": "claude_messages",
            "default_effort": "high",
            "description": "自定义高思考模型",
        })
        self.assertEqual(m["model_id"], "claude-3-7-custom")
        self.assertEqual(m["api_format"], "claude_messages")

        # 2. Activate model
        res = llm_manager.activate_provider_model("custom", "claude-3-7-custom", reasoning_effort="high")
        self.assertTrue(res["success"])
        self.assertEqual(res["active_model_id"], "claude-3-7-custom")
        self.assertEqual(res["api_format"], "claude_messages")

        active_runtime = llm_manager.get_active_llm_runtime()
        self.assertEqual(active_runtime["model"], "claude-3-7-custom")
        self.assertEqual(active_runtime["api_format"], "claude_messages")
        self.assertEqual(active_runtime["api_key"], "sk-ant-prod-key")

        # 3. Cannot delete currently active model
        with self.assertRaises(ValueError):
            llm_manager.delete_model("custom", "claude-3-7-custom")

        # 4. Upsert another model, switch to it, then delete claude-3-7-custom
        llm_manager.upsert_model("custom", {
            "id": "gemini-fallback",
            "name": "Gemini Fallback",
            "base_url": "https://api.openai.com/v1",
            "api_format": "openai_chat",
        })
        llm_manager.activate_provider_model("custom", "gemini-fallback")
        deleted = llm_manager.delete_model("custom", "claude-3-7-custom")
        self.assertTrue(deleted)

    @patch("urllib.request.urlopen")
    def test_connection_test_claude_messages(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = json.dumps({
            "id": "msg_123",
            "content": [
                {"type": "thinking", "thinking": "Thinking step 1... step 2..."},
                {"type": "text", "text": "PONG"}
            ],
            "usage": {"input_tokens": 15, "output_tokens": 40}
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = llm_manager.test_llm_connection(
            base_url="https://api.anthropic.com/v1",
            api_key="sk-ant-123",
            model="claude-3-7-sonnet-20250219",
            api_format="claude_messages",
            reasoning_effort="high",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["status_code"], 200)
        self.assertEqual(res["response_preview"], "PONG")
        self.assertTrue(res["reasoning_detected"])
        self.assertEqual(res["api_format"], "claude_messages")

    @patch("urllib.request.urlopen")
    def test_connection_test_openai_responses(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = json.dumps({
            "id": "resp_abc",
            "output_text": "PONG",
            "output": [
                {"type": "reasoning", "content": "Responses reasoning text..."},
                {"type": "message", "content": [{"type": "output_text", "text": "PONG"}]}
            ],
            "usage": {"total_tokens": 55, "output_tokens_details": {"reasoning_tokens": 30}}
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = llm_manager.test_llm_connection(
            base_url="https://api.openai.com/v1",
            api_key="sk-openai-123",
            model="gpt-4o",
            api_format="openai_responses",
            reasoning_effort="high",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["status_code"], 200)
        self.assertEqual(res["response_preview"], "PONG")
        self.assertTrue(res["reasoning_detected"])
        self.assertEqual(res["api_format"], "openai_responses")

    def test_admin_api_endpoints_models_crud(self):
        headers = self.login()

        # 1. GET /api/v1/admin/llm/models
        resp = self.client.get("/api/v1/admin/llm/models", headers=headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("models", data)
        self.assertIn("supported_api_formats", data)

        # 2. POST /api/v1/admin/llm/models (create new model with custom api_format)
        add_m = self.client.post("/api/v1/admin/llm/models", headers=headers, json={
            "id": "test-claude-api",
            "name": "Test Claude API",
            "provider_name": "Anthropic",
            "base_url": "https://api.anthropic.com/v1",
            "api_key": "sk-ant-test",
            "api_format": "claude_messages",
            "default_effort": "high",
        })
        self.assertEqual(add_m.status_code, 200)
        self.assertEqual(add_m.json()["api_format"], "claude_messages")

        # 3. POST /api/v1/admin/llm/activate
        act_resp = self.client.post("/api/v1/admin/llm/activate", headers=headers, json={
            "model_id": "test-claude-api",
            "reasoning_effort": "high"
        })
        self.assertEqual(act_resp.status_code, 200)
        self.assertEqual(act_resp.json()["active_model_id"], "test-claude-api")

        # 4. POST /api/v1/admin/llm/test with mock
        with patch.object(app_module, "test_llm_connection") as mock_test:
            mock_test.return_value = {
                "ok": True,
                "status_code": 200,
                "latency_ms": 320,
                "model": "test-claude-api",
                "api_format": "claude_messages",
                "response_preview": "PONG",
                "reasoning_detected": True,
            }
            test_resp = self.client.post("/api/v1/admin/llm/test", headers=headers, json={
                "model": "test-claude-api",
                "api_format": "claude_messages",
                "reasoning_effort": "high",
            })
            self.assertEqual(test_resp.status_code, 200)
            self.assertTrue(test_resp.json()["ok"])
            self.assertEqual(test_resp.json()["api_format"], "claude_messages")

        # 5. Activate fallback then DELETE model
        self.client.post("/api/v1/admin/llm/models", headers=headers, json={
            "id": "model-temp", "base_url": "https://api.openai.com/v1"
        })
        self.client.post("/api/v1/admin/llm/activate", headers=headers, json={"model_id": "model-temp"})
        del_m = self.client.delete("/api/v1/admin/llm/models/test-claude-api", headers=headers)
        self.assertEqual(del_m.status_code, 200)
        self.assertTrue(del_m.json()["deleted"])

    def test_fetch_remote_models_and_providers_crud(self):
        headers = self.login()

        # 1. Upsert provider
        p_resp = self.client.post("/api/v1/admin/llm/providers", headers=headers, json={
            "id": "testprov",
            "name": "Test Provider",
            "base_url": "https://api.testprovider.com/v1",
            "api_key": "sk-testprov",
            "description": "Custom Provider Unit Test",
        })
        self.assertEqual(p_resp.status_code, 200)
        self.assertEqual(p_resp.json()["id"], "testprov")

        # 2. Mock fetch remote models endpoint
        with patch.object(app_module, "fetch_remote_models") as mock_fetch:
            mock_fetch.return_value = {
                "ok": True,
                "endpoint_used": "https://api.testprovider.com/v1/models",
                "total": 2,
                "models": [
                    {"id": "testprov/flagship-1", "name": "Flagship 1", "reasoning_type": "standard_effort"},
                    {"id": "testprov/fast-1", "name": "Fast 1", "reasoning_type": "none"},
                ],
            }
            f_resp = self.client.post("/api/v1/admin/llm/fetch-models", headers=headers, json={
                "provider_id": "testprov",
            })
            self.assertEqual(f_resp.status_code, 200)
            self.assertTrue(f_resp.json()["ok"])
            self.assertEqual(f_resp.json()["total"], 2)

        # 3. Toggle provider
        t_resp = self.client.post("/api/v1/admin/llm/providers/testprov/toggle", headers=headers, json={"enabled": True})
        self.assertEqual(t_resp.status_code, 200)
        self.assertTrue(t_resp.json()["enabled"])

        # 4. Clear provider models
        c_resp = self.client.delete("/api/v1/admin/llm/providers/testprov/models", headers=headers)
        self.assertEqual(c_resp.status_code, 200)
        self.assertTrue(c_resp.json()["cleared"])

        # 5. Delete provider
        del_p = self.client.delete("/api/v1/admin/llm/providers/testprov", headers=headers)
        self.assertTrue(del_p.json()["deleted"])

    def test_llm_thinking_timeout_settings_and_update(self):
        headers = self.login()
        # 1. Initial config contains thinking_timeout
        cfg = llm_manager.load_llm_config()
        self.assertIn("thinking_timeout", cfg)
        self.assertEqual(cfg["thinking_timeout"], 120.0)

        # 2. Update via API
        resp = self.client.post("/api/v1/admin/llm/settings", headers=headers, json={
            "thinking_timeout": 180.0,
            "reasoning_effort": "high",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["thinking_timeout"], 180.0)

        # 3. Verify loaded runtime incorporates updated thinking timeout
        runtime = llm_manager.get_active_llm_runtime()
        self.assertEqual(runtime["thinking_timeout"], 180.0)

        # 4. Activate model with custom thinking timeout
        act_resp = self.client.post("/api/v1/admin/llm/activate", headers=headers, json={
            "model_id": "gemini-3.8-flash-high",
            "reasoning_effort": "high",
            "thinking_timeout": 240.0,
        })
        self.assertEqual(act_resp.status_code, 200)
        self.assertEqual(act_resp.json()["thinking_timeout"], 240.0)

    def test_update_status_and_check_endpoints(self):
        headers = self.login()
        # GET /api/v1/admin/update-status
        resp1 = self.client.get("/api/v1/admin/update-status", headers=headers)
        self.assertEqual(resp1.status_code, 200)
        self.assertIn("branch", resp1.json())
        self.assertIn("dirty", resp1.json())

        # POST /api/v1/admin/update/check (frontend compatibility route)
        resp2 = self.client.post("/api/v1/admin/update/check", headers=headers)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["branch"], resp1.json()["branch"])


if __name__ == "__main__":
    unittest.main()

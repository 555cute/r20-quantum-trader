"""LLM 韧性链路测试：单模型重试次数可配置、连接层失败必须重试、
非瞬时故障跨模型回退、空正文重试、回退事件留痕与后台配置接口。"""
from __future__ import annotations
import io
import json
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import r20_backend.app as app_module
import r20_backend.llm_manager as llm_manager
from r20_backend.admin_auth import AdminAuthStore


class FakeResp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def getcode(self):
        return 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def chat_response(text: str = "OK") -> FakeResp:
    return FakeResp({"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 7}})


def http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x/v1/chat/completions", code, f"HTTP {code}", {}, io.BytesIO(body.encode()))


class LLMResilienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp.name)
        self.orig_models_file = llm_manager.LLM_CONFIG_FILE
        self.orig_legacy_file = llm_manager.LEGACY_PROVIDERS_FILE
        self.orig_failover_file = llm_manager.FAILOVER_EVENTS_FILE
        test_file = self.temp_path / "llm_models.json"
        llm_manager.LLM_CONFIG_FILE = test_file
        llm_manager.LLM_PROVIDERS_FILE = test_file
        llm_manager.LEGACY_PROVIDERS_FILE = self.temp_path / "non_existent_legacy.json"
        llm_manager.FAILOVER_EVENTS_FILE = self.temp_path / "llm_failover_events.json"

        self.orig_auth = app_module.admin_auth
        app_module.admin_auth = AdminAuthStore(self.temp_path / "admin.db")
        app_module.admin_auth.initialize_from_legacy("TestAdminPass123456")

        self.patcher_env = patch("r20_backend.settings_store.update_env")
        self.patcher_refresh = patch("r20_backend.config.refresh_settings")
        self.patcher_sec = patch("r20_gateway.secrets.save_secrets")
        self.patcher_sleep = patch("time.sleep")
        self.patcher_env.start()
        self.patcher_refresh.start()
        self.patcher_sec.start()
        self.mock_sleep = self.patcher_sleep.start()

        self.client = TestClient(app_module.app)
        self._seed_models()

    def tearDown(self):
        self.patcher_sleep.stop()
        self.patcher_env.stop()
        self.patcher_refresh.stop()
        self.patcher_sec.stop()
        llm_manager.LLM_CONFIG_FILE = self.orig_models_file
        llm_manager.LLM_PROVIDERS_FILE = self.orig_models_file
        llm_manager.LEGACY_PROVIDERS_FILE = self.orig_legacy_file
        llm_manager.FAILOVER_EVENTS_FILE = self.orig_failover_file
        app_module.admin_auth = self.orig_auth
        self.temp.cleanup()

    def _seed_models(self):
        llm_manager.upsert_model("custom", {"id": "primary-m", "name": "主脑", "base_url": "https://a.example/v1", "api_key": "k1"})
        llm_manager.upsert_model("custom", {"id": "backup-m", "name": "备胎", "base_url": "https://b.example/v1", "api_key": "k2"})
        llm_manager.update_llm_settings(active_model_id="primary-m", reasoning_effort="high")

    def login(self) -> dict[str, str]:
        resp = self.client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "TestAdminPass123456"})
        self.assertEqual(resp.status_code, 200, resp.text)
        return {"X-R20-Session": resp.json()["session_token"]}

    # ---------- 配置 ----------

    def test_settings_update_and_validation(self):
        headers = self.login()
        resp = self.client.post("/api/v1/admin/llm/settings", headers=headers, json={
            "request_attempts": 5,
            "fallback_model_ids": ["backup-m"],
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["request_attempts"], 5)
        self.assertEqual(data["fallback_model_ids"], ["backup-m"])

        cfg = self.client.get("/api/v1/admin/llm/models", headers=headers).json()
        self.assertEqual(cfg["request_attempts"], 5)
        self.assertEqual(cfg["fallback_model_ids"], ["backup-m"])

        # 不存在的回退模型必须 400 拒绝
        bad = self.client.post("/api/v1/admin/llm/settings", headers=headers, json={
            "fallback_model_ids": ["ghost-model"],
        })
        self.assertEqual(bad.status_code, 400)
        self.assertIn("回退模型不存在", bad.text)

        # 请求次数越界拒绝（pydantic ge/le）
        bad2 = self.client.post("/api/v1/admin/llm/settings", headers=headers, json={"request_attempts": 99})
        self.assertEqual(bad2.status_code, 422)

        # 主脑模型自动从回退链剔除
        self.client.post("/api/v1/admin/llm/settings", headers=headers, json={
            "fallback_model_ids": ["primary-m", "backup-m"],
        })
        cfg2 = self.client.get("/api/v1/admin/llm/models", headers=headers).json()
        self.assertEqual(cfg2["fallback_model_ids"], ["backup-m"])

    def test_resolve_model_runtime(self):
        rt = llm_manager.resolve_model_runtime("backup-m")
        self.assertIsNotNone(rt)
        self.assertEqual(rt["model"], "backup-m")
        self.assertEqual(rt["base_url"], "https://b.example/v1")
        self.assertEqual(rt["api_key"], "k2")
        self.assertIsNone(llm_manager.resolve_model_runtime("nope"))

    # ---------- 重试 ----------

    def test_connection_error_is_retried_and_succeeds(self):
        """核心回归：URLError 连接层失败一次后必须继续重试，而非直接放弃。"""
        with patch("r20_backend.llm_manager.urllib.request.urlopen", side_effect=[
            urllib.error.URLError("connection refused"),
            chat_response("RECOVERED"),
        ]) as mock_open:
            content, _, usage, _ = llm_manager.execute_llm_request(
                messages=[{"role": "user", "content": "hi"}], timeout=10,
            )
        self.assertEqual(content, "RECOVERED")
        self.assertEqual(mock_open.call_count, 2)
        self.assertEqual(usage.get("total_tokens"), 7)

    def test_attempts_setting_limits_retries(self):
        llm_manager.update_llm_settings(request_attempts=2)
        failing = MagicMock(side_effect=urllib.error.URLError("reset by peer"))
        with patch("r20_backend.llm_manager.urllib.request.urlopen", failing):
            with self.assertRaises(Exception):
                llm_manager.execute_llm_request(messages=[{"role": "user", "content": "hi"}], timeout=10)
        self.assertEqual(failing.call_count, 2)

    def test_empty_content_retried_as_transient(self):
        with patch("r20_backend.llm_manager.urllib.request.urlopen", side_effect=[
            chat_response(""),
            chat_response("SECOND WINS"),
        ]) as mock_open:
            content, _, _, _ = llm_manager.execute_llm_request(messages=[{"role": "user", "content": "hi"}], timeout=10)
        self.assertEqual(content, "SECOND WINS")
        self.assertEqual(mock_open.call_count, 2)

    # ---------- 回退 ----------

    def test_transient_exhaustion_falls_back_to_next_model(self):
        llm_manager.update_llm_settings(request_attempts=2, fallback_model_ids=["backup-m"])
        calls = []

        def fake_open(req, timeout=None):
            calls.append(req.full_url)
            if "a.example" in req.full_url:
                raise urllib.error.URLError("connection refused")
            return chat_response("FROM BACKUP")

        with patch("r20_backend.llm_manager.urllib.request.urlopen", side_effect=fake_open):
            content, _, _, _ = llm_manager.execute_llm_request(messages=[{"role": "user", "content": "hi"}], timeout=10)
        self.assertEqual(content, "FROM BACKUP")
        self.assertEqual(calls, ["https://a.example/v1/chat/completions"] * 2 + ["https://b.example/v1/chat/completions"])

        events = llm_manager.recent_failover_events()
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["succeeded"])
        self.assertEqual(events[0]["type"], "fallback_hit")
        self.assertEqual(events[0]["from_model"], "primary-m")
        self.assertEqual(events[0]["to_model"], "backup-m")

    def test_hard_error_skips_same_model_retries_and_falls_back(self):
        llm_manager.update_llm_settings(request_attempts=3, fallback_model_ids=["backup-m"])
        calls = []

        def fake_open(req, timeout=None):
            calls.append(req.full_url)
            if "a.example" in req.full_url:
                raise http_error(401, "invalid api key")
            return chat_response("BACKUP SAVED THE ROUND")

        with patch("r20_backend.llm_manager.urllib.request.urlopen", side_effect=fake_open):
            content, _, _, _ = llm_manager.execute_llm_request(messages=[{"role": "user", "content": "hi"}], timeout=10)
        self.assertEqual(content, "BACKUP SAVED THE ROUND")
        # 401 硬故障不原地重试：主模型仅 1 次
        self.assertEqual(calls.count("https://a.example/v1/chat/completions"), 1)

    def test_allow_fallback_false_keeps_single_model(self):
        llm_manager.update_llm_settings(request_attempts=1, fallback_model_ids=["backup-m"])
        with patch("r20_backend.llm_manager.urllib.request.urlopen", side_effect=http_error(401, "invalid api key")) as mock_open:
            with self.assertRaises(RuntimeError) as ctx:
                llm_manager.execute_llm_request(
                    messages=[{"role": "user", "content": "hi"}], timeout=10, allow_fallback=False,
                )
        self.assertEqual(mock_open.call_count, 1)
        self.assertIn("HTTP 401", str(ctx.exception))

    def test_full_chain_failure_records_event_and_raises_summary(self):
        llm_manager.update_llm_settings(request_attempts=2, fallback_model_ids=["backup-m"])
        with patch("r20_backend.llm_manager.urllib.request.urlopen", side_effect=urllib.error.URLError("network down")):
            with self.assertRaises(RuntimeError) as ctx:
                llm_manager.execute_llm_request(messages=[{"role": "user", "content": "hi"}], timeout=10)
        self.assertIn("模型链全部失败", str(ctx.exception))
        self.assertIn("primary-m", str(ctx.exception))
        self.assertIn("backup-m", str(ctx.exception))
        events = llm_manager.recent_failover_events()
        self.assertEqual(events[0]["type"], "chain_failed")
        self.assertFalse(events[0]["succeeded"])

    def test_single_model_timeout_keeps_legacy_message(self):
        llm_manager.update_llm_settings(request_attempts=1, fallback_model_ids=[])
        with patch("r20_backend.llm_manager.urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            with self.assertRaises(TimeoutError) as ctx:
                llm_manager.execute_llm_request(messages=[{"role": "user", "content": "hi"}], timeout=10)
        self.assertIn("LLM 推演超时", str(ctx.exception))
        self.assertIn("调大思考上限时间", str(ctx.exception))

    def test_single_model_http_error_keeps_legacy_runtime_error(self):
        llm_manager.update_llm_settings(request_attempts=1, fallback_model_ids=[])
        with patch("r20_backend.llm_manager.urllib.request.urlopen", side_effect=http_error(404, "no such model")):
            with self.assertRaises(RuntimeError) as ctx:
                llm_manager.execute_llm_request(messages=[{"role": "user", "content": "hi"}], timeout=10)
        self.assertIn("LLM 网关返回 HTTP 404（模型 primary-m）", str(ctx.exception))

    # ---------- 后台事件接口 ----------

    def test_failover_events_endpoint(self):
        headers = self.login()
        llm_manager.record_failover_event({"type": "fallback_hit", "from_model": "a", "to_model": "b", "succeeded": True})
        resp = self.client.get("/api/v1/admin/llm/failover-events", headers=headers)
        self.assertEqual(resp.status_code, 200)
        events = resp.json()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["to_model"], "b")
        self.assertIn("time_str", events[0])
        unauth = self.client.get("/api/v1/admin/llm/failover-events")
        self.assertEqual(unauth.status_code, 401)


if __name__ == "__main__":
    unittest.main()

"""R21 双 bug 回归：

① Base URL 不再自动填充 /v1——智谱（/api/paas/v4）、Gemini（/v1beta）等带显式版本路径的
   供应商按原样拼接协议路径；仅裸域名（无路径）保留 /v1 兼容填充。覆盖请求构造、
   远端拉取候选端点与 401 不早退。
② 多供应商挂同名模型：激活一家后，另一家的副本必须可删除；只有「主脑归属供应商」
   名下的那份受保护。归属通过 active_provider_id + 持有者推导解析。
"""
from __future__ import annotations
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import r20_backend.llm_manager as llm_manager


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, f"HTTP {code}", {}, io.BytesIO(b""))


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


class ScopedLLMTestCase(unittest.TestCase):
    """把配置读写隔离进临时目录，绝不触碰生产 data/llm_models.json。"""

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

        self.patchers = [
            patch("r20_backend.settings_store.update_env"),
            patch("r20_backend.config.refresh_settings"),
            patch("r20_gateway.secrets.save_secrets"),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        llm_manager.LLM_CONFIG_FILE = self.orig_models_file
        llm_manager.LLM_PROVIDERS_FILE = self.orig_models_file
        llm_manager.LEGACY_PROVIDERS_FILE = self.orig_legacy_file
        llm_manager.FAILOVER_EVENTS_FILE = self.orig_failover_file
        self.temp.cleanup()

    def _add_provider(self, pid: str, base_url: str, api_key: str = "k") -> None:
        llm_manager.upsert_provider(
            {"id": pid, "name": pid.upper(), "base_url": base_url, "api_key": api_key,
             "api_format": "openai_chat", "enabled": True}
        )

    def _add_model(self, pid: str, mid: str) -> None:
        llm_manager.upsert_model(pid, {"id": mid, "name": mid, "provider_id": pid})

    def _write_raw(self, cfg: dict) -> None:
        llm_manager.LLM_CONFIG_FILE.write_text(json.dumps(cfg), encoding="utf-8")

    def _read_raw(self) -> dict:
        return json.loads(llm_manager.LLM_CONFIG_FILE.read_text(encoding="utf-8"))


# ────────────────────────────────────────────────────────────────
# ① /v1 自动填充取消
# ────────────────────────────────────────────────────────────────

class EndpointJoinTests(unittest.TestCase):
    """_join_api_path 纯函数语义。"""

    def test_explicit_version_path_is_authoritative(self):
        self.assertEqual(
            llm_manager._join_api_path("https://open.bigmodel.cn/api/paas/v4", "/chat/completions"),
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        )
        self.assertEqual(
            llm_manager._join_api_path("https://generativelanguage.googleapis.com/v1beta/openai", "/chat/completions"),
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        )

    def test_v1_base_unchanged(self):
        self.assertEqual(
            llm_manager._join_api_path("https://cpa.r20.cn/v1", "/chat/completions"),
            "https://cpa.r20.cn/v1/chat/completions",
        )

    def test_bare_host_gets_v1_compat_fill(self):
        self.assertEqual(
            llm_manager._join_api_path("https://relay.example.com", "/chat/completions"),
            "https://relay.example.com/v1/chat/completions",
        )

    def test_full_endpoint_passthrough(self):
        self.assertEqual(
            llm_manager._join_api_path("https://x.example/api/paas/v4/chat/completions", "/chat/completions"),
            "https://x.example/api/paas/v4/chat/completions",
        )


class BuildRequestSpecUrlTests(ScopedLLMTestCase):
    def _url(self, base: str, fmt: str = "openai_chat", api_path: str = "") -> str:
        endpoint, _, _ = llm_manager.build_request_spec(
            model="glm-5.3-flash",
            messages=[{"role": "user", "content": "hi"}],
            base_url=base,
            api_key="k",
            api_format=fmt,
            api_path=api_path,
        )
        return endpoint

    def test_zhipu_v4_chat_completions(self):
        self.assertEqual(
            self._url("https://open.bigmodel.cn/api/paas/v4"),
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        )

    def test_openai_style_unchanged(self):
        self.assertEqual(
            self._url("https://api.openai.com/v1"),
            "https://api.openai.com/v1/chat/completions",
        )

    def test_gemini_v1beta(self):
        self.assertEqual(
            self._url("https://generativelanguage.googleapis.com/v1beta/openai"),
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        )

    def test_claude_official_base(self):
        self.assertEqual(
            self._url("https://api.anthropic.com/v1", fmt="claude_messages"),
            "https://api.anthropic.com/v1/messages",
        )

    def test_responses_official_base(self):
        self.assertEqual(
            self._url("https://api.openai.com/v1", fmt="openai_responses"),
            "https://api.openai.com/v1/responses",
        )

    def test_custom_api_path_respected(self):
        self.assertEqual(
            self._url("https://gateway.example/llm/v4", api_path="/generate/chat"),
            "https://gateway.example/llm/v4/generate/chat",
        )

    def test_canonical_api_path_not_double_applied(self):
        # 「API 路径」填标准值时按协议格式决定，不再叠加
        self.assertEqual(
            self._url("https://open.bigmodel.cn/api/paas/v4", api_path="/chat/completions"),
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        )


class FetchRemoteModelsUrlTests(ScopedLLMTestCase):
    def test_v4_base_probes_own_models_endpoint(self):
        seen = []

        def fake_open(req, timeout=None):
            seen.append(req.full_url)
            if req.full_url == "https://open.bigmodel.cn/api/paas/v4/models":
                return FakeResp({"data": [{"id": "glm-5.3-flash", "name": "GLM"}]})
            raise _http_error(req.full_url, 404)

        with patch("r20_backend.llm_manager.urllib.request.urlopen", side_effect=fake_open):
            res = llm_manager.fetch_remote_models(
                base_url="https://open.bigmodel.cn/api/paas/v4", api_key="k"
            )
        self.assertTrue(res["ok"], res)
        self.assertEqual(seen[0], "https://open.bigmodel.cn/api/paas/v4/models")
        self.assertNotIn("https://open.bigmodel.cn/api/paas/v4/v1/models", seen)

    def test_401_on_one_candidate_does_not_abort_others(self):
        def fake_open(req, timeout=None):
            if req.full_url.endswith("/v1/models"):
                raise _http_error(req.full_url, 401)
            return FakeResp({"data": [{"id": "any-model"}]})

        with patch("r20_backend.llm_manager.urllib.request.urlopen", side_effect=fake_open):
            res = llm_manager.fetch_remote_models(base_url="https://gw.example/v1", api_key="k")
        self.assertTrue(res["ok"], res)

    def test_all_401_still_reports_key_problem(self):
        def fake_open(req, timeout=None):
            raise _http_error(req.full_url, 401)

        with patch("r20_backend.llm_manager.urllib.request.urlopen", side_effect=fake_open):
            res = llm_manager.fetch_remote_models(base_url="https://gw.example/v1", api_key="bad")
        self.assertFalse(res["ok"])
        self.assertIn("401", res["error"])


# ────────────────────────────────────────────────────────────────
# ② 同名模型跨供应商：删除按归属放行
# ────────────────────────────────────────────────────────────────

class SameNameModelDeleteTests(ScopedLLMTestCase):
    def _seed_two_providers_same_model(self):
        self._add_provider("prov-a", "https://a.example/v1", "key-a")
        self._add_provider("prov-b", "https://b.example/v1", "key-b")
        self._add_model("prov-a", "glm-x")
        self._add_model("prov-b", "glm-x")

    def test_activate_records_provider_attribution(self):
        self._seed_two_providers_same_model()
        llm_manager.activate_provider_model("prov-a", "glm-x")
        cfg = self._read_raw()
        self.assertEqual(cfg["active_model_id"], "glm-x")
        self.assertEqual(cfg["active_provider_id"], "prov-a")
        # 顶层扁平缓存钉在归属供应商的凭据上（运行时段点正确）
        rt = llm_manager.get_active_llm_runtime()
        self.assertEqual(rt["base_url"], "https://a.example/v1")
        self.assertEqual(rt["provider_id"], "prov-a")

    def test_delete_sibling_copy_allowed_active_copy_protected(self):
        self._seed_two_providers_same_model()
        llm_manager.activate_provider_model("prov-a", "glm-x")
        # 删 prov-b 的同名副本：允许
        self.assertTrue(llm_manager.delete_model("prov-b", "glm-x"))
        cfg = self._read_raw()
        self.assertEqual(cfg["active_model_id"], "glm-x")
        self.assertEqual(cfg["active_provider_id"], "prov-a")
        pa = next(p for p in cfg["providers"] if p["id"] == "prov-a")
        pb = next(p for p in cfg["providers"] if p["id"] == "prov-b")
        self.assertIn("glm-x", [m["id"] for m in pa["models"]])
        self.assertNotIn("glm-x", [m["id"] for m in pb["models"]])
        # 顶层缓存仍在且归属/凭据指向 prov-a
        flat = next(m for m in cfg["models"] if m["id"] == "glm-x")
        self.assertEqual(flat["provider_id"], "prov-a")
        # 删 prov-a（主脑名下）：拒绝
        with self.assertRaises(ValueError):
            llm_manager.delete_model("prov-a", "glm-x")
        # 旧全局路由（无供应商作用域）删主脑模型：仍拒绝
        with self.assertRaises(ValueError):
            llm_manager.delete_model("custom", "glm-x")

    def test_last_copy_of_non_active_model_deletes_flat_entry(self):
        self._seed_two_providers_same_model()
        llm_manager.activate_provider_model("prov-a", "glm-x")
        self._add_model("prov-a", "only-a")
        self._add_model("prov-b", "other-b")
        self.assertTrue(llm_manager.delete_model("prov-b", "other-b"))
        cfg = self._read_raw()
        self.assertNotIn("other-b", [m["id"] for m in cfg["models"]])
        self.assertIn("only-a", [m["id"] for m in cfg["models"]])

    def test_legacy_config_without_attribution_degrades_gracefully(self):
        # 构造无 active_provider_id 的存量配置：两家都挂 glm-x，主脑=glm-x，扁平归属 prov-a
        self._add_provider("prov-a", "https://a.example/v1", "key-a")
        self._add_provider("prov-b", "https://b.example/v1", "key-b")
        self._write_raw({
            "version": "3.2",
            "defaults_seeded": True,
            "active_model_id": "glm-x",
            "providers": [
                {"id": "prov-a", "name": "A", "base_url": "https://a.example/v1", "api_key": "key-a",
                 "api_format": "openai_chat", "enabled": True, "models": [{"id": "glm-x", "name": "glm-x"}]},
                {"id": "prov-b", "name": "B", "base_url": "https://b.example/v1", "api_key": "key-b",
                 "api_format": "openai_chat", "enabled": True, "models": [{"id": "glm-x", "name": "glm-x"}]},
            ],
            "models": [
                {"id": "glm-x", "name": "glm-x", "provider_id": "prov-a", "base_url": "https://a.example/v1"},
            ],
        })
        # 唯一可判定路径失效（双持有者 + 无显式归属）→ 扁平缓存归属兜底判定为 prov-a
        cfg = llm_manager.init_llm_config()
        self.assertEqual(cfg["active_provider_id"], "prov-a")
        self.assertTrue(llm_manager.delete_model("prov-b", "glm-x"))
        # 重新激活 prov-a 后一切照常
        llm_manager.activate_provider_model("prov-a", "glm-x")
        cfg2 = self._read_raw()
        self.assertEqual(cfg2["active_provider_id"], "prov-a")

    def test_ambiguous_attribution_blocks_scoped_delete(self):
        # 双持有者 + 扁平缓存也无归属（provider_id=custom）→ 无法判定，保守拦截
        self._write_raw({
            "version": "3.2",
            "defaults_seeded": True,
            "active_model_id": "glm-x",
            "providers": [
                {"id": "prov-a", "name": "A", "base_url": "https://a.example/v1", "api_key": "k",
                 "api_format": "openai_chat", "enabled": True, "models": [{"id": "glm-x", "name": "glm-x"}]},
                {"id": "prov-b", "name": "B", "base_url": "https://b.example/v1", "api_key": "k",
                 "api_format": "openai_chat", "enabled": True, "models": [{"id": "glm-x", "name": "glm-x"}]},
            ],
            "models": [
                {"id": "glm-x", "name": "glm-x", "provider_id": "custom", "base_url": "https://c.example/v1"},
            ],
        })
        with self.assertRaises(ValueError) as ctx:
            llm_manager.delete_model("prov-b", "glm-x")
        self.assertIn("无法判定", str(ctx.exception))

    def test_provider_guards_only_block_active_holder(self):
        self._seed_two_providers_same_model()
        llm_manager.activate_provider_model("prov-a", "glm-x")
        # 清空/删除非主脑供应商 prov-b：不再被同名模型误拦
        self.assertTrue(llm_manager.clear_provider_models("prov-b"))
        self.assertTrue(llm_manager.delete_provider("prov-b"))
        cfg = self._read_raw()
        self.assertEqual(cfg["active_model_id"], "glm-x")
        self.assertEqual(cfg["active_provider_id"], "prov-a")
        # 主脑供应商 prov-a 依旧受保护
        with self.assertRaises(ValueError):
            llm_manager.delete_provider("prov-a")

    def test_load_llm_config_is_active_badge_scoped_by_provider(self):
        self._seed_two_providers_same_model()
        llm_manager.activate_provider_model("prov-a", "glm-x")
        cfg = llm_manager.load_llm_config()
        badges = {
            p["id"]: [m["id"] for m in p["models"] if m.get("is_active")]
            for p in cfg["providers"]
        }
        self.assertEqual(badges["prov-a"], ["glm-x"])
        self.assertEqual(badges["prov-b"], [])
        self.assertEqual(cfg["active_provider_id"], "prov-a")

    def test_activate_unknown_copy_rejected(self):
        self._add_provider("prov-a", "https://a.example/v1")
        self._add_model("prov-a", "glm-x")
        with self.assertRaises(ValueError):
            llm_manager.activate_provider_model("prov-a", "not-there")


class UpModelProviderAttributionTests(ScopedLLMTestCase):
    def test_payload_provider_id_wins_over_custom_route(self):
        # 旧全局路由 provider_id="custom"，但 payload 携带真实归属 → 模型须落入该供应商
        self._add_provider("prov-a", "https://a.example/v1")
        llm_manager.upsert_model("custom", {"id": "glm-y", "name": "glm-y", "provider_id": "prov-a"})
        cfg = self._read_raw()
        pa = next(p for p in cfg["providers"] if p["id"] == "prov-a")
        self.assertIn("glm-y", [m["id"] for m in pa["models"]])
        flat = next(m for m in cfg["models"] if m["id"] == "glm-y")
        self.assertEqual(flat["provider_id"], "prov-a")


if __name__ == "__main__":
    unittest.main()

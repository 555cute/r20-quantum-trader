"""LLM 供应商凭据轮换联动与能力检测回归测试。

对应 v7.6.0 用户反馈两 Bug：
1. 修改密钥后原模型全部连不上，必须删掉重新添加；
2. deepseek 等纯文本模型被默认勾上视觉能力（"flash" 误判源）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEED = {
    "version": "3.1",
    "active_model_id": "deepseek-v4-flash",
    "active_reasoning_effort": "high",
    "thinking_timeout": 120.0,
    "providers": [{
        "id": "gw", "name": "测试网关", "type": "gw", "group": "其他", "enabled": True,
        "base_url": "https://gw.test/v1", "api_key": "sk-OLD-KEY", "api_format": "openai_chat",
        "api_path": "/chat/completions", "description": "", "models": [],
    }],
    "models": [{
        "id": "deepseek-v4-flash", "name": "DeepSeek V4", "provider_id": "gw", "provider_name": "测试网关",
        "base_url": "https://gw.test/v1", "api_key": "sk-OLD-KEY", "api_format": "openai_chat",
        "reasoning_type": "auto", "reasoning_effort": "high",
        "capabilities": ["chat", "tools"], "context_length": None, "description": "",
    }],
}


class CapabilityDetectionTests(unittest.TestCase):
    def setUp(self):
        from r20_backend.llm_manager import _detect_capabilities
        self.detect = _detect_capabilities

    def test_text_only_family_not_marked_vision(self):
        # deepseek 家族默认纯文本：即便名字含 flash 也不视觉
        for mid in ("deepseek-v4-flash", "deepseek-r1", "deepseek-v3"):
            self.assertNotIn("vision", self.detect(mid), f"纯文本模型被误标视觉: {mid}")
        # 未知家族且无显式视觉标记 → 保守不标视觉
        for mid in ("llama-3.1-70b-instruct", "kimi-k2", "mistral-large"):
            self.assertNotIn("vision", self.detect(mid), f"无视觉信号却误标: {mid}")

    def test_vision_family_models_detected(self):
        # 用户确认：该网关下 qwen / glm 的 flash 系列是视觉模型（家族感知，不靠 flash 词）
        for mid in ("qwen3.8-flash", "glm-5.3-flash", "gemini-3.8-flash-high",
                    "gpt-4o-mini", "claude-sonnet-4", "grok-4"):
            self.assertIn("vision", self.detect(mid), f"视觉家族模型漏标: {mid}")

    def test_explicit_vision_marker_overrides_text_family(self):
        # deepseek 是文本家族，但带显式 vision 标记的变体仍算视觉
        self.assertIn("vision", self.detect("deepseek-v4-flash-vision-exp"))
        self.assertIn("vision", self.detect("qwen3-vl-plus"))
        self.assertIn("vision", self.detect("glm-4v"))

    def test_vl_substring_inside_word_is_not_vision(self):
        self.assertNotIn("vision", self.detect("revlove-large"))


class LlmCredentialRotationTests(unittest.TestCase):
    def setUp(self):
        from tests.config_sandbox import isolate_config
        isolate_config(self)
        import r20_backend.llm_manager as lm
        import r20_backend.settings_store as ss
        import r20_backend.config as cfg
        import r20_gateway.secrets as sec
        self.lm = lm
        self.tmp = tempfile.TemporaryDirectory()
        #  tripwire：记录真实生产文件指纹，tearDown 校验测试绝无触碰
        self._real_file = ROOT / "data" / "llm_models.json"
        self._real_hash = self._real_file.stat() if self._real_file.exists() else None
        self._orig = [(lm.LLM_CONFIG_FILE, "llm_manager"), (ss.ENV_FILE, "settings_store")]
        lm.LLM_CONFIG_FILE = Path(self.tmp.name) / "llm_models.json"
        lm.LLM_PROVIDERS_FILE = lm.LLM_CONFIG_FILE  # 兼容别名必须同步 patch，否则端点写真实文件
        ss.ENV_FILE = Path(self.tmp.name) / ".env"
        self._orig_ld = cfg.load_dotenv
        cfg.load_dotenv = lambda path: None
        self._orig_les = cfg.load_encrypted_secrets
        cfg.load_encrypted_secrets = lambda *a, **k: None
        self._orig_save = sec.save_secrets
        sec.save_secrets = lambda d: None
        self._saved_env = {k: os.environ.pop(k, None) for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")}
        lm._atomic_write_json(lm.LLM_CONFIG_FILE, json.loads(json.dumps(SEED)))

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.lm.LLM_CONFIG_FILE = self._orig[0][0]
        import r20_backend.settings_store as ss
        ss.ENV_FILE = self._orig[1][0]
        import r20_backend.config as cfg
        cfg.load_dotenv = self._orig_ld
        cfg.load_encrypted_secrets = self._orig_les
        import r20_gateway.secrets as sec
        sec.save_secrets = self._orig_save
        self.tmp.cleanup()
        after = self._real_file.stat() if self._real_file.exists() else None
        self.assertEqual(after, self._real_hash, "测试触碰了生产 data/llm_models.json！")

    def _raw(self):
        return json.loads(self.lm.LLM_CONFIG_FILE.read_text(encoding="utf-8"))

    def test_stale_snapshot_key_is_healed_by_provider_authority(self):
        """磁盘上模型快照着旧键、供应商已换新键：init 解析必须以供应商为准。"""
        raw = self._raw()
        raw["providers"][0]["api_key"] = "sk-NEW-KEY"
        self.lm._atomic_write_json(self.lm.LLM_CONFIG_FILE, raw)
        config = self.lm.init_llm_config()
        model = next(m for m in config["models"] if m["id"] == "deepseek-v4-flash")
        self.assertEqual(model["api_key"], "sk-NEW-KEY", "供应商新键未覆盖历史快照旧键")
        runtime = self.lm.get_active_llm_runtime()
        self.assertEqual(runtime["api_key"], "sk-NEW-KEY")

    def test_provider_rotation_updates_models_and_env(self):
        """在供应商卡片轮换密钥：名下模型即刻生效，且激活模型的全局 env 同步回写。"""
        res = self.lm.upsert_provider({"id": "gw", "name": "测试网关", "base_url": "https://gw.test/v1",
                                       "api_key": "sk-ROTATED", "api_format": "openai_chat"})
        self.assertEqual(res["id"], "gw")
        config = self.lm.init_llm_config()
        model = next(m for m in config["models"] if m["id"] == "deepseek-v4-flash")
        self.assertEqual(model["api_key"], "sk-ROTATED")
        # 激活模型属于该供应商 → 全局 env/进程环境同步
        self.assertEqual(os.environ.get("LLM_API_KEY"), "sk-ROTATED")
        env_text = self.lm.LLM_CONFIG_FILE.parent.joinpath(".env").read_text(encoding="utf-8")
        self.assertIn("LLM_API_KEY=sk-ROTATED", env_text)

    def test_upsert_model_does_not_snapshot_provider_key(self):
        """新增模型不再把供应商密钥复制到模型条目（密钥只存供应商一处）。"""
        self.lm.upsert_model("gw", {"id": "glm-5.3-flash", "name": "GLM"})
        raw = self._raw()
        entry = next(m for m in raw["models"] if m["id"] == "glm-5.3-flash")
        self.assertFalse(entry.get("api_key"), "新模型不应再快照供应商密钥")
        config = self.lm.init_llm_config()
        model = next(m for m in config["models"] if m["id"] == "glm-5.3-flash")
        self.assertEqual(model["api_key"], "sk-OLD-KEY", "读取时应从供应商注入当前键")
        self.assertIn("vision", model["capabilities"], "glm 家族默认视觉（用户确认 glm-5.3-flash 为视觉模型）")

    def test_global_config_sync_updates_active_provider(self):
        """全局设置页改密钥（PUT /admin/config）必须同步到激活模型所属供应商。"""
        from fastapi.testclient import TestClient
        from r20_backend.admin_auth import AdminAuthStore
        import r20_backend.app as app_module
        orig_auth = app_module.admin_auth
        app_module.admin_auth = AdminAuthStore(Path(self.tmp.name) / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        try:
            client = TestClient(app_module.app)
            login = client.post("/api/v1/admin/auth/login",
                                json={"username": "admin", "password": "InitialAdmin123456"})
            headers = {"X-R20-Session": login.json()["session_token"]}
            res = client.put("/api/v1/admin/config", headers=headers,
                             json={"llm_api_key": "sk-GLOBAL-NEW"})
            self.assertEqual(res.status_code, 200, res.text)
            prov = next(p for p in self._raw()["providers"] if p["id"] == "gw")
            self.assertEqual(prov["api_key"], "sk-GLOBAL-NEW", "全局改密钥未同步供应商（active_provider_id 幽灵键 Bug 复发）")
        finally:
            app_module.admin_auth = orig_auth


if __name__ == "__main__":
    unittest.main()

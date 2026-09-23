"""LLM 配置与持久化存储（`r20_backend/llm/store.py`）残余分支收口测试 —— 第 339 刀。

本模块 893 行，是多供应商与模型管理的核心存储与运行时装配层：
- 配置初始化与平滑迁移（`init_llm_config`）：坏 JSON 容错、老默认项清洗、凭据权威源合并；
- 运行时解析（`get_active_llm_runtime` / `resolve_model_runtime`）：缺少出口报错、回退模型装配、超时类型安全；
- 一键激活与全局设置（`activate_provider_model` / `update_llm_settings`）：作用域判定、请求次数边界、回退链上限；
- 供应商与模型增删改（`upsert_model` / `delete_model` / `upsert_provider` / `toggle_provider` / `clear_provider_models` / `delete_provider`）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r20_backend.llm.store import (
    activate_provider_model,
    clear_provider_models,
    delete_model,
    delete_provider,
    get_active_llm_runtime,
    init_llm_config,
    load_llm_config,
    resolve_model_runtime,
    toggle_provider,
    update_llm_settings,
    upsert_model,
    upsert_provider,
)


class LlmStoreTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_llm_store_test_")
        self.addCleanup(self.tmp.cleanup)
        self.config_file = Path(self.tmp.name) / "llm_models.json"

        # 严防测试向生产 data/r20_secrets.enc 与 .env 写入
        p_sec = patch("r20_gateway.secrets.save_secrets")
        self.mock_save_secrets = p_sec.start()
        self.addCleanup(p_sec.stop)

        p_env = patch("r20_backend.settings_store.update_env")
        self.mock_update_env = p_env.start()
        self.addCleanup(p_env.stop)

        p_cfg = patch("r20_backend.config.refresh_settings")
        self.mock_refresh_settings = p_cfg.start()
        self.addCleanup(p_cfg.stop)

    def _write_config(self, cfg: dict) -> None:
        self.config_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def _reload(self) -> dict:
        if self.config_file.exists():
            return json.loads(self.config_file.read_text(encoding="utf-8"))
        return {}

    # -------------------------------------------------------------------------
    # 1. 配置加载与初始化 (init_llm_config & load_llm_config)
    # -------------------------------------------------------------------------
    def test_init_llm_config_corrupt_json_file_handled(self):
        # 配置文件损坏（坏 JSON）-> 异常被捕获，data = {} 重新初始化
        self.config_file.write_text("{ broken json", encoding="utf-8")
        cfg = init_llm_config(self.config_file)
        self.assertIn("providers", cfg)
        self.assertIn("models", cfg)
        self.assertTrue(cfg.get("defaults_seeded"))

    def test_init_llm_config_existing_provider_without_id_skipped(self):
        # providers 列表中混入无 id 的脏对象 -> continue 跳过
        self._write_config({"providers": [{"name": "no_id"}, {"id": "p1", "name": "Valid"}], "defaults_seeded": True})
        cfg = init_llm_config(self.config_file)
        p_ids = [p["id"] for p in cfg["providers"]]
        self.assertIn("p1", p_ids)
        self.assertEqual(len(p_ids), 1)

    def test_init_llm_config_openai_provider_missing_base_url_filled(self):
        # 种子迁移：已有 openai 供应商但显式 base_url 为空 -> 从 settings / 环境变量填入
        self._write_config({
            "providers": [{"id": "openai", "name": "OpenAI", "base_url": "", "api_key": "k"}],
            "defaults_seeded": False,
        })
        with patch("r20_backend.config.settings.llm_base_url", "https://custom.openai.com/v1"):
            cfg = init_llm_config(self.config_file)
        prov = next(p for p in cfg["providers"] if p["id"] == "openai")
        self.assertEqual(prov["base_url"], "https://custom.openai.com/v1")

    def test_init_llm_config_provider_model_missing_id_skipped(self):
        # 供应商下的 models 列表中混入缺少 id 的对象 -> continue
        self._write_config({
            "providers": [{"id": "p1", "models": [{"name": "no-id"}, {"id": "m1", "name": "Model 1"}]}],
            "defaults_seeded": True,
        })
        cfg = init_llm_config(self.config_file)
        m_ids = [m["id"] for m in cfg["models"]]
        self.assertIn("m1", m_ids)
        self.assertEqual(len(m_ids), 1)

    def test_init_llm_config_flat_model_inherits_provider_api_format(self):
        # 顶层模型缺少 api_format -> 继承对应供应商的 api_format
        self._write_config({
            "providers": [{"id": "p1", "api_format": "claude_messages", "api_key": "k", "base_url": "https://api.p1.com"}],
            "models": [{"id": "custom-m", "provider_id": "p1"}],
            "defaults_seeded": True,
        })
        cfg = init_llm_config(self.config_file)
        m = next(x for x in cfg["models"] if x["id"] == "custom-m")
        self.assertEqual(m["api_format"], "claude_messages")

    # -------------------------------------------------------------------------
    # 2. 运行时凭据解析 (get_active_llm_runtime & resolve_model_runtime)
    # -------------------------------------------------------------------------
    def test_get_active_llm_runtime_inherits_credentials_from_provider(self):
        # target_model 缺少 api_key / base_url -> 从对应供应商继承
        cfg = {
            "active_model_id": "m1",
            "active_reasoning_effort": "medium",
            "models": [{"id": "m1", "provider_id": "prov1", "provider_name": "自定义"}],
            "providers": [{"id": "prov1", "name": "CustomProv", "base_url": "https://api.prov1.com/v1", "api_key": "sec_key"}],
        }
        with patch("r20_backend.config.settings.llm_base_url", ""):
            with patch("r20_backend.config.settings.llm_api_key", ""):
                rt = get_active_llm_runtime(cfg)
        self.assertEqual(rt["model"], "m1")
        self.assertEqual(rt["base_url"], "https://api.prov1.com/v1")
        self.assertEqual(rt["api_key"], "sec_key")
        self.assertEqual(rt["provider_name"], "CustomProv")

    def test_get_active_llm_runtime_unconfigured_base_url_raises(self):
        # 既无配置也无环境变量中的 LLM_BASE_URL -> 明确抛出 RuntimeError
        with patch.dict("os.environ", {"LLM_BASE_URL": ""}, clear=False):
            with patch("r20_backend.config.settings.llm_base_url", ""):
                with self.assertRaises(RuntimeError) as ctx:
                    get_active_llm_runtime({"models": [], "providers": []})
                self.assertIn("LLM 出口未配置", str(ctx.exception))

    def test_resolve_model_runtime_inherits_provider_base_url_and_api_key(self):
        cfg = {
            "models": [{"id": "fallback-1", "provider_id": "prov-alt"}],
            "providers": [{"id": "prov-alt", "base_url": "https://alt.api.com", "api_key": "alt_key"}],
        }
        res = resolve_model_runtime(cfg, "fallback-1")
        self.assertIsNotNone(res)
        self.assertEqual(res["base_url"], "https://alt.api.com")
        self.assertEqual(res["api_key"], "alt_key")

    def test_resolve_model_runtime_returns_none_when_no_base_url(self):
        cfg = {
            "models": [{"id": "bad-model", "provider_id": "missing-prov"}],
            "providers": [],
        }
        self.assertIsNone(resolve_model_runtime(cfg, "bad-model"))

    def test_resolve_model_runtime_invalid_timeout_falls_back(self):
        cfg = {
            "thinking_timeout": 60.0,
            "models": [{"id": "m1", "base_url": "https://api.m.com", "thinking_timeout": "invalid-float"}],
        }
        res = resolve_model_runtime(cfg, "m1")
        self.assertIsNotNone(res)
        self.assertEqual(res["thinking_timeout"], 60.0)

    # -------------------------------------------------------------------------
    # 3. 模型激活与设置更新 (activate_provider_model & update_llm_settings)
    # -------------------------------------------------------------------------
    def test_activate_provider_model_unknown_scoped_provider_raises(self):
        self._write_config({"providers": [{"id": "p1"}], "models": []})
        with self.assertRaises(ValueError) as ctx:
            activate_provider_model(self.config_file, self._reload, "nonexistent", "m1")
        self.assertIn("供应商 nonexistent 未找到，无法激活", str(ctx.exception))

    def test_activate_provider_model_found_in_provider_models_list(self):
        # 目标模型不在顶层 models 中，但在供应商嵌套 models 中 -> 自动提取并设为主脑
        self._write_config({
            "providers": [{
                "id": "p1", "name": "P1", "base_url": "https://api.p1.com", "api_key": "k",
                "models": [{"id": "nested-m", "name": "Nested Model", "reasoning_type": "deepseek_reasoner"}],
            }],
            "models": [],
        })
        with patch("r20_backend.settings_store.update_env"):
            with patch("r20_backend.config.refresh_settings"):
                res = activate_provider_model(self.config_file, self._reload, "custom", "nested-m")
        self.assertEqual(res["active_model_id"], "nested-m")
        self.assertEqual(res["active_provider_id"], "p1")
        cfg = self._reload()
        self.assertEqual(cfg["active_model_id"], "nested-m")

    def test_activate_provider_model_invalid_effort_falls_back_to_auto(self):
        self._write_config({
            "providers": [{"id": "p1", "base_url": "https://api.p1.com"}],
            "models": [{"id": "m1", "provider_id": "p1"}],
        })
        with patch("r20_backend.settings_store.update_env"):
            with patch("r20_backend.config.refresh_settings"):
                res = activate_provider_model(self.config_file, self._reload, "p1", "m1", reasoning_effort="ultra_extreme")
        self.assertEqual(res["active_reasoning_effort"], "auto")

    def test_update_llm_settings_request_attempts_validation(self):
        self._write_config({"models": []})
        # 非整数
        with self.assertRaises(ValueError) as ctx1:
            update_llm_settings(self.config_file, self._reload, request_attempts="three")  # type: ignore
        self.assertIn("请求次数必须是整数", str(ctx1.exception))

        # 超出范围 (<1 或 >10)
        with self.assertRaises(ValueError) as ctx2:
            update_llm_settings(self.config_file, self._reload, request_attempts=0)
        self.assertIn("请求次数需在 1~10 之间", str(ctx2.exception))

    def test_update_llm_settings_fallback_models_validation_and_cleaning(self):
        self._write_config({
            "active_model_id": "m1",
            "models": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}, {"id": "m4"}, {"id": "m5"}, {"id": "m6"}, {"id": "m7"}],
        })
        # 空白项跳过，自身过滤
        res = update_llm_settings(self.config_file, self._reload, fallback_model_ids=["  ", "m1", "m2", "m2"])
        self.assertEqual(res["fallback_model_ids"], ["m2"])

        # 超过上限 (MAX_FALLBACK_MODELS = 5)
        with self.assertRaises(ValueError) as ctx:
            update_llm_settings(self.config_file, self._reload, fallback_model_ids=["m2", "m3", "m4", "m5", "m6", "m7"])
        self.assertIn("回退模型最多 5 个", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 4. 供应商与模型 CRUD (upsert, delete, toggle, clear)
    # -------------------------------------------------------------------------
    def test_upsert_model_missing_id_raises(self):
        self._write_config({})
        with self.assertRaises(ValueError) as ctx:
            upsert_model(self.config_file, self._reload, "p1", {"name": "No ID"})
        self.assertIn("模型 ID 不能为空", str(ctx.exception))

    def test_upsert_model_inherits_active_base_url_when_missing(self):
        self._write_config({
            "models": [{"id": "cur-active", "base_url": "https://api.openai.com/v1"}],
            "active_model_id": "cur-active",
            "providers": [{"id": "p1", "base_url": ""}],
        })
        res = upsert_model(self.config_file, self._reload, "p1", {"id": "m-new", "api_format": "invalid_fmt"})
        self.assertEqual(res["base_url"], "https://api.openai.com/v1")
        self.assertEqual(res["api_format"], "openai_chat")

    def test_upsert_model_updates_active_model_effort(self):
        self._write_config({
            "active_model_id": "m1",
            "active_reasoning_effort": "low",
            "models": [{"id": "m1", "base_url": "https://api.com"}],
            "providers": [{"id": "p1", "base_url": "https://api.com"}],
        })
        with patch("r20_backend.settings_store.update_env") as mock_env:
            upsert_model(self.config_file, self._reload, "p1", {"id": "m1", "base_url": "https://api.com", "default_effort": "high"})
            mock_env.assert_called_with({"LLM_REASONING_EFFORT": "high"})
        cfg = self._reload()
        self.assertEqual(cfg["active_reasoning_effort"], "high")

    def test_delete_model_scoped_provider_not_found_raises(self):
        self._write_config({"providers": []})
        with self.assertRaises(ValueError) as ctx:
            delete_model(self.config_file, self._reload, "ghost_prov", "m1")
        self.assertIn("供应商 ghost_prov 未找到", str(ctx.exception))

    def test_delete_model_not_found_returns_false(self):
        self._write_config({"providers": [{"id": "p1", "models": []}], "models": []})
        self.assertFalse(delete_model(self.config_file, self._reload, "p1", "not_exist"))

    def test_upsert_provider_auto_formats_and_paths(self):
        # 自动推断 claude_messages 与 /messages
        self._write_config({})
        res1 = upsert_provider(self.config_file, self._reload, {
            "id": "anthropic-prov",
            "base_url": "https://api.anthropic.com",
            "api_format": "",
        })
        cfg = self._reload()
        p1 = next(x for x in cfg["providers"] if x["id"] == "anthropic-prov")
        self.assertEqual(p1["api_format"], "claude_messages")
        self.assertEqual(p1["api_path"], "/messages")

        # 自动配置 openai_responses 与 /responses
        res2 = upsert_provider(self.config_file, self._reload, {
            "id": "resp-prov",
            "base_url": "https://api.responses.com",
            "api_format": "openai_responses",
            "api_path": "",
        })
        cfg2 = self._reload()
        p2 = next(x for x in cfg2["providers"] if x["id"] == "resp-prov")
        self.assertEqual(p2["api_path"], "/responses")
        self.assertTrue(p2["response_api_enabled"])

    def test_upsert_provider_auto_generates_pid_from_name(self):
        self._write_config({})
        upsert_provider(self.config_file, self._reload, {
            "name": "My Custom Provider!",
            "base_url": "https://api.mycustom.com",
        })
        cfg = self._reload()
        prov = cfg["providers"][0]
        self.assertEqual(prov["id"], "mycustomprovider")

    def test_upsert_provider_invalid_base_url_raises(self):
        self._write_config({})
        with self.assertRaises(ValueError) as ctx:
            upsert_provider(self.config_file, self._reload, {
                "id": "p1",
                "base_url": "ftp://invalid-url.com",
            })
        self.assertIn("供应商 Base URL 必须以 http:// 或 https:// 开头", str(ctx.exception))

    def test_upsert_provider_updates_existing_enabled_state(self):
        self._write_config({"providers": [{"id": "p1", "enabled": False, "base_url": "https://api.p1.com"}]})
        upsert_provider(self.config_file, self._reload, {
            "id": "p1",
            "base_url": "https://api.p1.com",
            "enabled": True,
        })
        cfg = self._reload()
        self.assertTrue(cfg["providers"][0]["enabled"])

    def test_toggle_provider_operations(self):
        self._write_config({"providers": [{"id": "p1", "enabled": False}]})
        # 翻转切换
        res1 = toggle_provider(self.config_file, self._reload, "p1", enabled=None)
        self.assertTrue(res1["enabled"])

        # 显式设值
        res2 = toggle_provider(self.config_file, self._reload, "p1", enabled=False)
        self.assertFalse(res2["enabled"])

        # 供应商不存在
        with self.assertRaises(ValueError) as ctx:
            toggle_provider(self.config_file, self._reload, "ghost")
        self.assertIn("供应商 ghost 未找到", str(ctx.exception))

    def test_clear_provider_models_provider_not_found_returns_false(self):
        self._write_config({"providers": []})
        self.assertFalse(clear_provider_models(self.config_file, self._reload, "ghost"))

    def test_delete_provider_not_found_returns_false(self):
        self._write_config({"providers": []})
        self.assertFalse(delete_provider(self.config_file, self._reload, "ghost"))

    # -------------------------------------------------------------------------
    # 5. 边缘残余分支 (Edge case fallbacks & ImportError handling)
    # -------------------------------------------------------------------------
    def test_get_active_llm_runtime_prov_missing_id_defaults_to_openai(self):
        # target_model 缺少 provider_id，匹配到的 prov 同样缺少 id -> 回退默认 "openai"
        cfg = {
            "active_model_id": "m1",
            "models": [{"id": "m1", "provider_id": "", "base_url": "https://a.com"}],
            "providers": [{"id": "", "base_url": "https://a.com"}],
        }
        rt = get_active_llm_runtime(cfg)
        self.assertEqual(rt["provider_id"], "openai")

    def test_upsert_model_prov_missing_id_defaults_to_openai(self):
        self._write_config({"providers": [{"name": "p_no_id", "id": "", "base_url": "https://a.com"}]})
        res = upsert_model(self.config_file, self._reload, "", {"id": "m_test", "provider_name": "p_no_id", "base_url": "https://a.com"})
        self.assertEqual(res["provider_id"], "openai")

    def test_activate_provider_model_save_secrets_import_error_handled(self):
        import sys
        self._write_config({
            "providers": [{"id": "p1", "base_url": "https://api.p1.com"}],
            "models": [{"id": "m1", "provider_id": "p1"}],
        })
        with patch.dict(sys.modules, {"r20_gateway.secrets": None}):
            with patch("r20_backend.settings_store.update_env"):
                with patch("r20_backend.config.refresh_settings"):
                    res = activate_provider_model(self.config_file, self._reload, "p1", "m1")
        self.assertEqual(res["active_model_id"], "m1")

    def test_upsert_model_update_env_failure_suppressed(self):
        self._write_config({
            "active_model_id": "m1",
            "active_reasoning_effort": "low",
            "models": [{"id": "m1", "base_url": "https://api.com"}],
            "providers": [{"id": "p1", "base_url": "https://api.com"}],
        })
        with patch("r20_backend.settings_store.update_env", side_effect=RuntimeError("env update error")):
            upsert_model(self.config_file, self._reload, "p1", {"id": "m1", "base_url": "https://api.com", "default_effort": "high"})
        cfg = self._reload()
        self.assertEqual(cfg["active_reasoning_effort"], "high")

    def test_upsert_provider_save_secrets_import_error_handled(self):
        import sys
        self._write_config({
            "active_model_id": "m1",
            "providers": [{"id": "p1", "base_url": "https://api.p1.com"}],
            "models": [{"id": "m1", "provider_id": "p1"}],
        })
        with patch.dict(sys.modules, {"r20_gateway.secrets": None}):
            with patch("r20_backend.settings_store.update_env"):
                with patch("r20_backend.config.refresh_settings"):
                    res = upsert_provider(self.config_file, self._reload, {"id": "p1", "base_url": "https://api.p1.com", "api_key": "new_k"})
        self.assertEqual(res["id"], "p1")

    def test_upsert_provider_update_env_failure_suppressed(self):
        self._write_config({
            "active_model_id": "m1",
            "providers": [{"id": "p1", "base_url": "https://api.p1.com"}],
            "models": [{"id": "m1", "provider_id": "p1"}],
        })
        with patch("r20_backend.settings_store.update_env", side_effect=RuntimeError("env fail")):
            res = upsert_provider(self.config_file, self._reload, {"id": "p1", "base_url": "https://api.p1.com", "api_key": "k"})
        self.assertEqual(res["id"], "p1")


if __name__ == "__main__":
    unittest.main()

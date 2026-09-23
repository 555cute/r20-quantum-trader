"""模型供应商路径拼接与活跃模型归属解析（`r20_backend/llm/providers.py`）全量分支收口测试 —— 第 361 刀。

本模块 84 行，负责网关基址解析、API 端点后缀拼装与活跃模型所属供应商判定：
- URL 路径解析容错（`_url_path_of`）：URL 解析异常时安全返回 `/unparseable`；
- API 端点后缀拼接（`_join_api_path`）：
  - 路径已包含目标后缀直接原样返回；
  - 裸域名（无路径或仅有 `/`）兼容补入 `/v1`；
  - 带特定路径（如智谱 `/api/paas/v4`）直接拼接协议后缀。
- 活跃供应商解析（`_resolve_active_provider_id`）：
  - `active_model_id` 为空返回空串；
  - 多供应商挂同名模型时，依顶层扁平模型配置中的 `provider_id` 进行解析；
  - 扁平配置不匹配或缺失时保守返回空串。
- 供应商持有模型判定（`_provider_holds_active_model`）：
  - 未配置活跃模型时返回 False。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from r20_backend.llm.providers import (
    _join_api_path,
    _provider_holds_active_model,
    _resolve_active_provider_id,
    _url_path_of,
)


class LLMProvidersTailsTests(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. URL 解析与 API 端点拼接 (_url_path_of & _join_api_path)
    # -------------------------------------------------------------------------
    def test_url_path_of_exception_returns_unparseable(self):
        # urlparse 发生异常时按有路径处理，安全返回 /unparseable (line 20)
        with patch("r20_backend.llm.providers.urlparse", side_effect=ValueError("bad url")):
            self.assertEqual(_url_path_of("http://invalid-url"), "/unparseable")

    def test_join_api_path_already_ends_with_suffix(self):
        # Base URL 已携带目标后缀时原样返回 (lines 26-27)
        full = "https://api.openai.com/v1/chat/completions"
        self.assertEqual(_join_api_path(full, "/chat/completions"), full)

    def test_join_api_path_bare_domain_appends_v1(self):
        # 裸域名无路径时自动补入 /v1 后缀 (lines 28-29)
        bare = "https://api.deepseek.com"
        self.assertEqual(_join_api_path(bare, "/chat/completions"), "https://api.deepseek.com/v1/chat/completions")
        bare_slash = "https://api.deepseek.com/"
        self.assertEqual(_join_api_path(bare_slash, "/chat/completions"), "https://api.deepseek.com/v1/chat/completions")

    def test_join_api_path_custom_versioned_path(self):
        # 具备显式版本路径的 URL 直接拼接协议后缀，不再强插 /v1 (line 30)
        url = "https://open.bigmodel.cn/api/paas/v4"
        self.assertEqual(_join_api_path(url, "/chat/completions"), "https://open.bigmodel.cn/api/paas/v4/chat/completions")

    # -------------------------------------------------------------------------
    # 2. 活跃模型供应商解析 (_resolve_active_provider_id)
    # -------------------------------------------------------------------------
    def test_resolve_active_provider_id_empty_model_returns_empty(self):
        # active_model_id 为空时返回空串 (lines 48-50)
        self.assertEqual(_resolve_active_provider_id({"active_model_id": ""}), "")
        self.assertEqual(_resolve_active_provider_id({}), "")

    def test_resolve_active_provider_id_no_holders_returns_empty(self):
        # 模型未嵌套在任何供应商下时返回空串 (lines 53-56)
        cfg = {"active_model_id": "m1", "providers": []}
        self.assertEqual(_resolve_active_provider_id(cfg), "")

    def test_resolve_active_provider_id_explicit_matching_pid(self):
        # 配置了有效的 active_provider_id 且在持有人列表中时返回该 ID (lines 57-59)
        cfg = {
            "active_model_id": "m1",
            "active_provider_id": "prov_custom",
            "providers": [{"id": "prov_custom", "models": [{"id": "m1"}]}],
        }
        self.assertEqual(_resolve_active_provider_id(cfg), "prov_custom")

    def test_resolve_active_provider_id_single_holder(self):
        # 仅有单个供应商持有该模型时自动返回该供应商 (lines 60-61)
        cfg = {
            "active_model_id": "m1",
            "active_provider_id": "",
            "providers": [{"id": "prov_single", "models": [{"id": "m1"}]}],
        }
        self.assertEqual(_resolve_active_provider_id(cfg), "prov_single")

    def test_resolve_active_provider_id_multiple_holders_flat_pid_fallback(self):
        # 多个供应商挂同名模型，依据 models 列表中的 provider_id 进行匹配 (lines 62-67)
        cfg = {
            "active_model_id": "deepseek-chat",
            "active_provider_id": "",
            "providers": [
                {"id": "prov_a", "models": [{"id": "deepseek-chat"}]},
                {"id": "prov_b", "models": [{"id": "deepseek-chat"}]},
            ],
            "models": [{"id": "deepseek-chat", "provider_id": "prov_b"}],
        }
        self.assertEqual(_resolve_active_provider_id(cfg), "prov_b")

    def test_resolve_active_provider_id_multiple_holders_flat_pid_unmatched(self):
        # models 列表中的 provider_id 不在持有人中时返回空串 (lines 66-68)
        cfg = {
            "active_model_id": "deepseek-chat",
            "active_provider_id": "",
            "providers": [
                {"id": "prov_a", "models": [{"id": "deepseek-chat"}]},
                {"id": "prov_b", "models": [{"id": "deepseek-chat"}]},
            ],
            "models": [{"id": "deepseek-chat", "provider_id": "prov_unregistered"}],
        }
        self.assertEqual(_resolve_active_provider_id(cfg), "")

    # -------------------------------------------------------------------------
    # 3. 供应商持有模型判定 (_provider_holds_active_model)
    # -------------------------------------------------------------------------
    def test_provider_holds_active_model_empty_active_model_returns_false(self):
        # active_model_id 未配置时返回 False (lines 78-79)
        self.assertFalse(_provider_holds_active_model({"active_model_id": ""}, "prov_a"))
        self.assertFalse(_provider_holds_active_model({}, "prov_a"))

    def test_provider_holds_active_model_not_holding_returns_false(self):
        # 供应商未持有该模型时返回 False (lines 80-82)
        cfg = {
            "active_model_id": "m1",
            "providers": [{"id": "prov_a", "models": []}],
        }
        self.assertFalse(_provider_holds_active_model(cfg, "prov_a"))

    def test_provider_holds_active_model_active_pid_matches(self):
        # 供应商持有该模型且与当前活跃供应商匹配或未指定时返回 True (lines 83-84)
        cfg_single = {
            "active_model_id": "m1",
            "providers": [{"id": "prov_a", "models": [{"id": "m1"}]}],
        }
        self.assertTrue(_provider_holds_active_model(cfg_single, "prov_a"))

        # 多供应商挂同名模型，仅匹配活跃的那个供应商
        cfg_multi = {
            "active_model_id": "m1",
            "active_provider_id": "prov_b",
            "providers": [
                {"id": "prov_a", "models": [{"id": "m1"}]},
                {"id": "prov_b", "models": [{"id": "m1"}]},
            ],
        }
        self.assertFalse(_provider_holds_active_model(cfg_multi, "prov_a"))
        self.assertTrue(_provider_holds_active_model(cfg_multi, "prov_b"))


if __name__ == "__main__":
    unittest.main()

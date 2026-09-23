"""LLM 配置归一化与归属判定（`r20_backend/llm/store_normalize.py`）残余分支收口测试 —— 第 372 刀。

本模块 195 行，负责 LLM 配置文档归一化、主脑供应商凭据继承与回退链净化：
- 扁平模型归属遍历跳过（`resolve_brain_provider_attribution` 遍历 `flat_models` 时跳过非主脑模型条目）；
- 环境变量重试次数格式异常回退（`finalize_config_document` 遇非法 `LLM_REQUEST_ATTEMPTS` 环境变量时安全捕获并置 0 回退默认值）。
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from r20_backend.llm.store_normalize import (
    finalize_config_document,
    resolve_brain_provider_attribution,
)


class LLMStoreNormalizeTailsTests(unittest.TestCase):
    def test_resolve_brain_provider_attribution_skips_non_matching_models(self):
        # 遍历扁平模型列表时，通过 continue 跳过非主脑模型，仅挂载目标主脑模型 (line 41)
        flat_models = [
            {"id": "other_model_1"},
            {"id": "active_brain_model"},
            {"id": "other_model_2"},
        ]
        merged_providers = [
            {
                "id": "prov_deepseek",
                "name": "DeepSeek Official",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-123",
                "models": [{"id": "active_brain_model"}],
            }
        ]

        pid = resolve_brain_provider_attribution(
            _resolve_active_provider_id=lambda cfg: "prov_deepseek",
            active_m_id="active_brain_model",
            data={"active_provider_id": "prov_deepseek"},
            merged_providers=merged_providers,
            flat_models=flat_models,
        )

        self.assertEqual(pid, "prov_deepseek")
        self.assertNotIn("provider_id", flat_models[0])
        self.assertEqual(flat_models[1]["provider_id"], "prov_deepseek")
        self.assertEqual(flat_models[1]["base_url"], "https://api.deepseek.com")
        self.assertNotIn("provider_id", flat_models[2])

    def test_finalize_config_document_env_attempts_invalid_integer_handled(self):
        # 环境变量 LLM_REQUEST_ATTEMPTS 包含非整数非法字符串时安全捕获 ValueError (lines 73-74)
        with patch.dict(os.environ, {"LLM_REQUEST_ATTEMPTS": "invalid_integer_string"}):
            doc = finalize_config_document(
                DEFAULT_REQUEST_ATTEMPTS=2,
                List=list,
                MAX_FALLBACK_MODELS=5,
                MAX_REQUEST_ATTEMPTS=10,
                MIN_REQUEST_ATTEMPTS=1,
                _atomic_write_json=lambda p, d: None,
                active_effort="auto",
                active_m_id="active_model",
                active_pid="prov_1",
                config_file=None,
                data={},
                flat_models=[{"id": "active_model"}],
                merged_providers=[],
                os=os,
                thinking_timeout=30.0,
            )
            # 环境变量异常回退为 0 后，最终采用 DEFAULT_REQUEST_ATTEMPTS (2)
            self.assertEqual(doc["request_attempts"], 2)


if __name__ == "__main__":
    unittest.main()

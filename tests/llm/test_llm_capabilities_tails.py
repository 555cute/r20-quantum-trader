"""大模型能力矩阵与API格式识别（`r20_backend/llm/capabilities.py`）全量分支收口测试 —— 第 360 刀。

本模块 64 行，负责大模型思考类型检测、多模态能力判定与 API 协议格式识别纯函数：
- 思考推理类型识别（`_detect_reasoning_type`）：
  - `deepseek_reasoner`：包含 `deepseek-reasoner`、`deepseek-r1`、`-r1`；
  - `standard_effort`：`o1`/`o3`/`o4`、`gemini`、`claude-3-7`、`qwq`、`qwen3`/`qwen-3` 系列；
  - `none`：`chat`、`gpt-4o`、`gpt-3`、`qwen`、`llama` 常规无显式思考模型；
  - `auto`：未知自定义模型回退 auto。
- 模型能力标签识别（`_detect_capabilities`）：
  - 基础 `chat` 标配；
  - 视觉能力（`vision`）：多模态显式标记、多模态家族（gemini, claude, gpt-4o, qwen 等）对冲纯文本家族（deepseek 纯文本）；
  - 工具能力（`tools`）：排除 `-r1-distill` 与 `-thinking` 后赋予；
  - 推理能力（`reasoning`）：识别 reasoner, r1, o1, o3, o4, gpt-5, gpt-6, thinking, qwq 等。
- API 协议格式识别（`_detect_api_format`）：
  - `claude_messages`：anthropic.com 域名或 claude 接口；
  - `openai_responses`：含 responses 端点；
  - `openai_chat`：默认标准 OpenAI Chat 协议。
"""
from __future__ import annotations

import unittest

from r20_backend.llm.capabilities import (
    _detect_api_format,
    _detect_capabilities,
    _detect_reasoning_type,
)


class LLMCapabilitiesTailsTests(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. 思考链类型检测 (_detect_reasoning_type)
    # -------------------------------------------------------------------------
    def test_detect_reasoning_type_deepseek(self):
        # 覆盖 line 15
        self.assertEqual(_detect_reasoning_type("deepseek-reasoner"), "deepseek_reasoner")
        self.assertEqual(_detect_reasoning_type("deepseek-r1"), "deepseek_reasoner")
        self.assertEqual(_detect_reasoning_type("custom-model-r1"), "deepseek_reasoner")

    def test_detect_reasoning_type_standard_effort(self):
        # 覆盖 line 28
        self.assertEqual(_detect_reasoning_type("o1-preview"), "standard_effort")
        self.assertEqual(_detect_reasoning_type("openai/o3-mini"), "standard_effort")
        self.assertEqual(_detect_reasoning_type("gemini-2.0-flash-thinking"), "standard_effort")
        self.assertEqual(_detect_reasoning_type("claude-3-7-sonnet"), "standard_effort")
        self.assertEqual(_detect_reasoning_type("qwq-32b-preview"), "standard_effort")
        self.assertEqual(_detect_reasoning_type("qwen3-72b"), "standard_effort")
        self.assertEqual(_detect_reasoning_type("qwen-3-vl"), "standard_effort")

    def test_detect_reasoning_type_none(self):
        # 覆盖 line 30
        self.assertEqual(_detect_reasoning_type("gpt-4o"), "none")
        self.assertEqual(_detect_reasoning_type("gpt-3.5-turbo"), "none")
        self.assertEqual(_detect_reasoning_type("qwen-max"), "none")
        self.assertEqual(_detect_reasoning_type("llama-3.1-70b-chat"), "none")

    def test_detect_reasoning_type_auto_fallback(self):
        # 覆盖 line 31
        self.assertEqual(_detect_reasoning_type("mistral-large-2407"), "auto")

    # -------------------------------------------------------------------------
    # 2. 多模态与能力标签检测 (_detect_capabilities)
    # -------------------------------------------------------------------------
    def test_detect_capabilities_vision_and_reasoning(self):
        # 显式视觉与推理模型
        caps = _detect_capabilities("gemini-2.0-pro-exp-02-05")
        self.assertIn("chat", caps)
        self.assertIn("vision", caps)
        self.assertIn("tools", caps)

        # 蒸馏纯推理模型禁用 tools
        caps_r1 = _detect_capabilities("deepseek-r1-distill-qwen-32b")
        self.assertIn("chat", caps_r1)
        self.assertIn("reasoning", caps_r1)
        self.assertNotIn("tools", caps_r1)

    # -------------------------------------------------------------------------
    # 3. API 协议格式识别 (_detect_api_format)
    # -------------------------------------------------------------------------
    def test_detect_api_format_claude_messages(self):
        # 覆盖 lines 60-61
        self.assertEqual(_detect_api_format("https://api.anthropic.com/v1", "claude-3-5-sonnet"), "claude_messages")
        self.assertEqual(_detect_api_format("https://my-proxy.com/claude/v1", "model"), "claude_messages")
        self.assertEqual(_detect_api_format("https://my-proxy.com/v1/messages", "claude-3-opus"), "claude_messages")

    def test_detect_api_format_openai_responses(self):
        # 覆盖 lines 62-63
        self.assertEqual(_detect_api_format("https://api.openai.com/v1/responses", "gpt-4o"), "openai_responses")

    def test_detect_api_format_openai_chat_default(self):
        # 覆盖 line 64
        self.assertEqual(_detect_api_format("https://api.deepseek.com/v1/chat/completions", "deepseek-chat"), "openai_chat")


if __name__ == "__main__":
    unittest.main()

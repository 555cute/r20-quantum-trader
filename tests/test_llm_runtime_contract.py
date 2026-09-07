"""Contract test: the LLM runtime payload keys consumed by the frontend.

背景（v7.5.4 修复）：后台总览「决策主脑模型」卡片曾读取 `llm_runtime.active_model`，
而后端 `get_active_llm_runtime()` 实际返回的键是 `model` / `reasoning_effort`。
键名漂移使该表达式恒为 undefined，前端因此永远落到硬编码字面量，切换模型不生效。
本测试锁定这份跨端契约，防止任何一方再次单方面改键名。
"""
from __future__ import annotations

import unittest

from r20_backend.llm_manager import get_active_llm_runtime

# 前端 OverviewPage.vue / SelfEvolutionLab.vue / stores/dashboard.ts 实际读取的键
FRONTEND_CONSUMED_KEYS = ("model", "provider_name", "reasoning_effort", "api_format")


class LlmRuntimeContractTests(unittest.TestCase):
    def test_runtime_exposes_keys_frontend_reads(self):
        runtime = get_active_llm_runtime()
        for key in FRONTEND_CONSUMED_KEYS:
            self.assertIn(key, runtime, f"后端 llm_runtime 缺少前端读取的键：{key}")

    def test_runtime_does_not_use_stale_active_prefixed_aliases(self):
        """曾导致界面冻结在硬编码模型名的错误键名不得重新出现。"""
        runtime = get_active_llm_runtime()
        for stale in ("active_model", "active_reasoning_effort", "active_provider_name"):
            self.assertNotIn(stale, runtime, f"检测到已废弃键名 {stale}，前端读取会恒为 undefined")

    def test_no_hardcoded_model_name_in_source(self):
        """源码与前端不得再内置具体模型名作为兜底，避免谎报当前使用的模型。"""
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        out = subprocess.run(
            ["grep", "-rn", "gemini-3.8-flash-high", "--include=*.py", "--include=*.ts", "--include=*.vue",
             "r20_backend", "scripts", "frontend/src", "dashboard"],
            cwd=root, capture_output=True, text=True,
        )
        hits = [line for line in out.stdout.splitlines() if line.strip()]
        self.assertEqual(hits, [], "发现硬编码模型名兜底：\n" + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()

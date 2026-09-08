"""qwen3 系思考模型 reasoning_effort 全链路回归（2026-09-08 用户反馈：设置 high 保存后显示 auto 且实际未生效）。

根因三层：
1. _detect_reasoning_type 把所有含 "qwen" 的模型判为 none（旧世代规则未跟上 qwen3 思考系）；
2. fetch-models 流程对 rtype=none 的模型写死 default_effort="auto"（用户看到的 auto）；
3. build_request_spec 的 auto 分支白名单不含 qwen3 → 即使手动添加存了 rtype=auto+effort=high，
   reasoning_effort 参数也从未发给上游。
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r20_backend import llm_manager


class Qwen3ReasoningEffortTests(unittest.TestCase):
    def test_detect_qwen3_family_is_standard_effort(self):
        for mid in ("qwen3.8-flash", "qwen3-max", "qwen-3.5-turbo", "qwen3-vl-plus", "qwq-32b", "dashscope/qwen3.8-flash"):
            self.assertEqual(llm_manager._detect_reasoning_type(mid), "standard_effort", mid)

    def test_detect_legacy_qwen_stays_none(self):
        for mid in ("qwen-max", "qwen-plus-2024", "qwen-turbo", "qwen2.5-72b-instruct"):
            self.assertEqual(llm_manager._detect_reasoning_type(mid), "none", mid)

    def test_openai_chat_sends_reasoning_effort_for_qwen3(self):
        # 新检测路径：rtype 显式 standard_effort
        _, _, payload = llm_manager.build_request_spec(
            model="qwen3.8-flash",
            messages=[{"role": "user", "content": "hi"}],
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="sk-test",
            api_format="openai_chat",
            reasoning_effort="high",
            reasoning_type="standard_effort",
        )
        self.assertEqual(payload.get("reasoning_effort"), "high")
        # 存量条目路径：库里存的 rtype="auto"，运行时白名单必须覆盖 qwen3
        _, _, payload_auto = llm_manager.build_request_spec(
            model="qwen3.8-flash",
            messages=[{"role": "user", "content": "hi"}],
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="sk-test",
            api_format="openai_chat",
            reasoning_effort="high",
            reasoning_type="auto",
        )
        self.assertEqual(payload_auto.get("reasoning_effort"), "high", "存量 rtype=auto 的 qwen3 条目也必须注入 effort")
        self.assertNotIn("temperature", payload_auto, "思考模型不应下发 temperature")

    def test_legacy_qwen_not_sent_effort_and_keeps_temperature(self):
        _, _, payload = llm_manager.build_request_spec(
            model="qwen-max",
            messages=[{"role": "user", "content": "hi"}],
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="sk-test",
            api_format="openai_chat",
            reasoning_effort="high",
            reasoning_type="none",
        )
        self.assertNotIn("reasoning_effort", payload)
        self.assertIn("temperature", payload)

    def test_upsert_model_honours_frontend_reasoning_effort(self):
        # Exercise the API schema and persistence without provider requests.
        import json, tempfile
        from unittest import mock
        from r20_backend.app import LLMModelUpsertRequest
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory) / "llm_models.json"
            tmp.write_text(json.dumps({"version": "3.1", "providers": [], "models": []}), encoding="utf-8")
            req = LLMModelUpsertRequest(
                id="qwen3.8-flash", name="qwen3.8-flash",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_format="openai_chat", reasoning_type="auto",
                reasoning_effort="low",
            )
            dump = req.model_dump(exclude_none=True)
            with mock.patch.object(llm_manager, "LLM_CONFIG_FILE", tmp):
                llm_manager.upsert_model("custom", dump)
                saved = json.loads(tmp.read_text(encoding="utf-8"))
                model = next(item for item in saved["models"] if item["id"] == "qwen3.8-flash")
                self.assertEqual(model["reasoning_effort"], "low")


if __name__ == "__main__":
    unittest.main()

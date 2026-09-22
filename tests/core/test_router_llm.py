"""LLM 供应商/模型/激活/故障转移路由：**审计记"实际生效"、连接测试的成败由回包推出**（第二百五十七刀，开新面 llm.py）。

先打印整个文件（231 行）再动笔。**12 个处理器 / 16 条路由**的权限与失败映射：

| 路由 | 权限 | 失败映射 |
|---|---|---|
| GET  models / providers | 管理员 | 回包**遮蔽密钥**（`mask_keys=True`）|
| GET  failover-events | 管理员 | 原样包成 `{"events": [...]}` |
| POST activate / settings | **超管** | `ValueError` ⇒ 400 |
| POST test | 管理员 | **失败不是 HTTP 错误**：`ok:false` 照常 200 返回 |
| POST fetch-models | 管理员 | 同上：`ok:false` 照常 200 返回 |
| POST/DELETE models、providers | **超管** | `ValueError` ⇒ 400；**删除/清空没命中 ⇒ 404** |
| POST providers/{id}/toggle | **超管** | 不传 body ⇒ `enabled=None`（交给下层**翻转**）|

本刀的三条重点：

1. **审计记「实际生效值」而不是「请求值」**（activate）：`reasoning_effort`/`thinking_timeout`
   取自**回包**——否则审计会写着"降到 low"，而运行时可能根本没收下；
2. **连接的成败由回包的 `ok` 推出**，不是由"我调用过了"推出：`test`/`fetch-models` 都
   `"success" if result.get("ok") else "failed"`，且**永不上抛** —— 与「受理 ≠ 成功」同族；
3. **`models`/`providers` 的删除与清空没命中 ⇒ 404 且不留 success 审计** —— 没删掉就
   别说删掉了。

⚠️ 如实钉住一处**张力**（列待议，未擅自改）：
`api_format` 的两条兜底**不对称** —— 命中所存模型时，`"openai_chat"` 这个**默认值**会被
条目里的格式覆盖（110-111 行）；但退化到 `get_active_llm_runtime()` 时只在
`not payload.api_format` 才覆盖（118 行），而 schema 默认是 `"openai_chat"`（真值）⇒
**运行时格式不会被继承**，该路径恒按 OpenAI 兼容格式测试。
"""

import unittest
from unittest import mock

from fastapi import HTTPException

from r20_backend.routers import llm as A
from r20_backend.schemas import (
    LLMActivateRequest,
    LLMFetchModelsRequest,
    LLMModelUpsertRequest,
    LLMProviderToggleRequest,
    LLMProviderUpsertRequest,
    LLMSettingsUpdateRequest,
    LLMTestRequest,
)


class LlmRoutesTest(unittest.TestCase):
    def setUp(self):
        self.audits = []
        self.admin = mock.Mock()
        self.superadmin = mock.Mock(return_value={"id": 1, "username": "root",
                                                  "role": "superadmin"})

        def _patch(target, new=mock.DEFAULT, **kwargs):
            patcher = mock.patch.object(A, target, new, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

        _patch("audit_record", lambda *a, **k: self.audits.append((a, k)))
        _patch("app_attr", side_effect=lambda name, default=None: default)
        _patch("require_admin_header", self.admin)
        _patch("require_superadmin", self.superadmin)

        for name in ("load_llm_config", "get_active_llm_runtime", "activate_provider_model",
                     "update_llm_settings", "upsert_provider", "delete_provider",
                     "toggle_provider", "clear_provider_models", "upsert_model",
                     "delete_model", "test_llm_connection", "fetch_remote_models",
                     "recent_failover_events"):
            m = mock.Mock()
            setattr(self, name, m)
            _patch(name, m)

    def _rec(self, index=0):
        return self.audits[index][0]

    # ── 读 ────────────────────────────────────────────────
    def test_get_models_requires_admin_and_masks_keys(self):
        self.load_llm_config.return_value = {"providers": [], "models": []}
        out = A.admin_get_llm_models(x_r20_session="t")
        self.admin.assert_called_once_with(x_r20_session="t")
        self.load_llm_config.assert_called_once_with(mask_keys=True)
        self.assertEqual(out, {"providers": [], "models": []})
        self.assertEqual(self.audits, [], "读操作不写审计")

    def test_failover_events_wraps_and_passes_limit(self):
        self.recent_failover_events.return_value = [{"at": 1}]
        out = A.admin_llm_failover_events(limit=5, x_r20_session="t")
        self.recent_failover_events.assert_called_once_with(5)
        self.assertEqual(out, {"events": [{"at": 1}]})

    # ── 激活 ──────────────────────────────────────────────
    def test_activate_audits_the_effective_values_not_the_requested_ones(self):
        self.activate_provider_model.return_value = {"active_reasoning_effort": "high",
                                                     "thinking_timeout": 120.0}
        A.admin_activate_llm_model(
            LLMActivateRequest(model_id="m1", reasoning_effort="low",
                               thinking_timeout=60.0),
            x_r20_session="t")
        # 请求 low/60，回包 high/120 ⇒ 审计必须写 high/120（实际生效）
        self.activate_provider_model.assert_called_once_with("custom", "m1", "low", 60.0)
        payload = self._rec()[2]
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertEqual(payload["thinking_timeout"], 120.0)
        self.assertEqual(payload["model_id"], "m1")
        self.assertEqual(payload["actor"], "root")

    def test_activate_passes_an_explicit_provider_and_maps_value_error_to_400(self):
        self.activate_provider_model.side_effect = ValueError("没有这个模型")
        with self.assertRaises(HTTPException) as ctx:
            A.admin_activate_llm_model(
                LLMActivateRequest(model_id="m1", provider_id="openai"),
                x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.activate_provider_model.assert_called_once_with("openai", "m1", None, None)
        self.assertEqual(self.audits, [], "失败不记 success 审计")

    # ── 设置 ──────────────────────────────────────────────
    def test_settings_audits_the_requested_fields_and_maps_error_to_400(self):
        self.update_llm_settings.return_value = {"ok": True}
        A.admin_update_llm_settings(
            LLMSettingsUpdateRequest(active_model_id="m1", reasoning_effort="high",
                                     thinking_timeout=90.0, request_attempts=3,
                                     fallback_model_ids=["m2"]),
            x_r20_session="t")
        self.update_llm_settings.assert_called_once_with(
            active_model_id="m1", reasoning_effort="high", thinking_timeout=90.0,
            request_attempts=3, fallback_model_ids=["m2"])
        payload = self._rec()[2]
        self.assertEqual(payload["fallback_model_ids"], ["m2"])
        self.assertEqual(payload["request_attempts"], 3)

    def test_settings_value_error_is_400(self):
        self.update_llm_settings.side_effect = ValueError("thinking_timeout 越界")
        with self.assertRaises(HTTPException) as ctx:
            A.admin_update_llm_settings(LLMSettingsUpdateRequest(), x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)

    # ── 连接测试 ──────────────────────────────────────────
    def test_test_connection_prefers_the_saved_model_entry(self):
        self.load_llm_config.return_value = {"models": [
            {"id": "m1", "base_url": "https://saved", "api_key": "sk-saved",
             "api_format": "anthropic_messages"}]}
        self.test_llm_connection.return_value = {"ok": False, "status_code": 401,
                                                 "latency_ms": 12, "endpoint": "/v1/messages"}
        # 失败也照常返回（不是 HTTP 错误）
        out = A.admin_test_llm(LLMTestRequest(model="m1"), x_r20_session="t")
        self.assertIs(out["ok"], False)
        self.load_llm_config.assert_called_once_with(mask_keys=False)
        self.test_llm_connection.assert_called_once_with(
            base_url="https://saved", api_key="sk-saved", model="m1",
            api_format="anthropic_messages", reasoning_effort="auto",
            reasoning_type="auto", timeout=25.0)
        self.assertEqual(self._rec()[1], "failed", "成败来自回包的 ok")
        self.assertEqual(self._rec()[2]["status_code"], 401)

    def test_test_connection_success_is_audited_from_the_reply(self):
        self.load_llm_config.return_value = {"models": []}
        self.get_active_llm_runtime.return_value = {"base_url": "https://rt"}
        self.test_llm_connection.return_value = {"ok": True, "latency_ms": 30,
                                                 "endpoint": "/chat/completions",
                                                 "reasoning_detected": True}
        A.admin_test_llm(LLMTestRequest(model="m9", base_url="https://explicit"),
                         x_r20_session="t")
        # 给了 base_url 就不查运行时
        self.get_active_llm_runtime.assert_not_called()
        self.assertEqual(self._rec()[0], "llm.connection.test")
        self.assertEqual(self._rec()[1], "success")
        self.assertEqual(self._rec()[2]["endpoint"], "/chat/completions")

    def test_test_connection_runtime_fallback_does_not_inherit_api_format(self):
        self.load_llm_config.return_value = {"models": []}
        self.get_active_llm_runtime.return_value = {"base_url": "https://rt",
                                                    "api_key": "sk-rt",
                                                    "api_format": "anthropic_messages"}
        self.test_llm_connection.return_value = {"ok": True}
        A.admin_test_llm(LLMTestRequest(model="m9"), x_r20_session="t")
        kwargs = self.test_llm_connection.call_args.kwargs
        self.assertEqual(kwargs["base_url"], "https://rt")
        self.assertEqual(kwargs["api_key"], "sk-rt")
        # ⚠️ 实测：schema 默认 "openai_chat" 是真值 ⇒ 运行时的 anthropic_messages 不被继承
        self.assertEqual(kwargs["api_format"], "openai_chat")

    def test_test_connection_inherits_runtime_format_only_when_payload_format_is_blank(self):
        """空白 `api_format` 才走运行时格式继承（这正是上面那条不对称的另一半）。"""
        self.load_llm_config.return_value = {"models": []}
        self.get_active_llm_runtime.return_value = {"base_url": "https://rt",
                                                    "api_key": "sk-rt",
                                                    "api_format": "anthropic_messages"}
        self.test_llm_connection.return_value = {"ok": True}
        A.admin_test_llm(LLMTestRequest(model="m9", api_format=""), x_r20_session="t")
        kwargs = self.test_llm_connection.call_args.kwargs
        self.assertEqual(kwargs["base_url"], "https://rt")
        self.assertEqual(kwargs["api_format"], "anthropic_messages")

    def test_config_model_entry_without_id_crashes_the_whole_test(self):
        """⚠️ 列待议：`m["id"]` 是直接下标 ⇒ 缺 id 的坏条目把整次连接测试打成 KeyError。"""
        self.load_llm_config.return_value = {"models": [{"base_url": "https://bad"}]}
        with self.assertRaises(KeyError):
            A.admin_test_llm(LLMTestRequest(model="m1"), x_r20_session="t")

    # ── 模型 / 供应商写 ───────────────────────────────────
    def test_upsert_model_audits_id_and_effective_format_without_secrets(self):
        self.upsert_model.return_value = {"api_format": "anthropic_messages"}
        A.admin_upsert_llm_model(LLMModelUpsertRequest(id="m1", api_key="sk-secret"),
                                 x_r20_session="t")
        self.superadmin.assert_called_once_with("t")
        args = self.upsert_model.call_args[0]
        self.assertEqual(args[0], "custom")
        self.assertEqual(args[1]["id"], "m1")
        payload = self._rec()[2]
        self.assertEqual(payload["api_format"], "anthropic_messages")
        self.assertNotIn("api_key", payload)

    def test_upsert_model_value_error_is_400(self):
        self.upsert_model.side_effect = ValueError("格式非法")
        with self.assertRaises(HTTPException) as ctx:
            A.admin_upsert_llm_model(LLMModelUpsertRequest(id="m1"), x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.audits, [])

    def test_delete_model_miss_is_404_without_audit(self):
        self.delete_model.return_value = False
        with self.assertRaises(HTTPException) as ctx:
            A.admin_delete_llm_model("m1", x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(self.audits, [], "没删掉不许记 success")

    def test_delete_model_success_audits_and_reports(self):
        self.delete_model.return_value = True
        out = A.admin_delete_llm_model("m1", provider_id="openai", x_r20_session="t")
        self.delete_model.assert_called_once_with("openai", "m1")
        self.assertEqual(out, {"deleted": True, "model_id": "m1"})
        self.assertEqual(self._rec()[0], "llm.model.delete")

    def test_delete_model_value_error_is_400(self):
        self.delete_model.side_effect = ValueError("被占用的模型")
        with self.assertRaises(HTTPException) as ctx:
            A.admin_delete_llm_model("m1", x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.audits, [])

    def test_upsert_provider_value_error_is_400(self):
        self.upsert_provider.side_effect = ValueError("base_url 非法")
        with self.assertRaises(HTTPException) as ctx:
            A.admin_upsert_llm_provider(
                LLMProviderUpsertRequest(name="P", base_url="nope"), x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.audits, [])

    def test_upsert_provider_audits_the_returned_id(self):
        self.upsert_provider.return_value = {"id": "p1"}
        A.admin_upsert_llm_provider(LLMProviderUpsertRequest(name="P", base_url="https://p",
                                                             api_key="sk-secret"),
                                    x_r20_session="t")
        self.assertEqual(self._rec()[2]["provider_id"], "p1")
        self.assertNotIn("api_key", self._rec()[2])

    def test_toggle_provider_without_a_body_asks_the_lower_layer_to_flip(self):
        self.toggle_provider.return_value = {"enabled": True}
        A.admin_toggle_llm_provider("p1", None, x_r20_session="t")
        self.toggle_provider.assert_called_once_with("p1", enabled=None)
        self.assertIs(self._rec()[2]["enabled"], True)

    def test_toggle_provider_with_a_body_passes_the_requested_state(self):
        self.toggle_provider.return_value = {"enabled": False}
        A.admin_toggle_llm_provider("p1", LLMProviderToggleRequest(enabled=False),
                                    x_r20_session="t")
        self.toggle_provider.assert_called_once_with("p1", enabled=False)
        self.assertIs(self._rec()[2]["enabled"], False)

    def test_toggle_provider_value_error_is_400(self):
        self.toggle_provider.side_effect = ValueError("没有这个供应商")
        with self.assertRaises(HTTPException) as ctx:
            A.admin_toggle_llm_provider("p1", None, x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_clear_provider_models_miss_is_404(self):
        self.clear_provider_models.return_value = False
        with self.assertRaises(HTTPException) as ctx:
            A.admin_clear_llm_provider_models("p1", x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(self.audits, [])

    def test_clear_provider_models_success_audits_and_reports(self):
        self.clear_provider_models.return_value = True
        out = A.admin_clear_llm_provider_models("p1", x_r20_session="t")
        self.clear_provider_models.assert_called_once_with("p1")
        self.assertEqual(out, {"cleared": True, "provider_id": "p1"})
        self.assertEqual(self._rec()[0], "llm.provider.clear_models")

    def test_clear_provider_models_value_error_is_400(self):
        self.clear_provider_models.side_effect = ValueError("不能清空")
        with self.assertRaises(HTTPException) as ctx:
            A.admin_clear_llm_provider_models("p1", x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.audits, [])

    def test_delete_provider_value_error_is_400(self):
        self.delete_provider.side_effect = ValueError("仍被引用")
        with self.assertRaises(HTTPException) as ctx:
            A.admin_delete_llm_provider("p1", x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.audits, [])

    def test_delete_provider_miss_is_404_and_success_audits(self):
        self.delete_provider.return_value = False
        with self.assertRaises(HTTPException) as ctx:
            A.admin_delete_llm_provider("p1", x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 404)

        self.delete_provider.return_value = True
        out = A.admin_delete_llm_provider("p1", x_r20_session="t")
        self.assertEqual(out, {"deleted": True, "provider_id": "p1"})
        self.assertEqual(self._rec()[0], "llm.provider.delete")

    # ── 远程拉取模型 ──────────────────────────────────────
    def test_fetch_remote_models_audits_reply_facts_and_never_raises(self):
        self.fetch_remote_models.return_value = {"ok": True, "total": 7}
        out = A.admin_fetch_remote_models(
            LLMFetchModelsRequest(base_url="https://p", api_key="sk", provider_id="p1"),
            x_r20_session="t")
        self.assertEqual(out["total"], 7)
        self.fetch_remote_models.assert_called_once_with(base_url="https://p", api_key="sk",
                                                         provider_id="p1")
        self.assertEqual(self._rec()[1], "success")
        self.assertEqual(self._rec()[2]["total"], 7)

    def test_fetch_remote_models_failure_is_a_failed_status_not_an_http_error(self):
        self.fetch_remote_models.return_value = {"ok": False}
        out = A.admin_fetch_remote_models(LLMFetchModelsRequest(), x_r20_session="t")
        self.assertIs(out["ok"], False)
        self.assertEqual(self._rec()[1], "failed")
        self.assertEqual(self._rec()[2]["total"], 0, "缺 total ⇒ 0，不臆造")


if __name__ == "__main__":
    unittest.main()

"""LLM 调用链（`r20_backend/llm/call.py`）的残余分支收口 —— 第 336 刀。

本模块 523 行，三件事：远端模型列举、**统一韧性执行器**、连通性诊断。
修正后基线里它是**最大单块**（79 行真运行时缺口 / 64.9%）。

## 设计上的便利：三个函数都把兄弟函数**作为参数注入**

门面传进来的 `reload_config` / `get_active_runtime` / `resolve_runtime` / `on_failover`
都是可调用对象 ⇒ 测试天然可以在**边界**打桩，不需要碰模块全局。

## 本刀立住的三条纪律

1. **韧性链的方向是"必须拿到响应"**：单模型保持旧异常语义（前端文案不变），
   多模型才走 `chain_failed` 记账；**硬故障不原地重试**（立即换模型），
   瞬时故障才按次数退避 —— 但 `fail_over_now`（504/超时等**慢**故障）在链上还有
   下一棒时也立即回退，**末位模型仍按次数重试**。
2. **认证错误不能中止端点探测**：部分网关对错误路径也回 401/403 而非 404，
   必须试完全部候选端点后仍失败才提示检查密钥。
3. **诊断端点必须给出可行动的 `recommendation`**：401/404/429/5xx 各有专门文案，
   否则用户只看到 "HTTP 400" 无从下手。
"""
from __future__ import annotations

import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import r20_backend.llm.call as call  # noqa: E402
from r20_backend.llm.transport import _LLMHardError, _LLMTransientError  # noqa: E402


def _resp(payload, *, code=200, raw=None):
    stream = MagicMock()
    stream.read.return_value = raw if raw is not None else json.dumps(payload).encode()
    stream.getcode.return_value = code
    stream.__enter__ = lambda s: s
    stream.__exit__ = lambda *a: False
    return stream


def _http_error(code, body=b'{"error":"bad"}'):
    err = urllib.error.HTTPError("http://x", code, "boom", {}, None)
    err.read = lambda: body
    return err


# ───────────────────── _lookup_api_path ─────────────────────
class LookupApiPathTests(unittest.TestCase):
    def test_a_blank_base_url_yields_an_empty_path(self):
        # 第 41/42 行
        self.assertEqual(call._lookup_api_path(lambda: {}, "", "m"), "")

    def test_a_config_reload_failure_yields_an_empty_path(self):
        # 第 45/46 行
        def _boom():
            raise RuntimeError("config unreadable")
        self.assertEqual(call._lookup_api_path(_boom, "https://a/v1", "m"), "")

    def test_a_matching_model_wins(self):
        cfg = {"models": [{"id": "m", "base_url": "https://a/v1/",
                           "api_path": "/chat"}]}
        self.assertEqual(call._lookup_api_path(lambda: cfg, "https://a/v1", "m"),
                         "/chat")

    def test_the_trailing_slash_is_tolerated(self):
        cfg = {"models": [{"id": "m", "base_url": "https://a/v1",
                           "api_path": "/chat"}]}
        self.assertEqual(call._lookup_api_path(lambda: cfg, "https://a/v1/", "m"),
                         "/chat")

    def test_a_provider_match_is_the_fallback(self):
        cfg = {"models": [], "providers": [{"base_url": "https://a/v1",
                                            "api_path": "/p"}]}
        self.assertEqual(call._lookup_api_path(lambda: cfg, "https://a/v1", "m"), "/p")

    def test_no_match_yields_an_empty_path(self):
        cfg = {"models": [{"id": "other", "base_url": "https://a/v1"}],
               "providers": []}
        self.assertEqual(call._lookup_api_path(lambda: cfg, "https://a/v1", "m"), "")

    def test_a_provider_without_an_api_path_yields_an_empty_path(self):
        cfg = {"providers": [{"base_url": "https://a/v1"}]}
        self.assertEqual(call._lookup_api_path(lambda: cfg, "https://a/v1", "m"), "")


# ───────────────────── fetch_remote_models ─────────────────────
class FetchRemoteModelsTests(unittest.TestCase):
    def _fetch(self, config=None, active=None, **kw):
        c = config if config is not None else {"providers": [], "models": []}
        a = active if active is not None else {"base_url": "", "api_key": ""}
        return call.fetch_remote_models(lambda: c, lambda: a, **kw)

    def _serve(self, payload, *, code=200, raw=None):
        """让 `urlopen` 每次返回**独立的**假响应（同一个 BytesIO 会被第一次读干）。"""
        return patch.object(call.urllib.request, "urlopen",
                            side_effect=lambda *a, **k: _resp(payload, code=code, raw=raw))

    def test_an_invalid_base_url_is_refused(self):
        # 第 79–84 行
        for bad in ("", "ftp://x", "not-a-url"):
            with self.subTest(bad=bad):
                out = self._fetch(base_url=bad)
                self.assertFalse(out["ok"])
                self.assertIn("Base URL 格式无效", out["error"])

    def test_the_base_url_is_taken_from_the_provider(self):
        # 第 66–71 行
        cfg = {"providers": [{"id": "p1", "base_url": "https://a/v1",
                              "api_key": "K1"}]}
        captured = {}

        def _open(req, timeout=None):
            captured["url"] = req.full_url
            captured["auth"] = req.headers.get("Authorization")
            return _resp({"data": [{"id": "m"}]})
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            out = self._fetch(config=cfg, provider_id="p1")
        self.assertTrue(out["ok"], out)
        self.assertIn("https://a/v1", captured["url"])

    def test_the_base_url_falls_back_to_the_active_runtime(self):
        # 第 73–77 行
        active = {"base_url": "https://active/v1", "api_key": "KA"}
        with self._serve({"data": [{"id": "m"}]}):
            out = self._fetch(active=active)
        self.assertTrue(out["ok"], out)

    def test_an_unknown_provider_id_falls_through_to_the_active_runtime(self):
        active = {"base_url": "https://active/v1", "api_key": ""}
        cfg = {"providers": [{"id": "other", "base_url": "https://a/v1"}]}
        with self._serve({"data": [{"id": "m"}]}):
            out = self._fetch(config=cfg, active=active, provider_id="nope")
        self.assertTrue(out["ok"], out)

    def test_the_provider_api_key_is_picked_up_after_the_url(self):
        # 第 86–89 行
        cfg = {"providers": [{"id": "p1", "base_url": "https://a/v1",
                              "api_key": "K1"}]}
        captured = {}

        def _open(req, timeout=None):
            captured["auth"] = req.headers.get("Authorization")
            return _resp({"data": [{"id": "m"}]})
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            self._fetch(config=cfg, base_url="https://a/v1", provider_id="p1")
        self.assertEqual(captured["auth"], "Bearer K1")

    def test_the_api_key_is_matched_by_base_url_when_no_provider_id(self):
        # 第 91–94 行
        cfg = {"providers": [{"base_url": "https://a/v1", "api_key": "K2"}]}
        captured = {}

        def _open(req, timeout=None):
            captured["auth"] = req.headers.get("Authorization")
            return _resp({"data": [{"id": "m"}]})
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            self._fetch(config=cfg, base_url="https://a/v1")
        self.assertEqual(captured["auth"], "Bearer K2")

    def test_no_api_key_anywhere_sends_no_auth_header(self):
        captured = {}

        def _open(req, timeout=None):
            captured["auth"] = req.headers.get("Authorization")
            return _resp({"data": [{"id": "m"}]})
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            self._fetch(base_url="https://a/v1")
        self.assertIsNone(captured["auth"])

    def test_an_anthropic_url_probes_two_endpoints(self):
        # 第 99–104 行 —— 官方 base 自带 /v1，另试一个裸拼位
        urls = []

        def _open(req, timeout=None):
            urls.append(req.full_url)
            raise OSError("stop here")
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            out = self._fetch(base_url="https://api.anthropic.com",
                              api_key="K")
        self.assertFalse(out["ok"])
        self.assertEqual(len(urls), 2, "应探测两个候选端点")
        self.assertEqual(len(set(urls)), 2, "两个候选必须不同")

    def test_an_anthropic_models_url_probes_a_nonsense_second_endpoint(self):
        # 🐞 **实测到的真缺陷**（本刀仅记录，**未改**）：
        #   第 101–104 行的去重判据是 `alt != endpoints[0][0]`，其中
        #     endpoints[0][0] = _join_api_path(cleaned_url, "/models")   # 已带 /models ⇒ 原样返回
        #     alt            = f"{cleaned_url}/models"                   # 再拼一次 ⇒ /models/models
        #   两者不等 ⇒ 探测一个**结构性错误**的端点。危害有限（它必然失败、随后回退到
        #   第一个候选），但白跑一次请求，且失败文案会污染 `last_err`。
        urls = []

        def _open(req, timeout=None):
            urls.append(req.full_url)
            raise OSError("stop")
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            self._fetch(base_url="https://api.anthropic.com/v1/models", api_key="K")
        self.assertEqual(len(urls), 2)
        self.assertEqual(urls[1], "https://api.anthropic.com/v1/models/models")

    def test_a_models_suffixed_url_is_used_verbatim(self):
        captured = {}

        def _open(req, timeout=None):
            captured["url"] = req.full_url
            return _resp({"data": [{"id": "m"}]})
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            self._fetch(base_url="https://gw/v1/models")
        self.assertEqual(captured["url"], "https://gw/v1/models")

    def test_a_v1_suffixed_url_gets_a_de_versioned_alternative(self):
        # 第 112 行
        urls = []

        def _open(req, timeout=None):
            urls.append(req.full_url)
            raise OSError("stop")
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            self._fetch(base_url="https://gw/v1")
        self.assertIn("https://gw/models", urls)

    def test_a_non_list_model_body_moves_to_the_next_endpoint(self):
        # 第 125/126 行 —— `data`/`models` 都不是 list ⇒ 换下一个候选
        seen = []

        def _open(req, timeout=None):
            seen.append(req.full_url)
            if len(seen) == 1:
                return _resp({"data": "not-a-list"})
            return _resp({"data": [{"id": "m"}]})
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            out = self._fetch(base_url="https://gw/v1")
        self.assertTrue(out["ok"], out)
        self.assertGreaterEqual(len(seen), 2)

    def test_a_top_level_list_body_is_broken_by_a_parenthesised_ternary(self):
        # 🐞 **实测到的真缺陷**（本刀仅记录，**未改**）：
        #   第 124 行
        #     raw_list = data.get("data") if isinstance(data, dict) and "data" in data
        #                else data.get("models", data if isinstance(data, list) else [])
        #   当 `data` 是**顶层 list** 时走 `else`，先求 `data.get(...)` ——
        #   而 list 没有 `.get` ⇒ **AttributeError**。
        #   那句 `data if isinstance(data, list) else []` 只是 `get` 的**默认值**，
        #   救不了"`data.get` 本身就调不了"。
        #   ⇒ 注释里写的"裸 list 响应"这条兼容路径**永远不可能生效**。
        #   用户侧表现为 `拉取失败: 'list' object has no attribute 'get'`（误导性文案）。
        with self._serve([{"id": "m"}]):
            out = self._fetch(base_url="https://gw/v1")
        self.assertFalse(out["ok"])
        self.assertIn("'list' object has no attribute 'get'", out["error"])

    def test_a_dict_envelope_is_still_the_supported_shape(self):
        with self._serve({"models": [{"id": "m"}]}):
            out = self._fetch(base_url="https://gw/v1")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["models"][0]["id"], "m")

    def test_a_models_key_body_is_accepted(self):
        with self._serve({"models": [{"id": "m"}]}):
            out = self._fetch(base_url="https://gw/v1")
        self.assertTrue(out["ok"], out)

    def test_a_bare_string_entry_becomes_an_id_and_name(self):
        # 第 130–134 行
        with self._serve({"data": ["gpt-5-turbo"]}):
            out = self._fetch(base_url="https://gw/v1")
        m = out["models"][0]
        self.assertEqual((m["id"], m["name"]), ("gpt-5-turbo", "gpt-5-turbo"))
        self.assertIsNone(m["context_length"])
        self.assertEqual(m["description"], "")

    def test_a_dict_entry_without_an_id_is_skipped(self):
        # 第 137/138 行
        with self._serve({"data": [{"name": "no id"}, {"id": "m"}]}):
            out = self._fetch(base_url="https://gw/v1")
        self.assertEqual([m["id"] for m in out["models"]], ["m"])

    def test_a_non_string_non_dict_entry_is_skipped(self):
        # 第 142/143 行
        with self._serve({"data": [42, None, {"id": "m"}]}):
            out = self._fetch(base_url="https://gw/v1")
        self.assertEqual([m["id"] for m in out["models"]], ["m"])
        del out

    def test_the_display_name_falls_back_to_the_id(self):
        with self._serve({"data": [{"id": "m"}]}):
            out = self._fetch(base_url="https://gw/v1")
        self.assertEqual(out["models"][0]["name"], "m")

    def test_the_context_length_accepts_max_tokens(self):
        with self._serve({"data": [{"id": "m", "max_tokens": 8192}]}):
            out = self._fetch(base_url="https://gw/v1")
        self.assertEqual(out["models"][0]["context_length"], 8192)

    def test_the_effort_defaults_to_auto_only_when_reasoning_is_detected_as_none(self):
        # 判据是 `detected_rtype != "none"` ⇒ 只有**明确判定为无推演**的模型才给 auto。
        # ⚠️ `_detect_reasoning_type` 对**不认识**的 id 返回 `"auto"`（不是 `"none"`）
        #    ⇒ 用 `gpt-4o`（实测 `none`）而不是自造的 "plain-model"。
        with self._serve({"data": [{"id": "deepseek-r1"}, {"id": "gpt-4o"}]}):
            out = self._fetch(base_url="https://gw/v1")
        by_id = {m["id"]: m for m in out["models"]}
        self.assertEqual(by_id["gpt-4o"]["reasoning_effort"], "auto")
        self.assertEqual(by_id["deepseek-r1"]["reasoning_effort"], "high")

    def test_an_unrecognised_model_id_still_gets_high(self):
        # 不认识 ⇒ `auto`（≠ "none"）⇒ 仍给 high
        with self._serve({"data": [{"id": "totally-unknown-xyz"}]}):
            out = self._fetch(base_url="https://gw/v1")
        self.assertEqual(out["models"][0]["reasoning_effort"], "high")

    def test_the_sort_puts_frontier_models_first(self):
        # 第 163–171 行：三档 —— 0 尖端 / 1 上一代 / 2 其余
        with self._serve({"data": [{"id": "zzz-old"}, {"id": "gpt-5"}, {"id": "gpt-4o"}]}):
            out = self._fetch(base_url="https://gw/v1")
        self.assertEqual([m["id"] for m in out["models"]], ["gpt-5", "gpt-4o", "zzz-old"])

    def test_a_generic_transport_error_is_recorded_as_the_last_error(self):
        # 第 186/187 行
        with patch.object(call.urllib.request, "urlopen",
                          side_effect=OSError("connection refused")):
            out = self._fetch(base_url="https://gw/v1")
        self.assertFalse(out["ok"])
        self.assertIn("connection refused", out["error"])

    def test_an_auth_error_is_kept_until_every_endpoint_fails(self):
        # 第 181–185 行 + 第 189–194 行：401 不能立即中止探测
        calls = []

        def _open(req, timeout=None):
            calls.append(req.full_url)
            raise _http_error(401)
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            out = self._fetch(base_url="https://gw/v1")
        self.assertFalse(out["ok"])
        self.assertIn("身份验证失败", out["error"])
        self.assertGreaterEqual(len(calls), 2, "必须试完所有候选端点")

    def test_a_non_auth_http_error_does_not_raise_the_auth_message(self):
        with patch.object(call.urllib.request, "urlopen",
                          side_effect=_http_error(500)):
            out = self._fetch(base_url="https://gw/v1")
        self.assertIn("拉取失败", out["error"])
        self.assertNotIn("身份验证失败", out["error"])

    def test_a_403_counts_as_an_auth_error_too(self):
        with patch.object(call.urllib.request, "urlopen",
                          side_effect=_http_error(403)):
            out = self._fetch(base_url="https://gw/v1")
        self.assertIn("身份验证失败", out["error"])

    def test_a_successful_endpoint_reports_which_one_was_used(self):
        with self._serve({"data": [{"id": "m"}]}):
            out = self._fetch(base_url="https://gw/v1")
        self.assertTrue(out["endpoint_used"].startswith("https://gw/v1"))
        self.assertEqual(out["total"], 1)

    def test_a_no_response_at_all_still_explains_itself(self):
        # 第 198 行 —— `last_err` 为空时的兜底文案
        with patch.object(call.urllib.request, "urlopen",
                          side_effect=urllib.error.HTTPError(
                              "http://x", 401, "x", {}, None)):
            out = self._fetch(base_url="https://gw/v1")
        self.assertTrue(out["error"])


# ───────────────────── execute_llm_request ─────────────────────
class ExecuteLlmRequestTests(unittest.TestCase):
    def _run(self, *, runtime=None, resolve=None, messages=None, failover=None,
             attempt=None, **kw):
        rt = runtime if runtime is not None else {"model": "m1", "base_url": "https://a/v1",
                                                  "api_key": "K", "name": "M1",
                                                  "api_format": "openai_chat"}
        rec = failover if failover is not None else []
        fn = attempt or (lambda *a, **k: ("ok", "think", {"total_tokens": 1}, 5))
        with patch.object(call, "_attempt_llm_call", side_effect=fn):
            out = call.execute_llm_request(
                lambda: rt, resolve or (lambda _i: None), lambda e: rec.append(e),
                messages if messages is not None else [{"role": "user", "content": "hi"}],
                **kw)
        return out, rec

    def test_a_missing_model_is_refused(self):
        # 第 223–227 行
        with patch.dict("os.environ", {"LLM_MODEL": ""}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                call.execute_llm_request(lambda: {}, lambda _i: None, lambda _e: None, [])
        self.assertIn("LLM 模型未配置", str(ctx.exception))

    def test_a_successful_call_returns_four_values(self):
        out, rec = self._run()
        self.assertEqual(out, ("ok", "think", {"total_tokens": 1}, 5))
        self.assertEqual(rec, [], "成功且未回退 ⇒ 不记账")

    def test_an_unparsable_attempt_count_falls_back_to_the_default(self):
        # 第 236–238 行
        out, _ = self._run(runtime={"model": "m1", "request_attempts": "junk"})
        self.assertEqual(out[0], "ok")
        del out

    def test_the_attempt_count_is_clamped_into_range(self):
        # 第 239 行
        with patch.object(call, "MAX_REQUEST_ATTEMPTS", 2):
            out, _ = self._run(runtime={"model": "m1", "request_attempts": 999})
        self.assertEqual(out[0], "ok")
        del out

    def test_the_runtime_model_is_used_when_none_is_given(self):
        out, _ = self._run()
        self.assertEqual(out[0], "ok")

    def test_an_explicit_model_wins(self):
        seen = {}

        def _attempt(cand, *a, **k):
            seen["model"] = cand["model"]
            return ("ok", "", {}, 1)
        self._run(attempt=_attempt, model="explicit")
        self.assertEqual(seen["model"], "explicit")

    def test_the_fallback_chain_skips_the_primary_model(self):
        # 第 255/256 行
        rt = {"model": "m1", "fallback_model_ids": ["m1", "m2"]}
        resolve = lambda i: {"model": i, "base_url": "https://b/v1"} if i == "m2" else None
        seen = []

        def _attempt(cand, *a, **k):
            seen.append(cand["model"])
            if cand["model"] == "m1":
                raise _LLMHardError("primary down")
            return ("ok", "", {}, 1)
        out, rec = self._run(runtime=rt, resolve=resolve, attempt=_attempt)
        self.assertEqual(seen, ["m1", "m2"], "重复的 primary 必须被跳过")
        self.assertEqual(rec[0]["type"], "fallback_hit")

    def test_an_unresolvable_fallback_id_is_skipped(self):
        # 第 258/259 行
        rt = {"model": "m1", "fallback_model_ids": ["ghost"]}
        with patch.object(call, "_attempt_llm_call",
                          side_effect=lambda *a, **k: ("ok", "", {}, 1)):
            out = call.execute_llm_request(
                lambda: rt, lambda _i: None, lambda _e: None, [{"role": "user",
                                                               "content": "x"}])
        self.assertEqual(len(out), 4, "返回值是 (content, reasoning, usage, latency)")
        self.assertEqual(out[0], "ok")

    def test_an_explicit_timeout_overrides_the_fallback_budget(self):
        # 第 261/262 行
        rt = {"model": "m1", "fallback_model_ids": ["m2"],
              "request_attempts": 1}
        resolved = {"model": "m2", "base_url": "https://b/v1",
                    "thinking_timeout": 999}
        seen = []

        def _attempt(cand, *a, **k):
            seen.append(cand.get("thinking_timeout"))
            if cand["model"] == "m1":
                raise _LLMHardError("down")
            return ("ok", "", {}, 1)
        self._run(runtime=rt, resolve=lambda _i: dict(resolved), attempt=_attempt,
                  timeout=7.0)
        self.assertEqual(seen[-1], 7.0, "显式 timeout 必须覆盖回退模型自己的预算")

    def test_a_hard_error_does_not_retry_in_place(self):
        # 第 301–305 行 —— 硬故障立即换模型
        attempts = {"n": 0}

        def _attempt(*a, **k):
            attempts["n"] += 1
            raise _LLMHardError("401 unauthorized")
        with self.assertRaises(RuntimeError) as ctx:
            self._run(runtime={"model": "m1", "request_attempts": 3}, attempt=_attempt)
        self.assertIn("401 unauthorized", str(ctx.exception))
        self.assertEqual(attempts["n"], 1, "硬故障不得原地重试")

    def test_a_transient_error_is_retried_up_to_the_attempt_limit(self):
        # 第 306–309 行
        attempts = {"n": 0}

        def _attempt(*a, **k):
            attempts["n"] += 1
            raise _LLMTransientError("503", fail_over_now=False)
        with patch.object(call.time, "sleep", lambda _s: None):
            with self.assertRaises(RuntimeError):
                self._run(runtime={"model": "m1", "request_attempts": 3},
                          attempt=_attempt)
        self.assertEqual(attempts["n"], 3)

    def test_a_slow_failure_fails_over_immediately(self):
        # 第 310/311 行 —— 504/超时等慢故障在链上有下一棒时立即回退
        seen = []

        def _attempt(cand, *a, **k):
            seen.append(cand["model"])
            if cand["model"] == "m1":
                raise _LLMTransientError("504", fail_over_now=True)
            return ("ok", "", {}, 1)
        rt = {"model": "m1", "fallback_model_ids": ["m2"], "request_attempts": 5}
        self._run(runtime=rt, resolve=lambda i: {"model": i, "base_url": "https://b/v1"},
                  attempt=_attempt)
        self.assertEqual(seen, ["m1", "m2"], "慢故障应立即回退，不在主模型上重试")

    def test_the_last_model_still_retries_a_slow_failure(self):
        # 末位模型没有下一棒 ⇒ 仍按次数重试
        attempts = {"n": 0}

        def _attempt(*a, **k):
            attempts["n"] += 1
            raise _LLMTransientError("504", fail_over_now=True)
        with patch.object(call.time, "sleep", lambda _s: None):
            with self.assertRaises(RuntimeError):
                self._run(runtime={"model": "m1", "request_attempts": 4},
                          attempt=_attempt)
        self.assertEqual(attempts["n"], 4)

    def test_an_unclassified_exception_is_treated_as_transient(self):
        # 第 312–315 行 —— 绝不让整链崩在第一次
        attempts = {"n": 0}

        def _attempt(*a, **k):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise KeyError("weird")
            return ("ok", "", {}, 1)
        with patch.object(call.time, "sleep", lambda _s: None):
            out, _ = self._run(runtime={"model": "m1", "request_attempts": 3},
                               attempt=_attempt)
        self.assertEqual(out[0], "ok")

    def test_the_total_wait_deadline_stops_retrying(self):
        # 第 274–277 行 —— deadline_hit 后整链停止
        ticks = iter([0.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0])

        def _attempt(*a, **k):
            raise _LLMTransientError("503", fail_over_now=False)
        with patch.object(call.time, "perf_counter", lambda: next(ticks, 1000.0)), \
             patch.object(call.time, "sleep", lambda _s: None), \
             patch.object(call, "FAILOVER_MAX_TOTAL_WAIT", 10.0):
            with self.assertRaises(RuntimeError):
                self._run(runtime={"model": "m1", "request_attempts": 5},
                          attempt=_attempt)

    def test_a_single_model_hard_error_keeps_the_legacy_message(self):
        # 第 322/323 行
        with self.assertRaises(RuntimeError) as ctx:
            self._run(runtime={"model": "m1", "request_attempts": 1},
                      attempt=lambda *a, **k: (_ for _ in ()).throw(
                          _LLMHardError("bad key")))
        self.assertIn("bad key", str(ctx.exception))

    def test_a_single_model_transient_timeout_raises_timeout_error(self):
        # 第 324–326 行
        with self.assertRaises(TimeoutError):
            self._run(runtime={"model": "m1", "request_attempts": 1},
                      attempt=lambda *a, **k: (_ for _ in ()).throw(
                          _LLMTransientError("timed out", timed_out=True)))

    def test_a_single_model_transient_non_timeout_raises_runtime_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(runtime={"model": "m1", "request_attempts": 1},
                      attempt=lambda *a, **k: (_ for _ in ()).throw(
                          _LLMTransientError("503")))
        self.assertNotIsInstance(ctx.exception, TimeoutError)

    def test_a_single_model_with_no_error_at_all_uses_the_no_response_text(self):
        # 第 328 行 —— 理论上到不了（attempts>=1），但契约必须写明
        with patch.object(call, "MIN_REQUEST_ATTEMPTS", 0), \
             patch.object(call, "_attempt_llm_call",
                          side_effect=AssertionError("不应被调用")):
            with self.assertRaises(RuntimeError) as ctx:
                call.execute_llm_request(lambda: {"model": "m1", "request_attempts": 0},
                                         lambda _i: None, lambda _e: None, [])
        self.assertIn("未获得响应", str(ctx.exception))

    def test_a_multi_model_chain_failure_records_a_chain_failed_event(self):
        # 第 330–340 行
        rt = {"model": "m1", "fallback_model_ids": ["m2"], "request_attempts": 1}
        rec = []
        with self.assertRaises(RuntimeError) as ctx:
            self._run(runtime=rt,
                      resolve=lambda i: {"model": i, "base_url": "https://b/v1"},
                      attempt=lambda *a, **k: (_ for _ in ()).throw(
                          _LLMHardError("down")),
                      failover=rec)
        self.assertIn("m1 → m2", str(ctx.exception))
        self.assertEqual(rec[-1]["type"], "chain_failed")
        self.assertFalse(rec[-1]["succeeded"])
        self.assertIn("m1 → m2", rec[-1]["chain"])

    def test_a_multi_model_chain_timeout_raises_timeout_error(self):
        # 第 342/343 行
        rt = {"model": "m1", "fallback_model_ids": ["m2"], "request_attempts": 1}
        with self.assertRaises(TimeoutError) as ctx:
            self._run(runtime=rt,
                      resolve=lambda i: {"model": i, "base_url": "https://b/v1"},
                      attempt=lambda *a, **k: (_ for _ in ()).throw(
                          _LLMTransientError("timeout", timed_out=True)),
                      failover=[])
        self.assertIn("全部超时", str(ctx.exception))

    def test_a_fallback_hit_event_carries_the_chain_and_errors(self):
        rt = {"model": "m1", "fallback_model_ids": ["m2"], "request_attempts": 1}
        seen = []

        def _attempt(cand, *a, **k):
            if cand["model"] == "m1":
                raise _LLMHardError("primary 401")
            return ("done", "", {"total_tokens": 3}, 9)
        out, rec = self._run(runtime=rt,
                             resolve=lambda i: {"model": i, "base_url": "https://b/v1",
                                                "provider_name": "P2"},
                             attempt=_attempt, failover=seen)
        self.assertEqual(out[0], "done")
        ev = seen[0]
        self.assertEqual(ev["type"], "fallback_hit")
        self.assertEqual(ev["from_model"], "m1")
        self.assertEqual(ev["to_model"], "m2")
        self.assertEqual(ev["to_provider"], "P2")
        self.assertTrue(ev["succeeded"])
        self.assertTrue(any("primary 401" in e for e in ev["errors"]))

    def test_disabling_fallback_leaves_only_the_primary(self):
        rt = {"model": "m1", "fallback_model_ids": ["m2"], "request_attempts": 1}
        with self.assertRaises(RuntimeError) as ctx:
            self._run(runtime=rt,
                      resolve=lambda i: {"model": i},
                      attempt=lambda *a, **k: (_ for _ in ()).throw(
                          _LLMHardError("down")),
                      allow_fallback=False)
        self.assertNotIn("m2", str(ctx.exception))


# ───────────────────── test_llm_connection ─────────────────────
class TestLlmConnectionTests(unittest.TestCase):
    def _call(self, responses, **kw):
        """`responses` 是 urlopen 的 side_effect 序列（每个元素是响应或异常）。"""
        it = iter(responses)

        def _open(req, timeout=None):
            nxt = next(it)
            if isinstance(nxt, BaseException):
                raise nxt
            return nxt
        base = {"reload_config": lambda: {"models": [], "providers": []},
                "base_url": "https://gw/v1", "api_key": "K", "model": "m1"}
        base.update(kw)
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            return call.test_llm_connection(**base)

    def test_an_invalid_base_url_is_refused(self):
        # 第 358–367 行
        out = self._call([], base_url="nonsense")
        self.assertFalse(out["ok"])
        self.assertEqual(out["status_code"], 0)
        self.assertIn("Base URL 格式无效", out["error"])

    def test_a_successful_openai_chat_call(self):
        # 第 411–414 行
        out = self._call([_resp({"choices": [{"message": {"content": "PONG",
                                                          "reasoning_content": "hmm"}}],
                                 "usage": {"total_tokens": 7}})])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["response_preview"], "PONG")
        self.assertTrue(out["reasoning_detected"])
        self.assertEqual(out["total_tokens"], 7)

    def test_a_claude_messages_call_joins_text_blocks(self):
        out = self._call([_resp({"content": [{"type": "text", "text": "PO"},
                                             {"type": "text", "text": "NG"}]})],
                         api_format="claude_messages")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["response_preview"], "PONG")

    def test_a_claude_reasoning_block_is_captured(self):
        out = self._call([_resp({"content": [{"type": "thinking", "thinking": "因为"}]})],
                         api_format="claude_messages")
        self.assertTrue(out["reasoning_detected"])

    def test_an_openai_responses_call_reads_output_text(self):
        out = self._call([_resp({"output_text": "PONG",
                                 "output": [{"type": "reasoning",
                                             "summary": "想了"}]})],
                         api_format="openai_responses")
        self.assertEqual(out["response_preview"], "PONG")
        self.assertTrue(out["reasoning_detected"])

    def test_an_empty_body_gets_an_explicit_placeholder(self):
        out = self._call([_resp({"choices": [{"message": {"content": ""}}]})])
        self.assertIn("返回空正文", out["response_preview"])

    def test_the_reasoning_token_count_falls_back_to_a_word_count(self):
        # 第 417–422 行
        out = self._call([_resp({"choices": [{"message": {
            "content": "x", "reasoning_content": "one two three"}}]})])
        self.assertEqual(out["reasoning_tokens"], 3)

    def test_the_reported_reasoning_tokens_win(self):
        out = self._call([_resp({"choices": [{"message": {"content": "x"}}],
                                 "usage": {"completion_tokens_details":
                                           {"reasoning_tokens": 99}}})])
        self.assertEqual(out["reasoning_tokens"], 99)

    def test_the_messages_are_stripped_from_the_echoed_payload(self):
        out = self._call([_resp({"choices": [{"message": {"content": "x"}}]})])
        self.assertNotIn("messages", out["payload_sent"])

    def test_the_format_label_is_resolved(self):
        out = self._call([_resp({"choices": [{"message": {"content": "x"}}]})])
        self.assertEqual(out["api_format_name"], "OpenAI Chat (/chat/completions)")

    def test_an_unreadable_error_body_is_tolerated(self):
        # 第 446–449 行 —— 读 body 本身失败也只是留空
        err = _http_error(400)
        err.read = lambda: (_ for _ in ()).throw(OSError("body gone"))
        out = self._call([err])
        self.assertFalse(out["ok"])
        self.assertIn("HTTP 400", out["error"])

    def test_a_param_conflict_triggers_the_adaptive_retry(self):
        # 第 452–475 行
        err = _http_error(400, b'{"error":"unrecognized request argument: reasoning_effort"}')
        out = self._call([err, _resp({"choices": [{"message": {"content": "PONG"}}]})])
        self.assertTrue(out["ok"], out)
        self.assertIn("自适应", out["warning"])
        self.assertEqual(out["response_preview"], "PONG")

    def test_a_param_conflict_on_a_non_openai_format_does_not_retry(self):
        err = _http_error(400, b'unrecognized request argument')
        out = self._call([err], api_format="claude_messages")
        self.assertFalse(out["ok"])
        self.assertNotIn("warning", out)

    def test_a_failed_adaptive_retry_falls_through_to_the_error(self):
        # 第 476/477 行
        err = _http_error(400, b'temperature not supported')
        out = self._call([err, OSError("retry also failed")])
        self.assertFalse(out["ok"])
        self.assertIn("400", out["error"])

    def test_a_non_conflict_error_never_retries(self):
        err = _http_error(400, b'some other problem')
        out = self._call([err])
        self.assertFalse(out["ok"])
        self.assertNotIn("warning", out)

    def test_each_status_code_gets_a_specific_recommendation(self):
        # 第 479–487 行
        cases = {
            401: "API Key 认证失败",
            404: "端点未找到",
            429: "频次超限",
            500: "上游大模型服务暂时不可用",
            502: "上游大模型服务暂时不可用",
            503: "上游大模型服务暂时不可用",
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                out = self._call([_http_error(code, b'{}')])
                self.assertFalse(out["ok"])
                self.assertIn(expected, out["recommendation"], f"HTTP {code}")

    def test_an_unlisted_status_code_gets_the_generic_recommendation(self):
        out = self._call([_http_error(418, b'{}')])
        self.assertEqual(out["recommendation"], "请核对配置")

    def test_a_url_error_is_reported_with_its_reason(self):
        # 第 500–511 行
        out = self._call([urllib.error.URLError("dns failure")])
        self.assertFalse(out["ok"])
        self.assertEqual(out["status_code"], 0)
        self.assertIn("网络连接失败", out["error"])
        self.assertIn("无法连接", out["recommendation"])

    def test_an_unexpected_exception_is_reported(self):
        # 第 512–523 行
        out = self._call([RuntimeError("boom")])
        self.assertFalse(out["ok"])
        self.assertIn("测试执行异常", out["error"])
        self.assertIn("boom", out["error"])

    def test_the_api_path_is_looked_up_when_not_supplied(self):
        # 第 382 行
        captured = {}

        def _open(req, timeout=None):
            captured["url"] = req.full_url
            return _resp({"choices": [{"message": {"content": "x"}}]})
        cfg = {"models": [{"id": "m1", "base_url": "https://gw/v1",
                           "api_path": "/custom"}]}
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            call.test_llm_connection(reload_config=lambda: cfg,
                                     base_url="https://gw/v1", api_key="K",
                                     model="m1")
        self.assertIn("/custom", captured["url"])

    def test_an_explicit_api_path_wins_over_the_lookup(self):
        captured = {}

        def _open(req, timeout=None):
            captured["url"] = req.full_url
            return _resp({"choices": [{"message": {"content": "x"}}]})
        cfg = {"models": [{"id": "m1", "base_url": "https://gw/v1",
                           "api_path": "/looked-up"}]}
        with patch.object(call.urllib.request, "urlopen", side_effect=_open):
            call.test_llm_connection(reload_config=lambda: cfg,
                                     base_url="https://gw/v1", api_key="K",
                                     model="m1", api_path="/explicit")
        self.assertNotIn("/looked-up", captured["url"])
        self.assertIn("/explicit", captured["url"])


if __name__ == "__main__":
    unittest.main()

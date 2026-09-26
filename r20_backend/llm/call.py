"""LLM 调用链：远端模型列举、单次调用与连通性测试。

**薄壳 + 核心**：门面把「会读模块全局」的兄弟函数（init_llm_config /
get_active_llm_runtime / resolve_model_runtime / record_failover_event）作为参数注入，
核心只调用注入的可调用对象 —— 这样测试对门面的 patch / 直接赋值依然生效，
且核心不 import llm_manager（无循环依赖）。结构优化阶段 2（B4）。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from r20_backend.llm.capabilities import (
    _detect_api_format,
    _detect_capabilities,
    _detect_reasoning_type,
)
from r20_backend.llm.policy import (
    DEFAULT_REQUEST_ATTEMPTS,
    FAILOVER_MAX_TOTAL_WAIT,
    MAX_REQUEST_ATTEMPTS,
    MIN_REQUEST_ATTEMPTS,
    SUPPORTED_API_FORMATS,
)
from r20_backend.llm.providers import _join_api_path
from r20_backend.llm.transport import (
    _LLMHardError,
    _LLMTransientError,
    _attempt_llm_call,
    build_request_spec,
)


def _lookup_api_path(reload_config: Callable[[], Dict[str, Any]], base_url: str, model_id: str) -> str:
    """按 base_url+模型 id 反查已配置的「API 路径」（连接测试端点不透传该字段，此处自解析）。"""
    bu = str(base_url or "").strip().rstrip("/")
    if not bu:
        return ""
    try:
        cfg = reload_config()
    except Exception:
        return ""
    for m in cfg.get("models", []):
        if m.get("id") == model_id and str(m.get("base_url", "")).rstrip("/") == bu:
            return str(m.get("api_path") or "")
    for p in cfg.get("providers", []):
        if str(p.get("base_url", "")).rstrip("/") == bu:
            return str(p.get("api_path") or "")
    return ""


def fetch_remote_models(reload_config: Callable[[], Dict[str, Any]], get_active_runtime: Callable[[], Dict[str, Any]], 
    base_url: str = "",
    api_key: str = "",
    provider_id: Optional[str] = None,
    timeout: float = 12.0,
) -> Dict[str, Any]:
    """Fetch live models list from an OpenAI / OpenRouter / Anthropic compatible endpoint."""
    cleaned_url = str(base_url or "").strip().rstrip("/")
    config = reload_config()

    if not cleaned_url and provider_id:
        prov = next((p for p in config.get("providers", []) if p["id"] == provider_id), None)
        if prov:
            cleaned_url = prov.get("base_url", "").strip().rstrip("/")
            if not api_key:
                api_key = prov.get("api_key", "")

    if not cleaned_url:
        active = get_active_runtime()
        cleaned_url = active.get("base_url", "").strip().rstrip("/")
        if not api_key:
            api_key = active.get("api_key", "")

    if not cleaned_url or not cleaned_url.startswith(("http://", "https://")):
        return {
            "ok": False,
            "error": "Base URL 格式无效，必须以 http:// 或 https:// 开头",
            "recommendation": "请填写有效的供应商 Base URL",
        }

    if not api_key and provider_id:
        prov = next((p for p in config.get("providers", []) if p["id"] == provider_id), None)
        if prov and prov.get("api_key"):
            api_key = prov.get("api_key")

    if not api_key:
        prov = next((p for p in config.get("providers", []) if p.get("base_url", "").rstrip("/") == cleaned_url and p.get("api_key")), None)
        if prov:
            api_key = prov.get("api_key", "")

    endpoints = []
    bearer_hdr = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    anth_hdr = {"x-api-key": api_key, "anthropic-version": "2023-06-01"} if api_key else {}
    if "anthropic.com" in cleaned_url:
        # 官方 base 自带 /v1；自建 Anthropic 代理以用户输入路径为准
        endpoints.append((_join_api_path(cleaned_url, "/models"), anth_hdr))
        alt = f"{cleaned_url}/models"
        if alt != endpoints[0][0]:
            endpoints.append((alt, anth_hdr))
    elif cleaned_url.endswith("/models"):
        endpoints.append((cleaned_url, bearer_hdr))
    else:
        # 主候选 = 按 base 原样拼接（/v4 → /v4/models，不再硬插 /v1）；
        # 备候选 = 去掉版本段或裸域名直挂 /models 的网关兼容位
        primary = _join_api_path(cleaned_url, "/models")
        endpoints.append((primary, bearer_hdr))
        alt = f"{cleaned_url[:-3]}/models" if cleaned_url.endswith("/v1") else f"{cleaned_url}/models"
        if alt != primary:
            endpoints.append((alt, bearer_hdr))

    last_err = ""
    saw_auth_error = False
    for ep, hdrs in endpoints:
        hdrs["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 AstraQuant/8.3"
        req = urllib.request.Request(ep, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                raw_list = data.get("data") if isinstance(data, dict) and "data" in data else data.get("models", data if isinstance(data, list) else [])
                if not isinstance(raw_list, list):
                    continue

                parsed_models = []
                for item in raw_list:
                    if isinstance(item, str):
                        m_id = item
                        m_name = item
                        ctx = None
                        desc = ""
                    elif isinstance(item, dict):
                        m_id = str(item.get("id", "")).strip()
                        if not m_id:
                            continue
                        m_name = str(item.get("name") or item.get("display_name") or m_id).strip()
                        ctx = item.get("context_length") or item.get("max_tokens")
                        desc = str(item.get("description") or "").strip()
                    else:
                        continue

                    detected_format = _detect_api_format(cleaned_url, m_id)
                    detected_rtype = _detect_reasoning_type(m_id)
                    detected_caps = _detect_capabilities(m_id)
                    default_effort = "high" if detected_rtype != "none" else "auto"

                    parsed_models.append({
                        "id": m_id,
                        "name": m_name,
                        "capabilities": detected_caps,
                        "context_length": ctx,
                        "description": desc,
                        "api_format": detected_format,
                        "reasoning_type": detected_rtype,
                        # 与 upsert_model 存储键统一为 reasoning_effort；default_effort 仅保留兼容读
                        "reasoning_effort": default_effort,
                        "default_effort": default_effort,
                    })

                def _model_sort_key(m: Dict[str, Any]) -> Tuple[int, str]:
                    mid = m["id"].lower()
                    if any(k in mid for k in ["gemini-3", "claude-3-7", "claude-3.7", "o3", "o4", "gpt-5", "deepseek-r1", "deepseek-v4", "qwen-max", "qwq"]):
                        return (0, mid)
                    if any(k in mid for k in ["gemini-2", "claude-3-5", "claude-3.5", "o1", "gpt-4o", "qwen-2.5", "doubao"]):
                        return (1, mid)
                    return (2, mid)

                parsed_models.sort(key=_model_sort_key)

                return {
                    "ok": True,
                    "endpoint_used": ep,
                    "total": len(parsed_models),
                    "models": parsed_models,
                }
        except urllib.error.HTTPError as exc:
            last_err = f"HTTP {exc.code}"
            if exc.code in (401, 403):
                # 部分网关对错误路径也回 401/403 而非 404——不能见状态码就中止，
                # 试完全部候选端点仍失败才提示检查密钥
                saw_auth_error = True
                continue
        except Exception as exc:
            last_err = str(exc)

    if saw_auth_error:
        return {
            "ok": False,
            "error": "供应商身份验证失败 (HTTP 401 Unauthorized)",
            "recommendation": "请先在此供应商填入正确的 API Key 后再拉取模型；若密钥无误，请检查 Base URL 的版本路径（如 /v1、/v4）是否与供应商要求一致",
        }

    return {
        "ok": False,
        "error": f"拉取失败: {last_err or '未响应模型列表'}",
        "recommendation": "请检查 Base URL 是否正确，或供应商是否支持 /models 端点查询",
    }


def execute_llm_request(get_active_runtime: Callable[[], Dict[str, Any]], resolve_runtime: Callable[[str], Optional[Dict[str, Any]]], on_failover: Callable[[Dict[str, Any]], None], 
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    api_format: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    temperature: Optional[float] = 0.2,
    response_format: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
    allow_fallback: bool = True,
) -> Tuple[str, str, Dict[str, Any], int]:
    """Unified resilient executor for LLM calls across all 3 protocols.

    韧性链路：每个模型按后台「请求次数」重试（指数退避），瞬时耗尽或遇
    硬故障（401/404 等）时按后台「回退模型」顺序切换下一个模型。
    Returns: (content, reasoning_content, usage_dict, latency_ms)
    """
    runtime = get_active_runtime()
    target_model = model or runtime.get("model") or os.getenv("LLM_MODEL") or ""
    if not target_model:
        raise RuntimeError(
            "LLM 模型未配置：请在后台「LLM Providers」选择模型，或在 .env 设置 LLM_MODEL。"
            "系统不再内置任何默认模型名，避免界面谎报当前实际使用的模型。"
        )
    target_url = base_url or runtime.get("base_url") or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    target_key = api_key if api_key is not None else runtime.get("api_key", "")
    target_format = api_format or runtime.get("api_format") or _detect_api_format(target_url, target_model)
    target_effort = reasoning_effort or runtime.get("reasoning_effort") or "high"
    target_rtype = runtime.get("reasoning_type", "auto")
    effective_timeout = float(timeout) if (timeout is not None and float(timeout) > 0) else float(runtime.get("thinking_timeout") or 120.0)

    try:
        attempts = int(runtime.get("request_attempts") or DEFAULT_REQUEST_ATTEMPTS)
    except (TypeError, ValueError):
        attempts = DEFAULT_REQUEST_ATTEMPTS
    attempts = max(MIN_REQUEST_ATTEMPTS, min(MAX_REQUEST_ATTEMPTS, attempts))

    primary = {
        "model": target_model,
        "name": runtime.get("name") or target_model,
        "provider_name": runtime.get("provider_name", ""),
        "base_url": target_url,
        "api_key": target_key,
        "api_format": target_format,
        "api_path": "" if base_url else str(runtime.get("api_path", "") or ""),
        "reasoning_effort": target_effort,
        "reasoning_type": target_rtype,
    }
    candidates: List[Dict[str, Any]] = [primary]
    if allow_fallback:
        for fid in runtime.get("fallback_model_ids", []) or []:
            if fid == primary["model"]:
                continue
            rt = resolve_runtime(fid)
            if not rt:
                continue
            # 调用方显式指定 timeout 时统一预算；否则用回退模型自身的思考上限
            if timeout is not None and float(timeout) > 0:
                rt["thinking_timeout"] = float(timeout)
            candidates.append(rt)

    call_started = time.perf_counter()
    failures: List[str] = []
    last_error: Optional[BaseException] = None
    last_timed_out = False
    deadline_hit = False

    for cand_idx, cand in enumerate(candidates):
        cand_timeout = effective_timeout if cand_idx == 0 else float(cand.get("thinking_timeout") or effective_timeout)
        for attempt in range(attempts):
            if attempt > 0:
                if (time.perf_counter() - call_started) > FAILOVER_MAX_TOTAL_WAIT:
                    deadline_hit = True
                    break
                time.sleep(min(2.0 * attempt, 8.0))
            try:
                content, reasoning, usage, latency = _attempt_llm_call(
                    cand, messages, temperature, response_format, cand_timeout
                )
                if cand_idx > 0:
                    print(
                        f"[LLM Failover] ✅ 主模型 {primary['model']} 请求失败，已回退至模型 {cand['model']}"
                        f"（{cand.get('provider_name') or '备用'} · 第 {cand_idx + 1}/{len(candidates)} 个候选 · 本模型第 {attempt + 1} 次尝试）"
                    )
                    on_failover({
                        "type": "fallback_hit",
                        "from_model": primary["model"],
                        "to_model": cand["model"],
                        "to_provider": cand.get("provider_name", ""),
                        "attempt": attempt + 1,
                        "attempts_per_model": attempts,
                        "chain": " → ".join(c["model"] for c in candidates),
                        "errors": [f[:220] for f in failures[-6:]],
                        "elapsed_seconds": round(time.perf_counter() - call_started, 1),
                        "succeeded": True,
                    })
                return content, reasoning, usage, latency
            except _LLMHardError as exc:
                failures.append(str(exc))
                last_error = exc
                last_timed_out = False
                break  # 该模型硬故障：不再原地重试，切换下一个回退模型
            except _LLMTransientError as exc:
                failures.append(str(exc))
                last_error = exc
                last_timed_out = exc.timed_out
                if exc.fail_over_now and cand_idx < len(candidates) - 1:
                    break  # 504/超时等慢故障：链上有下一个模型立即回退，末位模型仍按次数重试
            except Exception as exc:  # 兜底：任何未分类异常按瞬时处理，绝不让整链崩在第一次
                failures.append(f"模型 {cand['model']} 未预期异常：{type(exc).__name__}: {str(exc)[:200]}")
                last_error = exc
                last_timed_out = False
        if deadline_hit:
            break

    summary_tail = " | ".join(failures[-6:]) if failures else (str(last_error) if last_error else "无响应")
    if len(candidates) == 1:
        # 单模型（未配置回退）：保持旧版异常语义，前端提示文案不变
        if isinstance(last_error, _LLMHardError):
            raise RuntimeError(str(last_error)) from last_error
        if isinstance(last_error, _LLMTransientError):
            if last_error.timed_out:
                raise TimeoutError(str(last_error)) from last_error
            raise RuntimeError(str(last_error)) from last_error
        raise RuntimeError(f"LLM 请求未获得响应（模型 {primary['model']}）")

    on_failover({
        "type": "chain_failed",
        "from_model": primary["model"],
        "to_model": "",
        "chain": " → ".join(c["model"] for c in candidates),
        "attempts_per_model": attempts,
        "errors": [f[:220] for f in failures[-8:]],
        "elapsed_seconds": round(time.perf_counter() - call_started, 1),
        "deadline_hit": deadline_hit,
        "succeeded": False,
    })
    chain_names = " → ".join(c["model"] for c in candidates)
    if last_timed_out:
        raise TimeoutError(f"LLM 模型链全部超时（{chain_names}）：{summary_tail}")
    raise RuntimeError(f"LLM 模型链全部失败（{chain_names}）：{summary_tail}") from last_error


def test_llm_connection(reload_config: Callable[[], Dict[str, Any]], 
    base_url: str,
    api_key: str,
    model: str,
    api_format: str = "openai_chat",
    reasoning_effort: str = "auto",
    reasoning_type: str = "auto",
    timeout: float = 15.0,
    api_path: str = "",
) -> Dict[str, Any]:
    """Execute a real diagnostic ping across any of the 3 API formats."""
    cleaned_url = str(base_url or "").strip().rstrip("/")
    if not cleaned_url.startswith(("http://", "https://")):
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": 0,
            "model": model,
            "error": "Base URL 格式无效，必须以 http:// 或 https:// 开头",
            "recommendation": "请检查并填写正确的服务 Base URL，例如 https://api.openai.com/v1",
        }

    test_messages = [
        {"role": "user", "content": "Ping test for connection. Please respond with exactly the single word: PONG"}
    ]

    endpoint, headers, payload = build_request_spec(
        model=model,
        messages=test_messages,
        base_url=cleaned_url,
        api_key=api_key,
        api_format=api_format,
        reasoning_effort=reasoning_effort,
        temperature=0.1,
        reasoning_type=reasoning_type,
        api_path=api_path or _lookup_api_path(reload_config, cleaned_url, model),
    )

    t0 = time.perf_counter()
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            status_code = resp.getcode()
            body_bytes = resp.read()
            res_json = json.loads(body_bytes.decode("utf-8", errors="replace"))

            content = ""
            reasoning_content = ""
            usage = res_json.get("usage", {})

            if api_format == "claude_messages":
                content = "".join(c.get("text", "") for c in res_json.get("content", []) if c.get("type") == "text")
                reasoning_content = "\n".join(c.get("thinking", "") for c in res_json.get("content", []) if c.get("type") == "thinking")
            elif api_format == "openai_responses":
                content = str(res_json.get("output_text") or "")
                for item in res_json.get("output", []):
                    if item.get("type") == "reasoning":
                        reasoning_content += str(item.get("content") or item.get("summary") or "")
            else:
                msg = res_json.get("choices", [{}])[0].get("message", {})
                content = str(msg.get("content", ""))
                reasoning_content = str(msg.get("reasoning_content") or "")

            content = content.strip()
            reasoning_tokens = (
                usage.get("completion_tokens_details", {}).get("reasoning_tokens")
                or usage.get("output_tokens_details", {}).get("reasoning_tokens")
                or usage.get("reasoning_tokens")
                or (len(reasoning_content.split()) if reasoning_content else None)
            )

            format_label = next((f["name"] for f in SUPPORTED_API_FORMATS if f["id"] == api_format), api_format)

            return {
                "ok": True,
                "status_code": status_code,
                "latency_ms": latency_ms,
                "model": model,
                "api_format": api_format,
                "api_format_name": format_label,
                "endpoint": endpoint,
                "response_preview": content[:120] if content else "(响应成功，返回空正文)",
                "reasoning_detected": bool(reasoning_content),
                "reasoning_tokens": reasoning_tokens,
                "total_tokens": usage.get("total_tokens") or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)),
                "payload_sent": {k: v for k, v in payload.items() if k not in ("messages", "input")},
                "compatibility_note": f"协议 {api_format} 连接与解析成功" + (" · 已捕获链式推演输出" if reasoning_content else ""),
            }

    except urllib.error.HTTPError as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        status_code = exc.code
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass

        # Adaptive fallback retry
        is_param_conflict = any(kw in err_body.lower() for kw in [
            "reasoning_effort", "temperature", "unrecognized request argument", "unknown parameter", "invalid parameter"
        ])
        if is_param_conflict and api_format == "openai_chat":
            try:
                fb_payload = {"model": model, "messages": test_messages}
                fb_req = urllib.request.Request(endpoint, data=json.dumps(fb_payload).encode("utf-8"), headers=headers)
                t1 = time.perf_counter()
                with urllib.request.urlopen(fb_req, timeout=timeout) as fb_resp:
                    fb_latency = int((time.perf_counter() - t1) * 1000)
                    fb_body = fb_resp.read().decode("utf-8", errors="replace")
                    fb_json = json.loads(fb_body)
                    fb_msg = fb_json.get("choices", [{}])[0].get("message", {})
                    return {
                        "ok": True,
                        "status_code": 200,
                        "latency_ms": fb_latency,
                        "model": model,
                        "api_format": api_format,
                        "endpoint": endpoint,
                        "response_preview": str(fb_msg.get("content", ""))[:120] or "OK",
                        "warning": f"上游服务拒绝了参数 ({err_body[:80]}…)，系统已自适应去除冲突参数并测试成功",
                        "compatibility_note": "模型不支持自定义 reasoning_effort 或 temperature 参数；实际调用将自动去除",
                    }
            except Exception:
                pass

        rec = "请核对配置"
        if status_code == 401:
            rec = "API Key 认证失败，请检查密钥是否正确或是否已过期"
        elif status_code == 404:
            rec = f"端点未找到 (404)，请检查 API 格式协议是否选对（如 Anthropic 需选 Claude Messages，OpenAI 选 Chat 或 Responses），以及 Base URL 路径是否正确"
        elif status_code == 429:
            rec = "请求频次超限或账户配额/余额不足 (429 Rate Limit)"
        elif status_code in (500, 502, 503):
            rec = "上游大模型服务暂时不可用或内部服务故障"

        return {
            "ok": False,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "model": model,
            "api_format": api_format,
            "endpoint": endpoint,
            "error": f"HTTP {status_code}: {err_body[:240]}",
            "recommendation": rec,
        }

    except urllib.error.URLError as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": latency_ms,
            "model": model,
            "api_format": api_format,
            "endpoint": endpoint,
            "error": f"网络连接失败: {exc.reason}",
            "recommendation": "无法连接到该 Base URL，请检查网络通畅度、DNS 解析或代理网关配置",
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": latency_ms,
            "model": model,
            "api_format": api_format,
            "endpoint": endpoint,
            "error": f"测试执行异常: {str(exc)}",
            "recommendation": "发生未预期的连接错误，请检查输入配置格式",
        }

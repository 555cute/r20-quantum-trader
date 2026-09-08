"""Unified multi-format LLM management, connection testing, and runtime dispatch.
Supports:
1. openai_chat: OpenAI Standard /chat/completions (OpenAI, Gemini OpenAI endpoint, DeepSeek, etc.)
2. openai_responses: OpenAI Structured /responses API (Responses API format)
3. claude_messages: Anthropic Claude /messages API (Claude 3.7 / 3.5 native)
"""
from __future__ import annotations
import copy
import json
import os
import re
import socket
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LLM_CONFIG_FILE = DATA_DIR / "llm_models.json"
LEGACY_PROVIDERS_FILE = DATA_DIR / "llm_providers.json"

SUPPORTED_API_FORMATS = [
    {"id": "openai_chat", "name": "OpenAI Chat (/chat/completions)", "desc": "标准 ChatML 对话格式，兼容 OpenAI/Gemini/DeepSeek/主流中继"},
    {"id": "openai_responses", "name": "OpenAI Responses (/responses)", "desc": "OpenAI 专属 Responses API 结构化接口"},
    {"id": "claude_messages", "name": "Claude Messages (/messages)", "desc": "Anthropic Claude 原生 Messages API，支持原生长思维链"},
]

STANDARD_REASONING_EFFORTS = ["max", "xhigh", "high", "medium", "low", "minimal", "none", "auto"]

DEFAULT_MAX_OUTPUT_TOKENS = 4096
MAX_MAX_OUTPUT_TOKENS = 128000


class LlmProtocolError(RuntimeError):
    """Safe, typed LLM failure. Never includes headers, keys, prompts, or full bodies."""

    stage = "protocol"

    def __init__(
        self,
        message: str,
        *,
        output_chars: int = 0,
        usage: Optional[Dict[str, Any]] = None,
        reason: str = "",
    ) -> None:
        super().__init__(message)
        self.output_chars = int(output_chars or 0)
        self.usage = usage if isinstance(usage, dict) else {}
        self.reason = str(reason or "")


class LlmEnvelopeJsonError(LlmProtocolError):
    stage = "envelope_json"


class LlmIncompleteError(LlmProtocolError):
    stage = "incomplete"


class LlmRefusalError(LlmProtocolError):
    stage = "refusal"


class LlmEmptyContentError(LlmProtocolError):
    stage = "empty_content"


class LlmBusinessJsonError(LlmProtocolError):
    stage = "business_json"


class LlmHttpError(LlmProtocolError):
    stage = "http"


_SECRET_FRAGMENT_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+)", re.I)


def normalize_max_output_tokens(value: Any, *, default: int = DEFAULT_MAX_OUTPUT_TOKENS, strict: bool = False) -> int:
    """Validate an output-token limit. Config load is lenient; admin writes may be strict."""
    if value is None or value == "":
        if strict:
            raise ValueError("max_output_tokens is required")
        return int(default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        if strict:
            raise ValueError("max_output_tokens must be an integer") from None
        return int(default)
    if parsed < 1 or parsed > MAX_MAX_OUTPUT_TOKENS:
        if strict:
            raise ValueError(f"max_output_tokens must be between 1 and {MAX_MAX_OUTPUT_TOKENS}")
        return int(default)
    return parsed


def _chat_output_token_field(model: str) -> str:
    m = (model or "").lower()
    if m.startswith(("o1", "o3", "o4")) or "gpt-5" in m or "gpt-6" in m:
        return "max_completion_tokens"
    return "max_tokens"


def _safe_error_excerpt(raw: str, limit: int = 160) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict):
                text = str(err.get("message") or err.get("code") or "")
            elif isinstance(err, str):
                text = err
            else:
                text = str(parsed.get("message") or parsed.get("msg") or "")
    except Exception:
        pass
    text = _SECRET_FRAGMENT_RE.sub("[redacted]", text).replace("\n", " ").strip()
    return text[:limit]


def _strip_markdown_json_fence(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def parse_model_json_object(text: str) -> Dict[str, Any]:
    """Strict business JSON object parse. Permits an existing Markdown fence only; never repairs braces or quotes."""
    raw = str(text or "")
    cleaned = _strip_markdown_json_fence(raw)
    if not cleaned:
        raise LlmBusinessJsonError(
            f"LLM business JSON is empty (output_chars={len(raw)})",
            output_chars=len(raw),
        )
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LlmBusinessJsonError(
            f"LLM business JSON parse failed at pos {exc.pos}: {exc.msg} (output_chars={len(raw)})",
            output_chars=len(raw),
        ) from None
    if not isinstance(parsed, dict):
        raise LlmBusinessJsonError(
            f"LLM business JSON root must be an object (output_chars={len(raw)})",
            output_chars=len(raw),
        )
    return parsed


def _extract_protocol_content(api_format: str, res_json: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    usage = res_json.get("usage") if isinstance(res_json.get("usage"), dict) else {}
    content = ""
    reasoning_content = ""
    if api_format == "claude_messages":
        text_chunks = [c.get("text", "") for c in res_json.get("content", []) if isinstance(c, dict) and c.get("type") == "text"]
        thinking_chunks = [c.get("thinking", "") for c in res_json.get("content", []) if isinstance(c, dict) and c.get("type") == "thinking"]
        content = "".join(str(x) for x in text_chunks)
        reasoning_content = "\n".join(str(x) for x in thinking_chunks)
        if not usage:
            raw_usage = res_json.get("usage") if isinstance(res_json.get("usage"), dict) else {}
            usage = {
                "input_tokens": raw_usage.get("input_tokens", 0),
                "output_tokens": raw_usage.get("output_tokens", 0),
                "total_tokens": int(raw_usage.get("input_tokens") or 0) + int(raw_usage.get("output_tokens") or 0),
            }
    elif api_format == "openai_responses":
        content = str(res_json.get("output_text") or "")
        if not content.strip():
            for item in res_json.get("output") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "message":
                    for part in item.get("content") or []:
                        if isinstance(part, dict) and (part.get("type") == "output_text" or "text" in part):
                            content += str(part.get("text", ""))
                elif item.get("type") == "reasoning":
                    reasoning_content += str(item.get("content") or item.get("summary") or "")
        else:
            for item in res_json.get("output") or []:
                if isinstance(item, dict) and item.get("type") == "reasoning":
                    reasoning_content += str(item.get("content") or item.get("summary") or "")
    else:
        choices = res_json.get("choices") or []
        msg = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
        if not isinstance(msg, dict):
            msg = {}
        content = str(msg.get("content") or "")
        reasoning_content = str(msg.get("reasoning_content") or "")
    return content.strip(), reasoning_content.strip(), usage


def _protocol_refusal_reason(api_format: str, res_json: Dict[str, Any]) -> str:
    if api_format == "openai_responses":
        for item in res_json.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "refusal" or str(item.get("status") or "").lower() == "refused":
                return "refusal"
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "refusal":
                    return "refusal"
    elif api_format == "claude_messages":
        if str(res_json.get("stop_reason") or "").lower() == "refusal":
            return "refusal"
    else:
        choices = res_json.get("choices") or []
        if choices and isinstance(choices[0], dict):
            ch = choices[0]
            if str(ch.get("finish_reason") or "").lower() == "content_filter":
                return "content_filter"
            msg = ch.get("message") if isinstance(ch.get("message"), dict) else {}
            if msg.get("refusal"):
                return "refusal"
    return ""


def _protocol_incomplete_reason(api_format: str, res_json: Dict[str, Any]) -> str:
    if api_format == "openai_responses":
        status = str(res_json.get("status") or "").lower()
        if status in ("incomplete", "cancelled"):
            details = res_json.get("incomplete_details") if isinstance(res_json.get("incomplete_details"), dict) else {}
            return str(details.get("reason") or status)
        if status == "failed":
            return "failed"
    elif api_format == "claude_messages":
        if str(res_json.get("stop_reason") or "").lower() == "max_tokens":
            return "max_tokens"
    else:
        choices = res_json.get("choices") or []
        if choices and isinstance(choices[0], dict):
            if str(choices[0].get("finish_reason") or "").lower() == "length":
                return "length"
    return ""


def _finalize_protocol_response(api_format: str, res_json: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    content, reasoning_content, usage = _extract_protocol_content(api_format, res_json)
    output_chars = len(content)
    refusal = _protocol_refusal_reason(api_format, res_json)
    if refusal:
        raise LlmRefusalError(
            f"LLM response refused (reason={refusal}, output_chars={output_chars})",
            output_chars=output_chars,
            usage=usage,
            reason=refusal,
        )
    incomplete = _protocol_incomplete_reason(api_format, res_json)
    if incomplete:
        raise LlmIncompleteError(
            f"LLM response incomplete (reason={incomplete}, output_chars={output_chars})",
            output_chars=output_chars,
            usage=usage,
            reason=incomplete,
        )
    if not content:
        raise LlmEmptyContentError(
            f"LLM response empty (output_chars=0)",
            output_chars=0,
            usage=usage,
        )
    return content, reasoning_content, usage


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}{'*' * 8}{value[-visible:]}"


def _detect_reasoning_type(model_id: str) -> str:
    m = model_id.lower()
    if "deepseek-reasoner" in m or "deepseek-r1" in m or "-r1" in m:
        return "deepseek_reasoner"
    if (
        m.startswith(("o1", "o3", "o4"))
        or "/o1" in m or "/o3" in m or "/o4" in m
        or "gemini" in m
        or "claude-3-7" in m
        or "claude-3.7" in m
        or "qwq" in m
        # qwen3 全系（qwen3.x、qwen-3.x、qwen3-vl 等）为思考模型：
        # 旧规则把所有含 "qwen" 的模型判为 none，导致 qwen3.x 的
        # reasoning_effort 参数在运行时被静默丢弃（2026-09 用户反馈）。
        or "qwen3" in m or "qwen-3" in m
    ):
        return "standard_effort"
    if "chat" in m or "gpt-4o" in m or "gpt-3" in m or "qwen" in m or "llama" in m:
        return "none"
    return "auto"


def _detect_capabilities(model_id: str) -> List[str]:
    m = model_id.lower()
    caps = ["chat"]
    # 视觉能力按「显式多模态标记 ∪ 家族默认」判定，而非靠 flash 这类词——
    # 历史版本把 "flash" 当视觉关键词，会误标 deepseek-v4-flash 等纯文本模型。
    # 该网关下 qwen / glm / gemini / claude / gpt / grok 家族的新式模型普遍多模态，
    # 归为视觉家族；deepseek 归纯文本家族，除非名字带显式 vision 标记。
    vision_markers = ["vision", "image", "omni", "multimodal", "vl-", "-vl", "_vl", ".vl"]
    vision_families = ["gemini", "claude", "gpt-4o", "gpt-5", "gpt-6", "grok", "muse", "qwen", "glm"]
    text_only_families = ["deepseek"]
    tokens = {t for t in re.split(r"[^a-z0-9]+", m) if t}
    has_vision_marker = any(k in m for k in vision_markers) or bool(tokens & {"vl", "4v", "5v", "6v"})
    in_vision_family = any(f in m for f in vision_families)
    in_text_family = any(f in m for f in text_only_families)
    if has_vision_marker or (in_vision_family and not in_text_family):
        caps.append("vision")
    if not ("-r1-distill" in m or "-thinking" in m):
        caps.append("tools")
    if any(k in m for k in ["reasoner", "r1", "o1", "o3", "o4", "gpt-5", "gpt-6", "high", "thinking", "qwq", "deepseek-r1"]):
        caps.append("reasoning")
    return caps


def _detect_api_format(url: str, model_id: str) -> str:
    u = url.lower()
    m = model_id.lower()
    if "anthropic.com" in u or "claude" in u or "claude" in m and "messages" in u:
        return "claude_messages"
    if "responses" in u:
        return "openai_responses"
    return "openai_chat"


DEFAULT_PROVIDERS = [
    {
        "id": "openai",
        "name": "OpenAI",
        "type": "OpenAI",
        "group": "基础供应",
        "enabled": True,
        "multi_key_enabled": False,
        "response_api_enabled": False,
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "api_format": "openai_chat",
        "api_path": "/chat/completions",
        "description": "OpenAI 兼容协议端点，支持中继网关与官方直连",
        "models": [
            {
                "id": "gemini-3.7-flash-high",
                "name": "gemini-3.7-flash-high",
                "capabilities": ["chat", "vision", "tools", "reasoning"],
                "reasoning_type": "standard_effort",
                "reasoning_effort": "high",
                "context_length": 1048576,
                "description": "Gemini 3.7 Flash 深度推演版，极速响应与强逻辑决策",
            },
            {
                "id": "gemini-3.1-flash-image",
                "name": "gemini-3.1-flash-image",
                "capabilities": ["chat", "vision"],
                "reasoning_type": "none",
                "reasoning_effort": "none",
                "context_length": 131072,
                "description": "Gemini 多模态盘口图表视觉感知模型",
            },
        ],
    },
    {
        "id": "claude",
        "name": "Claude",
        "type": "Anthropic",
        "group": "基础供应",
        "enabled": False,
        "multi_key_enabled": False,
        "response_api_enabled": False,
        "base_url": "https://api.anthropic.com/v1",
        "api_key": "",
        "api_format": "claude_messages",
        "api_path": "/messages",
        "description": "Anthropic 官方原生 Messages API 直连",
        "models": [],
    },
    {
        "id": "gemini",
        "name": "Gemini",
        "type": "Gemini",
        "group": "基础供应",
        "enabled": False,
        "multi_key_enabled": False,
        "response_api_enabled": False,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key": "",
        "api_format": "openai_chat",
        "api_path": "/chat/completions",
        "description": "Google AI Studio 官方原生/OpenAI 兼容端点",
        "models": [],
    },
]



def init_llm_config() -> Dict[str, Any]:
    """Load or initialize clean, user-centric model configuration with multi-provider support."""
    from .config import settings

    data: Dict[str, Any] = {}
    if LLM_CONFIG_FILE.exists():
        try:
            with open(LLM_CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
        except Exception:
            data = {}

    # Extract current settings from .env / settings
    # 安全约束：不再内置任何私有中继网关作为静默默认，出口地址必须由用户显式配置。
    cur_url = getattr(settings, "llm_base_url", "") or os.getenv("LLM_BASE_URL") or ""
    cur_key = getattr(settings, "llm_api_key", "") or os.getenv("LLM_API_KEY") or ""
    cur_model = getattr(settings, "llm_model", "") or os.getenv("LLM_MODEL") or ""
    cur_effort = getattr(settings, "llm_reasoning_effort", "") or os.getenv("LLM_REASONING_EFFORT") or "high"

    existing_providers = data.get("providers", [])
    merged_providers: List[Dict[str, Any]] = []

    # 默认供应商只在「首次播种」时注入。播种完成后配置里落下 defaults_seeded 标记，
    # 此后用户删除的默认供应商绝不复活；openai 的 enabled 也只在播种时强制打开，
    # 之后尊重用户自己的开关。老配置文件（无标记）视为首次：合并一次并落标记，升级无感。
    seed_defaults = not data.get("defaults_seeded")
    default_by_id = {dp["id"]: dp for dp in DEFAULT_PROVIDERS}
    # (ignore legacy hardcoded providers from older versions)
    legacy_ids = {
        "siliconflow", "openrouter", "kelivoin", "tensdaq", "deepseek",
        "alhubmix", "suixiang", "dashscope", "zhipu", "grok", "volcengine"
    }

    for found in existing_providers:
        pid = found.get("id")
        if not pid or pid in legacy_ids:
            continue
        dp = default_by_id.get(pid)
        if dp:
            p_obj = copy.deepcopy(dp)
            p_obj.update(found)
            # Never overwrite models with global defaults if provider was already configured
            if "models" in found:
                p_obj["models"] = list(found.get("models", []))
            if pid == "openai":
                if not p_obj.get("api_key") and cur_key:
                    p_obj["api_key"] = cur_key
                if not p_obj.get("base_url"):
                    p_obj["base_url"] = cur_url
                if seed_defaults:
                    p_obj["enabled"] = True
            merged_providers.append(p_obj)
        else:
            merged_providers.append(found)

    if seed_defaults:
        have_ids = {p.get("id") for p in merged_providers}
        for dp in DEFAULT_PROVIDERS:
            if dp["id"] in have_ids:
                continue
            p_obj = copy.deepcopy(dp)
            if dp["id"] == "openai":
                if cur_key:
                    p_obj["api_key"] = cur_key
                if cur_url:
                    p_obj["base_url"] = cur_url
                p_obj["enabled"] = True
            merged_providers.append(p_obj)

    active_m_id = data.get("active_model_id") or cur_model or ""
    active_effort = data.get("active_reasoning_effort") or cur_effort or "high"
    cur_timeout = getattr(settings, "llm_thinking_timeout", 120.0) or float(os.getenv("LLM_THINKING_TIMEOUT", os.getenv("LLM_TIMEOUT_SECONDS", "120.0")))
    raw_timeout = data.get("thinking_timeout")
    thinking_timeout = float(raw_timeout) if raw_timeout is not None else float(cur_timeout or 120.0)
    cur_max_out = getattr(settings, "llm_max_output_tokens", None) or os.getenv("LLM_MAX_OUTPUT_TOKENS") or DEFAULT_MAX_OUTPUT_TOKENS
    raw_max_out = data.get("max_output_tokens")
    max_output_tokens = normalize_max_output_tokens(raw_max_out if raw_max_out is not None else cur_max_out)


    models_map: Dict[str, Dict[str, Any]] = {}
    for p in merged_providers:
        p_id = p.get("id", "")
        p_name = p.get("name", p_id)
        p_base = p.get("base_url", "")
        p_key = p.get("api_key", "")
        p_fmt = p.get("api_format", "openai_chat")
        for m in p.get("models", []):
            mid = m.get("id", "")
            if not mid:
                continue
            models_map[mid] = {
                "id": mid,
                "name": m.get("name", mid),
                "provider_id": p_id,
                "provider_name": p_name,
                "base_url": m.get("base_url") or p_base,
                "api_key": m.get("api_key") or p_key,
                "api_format": m.get("api_format") or p_fmt,
                "reasoning_type": m.get("reasoning_type", _detect_reasoning_type(mid)),
                "reasoning_effort": m.get("reasoning_effort") or m.get("default_effort", "high"),
                "capabilities": m.get("capabilities", _detect_capabilities(mid)),
                "context_length": m.get("context_length"),
                "max_output_tokens": m.get("max_output_tokens"),
                "description": m.get("description", ""),
            }

    # Preserve any custom models that were added by user or tests
    prov_by_id = {p.get("id"): p for p in merged_providers}
    for m in data.get("models", []):
        mid = m.get("id")
        if mid:
            if mid in models_map:
                fresh = models_map[mid]
                merged = dict(m)
                # 供应商凭据是唯一权威源：历史快照键/地址不得覆盖供应商当前值
                if fresh.get("api_key"):
                    merged["api_key"] = fresh["api_key"]
                if fresh.get("base_url"):
                    merged["base_url"] = fresh["base_url"]
                fresh.update(merged)
            else:
                entry = dict(m)
                prov = prov_by_id.get(entry.get("provider_id"))
                if prov:
                    # 顶层扁平模型同样按 provider_id 重挂供应商当前凭据——
                    # 否则轮换密钥后旧快照键永久粘住，模型必须删掉重加才能恢复
                    if prov.get("api_key"):
                        entry["api_key"] = prov["api_key"]
                    if prov.get("base_url"):
                        entry["base_url"] = prov["base_url"]
                    if not entry.get("api_format"):
                        entry["api_format"] = prov.get("api_format", "openai_chat")
                models_map[mid] = entry

    flat_models = list(models_map.values())
    if not any(m["id"] == active_m_id for m in flat_models) and flat_models:
        active_m_id = flat_models[0]["id"]

    config = {
        "version": "3.1",
        "defaults_seeded": True,
        "active_model_id": active_m_id,
        "active_reasoning_effort": active_effort,
        "thinking_timeout": thinking_timeout,
        "max_output_tokens": max_output_tokens,
        "providers": merged_providers,
        "models": flat_models,
    }
    _atomic_write_json(LLM_CONFIG_FILE, config)
    return config


# Backwards compatibility alias for app.py
LLM_PROVIDERS_FILE = LLM_CONFIG_FILE
init_llm_providers = init_llm_config


def save_llm_config(config: Dict[str, Any]) -> None:
    """唯一配置写入口：调用时解析 LLM_CONFIG_FILE 模块全局，测试沙箱 patch 必然生效。"""
    _atomic_write_json(LLM_CONFIG_FILE, config)


def load_llm_config(mask_keys: bool = True) -> Dict[str, Any]:
    """Return clean model configurations and configured providers matching modern client architecture."""
    config = init_llm_config()
    providers_list = config.get("providers", [])
    active_mid = config.get("active_model_id", "")
    active_effort = config.get("active_reasoning_effort", "high")

    res: Dict[str, Any] = {
        "version": config.get("version", "3.1"),
        "active_model_id": active_mid,
        "active_reasoning_effort": active_effort,
        "thinking_timeout": config.get("thinking_timeout", 120.0),
        "max_output_tokens": normalize_max_output_tokens(config.get("max_output_tokens")),
        "standard_reasoning_efforts": STANDARD_REASONING_EFFORTS,
        "supported_api_formats": SUPPORTED_API_FORMATS,
        "providers": [],
        "models": [],
        "active_provider_id": "openai",
    }

    for p in providers_list:
        pid = p.get("id", "")
        # ONLY return models that are explicitly registered under this specific provider
        models_in_p = list(p.get("models", []))
        formatted_p_models = []
        for m in models_in_p:
            m_id = m.get("id", "")
            formatted_p_models.append({
                "id": m_id,
                "name": m.get("name") or m_id,
                "capabilities": m.get("capabilities") or _detect_capabilities(m_id),
                "reasoning_type": m.get("reasoning_type") or _detect_reasoning_type(m_id),
                "reasoning_effort": m.get("reasoning_effort") or "high",
                "context_length": m.get("context_length"),
                "max_output_tokens": m.get("max_output_tokens"),
                "description": m.get("description", ""),
                "is_active": m_id == active_mid,
            })

        p_copy = {
            "id": pid,
            "name": p.get("name", pid),
            "type": p.get("type", p.get("name", pid)),
            "group": p.get("group", "其他"),
            "enabled": bool(p.get("enabled", False)),
            "multi_key_enabled": bool(p.get("multi_key_enabled", False)),
            "response_api_enabled": bool(p.get("response_api_enabled", False)),
            "base_url": p.get("base_url", ""),
            "api_format": p.get("api_format", "openai_chat"),
            "api_path": p.get("api_path", "/chat/completions"),
            "description": p.get("description", ""),
            "has_key": bool(p.get("api_key")),
            "models_count": len(models_in_p),
            "models": formatted_p_models,
        }
        if mask_keys:
            p_copy["api_key_masked"] = mask_secret(p.get("api_key", ""))
        else:
            p_copy["api_key"] = p.get("api_key", "")
        res["providers"].append(p_copy)

    # Flattened models for backward compatibility
    for m in config.get("models", []):
        m_pid = m.get("provider_id", "openai")
        p_entry = next((p for p in providers_list if p.get("id") == m_pid), None)
        m_key = m.get("api_key", "")
        has_key = bool(m_key or (p_entry and p_entry.get("api_key")))

        m_copy = {
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "provider_id": m_pid,
            "provider_name": m.get("provider_name") or (p_entry.get("name") if p_entry else "自定义"),
            "base_url": m.get("base_url", "") or (p_entry.get("base_url", "") if p_entry else ""),
            "api_format": m.get("api_format", "openai_chat"),
            "reasoning_type": m.get("reasoning_type", "auto"),
            "reasoning_effort": m.get("reasoning_effort", "high"),
            "capabilities": m.get("capabilities") or _detect_capabilities(m["id"]),
            "context_length": m.get("context_length"),
            "max_output_tokens": m.get("max_output_tokens"),
            "description": m.get("description", ""),
            "has_key": has_key,
            "is_active": m["id"] == active_mid,
        }
        if mask_keys:
            m_copy["api_key_masked"] = mask_secret(m_key) if m_key else (mask_secret(p_entry.get("api_key", "")) if p_entry and p_entry.get("api_key") else "")
        else:
            m_copy["api_key"] = m_key
        res["models"].append(m_copy)

    return res



def get_active_llm_runtime() -> Dict[str, Any]:
    """Retrieve active LLM credentials and configuration for runtime execution."""
    from .config import settings
    config = init_llm_config()
    active_mid = config.get("active_model_id", "")
    active_effort = config.get("active_reasoning_effort", "high")

    target_model = next((m for m in config.get("models", []) if m["id"] == active_mid), None)

    base_url = target_model.get("base_url") if target_model else getattr(settings, "llm_base_url", "")
    api_key = target_model.get("api_key") if target_model else getattr(settings, "llm_api_key", "")
    provider_id = target_model.get("provider_id", "") if target_model else ""
    provider_name = target_model.get("provider_name", "") if target_model else "默认"

    if target_model:
        t_base = target_model.get("base_url", "").rstrip("/")
        prov = next(
            (
                p for p in config.get("providers", [])
                if p.get("id") == provider_id or (t_base and p.get("base_url", "").rstrip("/") == t_base)
            ),
            None,
        )
        if prov:
            if not api_key:
                api_key = prov.get("api_key", "")
            if not base_url:
                base_url = prov.get("base_url", "")
            if not provider_name or provider_name == "自定义":
                provider_name = prov.get("name", provider_name)
            if not provider_id:
                provider_id = prov.get("id", "openai")

    base_url = (base_url or os.getenv("LLM_BASE_URL", "")).rstrip("/")
    if not base_url:
        raise RuntimeError(
            "LLM 出口未配置：请在 .env 设置 LLM_BASE_URL，或在后台「LLM Providers」中选择/新建供应商。"
            "出于数据流向透明要求，系统不再内置任何默认第三方中继网关。"
        )
    api_key = api_key or os.getenv("LLM_API_KEY", "")

    model_name = active_mid or getattr(settings, "llm_model", "") or os.getenv("LLM_MODEL", "")
    api_format = target_model.get("api_format") if target_model else _detect_api_format(base_url, model_name)
    reasoning_type = target_model.get("reasoning_type", "auto") if target_model else _detect_reasoning_type(model_name)
    thinking_timeout = float(
        target_model.get("thinking_timeout")
        if target_model and target_model.get("thinking_timeout")
        else config.get("thinking_timeout")
        or os.getenv("LLM_THINKING_TIMEOUT")
        or os.getenv("LLM_TIMEOUT_SECONDS")
        or getattr(settings, "llm_thinking_timeout", 120.0)
    )

    model_max = target_model.get("max_output_tokens") if target_model else None
    max_output_tokens = normalize_max_output_tokens(
        model_max if model_max not in (None, "") else (
            config.get("max_output_tokens")
            or os.getenv("LLM_MAX_OUTPUT_TOKENS")
            or getattr(settings, "llm_max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
        )
    )

    return {
        "model": model_name,
        "name": target_model.get("name", model_name) if target_model else model_name,
        "provider_name": provider_name or "默认",
        "provider_id": provider_id or "openai",
        "base_url": base_url,
        "api_key": api_key,
        "api_format": api_format,
        "reasoning_effort": active_effort,
        "reasoning_type": reasoning_type,
        "thinking_timeout": thinking_timeout,
        "max_output_tokens": max_output_tokens,
    }


def activate_provider_model(provider_id: str, model_id: str, reasoning_effort: Optional[str] = None, thinking_timeout: Optional[float] = None, max_output_tokens: Optional[int] = None) -> Dict[str, Any]:
    """One-click switch to activate a model. Updates config, .env, and encrypted store."""
    from .settings_store import update_env
    from .config import refresh_settings
    try:
        from r20_gateway.secrets import save_secrets
    except ImportError:
        save_secrets = None

    config = init_llm_config()
    target_model = next((m for m in config.get("models", []) if m["id"] == model_id), None)
    if not target_model:
        # Check providers models
        for p in config.get("providers", []):
            m_found = next((m for m in p.get("models", []) if m.get("id") == model_id), None)
            if m_found:
                target_model = {
                    "id": model_id,
                    "name": m_found.get("name", model_id),
                    "provider_id": p.get("id"),
                    "provider_name": p.get("name"),
                    "base_url": p.get("base_url"),
                    "api_key": p.get("api_key"),
                    "api_format": p.get("api_format", "openai_chat"),
                    "reasoning_type": m_found.get("reasoning_type", _detect_reasoning_type(model_id)),
                    "reasoning_effort": m_found.get("reasoning_effort", "high"),
                    "description": m_found.get("description", ""),
                }
                config.setdefault("models", []).append(target_model)
                break

    if not target_model:
        target_model = {
            "id": model_id,
            "name": model_id,
            "provider_name": "自定义",
            "base_url": os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            "api_key": os.getenv("LLM_API_KEY", ""),
            "api_format": "openai_chat",
            "reasoning_type": _detect_reasoning_type(model_id),
            "reasoning_effort": "high",
            "description": "一键激活时自动收录",
        }
        config.setdefault("models", []).append(target_model)

    effort = reasoning_effort or target_model.get("reasoning_effort") or "high"
    if effort not in STANDARD_REASONING_EFFORTS:
        effort = "auto"

    config["active_model_id"] = model_id
    config["active_reasoning_effort"] = effort
    if thinking_timeout is not None:
        timeout_val = max(5.0, min(float(thinking_timeout), 1800.0))
        config["thinking_timeout"] = timeout_val
    if max_output_tokens is not None:
        config["max_output_tokens"] = normalize_max_output_tokens(max_output_tokens, strict=True)
    _atomic_write_json(LLM_CONFIG_FILE, config)

    # Sync to .env and secrets
    base_url = target_model.get("base_url", "")
    api_key = target_model.get("api_key", "")
    m_pid = target_model.get("provider_id")
    if m_pid:
        prov = next((p for p in config.get("providers", []) if p.get("id") == m_pid), None)
        if prov:
            if not api_key:
                api_key = prov.get("api_key", "")
            if not base_url:
                base_url = prov.get("base_url", "")

    base_url = (base_url or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")

    env_values = {
        "LLM_BASE_URL": base_url,
        "LLM_MODEL": model_id,
        "LLM_REASONING_EFFORT": effort,
    }
    if thinking_timeout is not None:
        timeout_val = max(5.0, min(float(thinking_timeout), 1800.0))
        env_values["LLM_THINKING_TIMEOUT"] = str(int(timeout_val) if timeout_val.is_integer() else timeout_val)
    if max_output_tokens is not None:
        env_values["LLM_MAX_OUTPUT_TOKENS"] = str(config["max_output_tokens"])
    if api_key:
        env_values["LLM_API_KEY"] = api_key
        if save_secrets:
            save_secrets({"LLM_API_KEY": api_key})

    update_env(env_values)
    refresh_settings()

    return {
        "success": True,
        "active_model_id": model_id,
        "active_model_name": target_model.get("name"),
        "active_reasoning_effort": effort,
        "thinking_timeout": config.get("thinking_timeout", 120.0),
        "max_output_tokens": normalize_max_output_tokens(config.get("max_output_tokens")),
        "base_url": base_url,
        "api_format": target_model.get("api_format", "openai_chat"),
        "provider_name": target_model.get("provider_name", "自定义"),
        "active_provider_id": target_model.get("provider_id", "openai"),
        "active_provider_name": target_model.get("provider_name", "自定义"),
    }


def update_llm_settings(
    active_model_id: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    thinking_timeout: Optional[float] = None,
    max_output_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Update global LLM settings including model, reasoning effort, thinking timeout, and max output tokens."""
    from .settings_store import update_env
    from .config import refresh_settings

    config = init_llm_config()
    env_values: Dict[str, Any] = {}

    if active_model_id:
        config["active_model_id"] = active_model_id
        env_values["LLM_MODEL"] = active_model_id

    if reasoning_effort:
        config["active_reasoning_effort"] = reasoning_effort
        env_values["LLM_REASONING_EFFORT"] = reasoning_effort

    if thinking_timeout is not None:
        val = max(5.0, min(float(thinking_timeout), 1800.0))
        config["thinking_timeout"] = val
        env_values["LLM_THINKING_TIMEOUT"] = str(int(val) if val.is_integer() else val)

    if max_output_tokens is not None:
        val = normalize_max_output_tokens(max_output_tokens, strict=True)
        config["max_output_tokens"] = val
        env_values["LLM_MAX_OUTPUT_TOKENS"] = str(val)

    _atomic_write_json(LLM_CONFIG_FILE, config)
    if env_values:
        update_env(env_values)
        refresh_settings()

    return {
        "success": True,
        "active_model_id": config.get("active_model_id"),
        "active_reasoning_effort": config.get("active_reasoning_effort"),
        "thinking_timeout": config.get("thinking_timeout", 120.0),
        "max_output_tokens": normalize_max_output_tokens(config.get("max_output_tokens")),
    }


def upsert_model(provider_id: str, model_data: Dict[str, Any]) -> Dict[str, Any]:
    """Add or update a custom model definition."""
    mid = str(model_data.get("id", "")).strip()
    name = str(model_data.get("name", "")).strip() or mid
    base_url = str(model_data.get("base_url", "")).strip().rstrip("/")
    api_key = str(model_data.get("api_key", "")).strip()
    api_format = str(model_data.get("api_format", "openai_chat")).strip()
    provider_name = str(model_data.get("provider_name", "")).strip()
    reasoning_type = str(model_data.get("reasoning_type", "auto")).strip()
    default_effort = str(model_data.get("default_effort") or model_data.get("reasoning_effort", "high")).strip()
    desc = str(model_data.get("description", "")).strip()
    caps = model_data.get("capabilities") or _detect_capabilities(mid)
    ctx_len = model_data.get("context_length")
    model_max_tokens = None
    if "max_output_tokens" in model_data and model_data.get("max_output_tokens") not in (None, ""):
        model_max_tokens = normalize_max_output_tokens(model_data.get("max_output_tokens"), strict=True)

    if not mid:
        raise ValueError("模型 ID 不能为空")

    config = init_llm_config()

    prov = None
    if provider_id and provider_id != "custom":
        prov = next((p for p in config.get("providers", []) if p["id"] == provider_id), None)
    if not prov and provider_name:
        prov = next((p for p in config.get("providers", []) if p.get("name") == provider_name), None)

    if prov:
        if not base_url:
            base_url = prov.get("base_url", "")
        # 不再把供应商密钥快照进模型条目：密钥唯一存放处是供应商，
        # 读取时由 init_llm_config 合并注入；轮换密钥即刻对全部模型生效
        if not provider_name:
            provider_name = prov.get("name", "自定义")
        if not provider_id:
            provider_id = prov.get("id", "openai")

    if not base_url or not base_url.startswith(("http://", "https://")):
        active = get_active_llm_runtime()
        base_url = active.get("base_url", "https://api.openai.com/v1")

    valid_formats = [f["id"] for f in SUPPORTED_API_FORMATS]
    if api_format not in valid_formats:
        api_format = _detect_api_format(base_url, mid)

    models = config.setdefault("models", [])
    existing = next((m for m in models if m["id"] == mid), None)

    if existing:
        existing["name"] = name
        existing["provider_id"] = provider_id or existing.get("provider_id", "openai")
        existing["provider_name"] = provider_name or existing.get("provider_name", "自定义")
        existing["base_url"] = base_url
        if api_key:
            existing["api_key"] = api_key
        existing["api_format"] = api_format
        existing["reasoning_type"] = reasoning_type
        existing["reasoning_effort"] = default_effort
        existing["capabilities"] = caps
        existing["context_length"] = ctx_len
        if model_max_tokens is not None:
            existing["max_output_tokens"] = model_max_tokens
        elif "max_output_tokens" in model_data:
            existing.pop("max_output_tokens", None)
        existing["description"] = desc
    else:
        models.append({
            "id": mid,
            "name": name,
            "provider_id": provider_id or "openai",
            "provider_name": provider_name or "自定义",
            "base_url": base_url,
            "api_key": api_key,
            "api_format": api_format,
            "reasoning_type": reasoning_type,
            "reasoning_effort": default_effort,
            "capabilities": caps,
            "context_length": ctx_len,
            "description": desc,
        })
        if model_max_tokens is not None:
            models[-1]["max_output_tokens"] = model_max_tokens

    # Also update provider's local models array
    if prov:
        prov_models = prov.setdefault("models", [])
        p_existing = next((m for m in prov_models if m.get("id") == mid), None)
        if p_existing:
            p_existing["name"] = name
            p_existing["capabilities"] = caps
            p_existing["reasoning_type"] = reasoning_type
            p_existing["reasoning_effort"] = default_effort
            p_existing["context_length"] = ctx_len
            if model_max_tokens is not None:
                p_existing["max_output_tokens"] = model_max_tokens
            elif "max_output_tokens" in model_data:
                p_existing.pop("max_output_tokens", None)
            p_existing["description"] = desc
        else:
            prov_models.append({
                "id": mid,
                "name": name,
                "capabilities": caps,
                "reasoning_type": reasoning_type,
                "reasoning_effort": default_effort,
                "context_length": ctx_len,
                "description": desc,
            })
            if model_max_tokens is not None:
                prov_models[-1]["max_output_tokens"] = model_max_tokens

    _atomic_write_json(LLM_CONFIG_FILE, config)
    return {
        "model_id": mid,
        "name": name,
        "base_url": base_url,
        "api_format": api_format,
        "provider_id": provider_id or "openai",
    }


def delete_model(provider_id: str, model_id: str) -> bool:
    """Delete a custom model."""
    config = init_llm_config()
    if config.get("active_model_id") == model_id:
        raise ValueError("不能删除当前正在使用的模型；请先切换到其他模型后再删除。")

    models = config.get("models", [])
    filtered = [m for m in models if m["id"] != model_id]

    for prov in config.get("providers", []):
        if not provider_id or provider_id == "custom" or prov.get("id") == provider_id:
            prov["models"] = [m for m in prov.get("models", []) if m.get("id") != model_id]

    if len(filtered) == len(models):
        return False

    config["models"] = filtered
    _atomic_write_json(LLM_CONFIG_FILE, config)
    return True


def upsert_provider(provider_data: Dict[str, Any]) -> Dict[str, Any]:
    """Add or update an LLM provider definition."""
    pid = str(provider_data.get("id", "")).strip().lower()
    name = str(provider_data.get("name", "")).strip() or pid
    p_type = str(provider_data.get("type", "")).strip() or name
    p_group = str(provider_data.get("group", "")).strip() or "其他"
    enabled = bool(provider_data.get("enabled", False)) if "enabled" in provider_data else None
    multi_key_enabled = bool(provider_data.get("multi_key_enabled", False))
    response_api_enabled = bool(provider_data.get("response_api_enabled", False))
    base_url = str(provider_data.get("base_url", "")).strip().rstrip("/")
    api_key = str(provider_data.get("api_key", "")).strip()
    api_format = str(provider_data.get("api_format", "openai_chat")).strip()
    api_path = str(provider_data.get("api_path", "/chat/completions")).strip()
    desc = str(provider_data.get("description", "")).strip()

    if not api_format:
        api_format = "claude_messages" if "claude" in pid or "anthropic" in base_url.lower() else "openai_chat"

    # Automatically synchronize api_path with selected api_format if default was provided
    if api_path in ["/chat/completions", "/messages", "/responses", ""]:
        if api_format == "claude_messages":
            api_path = "/messages"
        elif api_format == "openai_responses":
            api_path = "/responses"
        else:
            api_path = "/chat/completions"

    response_api_enabled = (api_format == "openai_responses")

    if not pid:
        pid = re.sub(r"[^a-zA-Z0-9_\-]", "", name.lower()) or f"prov-{int(time.time())}"

    if not base_url or not base_url.startswith(("http://", "https://")):
        raise ValueError("供应商 Base URL 必须以 http:// 或 https:// 开头")

    config = init_llm_config()
    providers = config.setdefault("providers", [])
    existing = next((p for p in providers if p["id"] == pid), None)
    if existing:
        existing["name"] = name
        existing["type"] = p_type
        existing["group"] = p_group
        if enabled is not None:
            existing["enabled"] = enabled
        existing["multi_key_enabled"] = multi_key_enabled
        existing["response_api_enabled"] = response_api_enabled
        existing["base_url"] = base_url
        if api_key:
            existing["api_key"] = api_key
        existing["api_format"] = api_format
        existing["api_path"] = api_path
        existing["description"] = desc
    else:
        providers.append({
            "id": pid,
            "name": name,
            "type": p_type,
            "group": p_group,
            "enabled": enabled if enabled is not None else False,
            "multi_key_enabled": multi_key_enabled,
            "response_api_enabled": response_api_enabled,
            "base_url": base_url,
            "api_key": api_key,
            "api_format": api_format,
            "api_path": api_path,
            "description": desc,
            "models": [],
        })

    # ── 凭据轮换联动：供应商是密钥唯一权威源 ──
    # 1) 刷新该供应商下扁平缓存中的历史快照键/地址，杜绝旧键粘住导致"改完密钥模型全连不上"；
    # 2) 激活模型属于该供应商时，把新凭据回写全局 .env 与密钥库，交易引擎运行时同步对齐。
    if existing:
        affected = [m for m in config.get("models", []) if m.get("provider_id") == pid]
        for mm in affected:
            mm["base_url"] = base_url
            if api_key:
                mm["api_key"] = api_key
        active_mid = config.get("active_model_id", "")
        if affected and any(mm.get("id") == active_mid for mm in affected):
            try:
                from .settings_store import update_env
                from .config import refresh_settings
                try:
                    from r20_gateway.secrets import save_secrets
                except ImportError:
                    save_secrets = None
                env_values = {"LLM_BASE_URL": base_url}
                if api_key:
                    env_values["LLM_API_KEY"] = api_key
                    if save_secrets:
                        save_secrets({"LLM_API_KEY": api_key})
                update_env(env_values)
                refresh_settings()
            except Exception:
                pass

    _atomic_write_json(LLM_CONFIG_FILE, config)
    return {"id": pid, "name": name, "base_url": base_url}


def toggle_provider(provider_id: str, enabled: Optional[bool] = None) -> Dict[str, Any]:
    """Toggle a provider's enabled/disabled state."""
    config = init_llm_config()
    providers = config.get("providers", [])
    p = next((x for x in providers if x["id"] == provider_id), None)
    if not p:
        raise ValueError(f"供应商 {provider_id} 未找到")
    if enabled is None:
        p["enabled"] = not p.get("enabled", False)
    else:
        p["enabled"] = bool(enabled)
    _atomic_write_json(LLM_CONFIG_FILE, config)
    return {"id": provider_id, "enabled": p["enabled"]}


def clear_provider_models(provider_id: str) -> bool:
    """Clear all models under a specific provider."""
    config = init_llm_config()
    providers = config.get("providers", [])
    p = next((x for x in providers if x["id"] == provider_id), None)
    if not p:
        return False
    active_mid = config.get("active_model_id", "")
    if any(m.get("id") == active_mid for m in p.get("models", [])):
        raise ValueError(
            f"供应商 {provider_id} 名下挂着当前激活模型 {active_mid}；请先切换主脑模型再清空。"
        )
    p["models"] = []
    config["models"] = [m for m in config.get("models", []) if m.get("provider_id") != provider_id]
    _atomic_write_json(LLM_CONFIG_FILE, config)
    return True


def delete_provider(provider_id: str) -> bool:
    """Delete a provider definition (cascades to its models).

    保护：名下挂着当前激活模型（或顶层仍有其模型）的供应商不可删除，
    避免主脑 active_model_id 悬空或顶层残留幽灵模型。
    """
    config = init_llm_config()
    providers = config.get("providers", [])
    target = next((p for p in providers if p.get("id") == provider_id), None)
    if not target:
        return False
    active_mid = config.get("active_model_id", "")
    if any(m.get("id") == active_mid for m in target.get("models", [])):
        raise ValueError(
            f"供应商 {provider_id} 名下挂着当前激活模型 {active_mid}；请先切换主脑模型再删除。"
        )
    config["providers"] = [p for p in providers if p.get("id") != provider_id]
    # 级联：顶层扁平列表里属于该供应商的模型一并移除，避免幽灵模型
    config["models"] = [
        m for m in config.get("models", []) if m.get("provider_id") != provider_id
    ]
    _atomic_write_json(LLM_CONFIG_FILE, config)
    return True


def fetch_remote_models(
    base_url: str = "",
    api_key: str = "",
    provider_id: Optional[str] = None,
    timeout: float = 12.0,
) -> Dict[str, Any]:
    """Fetch live models list from an OpenAI / OpenRouter / Anthropic compatible endpoint."""
    cleaned_url = str(base_url or "").strip().rstrip("/")
    config = init_llm_config()

    if not cleaned_url and provider_id:
        prov = next((p for p in config.get("providers", []) if p["id"] == provider_id), None)
        if prov:
            cleaned_url = prov.get("base_url", "").strip().rstrip("/")
            if not api_key:
                api_key = prov.get("api_key", "")

    if not cleaned_url:
        active = get_active_llm_runtime()
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
    if "anthropic.com" in cleaned_url:
        ep = f"{cleaned_url}/models" if not cleaned_url.endswith("/v1") else f"{cleaned_url}/models"
        hdrs = {"x-api-key": api_key, "anthropic-version": "2023-06-01"} if api_key else {}
        endpoints.append((ep, hdrs))
    elif cleaned_url.endswith("/v1"):
        endpoints.append((f"{cleaned_url}/models", {"Authorization": f"Bearer {api_key}"} if api_key else {}))
        endpoints.append((f"{cleaned_url[:-3]}/models", {"Authorization": f"Bearer {api_key}"} if api_key else {}))
    elif cleaned_url.endswith("/models"):
        endpoints.append((cleaned_url, {"Authorization": f"Bearer {api_key}"} if api_key else {}))
    else:
        endpoints.append((f"{cleaned_url}/v1/models", {"Authorization": f"Bearer {api_key}"} if api_key else {}))
        endpoints.append((f"{cleaned_url}/models", {"Authorization": f"Bearer {api_key}"} if api_key else {}))

    last_err = ""
    for ep, hdrs in endpoints:
        hdrs["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 R20-Quantum-Trader/6.6"
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
            if exc.code == 401:
                return {
                    "ok": False,
                    "error": "供应商身份验证失败 (HTTP 401 Unauthorized)",
                    "recommendation": "请先在此供应商填入正确的 API Key 后再拉取模型",
                }
        except Exception as exc:
            last_err = str(exc)

    return {
        "ok": False,
        "error": f"拉取失败: {last_err or '未响应模型列表'}",
        "recommendation": "请检查 Base URL 是否正确，或供应商是否支持 /models 端点查询",
    }



def build_request_spec(
    model: str,
    messages: List[Dict[str, str]],
    base_url: str,
    api_key: str = "",
    api_format: str = "openai_chat",
    reasoning_effort: str = "high",
    temperature: Optional[float] = 0.2,
    response_format: Optional[Dict[str, Any]] = None,
    reasoning_type: str = "auto",
    max_tokens: int = 4096,
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """Build endpoint URL, headers, and request payload according to the specific API protocol format."""
    cleaned_url = base_url.rstrip("/")
    m_lower = model.lower()
    rtype = reasoning_type if reasoning_type != "auto" else _detect_reasoning_type(model)
    effort = (reasoning_effort or "auto").strip().lower()
    output_limit = normalize_max_output_tokens(max_tokens)

    # Protocol 1: Anthropic Claude Messages API
    if api_format == "claude_messages":
        if not cleaned_url.endswith("/messages"):
            endpoint = f"{cleaned_url}/messages" if cleaned_url.endswith("/v1") else f"{cleaned_url}/v1/messages"
        else:
            endpoint = cleaned_url

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "R20-Quantum-Trader/5.4 (Claude-Messages)",
            "anthropic-version": "2023-06-01",
        }
        if api_key:
            headers["x-api-key"] = api_key

        # Separate system message
        system_chunks = [m["content"] for m in messages if m.get("role") == "system"]
        chat_messages = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") != "system"]

        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": output_limit,
            "messages": chat_messages,
        }
        if system_chunks:
            payload["system"] = "\n\n".join(system_chunks)

        if effort in ("max", "xhigh", "high", "medium", "low"):
            budget_map = {
                "max": 64000,
                "xhigh": 32000,
                "high": 16000,
                "medium": 8000,
                "low": 2048,
            }
            budget = budget_map[effort]
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            payload["max_tokens"] = budget + output_limit
        elif effort == "none":
            payload["thinking"] = {"type": "disabled"}
            if temperature is not None:
                payload["temperature"] = temperature
        else:
            if temperature is not None:
                payload["temperature"] = temperature

        return endpoint, headers, payload

    # Protocol 2: OpenAI Responses API (/responses)
    elif api_format == "openai_responses":
        if not cleaned_url.endswith("/responses"):
            endpoint = f"{cleaned_url}/responses" if cleaned_url.endswith("/v1") else f"{cleaned_url}/v1/responses"
        else:
            endpoint = cleaned_url

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "R20-Quantum-Trader/5.4 (OpenAI-Responses)",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: Dict[str, Any] = {
            "model": model,
            "input": messages,
            "max_output_tokens": output_limit,
        }
        if response_format and response_format.get("type") == "json_object":
            payload["text"] = {"format": {"type": "json_object"}}
        if effort in ("max", "xhigh", "high", "medium", "low", "minimal"):
            payload["reasoning"] = {"effort": effort}

        return endpoint, headers, payload

    # Protocol 3: OpenAI Chat Completions (/chat/completions, Default)
    else:
        if not cleaned_url.endswith("/chat/completions"):
            endpoint = f"{cleaned_url}/chat/completions" if cleaned_url.endswith("/v1") else f"{cleaned_url}/v1/chat/completions"
        else:
            endpoint = cleaned_url

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "R20-Quantum-Trader/5.4 (OpenAI-Chat)",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }

        # Temperature handling for reasoning models vs normal models
        is_reasoning_model = (
            rtype in ("deepseek_reasoner", "standard_effort")
            or m_lower.startswith(("o1", "o3", "o4"))
            or "reasoner" in m_lower
            or "-r1" in m_lower
            or "qwen3" in m_lower or "qwen-3" in m_lower or "qwq" in m_lower
        )
        if not is_reasoning_model:
            if temperature is not None:
                payload["temperature"] = temperature
        else:
            if "gemini" in m_lower and temperature is not None:
                payload["temperature"] = temperature

        # Standard reasoning effort parameter (supports max, xhigh, high, medium, low, minimal, none)
        if rtype == "standard_effort" or (rtype == "auto" and ("gemini" in m_lower or "qwen3" in m_lower or "qwen-3" in m_lower or "qwq" in m_lower or m_lower.startswith(("o1", "o3", "o4", "gpt-5", "gpt-6")) or "gpt-5" in m_lower or "gpt-6" in m_lower)):
            if effort in ("max", "xhigh", "high", "medium", "low", "minimal"):
                payload["reasoning_effort"] = effort
            elif effort == "none" and ("gemini" in m_lower or "gpt" in m_lower):
                payload["reasoning_effort"] = "none"

        if response_format and rtype != "deepseek_reasoner":
            payload["response_format"] = response_format

        payload[_chat_output_token_field(model)] = output_limit

        return endpoint, headers, payload


def build_chat_payload(
    model: str,
    messages: List[Dict[str, str]],
    reasoning_effort: str = "high",
    temperature: Optional[float] = 0.2,
    response_format: Optional[Dict[str, Any]] = None,
    reasoning_type: str = "auto",
) -> Dict[str, Any]:
    """Compatibility wrapper for standard chat payload generation."""
    _, _, payload = build_request_spec(
        model=model,
        messages=messages,
        base_url="https://api.openai.com/v1",
        api_format="openai_chat",
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        response_format=response_format,
        reasoning_type=reasoning_type,
    )
    return payload


def execute_llm_request(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    api_format: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    temperature: Optional[float] = 0.2,
    response_format: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Tuple[str, str, Dict[str, Any], int]:
    """Unified executor for LLM calls across all 3 protocols.
    Returns: (content, reasoning_content, usage_dict, latency_ms)
    """
    runtime = get_active_llm_runtime()
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
    target_max_tokens = normalize_max_output_tokens(
        max_tokens if max_tokens is not None else runtime.get("max_output_tokens")
    )

    endpoint, headers, payload = build_request_spec(
        model=target_model,
        messages=messages,
        base_url=target_url,
        api_key=target_key,
        api_format=target_format,
        reasoning_effort=target_effort,
        temperature=temperature,
        response_format=response_format,
        reasoning_type=target_rtype,
        max_tokens=target_max_tokens,
    )

    t0 = time.perf_counter()
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )

    # Transient upstream faults (gateway route flaps, bot/rate shields, 5xx) must not
    # silently degrade a trading or self-evolution cycle into NO_CHANGE. Retry with backoff.
    TRANSIENT_MARKERS = (
        "unknown provider", "model_not_found", "no provider", "upstream",
        "temporarily unavailable", "overloaded", "rate limit", "too many requests",
        "capacity", "busy", "bad gateway", "gateway timeout",
    )

    def _is_transient(code: int, body: str) -> bool:
        low = (body or "").lower()
        if code in (408, 409, 425, 429, 500, 502, 503, 504):
            return True
        if code in (400, 401, 402, 403) and any(m in low for m in TRANSIENT_MARKERS):
            return True
        return False

    resp_handle = None
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp_handle = urllib.request.urlopen(req, timeout=effective_timeout)
            break
        except urllib.error.HTTPError as exc:
            err_b = ""
            try:
                err_b = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            last_exc = exc

            if _is_transient(exc.code, err_b) and attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            excerpt = _safe_error_excerpt(err_b)
            detail = f"：{excerpt}" if excerpt else ""
            raise LlmHttpError(
                f"LLM gateway returned HTTP {exc.code} (model={target_model}){detail}",
                output_chars=len(err_b or ""),
                reason=str(exc.code),
            ) from None
        except (TimeoutError, socket.timeout) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise TimeoutError(
                f"LLM 推演超时（已达到思考上限时间 {effective_timeout:.0f}s）：模型思考链过长未在时限内完成响应，可前往后台 AI 模型设置中调大思考上限时间"
            ) from exc
        except urllib.error.URLError as exc:
            last_exc = exc
            if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise TimeoutError(
                    f"LLM 推演超时（已达到思考上限时间 {effective_timeout:.0f}s）：模型思考链过长未在时限内完成响应，可前往后台 AI 模型设置中调大思考上限时间"
                ) from exc
            raise
    if resp_handle is None:
        raise last_exc if last_exc is not None else RuntimeError("LLM 请求未获得响应")

    with resp_handle as resp:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        body_bytes = resp.read()
        body_text = body_bytes.decode("utf-8", errors="replace")

    try:
        res_json = json.loads(body_text)
    except json.JSONDecodeError as exc:
        raise LlmEnvelopeJsonError(
            f"LLM HTTP envelope JSON parse failed at pos {exc.pos}: {exc.msg} (output_chars={len(body_text)})",
            output_chars=len(body_text),
        ) from None
    if not isinstance(res_json, dict):
        raise LlmEnvelopeJsonError(
            f"LLM HTTP envelope JSON root must be an object (output_chars={len(body_text)})",
            output_chars=len(body_text),
        )

    content, reasoning_content, usage = _finalize_protocol_response(target_format, res_json)
    return content, reasoning_content, usage, latency_ms


def test_llm_connection(
    base_url: str,
    api_key: str,
    model: str,
    api_format: str = "openai_chat",
    reasoning_effort: str = "auto",
    reasoning_type: str = "auto",
    timeout: float = 15.0,
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

        rec = "请核对配置"
        if status_code == 401:
            rec = "API Key 认证失败，请检查密钥是否正确或是否已过期"
        elif status_code == 404:
            rec = "端点未找到 (404)，请检查 API 格式协议是否选对（如 Anthropic 需选 Claude Messages，OpenAI 选 Chat 或 Responses），以及 Base URL 路径是否正确"
        elif status_code == 429:
            rec = "请求频次超限或账户配额/余额不足 (429 Rate Limit)"
        elif status_code in (500, 502, 503):
            rec = "上游大模型服务暂时不可用或内部服务故障"

        excerpt = _safe_error_excerpt(err_body, limit=240)
        return {
            "ok": False,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "model": model,
            "api_format": api_format,
            "endpoint": endpoint,
            "error": f"HTTP {status_code}" + (f": {excerpt}" if excerpt else ""),
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

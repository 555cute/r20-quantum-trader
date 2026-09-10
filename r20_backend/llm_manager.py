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
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

_BJ = timezone(timedelta(hours=8))

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LLM_CONFIG_FILE = DATA_DIR / "llm_models.json"
LEGACY_PROVIDERS_FILE = DATA_DIR / "llm_providers.json"
FAILOVER_EVENTS_FILE = DATA_DIR / "llm_failover_events.json"

# ── LLM 韧性（重试 / 回退）默认值 ──
# request_attempts：单次调用中每个模型的最大请求次数（含首次），后台可调 1~10。
# fallback_model_ids：主模型重试耗尽或非瞬时失败后按序回退的模型链，最多 5 个。
DEFAULT_REQUEST_ATTEMPTS = 3
MIN_REQUEST_ATTEMPTS = 1
MAX_REQUEST_ATTEMPTS = 10
MAX_FALLBACK_MODELS = 5
# 整条模型链的总等待软预算（只在新一次尝试发起前检查，不切断进行中的请求）；
# 必须低于网关 trader 任务 840s 超时，避免整轮推演被调度器硬杀。
FAILOVER_MAX_TOTAL_WAIT = float(os.getenv("LLM_FAILOVER_MAX_WAIT_SECONDS", "600"))

SUPPORTED_API_FORMATS = [
    {"id": "openai_chat", "name": "OpenAI Chat (/chat/completions)", "desc": "标准 ChatML 对话格式，兼容 OpenAI/Gemini/DeepSeek/主流中继"},
    {"id": "openai_responses", "name": "OpenAI Responses (/responses)", "desc": "OpenAI 专属 Responses API 结构化接口"},
    {"id": "claude_messages", "name": "Claude Messages (/messages)", "desc": "Anthropic Claude 原生 Messages API，支持原生长思维链"},
]

STANDARD_REASONING_EFFORTS = ["max", "xhigh", "high", "medium", "low", "minimal", "none", "auto"]


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


# ── Base URL 拼接：以用户显式输入的路径为准，不再强插 /v1 ──
# 历史缺陷：所有请求端点构造都在 base_url 不以 /v1 结尾时硬拼 "/v1/..."，
# 导致智谱（https://open.bigmodel.cn/api/paas/v4）、Gemini（.../v1beta）这类
# 带版本路径的供应商被拼成 /v4/v1/chat/completions 而 404/401。
# 规则：base 含任何显式路径 → 直接拼接协议后缀；裸域名（无路径）→ 兼容补 /v1。
def _url_path_of(base: str) -> str:
    try:
        return urlparse(base).path or ""
    except Exception:
        return "/unparseable"  # 解析失败按“有路径”处理，不做任何自动填充


def _join_api_path(base: str, suffix: str) -> str:
    """把协议后缀（/chat/completions、/messages、/responses、/models）拼到 Base URL 上。"""
    cleaned = str(base or "").strip().rstrip("/")
    if cleaned.endswith(suffix):
        return cleaned
    if _url_path_of(cleaned) in ("", "/"):
        return f"{cleaned}/v1{suffix}"
    return f"{cleaned}{suffix}"


def _model_holder_pids(providers: List[Dict[str, Any]], model_id: str) -> List[str]:
    """列出嵌套模型清单里持有该模型 id 的所有供应商 id。"""
    return [
        str(p.get("id", ""))
        for p in providers
        if any(m.get("id") == model_id for m in p.get("models", []))
    ]


def _resolve_active_provider_id(config: Dict[str, Any]) -> str:
    """判定主脑模型归属的供应商。

    优先级：显式 active_provider_id > 唯一持有者 > 顶层扁平缓存归属。
    多个供应商挂同名模型且无法判定时返回空串——调用方据此保守处理（宁可拦，不可放错）。
    """
    mid = config.get("active_model_id", "")
    if not mid:
        return ""
    providers = config.get("providers", [])
    holders = _model_holder_pids(providers, mid)
    if not holders:
        # 模型不嵌套在任何供应商名下（仅顶层注册）→ 没有归属可言，
        # 绝不能沿用陈旧的 active_provider_id，否则会被误重挂到该供应商凭据上
        return ""
    pid = str(config.get("active_provider_id") or "").strip()
    if pid and pid in holders:
        return pid
    if len(set(holders)) == 1:
        return holders[0]
    flat_pid = next(
        (str(m.get("provider_id") or "") for m in config.get("models", []) if m.get("id") == mid),
        "",
    )
    if flat_pid and flat_pid in holders:
        return flat_pid
    return ""


def _provider_holds_active_model(config: Dict[str, Any], provider_id: str) -> bool:
    """该供应商是否持有当前主脑模型。

    多供应商挂同名模型时按归属精确判定，不再让未启用的那份副本被误伤；
    归属无法判定（多持有者且无记录）时保守视为持有，防止主脑悬空。
    """
    mid = config.get("active_model_id", "")
    if not mid:
        return False
    prov = next((p for p in config.get("providers", []) if p.get("id") == provider_id), None)
    if not prov or not any(m.get("id") == mid for m in prov.get("models", [])):
        return False
    active_pid = _resolve_active_provider_id(config)
    return (not active_pid) or active_pid == provider_id



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
                "api_path": m.get("api_path") or p.get("api_path", ""),
                "reasoning_type": m.get("reasoning_type", _detect_reasoning_type(mid)),
                "reasoning_effort": m.get("reasoning_effort") or m.get("default_effort", "high"),
                "capabilities": m.get("capabilities", _detect_capabilities(mid)),
                "context_length": m.get("context_length"),
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

    # ── 主脑供应商归属：嵌套持有者为准（显式记录 > 唯一持有 > 顶层扁平缓存归属）──
    # models_map 按 id 去重（后出现的供应商覆盖先出现的），多供应商挂同名模型时
    # 顶层缓存的 base_url/api_key 会静默变成另一家的——必须把主脑条目钉回其供应商。
    raw_active_pid = str(data.get("active_provider_id") or "").strip()
    active_pid = _resolve_active_provider_id(
        {
            "active_model_id": active_m_id,
            "active_provider_id": raw_active_pid,
            "providers": merged_providers,
            "models": flat_models,
        }
    )
    if active_pid and active_m_id:
        ap = next((p for p in merged_providers if str(p.get("id", "")) == active_pid), None)
        if ap:
            for m in flat_models:
                if m.get("id") != active_m_id:
                    continue
                m["provider_id"] = active_pid
                m["provider_name"] = ap.get("name", active_pid)
                if ap.get("base_url"):
                    m["base_url"] = ap["base_url"]
                if ap.get("api_key"):
                    m["api_key"] = ap["api_key"]
                if not m.get("api_format"):
                    m["api_format"] = ap.get("api_format", "openai_chat")
                break

    # ── 韧性配置解析：请求次数 + 回退模型链（脏数据自愈）──
    raw_attempts = data.get("request_attempts")
    try:
        env_attempts = int(os.getenv("LLM_REQUEST_ATTEMPTS", "") or 0)
    except ValueError:
        env_attempts = 0
    try:
        request_attempts = int(raw_attempts) if raw_attempts is not None else (env_attempts or DEFAULT_REQUEST_ATTEMPTS)
    except (TypeError, ValueError):
        request_attempts = DEFAULT_REQUEST_ATTEMPTS
    request_attempts = max(MIN_REQUEST_ATTEMPTS, min(MAX_REQUEST_ATTEMPTS, request_attempts))

    known_model_ids = {m["id"] for m in flat_models}
    raw_fallbacks = data.get("fallback_model_ids")
    fallback_model_ids: List[str] = []
    if isinstance(raw_fallbacks, list):
        for fid in raw_fallbacks:
            fid = str(fid or "").strip()
            if not fid or fid == active_m_id or fid not in known_model_ids:
                continue
            if fid not in fallback_model_ids:
                fallback_model_ids.append(fid)
    fallback_model_ids = fallback_model_ids[:MAX_FALLBACK_MODELS]

    config = {
        "version": "3.2",
        "defaults_seeded": True,
        "active_model_id": active_m_id,
        "active_provider_id": active_pid,
        "active_reasoning_effort": active_effort,
        "thinking_timeout": thinking_timeout,
        "request_attempts": request_attempts,
        "fallback_model_ids": fallback_model_ids,
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
    active_pid = _resolve_active_provider_id(config)
    active_effort = config.get("active_reasoning_effort", "high")

    res: Dict[str, Any] = {
        "version": config.get("version", "3.2"),
        "active_model_id": active_mid,
        "active_reasoning_effort": active_effort,
        "thinking_timeout": config.get("thinking_timeout", 120.0),
        "request_attempts": config.get("request_attempts", DEFAULT_REQUEST_ATTEMPTS),
        "fallback_model_ids": config.get("fallback_model_ids", []),
        "max_request_attempts": MAX_REQUEST_ATTEMPTS,
        "max_fallback_models": MAX_FALLBACK_MODELS,
        "standard_reasoning_efforts": STANDARD_REASONING_EFFORTS,
        "supported_api_formats": SUPPORTED_API_FORMATS,
        "providers": [],
        "models": [],
        "active_provider_id": active_pid or (config.get("active_provider_id") or ""),
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
                "description": m.get("description", ""),
                # 同名模型挂多家供应商时，主脑徽标只打给归属供应商的那一份；
                # 归属无法判定（active_pid 为空）时保留全打，避免误导为"没启用"
                "is_active": bool(m_id == active_mid and (not active_pid or pid == active_pid)),
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
    prov_api_path = str((target_model or {}).get("api_path", "") or "")

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
            if not prov_api_path:
                prov_api_path = str(prov.get("api_path", "") or "")

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

    return {
        "model": model_name,
        "name": target_model.get("name", model_name) if target_model else model_name,
        "provider_name": provider_name or "默认",
        "provider_id": provider_id or "openai",
        "base_url": base_url,
        "api_key": api_key,
        "api_format": api_format,
        "api_path": prov_api_path,
        "reasoning_effort": active_effort,
        "reasoning_type": reasoning_type,
        "thinking_timeout": thinking_timeout,
        "request_attempts": config.get("request_attempts", DEFAULT_REQUEST_ATTEMPTS),
        "fallback_model_ids": config.get("fallback_model_ids", []),
    }


def resolve_model_runtime(model_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a configured model (e.g. a fallback) into a callable runtime spec.
    Returns None when the model is unknown or has no usable endpoint."""
    config = init_llm_config()
    target = next((m for m in config.get("models", []) if m.get("id") == model_id), None)
    if not target:
        return None
    base_url = (target.get("base_url") or "").rstrip("/")
    api_key = target.get("api_key") or ""
    if not api_key or not base_url:
        prov = next((p for p in config.get("providers", []) if p.get("id") == target.get("provider_id")), None)
        if prov:
            base_url = base_url or (prov.get("base_url") or "").rstrip("/")
            api_key = api_key or prov.get("api_key", "")
    if not base_url:
        return None
    mid = target.get("id", model_id)
    api_format = target.get("api_format") or _detect_api_format(base_url, mid)
    reasoning_type = target.get("reasoning_type") or _detect_reasoning_type(mid)
    effort = target.get("reasoning_effort") or target.get("default_effort") or "high"
    try:
        thinking_timeout = float(target.get("thinking_timeout") or config.get("thinking_timeout") or 120.0)
    except (TypeError, ValueError):
        thinking_timeout = float(config.get("thinking_timeout") or 120.0)
    return {
        "model": mid,
        "name": target.get("name", mid),
        "provider_id": target.get("provider_id", ""),
        "provider_name": target.get("provider_name", "自定义"),
        "base_url": base_url,
        "api_key": api_key,
        "api_format": api_format,
        "api_path": str(target.get("api_path", "") or ""),
        "reasoning_effort": effort if effort in STANDARD_REASONING_EFFORTS else "high",
        "reasoning_type": reasoning_type,
        "thinking_timeout": thinking_timeout,
    }


def record_failover_event(entry: Dict[str, Any]) -> None:
    """Append a resilience event (retry exhausted / fallback hit / chain failure) for admin visibility."""
    try:
        events: List[Dict[str, Any]] = []
        if FAILOVER_EVENTS_FILE.exists():
            try:
                with open(FAILOVER_EVENTS_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    events = loaded
            except Exception:
                events = []
        entry["ts"] = int(time.time())
        entry["time_str"] = datetime.fromtimestamp(entry["ts"], _BJ).isoformat(sep=" ", timespec="seconds")
        events.insert(0, entry)
        _atomic_write_json(FAILOVER_EVENTS_FILE, events[:200])
    except Exception:
        # Resilience telemetry must never break the trading path.
        pass


def recent_failover_events(limit: int = 30) -> List[Dict[str, Any]]:
    if FAILOVER_EVENTS_FILE.exists():
        try:
            with open(FAILOVER_EVENTS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                return loaded[: max(1, min(int(limit), 200))]
        except Exception:
            pass
    return []


def activate_provider_model(provider_id: str, model_id: str, reasoning_effort: Optional[str] = None, thinking_timeout: Optional[float] = None) -> Dict[str, Any]:
    """One-click switch to activate a model. Updates config, .env, and encrypted store."""
    from .settings_store import update_env
    from .config import refresh_settings
    try:
        from r20_gateway.secrets import save_secrets
    except ImportError:
        save_secrets = None

    config = init_llm_config()
    flat_model = next((m for m in config.get("models", []) if m["id"] == model_id), None)
    target_model: Optional[Dict[str, Any]] = None
    scoped_pid = str(provider_id or "").strip()
    scoped_prov: Optional[Dict[str, Any]] = None

    if scoped_pid and scoped_pid != "custom":
        # 从指定供应商的「模型」页设为主脑：归属以本次调用的供应商为准，
        # 解决多供应商挂同名模型时顶层缓存只按 model id 记录导致的混挂。
        scoped_prov = next((p for p in config.get("providers", []) if p.get("id") == scoped_pid), None)
        if scoped_prov is None:
            raise ValueError(f"供应商 {scoped_pid} 未找到，无法激活")
        nested = next((m for m in scoped_prov.get("models", []) if m.get("id") == model_id), None)
        if nested is None and not (flat_model and str(flat_model.get("provider_id") or "") == scoped_pid):
            raise ValueError(
                f"供应商 {scoped_prov.get('name')} 名下没有模型 {model_id}；请先在该供应商下添加，再设为主脑。"
            )
        nested = nested or {}
        target_model = {
            "id": model_id,
            "name": nested.get("name") or (flat_model or {}).get("name") or model_id,
            "provider_id": scoped_prov.get("id"),
            "provider_name": scoped_prov.get("name", scoped_pid),
            "base_url": scoped_prov.get("base_url", "") or (flat_model or {}).get("base_url", ""),
            "api_key": scoped_prov.get("api_key", ""),
            "api_format": nested.get("api_format")
            or (flat_model or {}).get("api_format")
            or scoped_prov.get("api_format", "openai_chat"),
            "reasoning_type": nested.get("reasoning_type")
            or (flat_model or {}).get("reasoning_type")
            or _detect_reasoning_type(model_id),
            "reasoning_effort": nested.get("reasoning_effort")
            or nested.get("default_effort")
            or (flat_model or {}).get("reasoning_effort", "high"),
            "capabilities": nested.get("capabilities") or (flat_model or {}).get("capabilities", []),
            "context_length": nested.get("context_length") or (flat_model or {}).get("context_length"),
            "description": nested.get("description") or (flat_model or {}).get("description", ""),
        }
        # 顶层扁平缓存钉到主脑供应商的凭据上，交易引擎与全局列表随之对齐
        if flat_model is not None:
            flat_model.update(target_model)
        else:
            config.setdefault("models", []).append(target_model)

    if target_model is None:
        target_model = flat_model
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
    config["active_provider_id"] = str(target_model.get("provider_id") or "")
    config["active_reasoning_effort"] = effort
    if thinking_timeout is not None:
        timeout_val = max(5.0, min(float(thinking_timeout), 1800.0))
        config["thinking_timeout"] = timeout_val
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
    request_attempts: Optional[int] = None,
    fallback_model_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Update global LLM settings: model, reasoning effort, thinking timeout, retry attempts and fallback chain."""
    from .settings_store import update_env
    from .config import refresh_settings

    config = init_llm_config()
    env_values: Dict[str, Any] = {}

    if active_model_id:
        config["active_model_id"] = active_model_id
        env_values["LLM_MODEL"] = active_model_id
        # 主脑换了模型 → 供应商归属立即重判，防止 active_provider_id 悬在旧模型上
        config["active_provider_id"] = _resolve_active_provider_id(config)

    if reasoning_effort:
        config["active_reasoning_effort"] = reasoning_effort
        env_values["LLM_REASONING_EFFORT"] = reasoning_effort

    if thinking_timeout is not None:
        val = max(5.0, min(float(thinking_timeout), 1800.0))
        config["thinking_timeout"] = val
        env_values["LLM_THINKING_TIMEOUT"] = str(int(val) if val.is_integer() else val)

    if request_attempts is not None:
        try:
            att = int(request_attempts)
        except (TypeError, ValueError) as exc:
            raise ValueError("请求次数必须是整数") from exc
        if not (MIN_REQUEST_ATTEMPTS <= att <= MAX_REQUEST_ATTEMPTS):
            raise ValueError(f"请求次数需在 {MIN_REQUEST_ATTEMPTS}~{MAX_REQUEST_ATTEMPTS} 之间")
        config["request_attempts"] = att

    if fallback_model_ids is not None:
        known_ids = {m.get("id") for m in config.get("models", [])}
        new_active = config.get("active_model_id", "")
        cleaned: List[str] = []
        for fid in fallback_model_ids:
            fid = str(fid or "").strip()
            if not fid:
                continue
            if fid == new_active:
                continue  # 主脑模型不能同时是自己的回退
            if fid not in known_ids:
                raise ValueError(f"回退模型不存在：{fid}（请先在模型列表中添加）")
            if fid not in cleaned:
                cleaned.append(fid)
        if len(cleaned) > MAX_FALLBACK_MODELS:
            raise ValueError(f"回退模型最多 {MAX_FALLBACK_MODELS} 个，避免整轮推演超时")
        config["fallback_model_ids"] = cleaned

    _atomic_write_json(LLM_CONFIG_FILE, config)
    if env_values:
        update_env(env_values)
        refresh_settings()

    return {
        "success": True,
        "active_model_id": config.get("active_model_id"),
        "active_reasoning_effort": config.get("active_reasoning_effort"),
        "thinking_timeout": config.get("thinking_timeout", 120.0),
        "request_attempts": config.get("request_attempts", DEFAULT_REQUEST_ATTEMPTS),
        "fallback_model_ids": config.get("fallback_model_ids", []),
    }


def upsert_model(provider_id: str, model_data: Dict[str, Any]) -> Dict[str, Any]:
    """Add or update a custom model definition."""
    mid = str(model_data.get("id", "")).strip()
    provider_id = str(provider_id or "").strip()
    # 旧全局路由 /llm/models 不带路径参数（provider_id 恒为 "custom"），
    # 但前端 payload 一直携带归属供应商——尊重 payload，避免模型落错家。
    payload_pid = str(model_data.get("provider_id", "") or "").strip()
    if (not provider_id or provider_id == "custom") and payload_pid and payload_pid != "custom":
        provider_id = payload_pid
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

    _atomic_write_json(LLM_CONFIG_FILE, config)
    return {
        "model_id": mid,
        "name": name,
        "base_url": base_url,
        "api_format": api_format,
        "provider_id": provider_id or "openai",
    }


def delete_model(provider_id: str, model_id: str) -> bool:
    """Delete a custom model.

    多供应商挂同名模型时的作用域规则：
    - 只有「主脑所属供应商」名下的那份不可删，其他供应商的同名副本可正常删除；
    - 顶层扁平缓存只按 (id, provider_id) 摘除对应条目，别家持有者由下次加载自动重挂；
    - 不带供应商作用域的旧路由保持全量删除语义（各供应商一并移除）。
    """
    config = init_llm_config()
    scoped = bool(provider_id) and provider_id != "custom"
    active_mid = config.get("active_model_id", "")

    if active_mid and model_id == active_mid:
        active_pid = _resolve_active_provider_id(config)
        if not scoped:
            raise ValueError(
                "不能删除当前正在使用的模型；请先切换到其他模型后再删除。"
                "若只想删某家供应商名下的副本，请进入该供应商的模型页操作。"
            )
        if active_pid:
            if active_pid == provider_id:
                prov_name = next(
                    (p.get("name", provider_id) for p in config.get("providers", []) if p.get("id") == provider_id),
                    provider_id,
                )
                raise ValueError(
                    f"{model_id} 是供应商「{prov_name}」名下正在使用的主脑模型；"
                    "请先切换主脑或删除其他供应商名下的同名副本。"
                )
        else:
            raise ValueError(
                f"多个供应商名下挂有同名主脑模型 {model_id}，无法判定启用的是哪一家；"
                "请先在该供应商的模型页点「设为主脑」明确归属，再执行删除。"
            )

    models = config.get("models", [])
    providers = config.get("providers", [])

    nested_removed = False
    if scoped:
        prov = next((p for p in providers if p.get("id") == provider_id), None)
        if prov is None:
            raise ValueError(f"供应商 {provider_id} 未找到")
        before = len(prov.get("models", []))
        prov["models"] = [m for m in prov.get("models", []) if m.get("id") != model_id]
        nested_removed = len(prov["models"]) < before
        # 顶层缓存只摘属于本供应商的条目；归属漂移挂在别家名下的条目留给 init 重建时自愈
        filtered = [
            m
            for m in models
            if not (m.get("id") == model_id and str(m.get("provider_id") or "") == provider_id)
        ]
    else:
        for prov in providers:
            before = len(prov.get("models", []))
            prov["models"] = [m for m in prov.get("models", []) if m.get("id") != model_id]
            nested_removed = nested_removed or len(prov["models"]) < before
        filtered = [m for m in models if m["id"] != model_id]

    if len(filtered) == len(models) and not nested_removed:
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
    if _provider_holds_active_model(config, provider_id):
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
    if _provider_holds_active_model(config, provider_id):
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
    api_path: str = "",
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """Build endpoint URL, headers, and request payload according to the specific API protocol format."""
    cleaned_url = base_url.rstrip("/")
    # 「API 路径」字段生效：标准路径由协议格式决定；仅非标准自定义路径覆盖之。
    custom_path = str(api_path or "").strip()
    if custom_path and not custom_path.startswith("/"):
        custom_path = "/" + custom_path
    if custom_path in ("/chat/completions", "/messages", "/responses", "/v1/chat/completions", "/v1/messages", "/v1/responses"):
        custom_path = ""
    m_lower = model.lower()
    rtype = reasoning_type if reasoning_type != "auto" else _detect_reasoning_type(model)
    effort = (reasoning_effort or "auto").strip().lower()

    # Protocol 1: Anthropic Claude Messages API
    if api_format == "claude_messages":
        endpoint = _join_api_path(cleaned_url, custom_path or "/messages")

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
            "max_tokens": max_tokens,
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
            payload["max_tokens"] = budget + max_tokens
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
        endpoint = _join_api_path(cleaned_url, custom_path or "/responses")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "R20-Quantum-Trader/5.4 (OpenAI-Responses)",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: Dict[str, Any] = {
            "model": model,
            "input": messages,
        }
        if response_format and response_format.get("type") == "json_object":
            payload["text"] = {"format": {"type": "json_object"}}
        if effort in ("max", "xhigh", "high", "medium", "low", "minimal"):
            payload["reasoning"] = {"effort": effort}

        return endpoint, headers, payload

    # Protocol 3: OpenAI Chat Completions (/chat/completions, Default)
    else:
        endpoint = _join_api_path(cleaned_url, custom_path or "/chat/completions")

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


# ── LLM 韧性调用链：模型内重试 + 跨模型回退 ──

# Transient upstream faults (gateway route flaps, bot/rate shields, 5xx) must not
# silently degrade a trading or self-evolution cycle into NO_CHANGE. Retry with backoff.
# 注：unknown provider / model_not_found 已移出瞬时名单（2026-09-09 事件复盘）——
# 网关不认识该模型是轮内持续故障，原地重试只会白白烧掉请求窗口，按硬故障立即换模型。
TRANSIENT_MARKERS = (
    "upstream", "temporarily unavailable", "overloaded", "rate limit",
    "too many requests", "capacity", "busy", "bad gateway", "gateway timeout",
)


def _is_transient_http(code: int, body: str) -> bool:
    low = (body or "").lower()
    if code in (408, 409, 425, 429, 500, 502, 503, 504):
        return True
    if code in (400, 401, 402, 403) and any(m in low for m in TRANSIENT_MARKERS):
        return True
    return False


class _LLMTransientError(Exception):
    """可重试错误：瞬时 HTTP、超时、连接层异常（拒绝/重置/DNS/TLS）、坏响应体、空正文。

    fail_over_now=True：错误本身可再试（末位模型仍会重试），但链上还有下一个模型时
    立即切换——504/思考超时属"慢故障"，同一轮内原地重试大概率再烧满一个超时窗口。"""

    def __init__(self, message: str, timed_out: bool = False, fail_over_now: bool = False):
        super().__init__(message)
        self.timed_out = timed_out
        self.fail_over_now = fail_over_now


class _LLMHardError(Exception):
    """不可重试错误（对该模型）：认证失败、404、参数被拒等——直接切换下一个回退模型。"""


def _parse_llm_response(target_format: str, res_json: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    content = ""
    reasoning_content = ""
    usage = res_json.get("usage", {}) if isinstance(res_json, dict) else {}

    # Protocol 1: Claude Messages Response
    if target_format == "claude_messages":
        text_chunks = [c.get("text", "") for c in res_json.get("content", []) if c.get("type") == "text"]
        thinking_chunks = [c.get("thinking", "") for c in res_json.get("content", []) if c.get("type") == "thinking"]
        content = "".join(text_chunks).strip()
        reasoning_content = "\n".join(thinking_chunks).strip()
        if not usage:
            usage = {
                "total_tokens": res_json.get("usage", {}).get("input_tokens", 0) + res_json.get("usage", {}).get("output_tokens", 0)
            }

    # Protocol 2: OpenAI Responses Response
    elif target_format == "openai_responses":
        content = str(res_json.get("output_text") or "").strip()
        if not content:
            for item in res_json.get("output", []):
                if item.get("type") == "message":
                    for part in item.get("content", []):
                        if part.get("type") == "output_text" or "text" in part:
                            content += str(part.get("text", ""))
                elif item.get("type") == "reasoning":
                    reasoning_content += str(item.get("content") or item.get("summary") or "")
        content = content.strip()
        reasoning_content = reasoning_content.strip()

    # Protocol 3: OpenAI Chat Completions Response
    else:
        msg = res_json.get("choices", [{}])[0].get("message", {})
        content = str(msg.get("content", "")).strip()
        reasoning_content = str(msg.get("reasoning_content") or "").strip()

    return content, reasoning_content, usage


def _attempt_llm_call(
    cand: Dict[str, Any],
    messages: List[Dict[str, str]],
    temperature: Optional[float],
    response_format: Optional[Dict[str, Any]],
    effective_timeout: float,
) -> Tuple[str, str, Dict[str, Any], int]:
    """单次请求一个模型；失败时抛 _LLMTransientError（可重试）或 _LLMHardError（换模型）。"""
    endpoint, headers, payload = build_request_spec(
        model=cand["model"],
        messages=messages,
        base_url=cand["base_url"],
        api_key=cand.get("api_key", ""),
        api_format=cand.get("api_format", "openai_chat"),
        reasoning_effort=cand.get("reasoning_effort", "high"),
        temperature=temperature,
        response_format=response_format,
        reasoning_type=cand.get("reasoning_type", "auto"),
        api_path=cand.get("api_path", ""),
    )

    t0 = time.perf_counter()
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        resp_handle = urllib.request.urlopen(req, timeout=effective_timeout)
    except urllib.error.HTTPError as exc:
        err_b = ""
        try:
            err_b = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        # Adaptive fallback retry on rejected parameter (400: reasoning_effort/temperature/response_format)
        if (
            exc.code == 400
            and cand.get("api_format", "openai_chat") == "openai_chat"
            and any(kw in err_b.lower() for kw in ["reasoning_effort", "temperature", "response_format", "invalid parameter"])
        ):
            fb_payload = {"model": cand["model"], "messages": messages}
            fb_req = urllib.request.Request(endpoint, data=json.dumps(fb_payload).encode("utf-8"), headers=headers)
            try:
                with urllib.request.urlopen(fb_req, timeout=effective_timeout) as fb_resp:
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    fb_json = json.loads(fb_resp.read().decode("utf-8", errors="replace"))
                content, reasoning, usage = _parse_llm_response(cand.get("api_format", "openai_chat"), fb_json)
                if not content and not reasoning:
                    raise _LLMTransientError(f"模型 {cand['model']} 返回空正文（已自适应去参数重试）")
                return content, reasoning, usage, latency_ms
            except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError) as fb_exc:
                fb_code = getattr(fb_exc, "code", 0) or 0
                if fb_code and not _is_transient_http(fb_code, str(getattr(fb_exc, "msg", "") or fb_exc)):
                    raise _LLMHardError(f"LLM 网关返回 HTTP {fb_code}（模型 {cand['model']}）：{str(fb_exc)[:280]}") from fb_exc
                raise _LLMTransientError(f"LLM 网关返回 HTTP {exc.code}（模型 {cand['model']}）：{(err_b or '')[:280]}") from fb_exc
        if _is_transient_http(exc.code, err_b):
            raise _LLMTransientError(
                f"LLM 网关返回 HTTP {exc.code}（模型 {cand['model']}）：{(err_b or '')[:280]}",
                fail_over_now=(exc.code == 504),  # 504=上游已超时：链上有下一个模型则立即切换
            ) from exc
        raise _LLMHardError(f"LLM 网关返回 HTTP {exc.code}（模型 {cand['model']}）：{(err_b or '')[:280]}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise _LLMTransientError(
            f"LLM 推演超时（已达到思考上限时间 {effective_timeout:.0f}s）：模型思考链过长未在时限内完成响应，可前往后台 AI 模型设置中调大思考上限时间",
            timed_out=True,
            fail_over_now=True,  # 慢故障：有回退链时立即切换，不再原地烧第二个超时窗口
        ) from exc
    except urllib.error.URLError as exc:
        # 连接层异常（拒绝/重置/DNS/TLS/断线）与超时包装同样属于瞬时故障：
        # 旧版在此处直接 raise，导致「失败一次就不再请求」——现在纳入重试与回退。
        reason = getattr(exc, "reason", None)
        timed_out = isinstance(reason, (socket.timeout, TimeoutError))
        raise _LLMTransientError(
            f"LLM 连接层异常（模型 {cand['model']}）：{type(reason).__name__ if reason is not None else type(exc).__name__}: {str(reason or exc)[:220]}",
            timed_out=timed_out,
            fail_over_now=timed_out,  # 连接层包装的超时同样按慢故障快速换模型
        ) from exc
    except (ValueError, OSError) as exc:
        # 响应体非 JSON（如反代 HTML 错误页）、读取中断等：可重试
        raise _LLMTransientError(f"LLM 响应体解析失败（模型 {cand['model']}）：{str(exc)[:200]}") from exc

    with resp_handle as resp:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        body_bytes = resp.read()
        try:
            res_json = json.loads(body_bytes.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise _LLMTransientError(f"LLM 响应体非 JSON（模型 {cand['model']}）：{str(body_bytes[:160])!r}") from exc

    content, reasoning, usage = _parse_llm_response(cand.get("api_format", "openai_chat"), res_json)
    if not content and not reasoning:
        raise _LLMTransientError(f"模型 {cand['model']} 返回空正文（HTTP 200 但无 content/reasoning，疑似上游静默失败）")
    return content, reasoning, usage, latency_ms


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
    allow_fallback: bool = True,
) -> Tuple[str, str, Dict[str, Any], int]:
    """Unified resilient executor for LLM calls across all 3 protocols.

    韧性链路：每个模型按后台「请求次数」重试（指数退避），瞬时耗尽或遇
    硬故障（401/404 等）时按后台「回退模型」顺序切换下一个模型。
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
            rt = resolve_model_runtime(fid)
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
                    record_failover_event({
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

    record_failover_event({
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


def _lookup_api_path(base_url: str, model_id: str) -> str:
    """按 base_url+模型 id 反查已配置的「API 路径」（连接测试端点不透传该字段，此处自解析）。"""
    bu = str(base_url or "").strip().rstrip("/")
    if not bu:
        return ""
    try:
        cfg = init_llm_config()
    except Exception:
        return ""
    for m in cfg.get("models", []):
        if m.get("id") == model_id and str(m.get("base_url", "")).rstrip("/") == bu:
            return str(m.get("api_path") or "")
    for p in cfg.get("providers", []):
        if str(p.get("base_url", "")).rstrip("/") == bu:
            return str(p.get("api_path") or "")
    return ""


def test_llm_connection(
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
        api_path=api_path or _lookup_api_path(cleaned_url, model),
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

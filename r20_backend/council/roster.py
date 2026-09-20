"""委员会名册与席位模型绑定（纯函数，无模块级可变状态）。

这些函数不读 COUNCIL_CONFIG_FILE / DATA_DIR / _LEGACY_PRESET_PROMPT_HASHES，
故门面**直接重导出**即可，无需薄壳注入。结构优化阶段 2（B5 第二刀）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from r20_backend.council.policy import (
    DEFAULT_COUNCIL_TIMEOUT,
    MAX_COUNCIL_TIMEOUT,
    MIN_COUNCIL_TIMEOUT,
)


def seat_model_health(roles: Any) -> List[Dict[str, Any]]:
    """逐席位核对 model_id 是否已在模型库登记（供 UI 标注"模型缺失"）。

    审计 P1-4b：线上 4 席里 3 席绑了未登记 id（qwen3.8-flash / deepseek-v4-flash-0731），
    引擎查不到就静默回落主脑 → 页面显示"多模型投委会"，实际只有一个模型在答。
    """
    from r20_backend.llm_manager import load_llm_config
    try:
        cfg = load_llm_config(mask_keys=False)
        registered = {str(i.get("id")) for i in (cfg.get("models") or []) if isinstance(i, dict) and i.get("id")}
    except Exception:
        registered = set()
    rows = []
    for role_id, role in (roles or {}).items():
        if not isinstance(role, dict):
            continue
        requested = str(role.get("model_id") or "").strip()
        rows.append({
            "role_id": str(role_id),
            "model_id": requested,
            "mode": "follow_main" if not requested else ("registered" if requested in registered else "missing"),
            "registered": (not requested) or requested in registered,
        })
    return rows


def resolve_seat_model(role_spec: Dict[str, Any]) -> Dict[str, Any]:
    """把席位的 model_id 解析为可调用模型；**未登记必须显式暴露**，不再静默回落。

    返回 requested / registered / fallback / registered_ids，调用方负责把这几个字段
    透出到提案载荷（UI 与审计据此标注"该席位实际由主脑代答"）。
    """
    from r20_backend.llm_manager import load_llm_config
    requested = str(role_spec.get("model_id") or "").strip()
    effort = role_spec.get("reasoning_effort") or "medium"
    try:
        cfg = load_llm_config(mask_keys=False)
    except Exception as exc:
        return {"model": "", "base_url": None, "api_key": None, "api_format": None,
                "effort": effort, "requested": requested, "registered": False,
                "fallback": bool(requested), "reason": f"模型库读取失败：{exc}", "registered_ids": []}
    models = [i for i in (cfg.get("models") or []) if isinstance(i, dict)]
    if not requested:
        return {"model": "", "base_url": None, "api_key": None, "api_format": None,
                "effort": cfg.get("active_reasoning_effort", "medium"), "requested": "",
                "registered": True, "fallback": False, "reason": "", "registered_ids": []}
    for item in models:
        if item.get("id") == requested:
            return {"model": item.get("id"), "base_url": item.get("base_url"),
                    "api_key": item.get("api_key"), "api_format": item.get("api_format"),
                    "effort": item.get("reasoning_effort") or effort, "requested": requested,
                    "registered": True, "fallback": False, "reason": "", "registered_ids": []}
    return {"model": "", "base_url": None, "api_key": None, "api_format": None,
            "effort": effort, "requested": requested, "registered": False, "fallback": True,
            "reason": f"席位绑定的模型 {requested} 未在模型库登记，已回落主脑代答",
            "registered_ids": sorted(str(i.get("id")) for i in models if i.get("id"))}


def validate_seat_model_bindings(roles: Any, previous_roles: Any = None) -> List[str]:
    """写闸：**新绑定**的未登记模型一律拒绝（沿用旧绑定的不算新错，避免堵死保存）。

    返回问题列表（空 = 通过）。只比对 model_id 变化过的席位，管理员点保存不会被历史遗留挡住。

    ## 不可判定 ⇒ **拒绝保存**（第一百四十五刀）

    本函数是**写闸**：它存在的意义就是"判断不了就不放行"。旧实现在模型库**读不出来**时
    `return []`（= 无问题 ⇒ 放行）—— 写闸在**无法判断**时开门，与自身目的矛盾；
    注释里那句"只读侧会标记"至多覆盖到运行期回落（主脑代答），覆盖不了"把一个查不到的
    id 写进配置并在管理页显示为已绑定"。

    现约定（对齐 `docs/FAILURE_SEMANTICS.md`）：

    - 模型库**读不出来** ⇒ 返回一条问题 ⇒ 调用方（`council_manager.save_council_config`）
      `raise ValueError` ⇒ **保存被拒**并带上原因；
    - 模型库**合法为空**（全新环境尚未登记模型）⇒ 仍 `[]`（此时没有任何绑定可能合法，
      把首次配置也堵死没有意义）；
    - **导入/套用整包**路径（`enforce_models=False`）按既有语义继续（不整包拒绝），
      本函数返回的问题在那里只作回报 —— 这是既有的、有意的区分。
    """
    from r20_backend.llm_manager import load_llm_config
    try:
        cfg = load_llm_config(mask_keys=False)
        registered = {str(i.get("id")) for i in (cfg.get("models") or []) if isinstance(i, dict) and i.get("id")}
    except Exception as exc:      # noqa: BLE001 - 读闸失败要变成"拒绝保存"而不是"放行"
        return [f"模型库读不出来（{exc!r}）⇒ 无法核验席位绑定，已拒绝保存"
                "（不可判定 ≠ 安全：" + "读闸不该在判断不了时放行）"]
    if not registered:
        return []
    prev = previous_roles if isinstance(previous_roles, dict) else {}
    problems = []
    for role_id, role in (roles or {}).items():
        if not isinstance(role, dict):
            continue
        requested = str(role.get("model_id") or "").strip()
        if not requested or requested in registered:
            continue
        old = str((prev.get(role_id) or {}).get("model_id") or "").strip() if isinstance(prev.get(role_id), dict) else ""
        if old == requested:
            continue  # 历史遗留绑定：读侧标红由 UI 处理，不阻断本次保存
        problems.append(f"席位 {role_id} 绑定的模型 {requested} 未在模型库登记（可选：{', '.join(sorted(registered))} 或留空=跟随主脑）")
    return problems


def validate_council_roles(roles: Any) -> str:
    """读写两侧共用同一套结构校验，返回人话问题描述（"" = 通过）。

    审计 P1-4a(2026-09-13)：旧读闸只认 `trader_trend`/`cio` 两个 id，而写闸认任意
    `is_arbitrator` 席位 → 管理员把 CIO 改名/换成自定义仲裁官后，写入成功、接口返回 ok，
    **下一次读取就把整份配置覆盖成工厂默认**（enabled=false、自写提示词全丢、无备份）。
    现在两侧共用本函数：写闸拒绝、读闸只警告绝不覆盖用户数据。
    """
    if not isinstance(roles, dict) or not roles:
        return "没有任何席位配置"
    has_arbitrator = any(
        (isinstance(r, dict) and r.get("is_arbitrator")) or str(k).lower() in {"cio", "arbitrator"}
        for k, r in roles.items()
    )
    if not has_arbitrator:
        return "缺少首席终审仲裁官/交易总监(CIO)席位"
    return ""


def clamp_council_timeout(value: Any) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return DEFAULT_COUNCIL_TIMEOUT
    if num != num or num in (float("inf"), float("-inf")):
        return DEFAULT_COUNCIL_TIMEOUT
    return min(MAX_COUNCIL_TIMEOUT, max(MIN_COUNCIL_TIMEOUT, round(num, 1)))

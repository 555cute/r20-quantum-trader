"""R20 Quantum Hedge Fund Investment Committee (Trading Desk Council).
Fully Re-architected in v7.2.2 with Full Account Awareness:
1. Symmetrical Trader Roles (Equal Peer Traders):
   - Trader A: Senior Trend-Pullback Trader (Conservative & High Win-rate)
   - Trader B: Senior Momentum-Breakout Trader (Aggressive & High R:R)
   - Trader C: Senior Quantitative & Calculus Trader (Data-Driven & Microstructure)
2. Trade Proposal & Portfolio Review Protocol:
   Every trader analyzes:
   - Account available capital (USDT balance), position count & risk limits;
   - Active position lifecycle (HOLD / CLOSE_MARKET / UPDATE_SL for trailing profit);
   - Pending maker limit orders lifecycle (CANCEL stale orders vs. KEEP active setups);
   - Opening/Pyramiding proposals for all 6 active instruments with exact parameters.
3. Chief Investment Officer (CIO / Head of Trading) Verdict:
   The CIO reviews all submitted proposals, weighs cross-examination feedback, determines
   which trader's plan to fund and execute (or rejects all for WAIT), and outputs the
   final deterministic trading JSON contract covering decisions, position_management,
   and pending_orders_management.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
COUNCIL_CONFIG_FILE = DATA_DIR / "council_config.json"

VALID_CONSENSUS_MODES = {"standard", "cross_examination"}
DEFAULT_CONSENSUS_MODE = "standard"
MIN_SAFE_REASONING_TIME: float = 5.0
DEFAULT_COUNCIL_TIMEOUT: float = 60.0

DEFAULT_PRESET_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "trader_trend": {
        "id": "trader_trend",
        "name": "资深交易员 A (顺势稳健型)",
        "role_title": "Senior Trend Trader",
        "description": "专注顺大势回踩低吸，全盘审视可用资金与持仓浮盈，严守三阶动态利润棘轮防磨损与高胜率。",
        "prompt": (
            "【角色：资深交易员 A · 稳健顺势波段操盘手】\n"
            "你是对冲基金交易台的核心波段交易员，你的交易哲学是「顺应大势、重视资金利用效率、保本第一」：\n\n"
            "【核心分析工具与审查插槽】：\n"
            "- 【资金与敞口审查】：必须先核验【当前账户可用资金】与【在途持仓概况】，开仓保证金严格控制在可用余额的 5%~15%，空仓槽位不足时坚决克制！\n"
            "- 【在途持仓审查】：逐一审视当前活动持仓：波段顺畅且未破位时坚决主张 HOLD；浮盈达到 1.0R~1.2x ATR 时主张 UPDATE_SL 上移止损锁利；趋势跌破 4H/1H 支撑时主张 CLOSE_MARKET 止损！\n"
            "- 【在途挂单审查】：审视未成交限价挂单，若挂单价已偏离最新支撑或行情已走远，主张 CANCEL 撤单；若依旧属于黄金回踩打折位，主张 KEEP。\n"
            "- 【行情与微结构】：重点核验 {{macro_4h}} 与 {{trading_memory}}，只在 4H 多头通道中找回踩企稳买点。\n\n"
            "【你的任务】：\n"
            "向 CIO 提交你的实战审查报告与完整提案：\n"
            "1. 持仓与挂单审查建议：逐一指出哪些在途持仓需要 HOLD/CLOSE_MARKET/UPDATE_SL，哪些挂单需要 CANCEL/KEEP。\n"
            "2. 新开/加仓作战提案：对 6 大币种逐一给出明确倾向（BUY_LONG/SELL_SHORT/WAIT），包含 entry_price、2.0x ATR 止损、2.0R 止盈、拟占用保证金与依据。\n"
            "3. 简要指出激进追高型同行可能导致账户资金链过紧的致命隐患。"
        ),
        "weight": 0.35,
        "enabled": True,
        "reasoning_effort": "medium",
        "temperature": 0.2,
        "is_arbitrator": False,
        "model_id": "",
    },
    "trader_momentum": {
        "id": "trader_momentum",
        "name": "资深交易员 B (动能突破型)",
        "role_title": "Senior Momentum Trader",
        "description": "专注微积分速度与加速度爆发，敏锐捕捉持仓动能衰竭与在途挂单滞纳风险。",
        "prompt": (
            "【角色：资深交易员 B · 进取动能突破操盘手】\n"
            "你是对冲基金交易台的进攻型突破交易员，你的交易哲学是「紧跟资金最凶猛的非线性动能爆发，动态优化资金周转」：\n\n"
            "【核心分析工具与审查插槽】：\n"
            "- 【资金与仓位审查】：核验【当前账户可用资金】，单笔开仓占用 8%~15% 保证金，若账户已有 3 个以上持仓则提高开仓门槛。\n"
            "- 【在途持仓审查】：核验在途持仓的 1H/15M 动能：一阶速度 v > 0 且加速度 a > 0 坚决主张 HOLD 顺势奔跑；若动能严重背离减速或转负，主张 CLOSE_MARKET 锁定胜果！\n"
            "- 【在途挂单审查】：突破型挂单必须紧贴最新盘口，若挂单滞留超过周期或动能消退，主张 CANCEL 撤单，坚决不接下落飞刀！\n"
            "- 【行情与微结构】：重点核验 {{calculus_1h}} (速度 v 与加速度 a) 与 {{sentiment}}。\n\n"
            "【你的任务】：\n"
            "向 CIO 提交你的实战审查报告与完整提案：\n"
            "1. 持仓与挂单审查建议：从动能角度指出哪些持仓该 HOLD/CLOSE_MARKET，哪些挂单必须 CANCEL。\n"
            "2. 新开/加仓作战提案：给出 6 大标的的具体作战参数（限价、止损、2.5R 止盈、保证金规划与爆发理由）。\n"
            "3. 简要评价当前市场是否处于假突破高危期及同行的保守观望是否会错失主升浪。"
        ),
        "weight": 0.35,
        "enabled": True,
        "reasoning_effort": "medium",
        "temperature": 0.2,
        "is_arbitrator": False,
        "model_id": "",
    },
    "trader_quant": {
        "id": "trader_quant",
        "name": "资深交易员 C (数理筹码型)",
        "role_title": "Senior Quantitative Trader",
        "description": "专注全盘资金流动性、持仓数学期望、聪明钱筹码搬家与盘口挂单陷阱审查。",
        "prompt": (
            "【角色：资深交易员 C · 数理量化与筹码操盘手】\n"
            "你是对冲基金交易台的客观量化交易员，你的交易哲学是「用严密数学公式对全盘资金、持仓和盘口挂单进行硬核压力测试」：\n\n"
            "【核心分析工具与审查插槽】：\n"
            "- 【资金与敞口审查】：严格依据账户可用余额计算凯利公式最优仓位，确保当前持仓敞口总保证金不超过总资产警戒线！\n"
            "- 【在途持仓审查】：对当前持仓进行筹码定积分与主力流向审查：若主力聪明钱反向减持派发，即便浮盈也坚决主张 CLOSE_MARKET 撤离；若盘口深度支撑强劲，主张 HOLD。\n"
            "- 【在途挂单审查】：审查挂单所处价位的盘口挂单墙深度（{{orderbook_depth}}），若挂单下方无大买单防护，主张立即 CANCEL 防止被流动性滑点掠食！\n"
            "- 【行情与数理指标】：重点核验 {{smart_money}} 与延续概率 P续，若 ADX < 18 判定为垃圾时间，坚决反对开仓。\n\n"
            "【你的任务】：\n"
            "向 CIO 提交你的实战审查报告与完整提案：\n"
            "1. 持仓与挂单审查建议：从筹码与挂单深度角度明确指出哪些持仓该撤该留，哪些在途挂单属于流动性陷阱须 CANCEL。\n"
            "2. 新开/加仓作战提案：给出 6 大标的的数学期望评价（BUY/SELL/WAIT、限价、止损、止盈与置信度）。\n"
            "3. 质疑其他交易员的方案：指出是否存在账户资金配置过载或忽视主力暗中出逃的重大漏洞。"
        ),
        "weight": 0.30,
        "enabled": True,
        "reasoning_effort": "high",
        "temperature": 0.1,
        "is_arbitrator": False,
        "model_id": "",
    },
    "cio": {
        "id": "cio",
        "name": "首席投资官 / 交易总监 (Chief Investment Officer)",
        "role_title": "Head of Trading / CIO",
        "description": "统筹全局可用资金、在途持仓与挂单生命周期，审阅各交易员提案与质询，终审拍板发单与风控指令。",
        "prompt": (
            "【角色：对冲基金首席投资官 (CIO) 兼交易总监】\n"
            "你统领交易台全体资深交易员，对基金总资产、可用保证金、在途持仓与挂单池负有全权风控与盈亏责任！\n\n"
            "【你的决策权力与使命】：\n"
            "1. 【资金池统筹审查】：时刻监督账户可用余额（usdt_available）与总持仓数。在可用资金紧张或已达持仓上限时，坚决驳回盲目开仓，优先保全资本。\n"
            "2. 【在途持仓动态裁决 (position_management)】：综合交易员意见，对每一个在途活动持仓做出 HOLD（继续持有）、CLOSE_MARKET（平仓斩仓）或 UPDATE_SL（移动止损保本）的权威批复，严禁让盈利单演变为亏损！\n"
            "3. 【在途挂单生命周期管理 (pending_orders_management)】：对每一个未成交限价挂单做出 CANCEL（撤单）或 KEEP（保留）裁决，坚决清理僵尸挂单与高风险挂单。\n"
            "4. 【新标的开仓方案终审 (decisions)】：审阅各位交易员就 6 大标的提交的完整提案与攻防互评，明确裁定采纳谁的方案执行（输出完整四维点位：limit_price, stop_loss, take_profit, leverage, margin_usd）或全员驳回观望 WAIT。\n"
            "5. 最终必须输出符合交易所执行层契约的标准完整 JSON！"
        ),
        "weight": 1.0,
        "enabled": True,
        "reasoning_effort": "high",
        "temperature": 0.2,
        "is_arbitrator": True,
        "model_id": "",
    },
}

ALL_AVAILABLE_PRESETS = dict(DEFAULT_PRESET_TEMPLATES)

COUNCIL_PRESET_SUITES: Dict[str, Dict[str, Any]] = {
    "hedge_fund_desk": {
        "id": "hedge_fund_desk",
        "name": "对冲基金投委会标准台 (Hedge Fund Desk)",
        "desc": "全息审阅账户资金、持仓与挂单，Trader A/B/C 提案与 CIO 终审查决",
        "consensus_mode": "standard",
        "roles": ["trader_trend", "trader_momentum", "trader_quant", "cio"],
    },
}


def _atomic_write_json(file_path: Path, data: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = file_path.parent
    with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        temp_name = tf.name
    os.replace(temp_name, file_path)


def load_council_config() -> Dict[str, Any]:
    if COUNCIL_CONFIG_FILE.is_file():
        try:
            with open(COUNCIL_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "roles" in data:
                roles = data.get("roles", {})
                if "trader_trend" in roles or "cio" in roles:
                    mode = str(data.get("consensus_mode", DEFAULT_CONSENSUS_MODE)).strip().lower()
                    if mode not in VALID_CONSENSUS_MODES:
                        data["consensus_mode"] = DEFAULT_CONSENSUS_MODE
                    return data
        except Exception:
            pass

    default_config: Dict[str, Any] = {
        "enabled": False,
        "consensus_mode": DEFAULT_CONSENSUS_MODE,
        "timeout_seconds": DEFAULT_COUNCIL_TIMEOUT,
        "roles": {k: dict(v) for k, v in DEFAULT_PRESET_TEMPLATES.items()},
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _atomic_write_json(COUNCIL_CONFIG_FILE, default_config)
    return default_config


def save_council_config(config: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("Council config must be a dict")
    roles = config.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise ValueError("委员会至少需要包含角色配置")

    has_arbitrator = any(r.get("is_arbitrator") or k in {"cio", "arbitrator"} for k, r in roles.items())
    if not has_arbitrator:
        raise ValueError("委员会必须保留至少一位首席终审仲裁官/交易总监(CIO)！")

    for role_id, role in roles.items():
        if not isinstance(role, dict):
            raise ValueError(f"角色 {role_id} 配置必须为字典")
        role["id"] = role_id
        role.setdefault("enabled", True)
        role.setdefault("weight", 0.3)
        role.setdefault("reasoning_effort", "medium")
        role.setdefault("temperature", 0.2)

    mode = str(config.get("consensus_mode", DEFAULT_CONSENSUS_MODE)).strip().lower()
    if mode not in VALID_CONSENSUS_MODES:
        mode = DEFAULT_CONSENSUS_MODE
    config["consensus_mode"] = mode

    config["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_write_json(COUNCIL_CONFIG_FILE, config)
    return config


def get_available_presets() -> List[Dict[str, Any]]:
    return list(ALL_AVAILABLE_PRESETS.values())


def get_preset_suites() -> List[Dict[str, Any]]:
    return list(COUNCIL_PRESET_SUITES.values())


def apply_preset_suite(suite_id: str) -> Dict[str, Any]:
    suite = COUNCIL_PRESET_SUITES.get(suite_id)
    if not suite:
        suite = list(COUNCIL_PRESET_SUITES.values())[0]

    config = load_council_config()
    new_roles: Dict[str, Any] = {}
    for r_id in suite["roles"]:
        if r_id in ALL_AVAILABLE_PRESETS:
            preset = dict(ALL_AVAILABLE_PRESETS[r_id])
            old_model = config.get("roles", {}).get(r_id, {}).get("model_id", "")
            preset["model_id"] = old_model
            new_roles[r_id] = preset

    config["consensus_mode"] = suite.get("consensus_mode", DEFAULT_CONSENSUS_MODE)
    config["roles"] = new_roles
    return save_council_config(config)


def reset_role_template(role_id: str) -> Dict[str, Any]:
    config = load_council_config()
    roles = config.get("roles", {})
    if role_id not in roles:
        raise ValueError(f"未找到角色 ID: {role_id}")

    preset = ALL_AVAILABLE_PRESETS.get(role_id)
    if not preset:
        if role_id in {"cio", "arbitrator"} or roles[role_id].get("is_arbitrator"):
            preset = DEFAULT_PRESET_TEMPLATES["cio"]
        else:
            raise ValueError(f"该角色无内置出厂模板: {role_id}")

    old_model = roles[role_id].get("model_id", "")
    new_role = dict(preset)
    new_role["model_id"] = old_model
    roles[role_id] = new_role
    config["roles"] = roles
    return save_council_config(config)


def _call_single_trader(
    role_id: str,
    role_spec: Dict[str, Any],
    market_prompt: str,
    master_constitutional_rules: str,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    """Invokes a senior trader role to pitch their complete trade proposal and account review."""
    from r20_backend.llm_manager import execute_llm_request, get_active_llm_runtime, load_llm_config

    model_id = role_spec.get("model_id") or ""
    override_model = None
    override_url = None
    override_key = None
    override_format = None
    override_max_tokens = None
    override_effort = role_spec.get("reasoning_effort") or "medium"
    temperature = float(role_spec.get("temperature", 0.2))

    cfg = load_llm_config(mask_keys=False)
    if model_id:
        for item in cfg.get("models", []):
            if item.get("id") == model_id:
                override_model = item.get("id")
                override_url = item.get("base_url")
                override_key = item.get("api_key")
                override_format = item.get("api_format")
                override_effort = item.get("reasoning_effort") or override_effort
                override_max_tokens = item.get("max_output_tokens") or cfg.get("max_output_tokens")
                break
    else:
        override_effort = cfg.get("active_reasoning_effort", "medium")

    prompt_content = role_spec.get("prompt", "")
    role_name = role_spec.get("name", role_id)
    proposal_id = f"{role_id}_prop"

    trader_system_prompt = (
        f"【最高交易宪法与策略纪律】\n"
        f"{master_constitutional_rules}\n\n"
        f"====================================================\n"
        f"【你的交易员身份与操盘职责】\n"
        f"{prompt_content}\n"
        f"注意：你作为专业交易员，必须在上述【最高交易宪法】框架内提交实战作战提案（提案标识: {proposal_id}），"
        f"重点覆盖【账户可用余额】、【在途持仓动态处理】、【在途未成交挂单撤留】与【新标的点位规划】！"
    )

    trader_user_prompt = (
        f"【当前全景市场数据、账户资金与在途持仓挂单】\n"
        f"{market_prompt}\n\n"
        f"请以你「{role_name}」（提案标识: {proposal_id}）的专业视角，向首席投资官 (CIO) 提交本轮实操审查与作战方案：\n"
        f"1. 账户持仓与挂单审查：\n"
        f"   - 对在途持仓逐一给出管理建议：HOLD（波段完好继续持有）、CLOSE_MARKET（结构破位斩仓）或 UPDATE_SL（浮盈锁定移动止损）；\n"
        f"   - 对在途未成交限价挂单逐一给出建议：CANCEL（偏离盘口或动能失效立即撤单）或 KEEP（继续保留）；\n"
        f"2. 6大标的新开/加仓作战提案：\n"
        f"   - 针对各标的输出明确方案：倾向（BUY_LONG / SELL_SHORT / WAIT）、入场限价 limit_price、2.0x ATR 止损 stop_loss、止盈 take_profit、拟投入保证金与置信度；\n"
        f"3. 质询与风控：简要指出其他交易员方案可能带来的资金过载或流动性风险（60字内/标的）。"
    )

    messages = [
        {"role": "system", "content": trader_system_prompt},
        {"role": "user", "content": trader_user_prompt},
    ]

    try:
        content, reasoning, usage, latency = execute_llm_request(
            messages=messages,
            model=override_model,
            base_url=override_url,
            api_key=override_key,
            api_format=override_format,
            reasoning_effort=override_effort,
            temperature=temperature,
            timeout=timeout,
            max_tokens=override_max_tokens,
        )
        return {
            "proposal_id": proposal_id,
            "role_id": role_id,
            "role_name": role_name,
            "model_used": override_model or get_active_llm_runtime().get("model", "default"),
            "status": "ok",
            "content": content.strip(),
            "reasoning": reasoning.strip() if reasoning else "",
            "latency_ms": latency,
            "weight": role_spec.get("weight", 1.0),
        }
    except Exception as e:
        return {
            "proposal_id": proposal_id,
            "role_id": role_id,
            "role_name": role_name,
            "model_used": override_model or "unknown",
            "status": "error",
            "content": f"交易员方案提交异常/超时降级: {e}",
            "reasoning": "",
            "latency_ms": 0,
            "weight": 0.0,
        }


def _call_single_trader_critique(
    role_id: str,
    role_spec: Dict[str, Any],
    my_proposal: str,
    peer_proposals: str,
    master_constitutional_rules: str,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Invokes a senior trader role to cross-examine peer proposals for hidden risks, timing, or sizing flaws."""
    from r20_backend.llm_manager import execute_llm_request, get_active_llm_runtime, load_llm_config

    model_id = role_spec.get("model_id") or ""
    override_model = None
    override_url = None
    override_key = None
    override_format = None
    override_max_tokens = None
    override_effort = role_spec.get("reasoning_effort") or "medium"
    temperature = float(role_spec.get("temperature", 0.2))

    cfg = load_llm_config(mask_keys=False)
    if model_id:
        for item in cfg.get("models", []):
            if item.get("id") == model_id:
                override_model = item.get("id")
                override_url = item.get("base_url")
                override_key = item.get("api_key")
                override_format = item.get("api_format")
                override_effort = item.get("reasoning_effort") or override_effort
                override_max_tokens = item.get("max_output_tokens") or cfg.get("max_output_tokens")
                break
    else:
        override_effort = cfg.get("active_reasoning_effort", "medium")

    prompt_content = role_spec.get("prompt", "")
    role_name = role_spec.get("name", role_id)

    critique_system_prompt = (
        f"【最高交易宪法与策略纪律】\n"
        f"{master_constitutional_rules}\n\n"
        f"====================================================\n"
        f"【你的交易员身份与操盘职责】\n"
        f"{prompt_content}\n"
        f"注意：你现在进入第二轮「同行方案交叉漏洞质询（Cross-Examination）」。你的职责是站在你的专业立场，严肃审查同行交易员的方案，指出其盲区、追高风险或防插针止损不足！"
    )

    critique_user_prompt = (
        f"【你第一轮提交的作战提案】\n"
        f"{my_proposal}\n\n"
        f"====================================================\n"
        f"【同行交易员提交的第一轮作战提案卷宗】\n"
        f"{peer_proposals}\n\n"
        f"====================================================\n"
        f"请以你「{role_name}」的专业视角，对同行的方案展开针对性质询（Cross-Examination）：\n"
        f"1. 逐一质询同行方案在点位入场（是否追高）、2.0x ATR 止损距离、拟用保证金或假突破风险上的漏洞；\n"
        f"2. 明确论证为何你的方案在当前资金与市场环境下更安全或盈亏比更优；\n"
        f"3. 保持专业精炼，直击漏洞要害。"
    )

    messages = [
        {"role": "system", "content": critique_system_prompt},
        {"role": "user", "content": critique_user_prompt},
    ]

    try:
        content, reasoning, usage, latency = execute_llm_request(
            messages=messages,
            model=override_model,
            base_url=override_url,
            api_key=override_key,
            api_format=override_format,
            reasoning_effort=override_effort,
            temperature=temperature,
            timeout=timeout,
            max_tokens=override_max_tokens,
        )
        return {
            "role_id": role_id,
            "role_name": role_name,
            "model_used": override_model or get_active_llm_runtime().get("model", "default"),
            "status": "ok",
            "content": content.strip(),
            "reasoning": reasoning.strip() if reasoning else "",
            "latency_ms": latency,
        }
    except Exception as e:
        return {
            "role_id": role_id,
            "role_name": role_name,
            "model_used": override_model or "unknown",
            "status": "error",
            "content": f"质询提交异常/超时降级: {e}",
            "reasoning": "",
            "latency_ms": 0,
        }


def execute_council_debate(
    market_prompt: str,
    original_system_prompt: str,
    timeout: float = 60.0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Execute Hedge Fund Investment Committee Deliberation:

    Converged Real Modes:
    - "standard": 1-round trader proposals -> compiled docket -> CIO final verdict.
    - "cross_examination": Round 1 proposals -> Round 2 peer cross-examination -> CIO verdict.

    Strict Timeout Control:
    - Anchored on deadline = t_start + timeout.
    - Dynamically evaluates rem = deadline - time.time() before every stage.
    - Safely downgrades or raises TimeoutError if remaining budget < MIN_SAFE_REASONING_TIME (5.0s).

    Structured Contract & Adoption Traceability:
    - Proposals are tagged with proposal_id (e.g. trader_trend_prop).
    - CIO decisions must include adopted_role (e.g. 'trader_trend' or 'REJECT_ALL'/None).
    """
    from r20_backend.llm_manager import execute_llm_request, get_active_llm_runtime, load_llm_config, parse_model_json_object

    config = load_council_config()
    roles = config.get("roles", {})
    consensus_mode = str(config.get("consensus_mode", DEFAULT_CONSENSUS_MODE)).strip().lower()
    if consensus_mode not in VALID_CONSENSUS_MODES:
        consensus_mode = DEFAULT_CONSENSUS_MODE

    t_start = time.time()
    effective_timeout = max(1.0, float(timeout))
    deadline = t_start + effective_timeout

    rem = deadline - time.time()
    if rem < MIN_SAFE_REASONING_TIME:
        raise TimeoutError(
            f"Council deliberation timeout: remaining time {rem:.2f}s is below safety threshold {MIN_SAFE_REASONING_TIME}s"
        )

    # Identify CIO (Arbitrator) and Active Traders
    cio_key = next(
        (k for k, r in roles.items() if r.get("is_arbitrator") or k in {"cio", "arbitrator"}),
        "cio",
    )
    cio_spec = roles.get(cio_key, DEFAULT_PRESET_TEMPLATES["cio"])
    trader_keys = [
        k for k in roles.keys()
        if k != cio_key and roles[k].get("enabled", True) is not False
    ]

    trader_proposals: Dict[str, Dict[str, Any]] = {}
    trader_critiques: Dict[str, Dict[str, Any]] = {}

    if not trader_keys:
        # Fallback: Solo CIO decision if no active traders enabled
        pass
    elif consensus_mode == "cross_examination":
        # === MODE: Cross-Examination (Double-Round Real Debate) ===
        rem = deadline - time.time()
        if rem < MIN_SAFE_REASONING_TIME * 2.0:
            raise TimeoutError(
                f"Council timeout: remaining time {rem:.2f}s insufficient for cross-examination mode (requires >= {MIN_SAFE_REASONING_TIME * 2.0}s)"
            )

        # Stage 1: Round 1 Independent Proposals
        round1_budget = max(2.0, min(rem * 0.35, rem - (MIN_SAFE_REASONING_TIME * 2.0)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(trader_keys))) as pool:
            futures = {
                pool.submit(
                    _call_single_trader,
                    key,
                    roles[key],
                    market_prompt,
                    original_system_prompt,
                    round1_budget,
                ): key
                for key in trader_keys
            }
            for fut in concurrent.futures.as_completed(futures):
                key = futures[fut]
                try:
                    trader_proposals[key] = fut.result()
                except Exception as exc:
                    trader_proposals[key] = {
                        "proposal_id": f"{key}_prop",
                        "role_id": key,
                        "role_name": roles[key].get("name", key),
                        "status": "error",
                        "content": f"Proposal exception: {exc}",
                        "weight": 0.0,
                    }

        # Stage 2: Round 2 Cross-Examination Critiques
        rem = deadline - time.time()
        if rem < MIN_SAFE_REASONING_TIME + 2.0:
            # Insufficient budget for second round -> safe degradation: skip critiques to preserve CIO verdict
            for k in trader_keys:
                trader_critiques[k] = {
                    "role_id": k,
                    "role_name": roles[k].get("name", k),
                    "status": "skipped",
                    "content": f"时间预算紧缺 (剩余 {rem:.2f}s < 7.0s)，安全降级跳过交叉质询以确保 CIO 终审",
                    "latency_ms": 0,
                }
        else:
            round2_budget = max(2.0, min(rem * 0.40, rem - MIN_SAFE_REASONING_TIME))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(trader_keys))) as pool:
                critique_futures = {}
                for k in trader_keys:
                    my_prop = trader_proposals.get(k, {}).get("content", "（该交易员第一轮未提交有效提案）")
                    peers_text_list = []
                    for pk in trader_keys:
                        if pk != k:
                            p_res = trader_proposals.get(pk, {})
                            p_id = p_res.get("proposal_id", f"{pk}_prop")
                            p_name = p_res.get("role_name", pk)
                            peers_text_list.append(
                                f"=== 【{p_name}】(提案标识: {p_id}) ===\n"
                                f"{p_res.get('content', '（未提交）')}"
                            )
                    peers_text = "\n\n".join(peers_text_list) if peers_text_list else "（无其他同行提案）"
                    critique_futures[pool.submit(
                        _call_single_trader_critique,
                        k,
                        roles[k],
                        my_prop,
                        peers_text,
                        original_system_prompt,
                        round2_budget,
                    )] = k
                for fut in concurrent.futures.as_completed(critique_futures):
                    k = critique_futures[fut]
                    try:
                        trader_critiques[k] = fut.result()
                    except Exception as exc:
                        trader_critiques[k] = {
                            "role_id": k,
                            "role_name": roles[k].get("name", k),
                            "status": "error",
                            "content": f"质询异常: {exc}",
                            "latency_ms": 0,
                        }
    else:
        # === MODE: Standard (Single-Round Proposals -> CIO Verdict) ===
        rem = deadline - time.time()
        if rem < MIN_SAFE_REASONING_TIME + 2.0:
            raise TimeoutError(
                f"Council timeout: remaining time {rem:.2f}s insufficient for standard deliberation (requires >= {MIN_SAFE_REASONING_TIME + 2.0}s)"
            )

        member_timeout = max(2.0, min(rem * 0.50, rem - MIN_SAFE_REASONING_TIME))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(trader_keys))) as pool:
            futures = {
                pool.submit(
                    _call_single_trader,
                    key,
                    roles[key],
                    market_prompt,
                    original_system_prompt,
                    member_timeout,
                ): key
                for key in trader_keys
            }
            for fut in concurrent.futures.as_completed(futures):
                key = futures[fut]
                try:
                    trader_proposals[key] = fut.result()
                except Exception as exc:
                    trader_proposals[key] = {
                        "proposal_id": f"{key}_prop",
                        "role_id": key,
                        "role_name": roles[key].get("name", key),
                        "status": "error",
                        "content": f"Proposal exception: {exc}",
                        "weight": 0.0,
                    }

    # Compile the Structured Investment Committee Docket
    transcript_blocks = []
    for k in trader_keys:
        res = trader_proposals.get(k, {})
        weight_str = f" [绩效权重: {res.get('weight', 1.0)}]" if res.get("weight") is not None else ""
        p_id = res.get("proposal_id", f"{k}_prop")
        transcript_blocks.append(
            f"=== 【{res.get('role_name', k)}】实操审查与作战提案 [提案标识: {p_id}]（模型：{res.get('model_used', 'default')}{weight_str}）===\n"
            f"{res.get('content', '（该交易员本轮未提交有效提案）')}\n"
        )
    compiled_proposals = "\n".join(transcript_blocks) if transcript_blocks else "（无其他交易员提交方案，首席投资官独立决策）"

    if consensus_mode == "cross_examination" and trader_critiques:
        critique_blocks = []
        for k in trader_keys:
            c_res = trader_critiques.get(k, {})
            critique_blocks.append(
                f"=== 【{c_res.get('role_name', k)}】针对同行方案的交叉漏洞质询 ===\n"
                f"{c_res.get('content', '（该交易员未提交质询）')}\n"
            )
        compiled_critiques = "\n".join(critique_blocks)
        docket_content = (
            f"【第一轮：各交易员独立作战提案卷宗】\n{compiled_proposals}\n\n"
            f"====================================================\n"
            f"【第二轮：同行方案交叉漏洞质询与攻防辩论】\n{compiled_critiques}"
        )
    else:
        docket_content = f"【交易员实战作战提案卷宗】\n{compiled_proposals}"

    # CIO Final Review & Funding Verdict
    rem = deadline - time.time()
    if rem < MIN_SAFE_REASONING_TIME:
        raise TimeoutError(
            f"Council deliberation timeout before CIO arbitration: {rem:.2f}s remaining is below safety threshold {MIN_SAFE_REASONING_TIME}s"
        )

    cio_model_id = cio_spec.get("model_id") or ""
    override_model = None
    override_url = None
    override_key = None
    override_format = None
    override_max_tokens = None
    override_effort = "high"
    cio_temperature = float(cio_spec.get("temperature", 0.2))

    cfg = load_llm_config(mask_keys=False)
    if cio_model_id:
        for item in cfg.get("models", []):
            if item.get("id") == cio_model_id:
                override_model = item.get("id")
                override_url = item.get("base_url")
                override_key = item.get("api_key")
                override_format = item.get("api_format")
                override_effort = item.get("reasoning_effort") or "high"
                override_max_tokens = item.get("max_output_tokens") or cfg.get("max_output_tokens")
                break
    else:
        override_effort = cfg.get("active_reasoning_effort", "high")

    cio_system_prompt = (
        f"{original_system_prompt}\n\n"
        "====================================================\n"
        f"【身份特别授权：你是对冲基金首席投资官 (CIO) 兼交易总监】\n"
        f"{cio_spec.get('prompt', '')}\n\n"
        "====================================================\n"
        "【投委会终审发单契约强约束（全面落盘持仓处理、挂单撤留与新标的点位！）】\n"
        "你必须对全局资金、在途持仓、在途挂单及 6 大主力标的做出终审裁决：\n"
        "1. 【持仓与挂单闭环管理】：\n"
        "   - 在 position_management 中对所有活动持仓下达权威指令（HOLD / CLOSE_MARKET / UPDATE_SL）及理由；\n"
        "   - 在 pending_orders_management 中对所有在途未成交挂单下达处理指令（CANCEL / KEEP）及理由；\n"
        "2. 【6 大标的开仓方案终审 (decisions) 与采纳归属 (adopted_role)】：\n"
        f"   - 仔细比对各位交易员提交的方案{'与交叉质询辩论' if consensus_mode == 'cross_examination' else ''}，评估逻辑最扎实者采纳，存在漏洞者驳回；\n"
        "   - decisions 必须是标的字典（如 \"BTC-USDT-SWAP\"），每个标的必须包含 \"adopted_role\" 字段：\n"
        "     * 采纳某位交易员方案时填写其 role_id（例如 \"trader_trend\"、\"trader_momentum\"、\"trader_quant\"）；\n"
        "     * 全员驳回或无人被采纳时填写 \"REJECT_ALL\" 或 null；\n"
        "   - 在 reasoning 中明确写出你的仲裁依据（如「【CIO批复】采纳交易员 A 对 BTC 稳健回踩买多方案，驳回交易员 B 的追多」或「【CIO批复】驳回全员方案，市场震荡全员空仓 WAIT」）；\n"
        "   - 若批准对某标的开仓（BUY_LONG 或 SELL_SHORT），必须输出完整的四维点位与采纳归属：\n"
        "     {\n"
        '       "action": "BUY_LONG" 或 "SELL_SHORT",\n'
        '       "adopted_role": "trader_trend",  // 明确采纳的交易员 ID（如 trader_trend / trader_momentum / trader_quant），若无则填写 null\n'
        '       "confidence": 82,  // 最终核定置信度整数 0~100\n'
        '       "entry_price": 78250.0,  // 挂单入场限价（数字），严禁市价追高\n'
        '       "limit_price": 78250.0,  // 入场限价同义兼容\n'
        '       "stop_loss": 76500.0,  // 严格基于 1.8~2.2x 1H ATR 设置的防插针止损价（数字）\n'
        '       "stop_loss_price": 76500.0,  // 止损价同义兼容\n'
        '       "take_profit": 81750.0,  // 至少 2.0R 盈亏比的目标止盈价（数字）\n'
        '       "take_profit_price": 81750.0,  // 止盈价同义兼容\n'
        '       "leverage": 3,  // 杠杆倍数（整型 2~5）\n'
        '       "margin_usdt": 150.0,  // 拟投入保证金（须在可用余额安全范围内）\n'
        '       "reasoning": "【CIO批复】采纳/驳回了哪位交易员的提案，资金与风控考量"\n'
        "     }\n"
        '   - 若判定为 WAIT 观望，输出: {"action": "WAIT", "adopted_role": "REJECT_ALL", "confidence": 50, "reasoning": "【CIO批复】驳回理由与资金保全考量"}\n\n'
        "3. 最终必须且只能输出严格符合交易契约的 JSON 格式，绝不包含任何 markdown 代码块外部的多余文本！\n"
        "必须包含三个顶层键：\"macro_assessment\", \"position_management\", \"decisions\"（可选包含 \"pending_orders_management\"）。"
    )

    cio_user_prompt = (
        "【市场实时全景数据、账户可用资金与在途持仓挂单】\n"
        f"{market_prompt}\n\n"
        "====================================================\n"
        f"{docket_content}\n\n"
        "====================================================\n"
        "请作为首席投资官 (CIO) 审阅卷宗，统筹资金安全，裁定本轮发单并输出标准 JSON：\n"
        "1. 在 macro_assessment 中给出全局资金偏好、仓位总敞口与宏观裁定总括。\n"
        "2. 在 position_management 中落实每一个现有持仓的动态处理。\n"
        "3. 在 decisions 中对 6 大标的逐一下达方案采纳或驳回批复（包含 adopted_role 与 reasoning），并给出完整四维点位！"
    )

    cio_timeout = max(MIN_SAFE_REASONING_TIME, deadline - time.time())
    content, reasoning, usage, latency = execute_llm_request(
        messages=[
            {"role": "system", "content": cio_system_prompt},
            {"role": "user", "content": cio_user_prompt},
        ],
        model=override_model,
        base_url=override_url,
        api_key=override_key,
        api_format=override_format,
        reasoning_effort=override_effort,
        temperature=cio_temperature,
        response_format={"type": "json_object"},
        timeout=cio_timeout,
        max_tokens=override_max_tokens,
    )

    brain_output = parse_model_json_object(content)

    # Post-process & normalize adopted_role in decisions for traceability
    decisions = brain_output.get("decisions")
    if isinstance(decisions, dict):
        for sym, dec in decisions.items():
            if isinstance(dec, dict):
                act = str(dec.get("action", "")).upper()
                ar = dec.get("adopted_role")
                if ar is None or str(ar).strip() == "" or str(ar).strip().lower() == "none":
                    if act == "WAIT":
                        dec["adopted_role"] = "REJECT_ALL"
                    else:
                        # Attempt to resolve from reasoning text
                        reasoning_text = str(dec.get("reasoning", ""))
                        matched_role = None
                        for r_k in trader_keys:
                            r_name = roles.get(r_k, {}).get("name", "")
                            alias_candidates = [r_k, r_name]
                            if "trend" in r_k:
                                alias_candidates.extend(["交易员 A", "交易员A", "Trader A", "trader a", "稳健型"])
                            elif "momentum" in r_k:
                                alias_candidates.extend(["交易员 B", "交易员B", "Trader B", "trader b", "动能型"])
                            elif "quant" in r_k:
                                alias_candidates.extend(["交易员 C", "交易员C", "Trader C", "trader c", "量化型"])

                            if any(alias and alias.lower() in reasoning_text.lower() for alias in alias_candidates):
                                matched_role = r_k
                                break
                        dec["adopted_role"] = matched_role
                else:
                    dec["adopted_role"] = str(ar).strip()

    council_transcript = {
        "council_mode": True,
        "council_architecture": "Hedge Fund Investment Committee",
        "consensus_mode": consensus_mode,
        "total_duration_ms": int((time.time() - t_start) * 1000),
        "arbitrator": {
            "role_name": cio_spec.get("name", "首席投资官 (CIO)"),
            "model_used": override_model or get_active_llm_runtime().get("model", "default"),
            "latency_ms": latency,
            "reasoning": reasoning,
        },
        "advisors": trader_proposals,
        "cross_examinations": trader_critiques if consensus_mode == "cross_examination" else {},
    }

    brain_output["council_transcript"] = council_transcript
    return brain_output, council_transcript

"""Versioned prompt profile library used directly by Python trading processes."""
from __future__ import annotations
import copy
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LIBRARY_FILE = ROOT / "data" / "prompt_library.json"
BJ_TZ = timezone(timedelta(hours=8))
TEMPLATE_KEYS = ("trading_system", "trading_user", "evolution_system", "evolution_user")

TEMPLATE_VARIABLES_METADATA = [
    {
        "key": "news_intelligence",
        "label": "全网实时资讯",
        "category": "实时情报",
        "description": "实时注入全网突发重大加密快讯、宏观经济基调与市场情绪倾向",
        "sample": "【宏观环境基调】: 偏多倾向\n【最新核心资讯要闻】:\n- [2026-09-03 23:45] 美联储官员表态对通胀回落充满信心...",
    },
    {
        "key": "trading_memory",
        "label": "自进化实战心法",
        "category": "自进化",
        "description": "注入每日复盘根据历史平仓台账提炼的核心实战心法、避坑指南与痛点归因",
        "sample": "# R20 AI 交易大脑长期记忆与启发式心法\n1. [2026-09-04] 4H主升浪中回调即是做多机会，严禁盲目摸顶开空...",
    },
    {
        "key": "market_matrix",
        "label": "标的行情数理矩阵",
        "category": "行情数据",
        "description": "注入6币种K线、现价、盘口买卖价、聪明钱流向、1H三大数理基石硬证据(v/a/j/I/E/A/VaR)",
        "sample": "【BTC (BTC-USDT-SWAP)】| 现价: 77575 | 4H宏观大势=4H_MACRO_BULL\n- 1H三大数理基石硬证据: 1H:v=+0.08,a=+0.42...",
    },
    {
        "key": "account_positions",
        "label": "账户当前持仓",
        "category": "账户敞口",
        "description": "注入系统持仓概况、在途持仓方向、均价、标记价、持仓张数、未结浮盈ROI与动态止损线",
        "sample": "【账户持仓概况】: 当前系统总持仓 1/6\n- 标的: SOL-USDT-SWAP | 方向: long 3x | 开仓均价: 103.55 | 未结浮盈: +9.00 U",
    },
    {
        "key": "pending_orders",
        "label": "在途未成交挂单",
        "category": "账户敞口",
        "description": "注入当前在途未成交的 Maker 限价挂单、买卖方向、价格、数量及附带的云端OCO止盈止损",
        "sample": "- [挂单ID: 38790...] LINK-USDT-SWAP | 限价买多 5张 @ 10.85 | 附带云端止盈: 12.00 / 止损: 10.30",
    },
    {
        "key": "account_balance",
        "label": "账户可用资金",
        "category": "账户敞口",
        "description": "注入当前账户实际可用于开仓和加仓的 USDT 现金可用余额",
        "sample": "3858.73 USDT",
    },
    {
        "key": "decision_timestamp",
        "label": "决策基准时间",
        "category": "系统环境",
        "description": "注入当前交易推演周期的精确北京时间戳与时效基准",
        "sample": "2026-09-04 02:30:00 (北京时间)",
    },
    {
        "key": "active_instruments",
        "label": "监控标的列表",
        "category": "系统环境",
        "description": "注入当前系统跟踪并推演的加密货币标的列表",
        "sample": "BTC,ETH,SOL,DOGE,SUI,LINK",
    },
    {
        "key": "strategy_version",
        "label": "系统版本号",
        "category": "系统环境",
        "description": "当前 R20 Quantum Trader 交易引擎版本",
        "sample": "6.8.1",
    },
    {
        "key": "timezone",
        "label": "系统基准时区",
        "category": "系统环境",
        "description": "系统统一时间戳时区",
        "sample": "Asia/Shanghai",
    },
    {
        "key": "timestamp_beijing",
        "label": "复盘基准时间",
        "category": "系统环境",
        "description": "自进化复盘周期的精确北京时间戳别名（与 decision_timestamp 同源，用于复盘管线）",
        "sample": "2026-09-06 06:00:00 (北京时间)",
    },
    {
        "key": "existing_memory_markdown",
        "label": "历史长期记忆库",
        "category": "自进化",
        "description": "注入当前系统已沉淀的完整长期记忆 Markdown 原文，供复盘对照与增量修订",
        "sample": "# R20 AI 交易大脑长期记忆与启发式心法\n1. [2026-09-05] 4H 顺势回踩优先做多...",
    },
    {
        "key": "total",
        "label": "总平仓笔数",
        "category": "交易台账",
        "description": "复盘窗口内已平仓交易总笔数",
        "sample": "12",
    },
    {
        "key": "wins",
        "label": "盈利笔数",
        "category": "交易台账",
        "description": "复盘窗口内净盈亏为正的平仓笔数",
        "sample": "7",
    },
    {
        "key": "losses",
        "label": "亏损笔数",
        "category": "交易台账",
        "description": "复盘窗口内净盈亏为负或持平的平仓笔数",
        "sample": "5",
    },
    {
        "key": "win_rate",
        "label": "胜率",
        "category": "交易台账",
        "description": "复盘窗口内盈利笔数占总平仓笔数的百分比",
        "sample": "58.3",
    },
    {
        "key": "total_net",
        "label": "累计净盈亏",
        "category": "交易台账",
        "description": "复盘窗口内所有已平仓交易的累计净盈亏（USDT）",
        "sample": "+123.45",
    },
    {
        "key": "total_fees",
        "label": "累计手续费",
        "category": "交易台账",
        "description": "复盘窗口内所有已平仓交易累计消耗的手续费（USDT）",
        "sample": "5.60",
    },
    {
        "key": "target_instruments",
        "label": "聚焦标的池",
        "category": "交易台账",
        "description": "当前系统聚焦交易与复盘的加密货币标的列表",
        "sample": "BTC-USDT-SWAP, ETH-USDT-SWAP, SOL-USDT-SWAP",
    },
    {
        "key": "closed_trades_json",
        "label": "逐笔交易台账",
        "category": "交易台账",
        "description": "复盘窗口内逐笔已平仓交易明细的 JSON 序列化文本",
        "sample": '[{"symbol":"BTC","net_pnl":12.3,"fee":0.4}]',
    },
]

ALLOWED_VARIABLES = {item["key"] for item in TEMPLATE_VARIABLES_METADATA} | {"profile_name", "timestamp"}
EXPORT_FORMAT = "r20-prompt-profile"
EXPORT_VERSION = 4
_IMPORT_FORMAT_HINT = (
    "无法识别的提示词文件。请提供以下三种格式之一："
    "(1) 标准导出包 {\"format\":\"r20-prompt-profile\",\"version\":4,\"profile\":{...}}（v1~v4 均可）；"
    "(2) 整库导出文件 {\"version\":2,\"active_profile_id\":\"...\",\"profiles\":{...}}，将导入其中的启用方案；"
    "(3) 裸方案对象（直接包含 pipelines 或 trading_system/trading_user/evolution_system/evolution_user 字段）。"
)
MAX_TEMPLATE_CHARS = 12_000
MAX_PROFILE_CHARS = 32_000
MAX_REVISIONS = 100
MAX_MODULES_PER_PIPELINE = 40
_SECTION_RE = re.compile(r"(?m)(?=^={0,30}\s*【[^\n】]+】[^\n]*$)")

PRESETS: dict[str, dict[str, Any]] = {
    "stable": {
        "id": "stable", "name": "全维度波段强化版", "description": "基于 1H~4H 宏观多空趋势、因果微积分、定积分能量、概率论高权重定价与智能加仓的量化决策方案（系统唯一主策略）。", "editable": True,
        "editor_mode": "modules",
        "pipelines": {
            "trading_system": [
                {"id": "custom-ts-style", "title": "全维度波段强化交易风格", "locked": False, "enabled": True, "source": "custom",
                 "content": "【交易风格：全维度波段强化（概率论权重提升·强开单·高胜率·防割肉）】\n所有 P0 硬约束保持不变，不得把“稳健”解释为长期空仓。核心裁决由【概率论与期望值】优先定性：当条件延续/击穿概率具有优势且 R:R≥2.0 时果断进场！杜绝频繁随意割肉：止损给足 1.8~2.2x ATR 彻底隔绝杂波插针扫损；浮盈达 0.8R~1.0R 坚决启动保本移损锁死胜率，杜绝浮盈变亏损。多空对称顺势，形态契合时自信评定 78%~88% 积极开单进场！"},
            ],
            "trading_user": [
                {"id": "base-ts-time", "title": "当前决策时间戳与市场时效", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【当前决策时间戳与市场时效】 =======================\n{{decision_timestamp}}\n{{account_balance}}"},
                {"id": "base-ts-news", "title": "全网实时重大快讯与宏观情报", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【全网实时重大快讯与宏观情报】 =======================\n{{news_intelligence}}"},
                {"id": "base-ts-pos", "title": "账户当前持仓与风险敞口全景", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【账户当前持仓与风险敞口全景】 =======================\n{{account_positions}}"},
                {"id": "base-ts-pending", "title": "在途未成交限价挂单 (Pending Maker Orders)", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【在途未成交限价挂单 (Pending Maker Orders)】 =======================\n{{pending_orders}}"},
                {"id": "base-ts-memory", "title": "R20 启发式实战认知与长期记忆", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【R20 启发式实战认知与长期记忆】 =======================\n{{trading_memory}}"},
                {"id": "base-ts-matrix", "title": "六币种原生行情、技术指标与筹码矩阵", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【六币种原生行情、技术指标与筹码矩阵】 =======================\n{{market_matrix}}"},
                {"id": "base-ts-task", "title": "推演与决策任务", "locked": False, "enabled": True, "source": "base", "content": ""},
                {"id": "custom-tu-style", "title": "全维度波段强化裁决偏好（概率论高权重·多空对称·高胜率·防割肉体系）", "locked": False, "enabled": True, "source": "custom",
                 "content": "【全维度波段强化裁决偏好（概率论高权重·多空对称·高胜率·防割肉体系）】\n1. 概率论最高权重决策：优先根据条件延续概率 P续 与击穿概率 P破 的数学期望定价；P续 占优专注做多回踩，P破 占优专注承压做空；\n2. 强烈开单欲望：拒绝机械空仓观望，普通回抽优先作为限价入场定位，4H/1H 顺势多头找回踩低吸挂多，4H/1H 顺势空头找反弹承压挂空，箱体震荡边界双向高抛低吸；\n3. 彻底拒绝随意割肉：止损距离外扩给足 1.8x~2.2x 1H ATR 呼吸空间，隔绝 15M/5M 噪音假动作；\n4. 浮盈达 0.8R~1.0R 主动输出 UPDATE_SL 移至保本位，锁定胜率下限，杜绝盈利单回吐成亏损割肉；\n5. 形态确立且 R:R≥2.0 时，自信给出 78%~88% 置信度果断开单！"},
            ],
            "evolution_system": [
                {"id": "custom-es-style", "title": "全维度波段复盘风格", "locked": False, "enabled": True, "source": "custom",
                 "content": "【全维度波段复盘风格】\n优先识别回撤、过度交易、追价和低质量入场，但只使用真实可观测证据。小样本、数理快照缺失或因果不可辨时 NO_CHANGE；任何记忆都不得成为绕过硬风控的新阈值。"},
            ],
            "evolution_user": [
                {"id": "base-eu-time-hdr", "title": "当前认知复盘基准时间", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【当前认知复盘基准时间】 ======================="},
                {"id": "base-eu-time", "title": "复盘基准时间", "locked": False, "enabled": True, "source": "base",
                 "content": "【复盘基准时间】: {{timestamp_beijing}}"},
                {"id": "base-eu-mem", "title": "当前系统已有的历史长期记忆库", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【当前系统已有的历史长期记忆库】 =======================\n{{existing_memory_markdown}}"},
                {"id": "base-eu-ledger-hdr", "title": "R20 加密量化实盘战绩与历史交易台账", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【R20 加密量化实盘战绩与历史交易台账】 ======================="},
                {"id": "base-eu-stats", "title": "统计汇总", "locked": False, "enabled": True, "source": "base",
                 "content": "【统计汇总】:\n- 总平仓笔数: {{total}} 笔（胜 {{wins}} / 负 {{losses}} | 胜率 {{win_rate}}%）\n- 累计净盈亏: {{total_net}} USDT | 累计手续费: {{total_fees}} USDT\n- 当前聚焦标的池: {{target_instruments}}"},
                {"id": "base-eu-trades", "title": "逐笔历史交易明细 (按时间排序)", "locked": False, "enabled": True, "source": "base",
                 "content": "【逐笔历史交易明细】:\n{{closed_trades_json}}"},
                {"id": "base-eu-task", "title": "复盘与长期记忆进化任务", "locked": False, "enabled": True, "source": "base",
                 "content": "【复盘与长期记忆进化任务】:\n严格基于可观测台账证据复盘；没有交易发生时的微积分、定积分、概率与 VaR/CVaR 快照时，必须标记“数理快照不可观测”，不得事后编造。证据不足时输出 NO_CHANGE 并保留现有记忆。输出 change_status、diagnosis_insights、evolution_actions、ai_long_term_memory、memory_overwrites_reason 的严格 JSON。"},
                {"id": "custom-eu-task", "title": "全维度波段进化任务", "locked": False, "enabled": True, "source": "custom",
                 "content": "【全维度波段进化任务】\n评估信号一致性、风险预算、手续费、入场与退出质量；只有多个独立样本支持时才沉淀新经验，否则保留旧记忆并提出需要补充的证据。"},
            ],
        },
        "trading_system": """【交易风格：全维度波段强化（概率论权重提升·强开单·高胜率·防割肉）】\n所有 P0 硬约束保持不变，不得把“稳健”解释为长期空仓。核心裁决由【概率论与期望值】优先定性：当条件延续/击穿概率具有优势且 R:R≥2.0 时果断进场！杜绝频繁随意割肉：止损给足 1.8~2.2x ATR 彻底隔绝杂波插针扫损；浮盈达 0.8R~1.0R 坚决启动保本移损锁死胜率，杜绝浮盈变亏损。多空对称顺势，形态契合时自信评定 78%~88% 积极开单进场！""",
        "trading_user": """【全维度波段强化裁决偏好（概率论高权重·多空对称·高胜率·防割肉体系）】
1. 概率论最高权重决策：优先根据条件延续概率 P续 与击穿概率 P破 的数学期望定价；P续 占优专注做多回踩，P破 占优专注承压做空；
2. 强烈开单欲望：拒绝机械空仓观望，普通回抽优先作为限价入场定位，4H/1H 顺势多头找回踩低吸挂多，4H/1H 顺势空头找反弹承压挂空，箱体震荡边界双向高抛低吸；
3. 彻底拒绝随意割肉：止损距离外扩给足 1.8x~2.2x 1H ATR 呼吸空间，隔绝 15M/5M 噪音假动作；
4. 浮盈达 0.8R~1.0R 主动输出 UPDATE_SL 移至保本位，锁定胜率下限，杜绝盈利单回吐成亏损割肉；
5. 形态确立且 R:R≥2.0 时，自信给出 78%~88% 置信度果断开单！""",
        "evolution_system": """【全维度波段复盘风格】\n优先识别回撤、过度交易、追价和低质量入场，但只使用真实可观测证据。小样本、数理快照缺失或因果不可辨时 NO_CHANGE；任何记忆都不得成为绕过硬风控的新阈值。""",
        "evolution_user": """【全维度波段进化任务】\n评估信号一致性、风险预算、手续费、入场与退出质量；只有多个独立样本支持时才沉淀新经验，否则保留旧记忆并提出需要补充的证据。""",
    },
}

EMPTY_CUSTOM = {
    "id": "custom-default", "name": "自定义方案", "description": "用自然语言调整策略，硬风控始终由系统锁定。", "editable": True,
    "enabled": True, "created_at": "", "updated_at": "", "editor_mode": "simple",
    "simple_policy": {"strategy": "", "review_focus": "", "participation": "balanced", "evidence": "strict", "risk_budget": "middle"},
    "trading_system": "", "trading_user": "", "evolution_system": "", "evolution_user": "",
}

_FORBIDDEN = (
    (re.compile(r"(?is)(忽略|绕过|取消|覆盖).{0,24}(P0|硬风控|风险门禁|OCO|JSON|止损|保证金上限)"), "不得要求忽略或覆盖 P0 与执行层硬约束"),
    (re.compile(r"(?is)(允许|可以).{0,20}(逆势补仓|无止损|跳过OCO|突破持仓上限)"), "不得放宽逆势补仓、OCO、止损或持仓上限"),
    (re.compile(r"(?is)ignore.{0,30}(system|risk|safety|json|oco)"), "不得要求忽略系统、风险、安全或 JSON 契约"),
    (re.compile(r"(?i)(sk-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{16,}|api[_ -]?key\s*[:=]\s*\S+)"), "提示词中禁止写入 API Key 或密钥"),
)
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _now() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_path): os.unlink(temp_path)


def _default() -> dict[str, Any]:
    return {"version": 2, "active_profile_id": "stable", "profiles": {}, "revisions": []}


def _module(module: dict[str, Any], index: int = 0) -> dict[str, Any]:
    return {"id":str(module.get("id") or f"module-{uuid.uuid4().hex[:10]}")[:80],"title":str(module.get("title") or f"模块 {index+1}").strip()[:100],"content":str(module.get("content") or "").strip()[:MAX_TEMPLATE_CHARS],"enabled":bool(module.get("enabled",True)),"locked":bool(module.get("locked",False)),"source":str(module.get("source") or "custom")[:40]}


def text_to_modules(text: str, source: str = "legacy", locked: bool = False) -> list[dict[str, Any]]:
    chunks=[chunk.strip() for chunk in _SECTION_RE.split(str(text or "")) if chunk.strip()]
    if not chunks and str(text or "").strip(): chunks=[str(text).strip()]
    result=[]
    for i,chunk in enumerate(chunks):
        first=chunk.splitlines()[0].strip(" =") if chunk.splitlines() else f"模块 {i+1}"
        title=(re.search(r"【([^】]+)】",first).group(1) if re.search(r"【([^】]+)】",first) else first)[:100]
        result.append(_module({"title":title,"content":chunk,"locked":locked,"source":source},i))
    return result


def compile_modules(modules: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        str(item.get("content") or "")
        for item in modules
        if isinstance(item, dict) and item.get("enabled", True) and str(item.get("content") or "").strip()
    ).strip()


def base_template_modules(text: str, pipeline: str) -> list[dict[str, Any]]:
    modules = text_to_modules(text, "base", locked=False)
    for module in modules:
        module["locked"] = False
    return modules


def _clean_pipelines(raw: Any, legacy: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    source=raw if isinstance(raw,dict) else {}
    result={}
    for key in TEMPLATE_KEYS:
        modules=source.get(key) if isinstance(source.get(key),list) else text_to_modules(str(legacy.get(key) or ""),"legacy")
        result[key]=[_module(item,i) for i,item in enumerate(modules[:MAX_MODULES_PER_PIPELINE]) if isinstance(item,dict)]
    return result


def _clean_profile(raw: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    now = _now()
    result = copy.deepcopy(EMPTY_CUSTOM)
    result.update({key: raw.get(key, result.get(key)) for key in result})
    result["id"] = profile_id or str(raw.get("id") or f"custom-{uuid.uuid4().hex[:10]}")
    result["name"] = str(result.get("name") or "自定义方案").strip()[:60]
    result["description"] = str(result.get("description") or "").strip()[:240]
    result["editable"] = True
    result["enabled"] = bool(result.get("enabled", True))
    result["editor_mode"] = str(raw.get("editor_mode") or ("advanced" if any(raw.get(k) for k in TEMPLATE_KEYS) else "simple"))
    if result["editor_mode"] not in {"simple", "advanced", "modules"}:
        result["editor_mode"] = "modules" if isinstance(raw.get("pipelines"), dict) else "simple"
    policy = raw.get("simple_policy") if isinstance(raw.get("simple_policy"), dict) else {}
    result["simple_policy"] = {
        "strategy": str(policy.get("strategy") or "").strip()[:8000],
        "review_focus": str(policy.get("review_focus") or "").strip()[:4000],
        "participation": str(policy.get("participation") or "balanced"),
        "evidence": str(policy.get("evidence") or "strict"),
        "risk_budget": str(policy.get("risk_budget") or "middle"),
    }
    result["created_at"] = str(result.get("created_at") or now)
    result["updated_at"] = str(result.get("updated_at") or now)
    for key in TEMPLATE_KEYS:
        result[key] = str(result.get(key) or "").strip()
    if result["editor_mode"] == "simple" and not isinstance(raw.get("pipelines"), dict):
        result["pipelines"] = {}
    else:
        result["pipelines"] = _clean_pipelines(raw.get("pipelines"), result)
        if isinstance(raw.get("pipelines"), dict) or result["editor_mode"] == "modules":
            for key in TEMPLATE_KEYS:
                result[key] = compile_modules(result["pipelines"][key])
        result["editor_mode"] = "modules"
    return result


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    if int(raw.get("version", 1)) >= 2 and isinstance(raw.get("profiles"), dict):
        payload = _default(); payload.update(raw)
        payload["profiles"] = {str(k): _clean_profile(v, str(k)) for k, v in raw.get("profiles", {}).items() if isinstance(v, dict)}
        payload["revisions"] = list(raw.get("revisions", []))[-MAX_REVISIONS:]
        return payload
    custom = _clean_profile(raw.get("custom") or {}, "custom-default")
    active_style = str(raw.get("active_style") or "stable")
    return {"version": 2, "active_profile_id": active_style if active_style in PRESETS else custom["id"], "profiles": {custom["id"]: custom}, "revisions": []}


def load_library() -> dict[str, Any]:
    try:
        raw = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
        payload = _migrate(raw if isinstance(raw, dict) else {})
    except (OSError, json.JSONDecodeError, ValueError):
        payload = _default()
    active = str(payload.get("active_profile_id") or "stable")
    if active not in PRESETS and active not in payload["profiles"]:
        active = "stable"
    payload["active_profile_id"] = active
    # Backward compatibility for old API/tests.
    payload["active_style"] = active if active in PRESETS else "custom"
    payload["custom"] = copy.deepcopy(payload["profiles"].get(active) or payload["profiles"].get("custom-default") or EMPTY_CUSTOM)
    return payload


def save_library(payload: dict[str, Any]) -> None:
    # Accept the v1 shape used by older admin clients. If a caller changed only
    # active_style/custom while active_profile_id still equals the persisted value,
    # treat it as an intentional legacy update.
    legacy_update = "profiles" not in payload
    if not legacy_update and "active_style" in payload:
        try:
            persisted_raw = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
            persisted_active = _migrate(persisted_raw).get("active_profile_id", "stable")
        except (OSError, json.JSONDecodeError, ValueError):
            persisted_active = "stable"
        requested_style = str(payload.get("active_style") or "stable")
        mapped_active = requested_style if requested_style in PRESETS else "custom"
        current_mapped = payload.get("active_profile_id") if payload.get("active_profile_id") in PRESETS else "custom"
        legacy_update = payload.get("active_profile_id", "stable") == persisted_active and mapped_active != current_mapped
    if legacy_update:
        existing = load_library()
        legacy_custom = copy.deepcopy(payload.get("custom") or existing.get("custom") or {})
        if any(legacy_custom.get(key) for key in TEMPLATE_KEYS): legacy_custom["editor_mode"] = "advanced"
        custom = _clean_profile(legacy_custom, "custom-default")
        existing["profiles"][custom["id"]] = custom
        style = str(payload.get("active_style") or "stable")
        existing["active_profile_id"] = style if style in PRESETS else custom["id"]
        payload = existing
    normalized = _migrate(payload)
    normalized.pop("active_style", None); normalized.pop("custom", None)
    _atomic_write(LIBRARY_FILE, normalized)


def resolve_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Compile ordered module pipelines while preserving V2 simple-profile compatibility."""
    resolved = copy.deepcopy(profile)
    if isinstance(resolved.get("pipelines"),dict) and any(resolved["pipelines"].get(key) for key in TEMPLATE_KEYS):
        for key in TEMPLATE_KEYS: resolved[key]=compile_modules(resolved["pipelines"].get(key,[]))
        return resolved
    if resolved.get("editor_mode") != "simple": return resolved
    policy = resolved.get("simple_policy") or {}; strategy = str(policy.get("strategy") or "").strip(); review = str(policy.get("review_focus") or "").strip()
    participation = {"conservative":"稳健参与，证据不足时等待", "balanced":"均衡参与高质量机会", "active":"积极参与证据完整的趋势机会"}.get(policy.get("participation"), "均衡参与高质量机会")
    evidence = {"strict":"要求多周期、动力学、能量与概率风险严格共振", "balanced":"允许轻微证据分歧但必须可解释", "trend":"趋势与动力学优先，风险异常仍等待"}.get(policy.get("evidence"), "要求多周期、动力学、能量与概率风险严格共振")
    risk = {"low":"优先使用允许风险区间低位", "middle":"优先使用允许风险区间中位", "high":"强信号可使用允许风险区间高位但绝不突破硬上限"}.get(policy.get("risk_budget"), "优先使用允许风险区间中位")
    resolved["trading_system"] = f"【用户策略偏好·简单模式】\n{participation}；{evidence}；{risk}。\n{strategy}".strip()
    resolved["trading_user"] = ""
    resolved["evolution_system"] = f"【用户复盘关注·简单模式】\n围绕用户策略检查执行一致性，不得修改 P0、OCO、JSON 契约或风险硬门禁。\n{review or strategy}".strip()
    resolved["evolution_user"] = ""
    return resolved


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    name = str(profile.get("name") or "").strip()
    if not 1 <= len(name) <= 60:
        errors.append("方案名称长度必须为 1-60")
    pipelines = profile.get("pipelines") if isinstance(profile.get("pipelines"), dict) else {}
    module_total = 0
    for pipeline, modules in pipelines.items():
        if pipeline not in TEMPLATE_KEYS or not isinstance(modules, list):
            errors.append(f"无效消息管线：{pipeline}")
            continue
        if len(modules) > MAX_MODULES_PER_PIPELINE:
            errors.append(f"{pipeline} 模块数不得超过 {MAX_MODULES_PER_PIPELINE}")
        seen = set()
        for module in modules:
            if not isinstance(module, dict):
                errors.append(f"{pipeline} 包含无效模块对象")
                continue
            module_id = str(module.get("id") or "")
            if not module_id or module_id in seen:
                errors.append(f"{pipeline} 模块 ID 缺失或重复")
            seen.add(module_id)
            value = str(module.get("content") or "")
            module_total += len(value)
            if len(value) > MAX_TEMPLATE_CHARS:
                errors.append(f"{pipeline}/{module.get('title', '模块')} 超过 {MAX_TEMPLATE_CHARS} 字符")
            unknown = sorted(set(_VAR_RE.findall(value)) - ALLOWED_VARIABLES)
            if unknown:
                errors.append(f"{pipeline}/{module.get('title', '模块')} 包含未知变量：{', '.join(unknown)}")
            if module.get("source") == "base" and module.get("locked"):
                continue
            for pattern, message in _FORBIDDEN:
                for match in pattern.finditer(value):
                    prefix = value[max(0, match.start() - 12):match.start()]
                    if re.search(r"(不得|严禁|禁止|不可).{0,10}$", prefix):
                        continue
                    errors.append(f"{pipeline}/{module.get('title', '模块')}：{message}")
                    break
    policy = profile.get("simple_policy") if isinstance(profile.get("simple_policy"), dict) else {}
    if profile.get("editor_mode") == "simple":
        strategy = str(policy.get("strategy") or "")
        review = str(policy.get("review_focus") or "")
        if not strategy.strip():
            warnings.append("简单策略说明为空，将仅使用选择项和系统基础提示词")
        for label, value in (("策略说明", strategy), ("复盘重点", review)):
            unknown = sorted(set(_VAR_RE.findall(value)) - ALLOWED_VARIABLES)
            if unknown:
                errors.append(f"{label} 包含未知变量：{', '.join(unknown)}")
            for pattern, message in _FORBIDDEN:
                if pattern.search(value):
                    errors.append(f"{label}：{message}")
        if policy.get("participation", "balanced") not in {"conservative", "balanced", "active"}:
            errors.append("参与风格无效")
        if policy.get("evidence", "strict") not in {"strict", "balanced", "trend"}:
            errors.append("证据要求无效")
        if policy.get("risk_budget", "middle") not in {"low", "middle", "high"}:
            errors.append("风险预算无效")
    total = 0
    for key in TEMPLATE_KEYS:
        value = "" if pipelines else str(profile.get(key) or "")
        total += len(value)
        if len(value) > MAX_TEMPLATE_CHARS:
            errors.append(f"{key} 超过 {MAX_TEMPLATE_CHARS} 字符")
        unknown = sorted(set(_VAR_RE.findall(value)) - ALLOWED_VARIABLES)
        if unknown:
            errors.append(f"{key} 包含未知变量：{', '.join(unknown)}")
        for pattern, message in _FORBIDDEN:
            for match in pattern.finditer(value):
                prefix = value[max(0, match.start() - 12):match.start()]
                if re.search(r"(不得|严禁|禁止|不可).{0,10}$", prefix):
                    continue
                errors.append(f"{key}：{message}")
                break
    total_chars = module_total if pipelines else total
    if total_chars > MAX_PROFILE_CHARS:
        errors.append(f"四类模板合计不得超过 {MAX_PROFILE_CHARS} 字符")
    if not total and not pipelines and profile.get("editor_mode") != "simple":
        warnings.append("当前方案四类附加模板均为空，将只使用基础提示词")
    return {"valid": not errors, "errors": list(dict.fromkeys(errors)), "warnings": warnings, "characters": total_chars}


def _revision(profile: dict[str, Any], action: str, note: str = "") -> dict[str, Any]:
    return {"id": f"rev-{uuid.uuid4().hex[:12]}", "profile_id": profile["id"], "action": action, "note": str(note)[:240], "created_at": _now(), "snapshot": copy.deepcopy(profile)}


def create_profile(name: str, description: str = "", source_id: str = "stable", note: str = "创建方案") -> dict[str, Any]:
    library = load_library()
    source = get_profile(source_id)
    profile_data = copy.deepcopy(source)
    profile_data.update({
        "id": f"custom-{uuid.uuid4().hex[:10]}",
        "name": name,
        "description": description,
        "created_at": _now(),
        "updated_at": _now(),
    })
    profile = _clean_profile(profile_data)
    check = validate_profile(profile)
    if not check["valid"]:
        raise ValueError("；".join(check["errors"]))
    library["profiles"][profile["id"]] = profile
    library["revisions"].append(_revision(profile, "create", note))
    library["revisions"] = library["revisions"][-MAX_REVISIONS:]
    save_library(library)
    return profile


def update_profile(profile_id: str, changes: dict[str, Any], note: str = "更新方案") -> dict[str, Any]:
    library = load_library()
    if profile_id in PRESETS and profile_id not in library["profiles"]:
        current = copy.deepcopy(PRESETS[profile_id])
        current["editable"] = True
    elif profile_id in library["profiles"]:
        current = library["profiles"][profile_id]
    else:
        raise ValueError("提示词方案不存在")
    accepted = {k: v for k, v in changes.items() if k in {"name", "description", "enabled", "editor_mode", "simple_policy", "pipelines", *TEMPLATE_KEYS}}
    flat_updates = [key for key in TEMPLATE_KEYS if key in accepted]
    if "pipelines" not in accepted and flat_updates:
        accepted["pipelines"] = copy.deepcopy(current.get("pipelines") or {})
        for key in flat_updates: accepted["pipelines"][key] = text_to_modules(str(accepted[key] or ""), "legacy")
        accepted["editor_mode"] = "modules"
    elif "editor_mode" not in accepted and any(str(accepted.get(key) or "").strip() for key in TEMPLATE_KEYS): accepted["editor_mode"] = "advanced"
    updated = _clean_profile({**current, **accepted, "updated_at": _now()}, profile_id)
    check = validate_profile(updated)
    if not check["valid"]: raise ValueError("；".join(check["errors"]))
    library["profiles"][profile_id] = updated
    library["revisions"].append(_revision(updated, "update", note))
    library["revisions"] = library["revisions"][-MAX_REVISIONS:]
    save_library(library)
    return updated


def delete_profile(profile_id: str) -> None:
    if profile_id in PRESETS:
        raise ValueError("内置预设不可删除")
    library = load_library()
    if profile_id == library["active_profile_id"]:
        raise ValueError("当前启用方案不能删除，请先切换方案")
    if profile_id in library["profiles"]:
        del library["profiles"][profile_id]
        save_library(library)
    else:
        raise ValueError("提示词方案不存在")


def activate_profile(profile_id: str) -> dict[str, Any]:
    library = load_library()
    profile = get_profile(profile_id)
    if not profile.get("enabled", True): raise ValueError("该方案已停用")
    library["active_profile_id"] = profile_id
    save_library(library)
    return profile


def get_profile(profile_id: str) -> dict[str, Any]:
    library = load_library()
    if profile_id in library["profiles"]:
        return copy.deepcopy(library["profiles"][profile_id])
    if profile_id in PRESETS:
        preset = _clean_profile(copy.deepcopy(PRESETS[profile_id]), profile_id)
        preset["editable"] = True
        return preset
    raise ValueError("提示词方案不存在")


def profile_history(profile_id: str) -> list[dict[str, Any]]:
    return [copy.deepcopy(x) for x in reversed(load_library()["revisions"]) if x.get("profile_id") == profile_id]


def rollback_profile(profile_id: str, revision_id: str) -> dict[str, Any]:
    library = load_library()
    revision = next((x for x in library["revisions"] if x.get("id") == revision_id and x.get("profile_id") == profile_id), None)
    if not revision:
        raise ValueError("历史版本不存在")
    restored = _clean_profile({**revision["snapshot"], "updated_at": _now()}, profile_id)
    check = validate_profile(restored)
    if not check["valid"]:
        raise ValueError("；".join(check["errors"]))
    library["profiles"][profile_id] = restored
    library["revisions"].append(_revision(restored, "rollback", f"回滚到 {revision_id}"))
    library["revisions"] = library["revisions"][-MAX_REVISIONS:]
    save_library(library)
    return restored


def export_profile(profile_id: str) -> dict[str, Any]:
    """Self-describing profile export: one JSON file is enough to restore an
    equivalent profile on any system of the same version, including the list of
    template variables the file is allowed to reference."""
    profile = get_profile(profile_id)
    payload = {k: profile.get(k) for k in ("name", "description", "editor_mode", "pipelines", "simple_policy", *TEMPLATE_KEYS)}
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": _now(),
        "profile_id": profile_id,
        "profile": payload,
        "variables": copy.deepcopy(TEMPLATE_VARIABLES_METADATA),
        "allowed_variables": sorted(ALLOWED_VARIABLES),
    }


def _normalize_import_source(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Accept every shape users actually hold on disk and return (source, origin).

    A) standard wrapper ``{"format": "r20-prompt-profile", "profile": {...}}`` (v1..v4)
    B) whole-library export ``{"version": 2, "active_profile_id": ..., "profiles": {...}}``
    C) bare profile object (carries ``pipelines`` or any flat template key)
    """
    if not isinstance(payload, dict) or not payload:
        raise ValueError(_IMPORT_FORMAT_HINT)
    if payload.get("format") == EXPORT_FORMAT and isinstance(payload.get("profile"), dict):
        return payload["profile"], "wrapped"
    if isinstance(payload.get("profiles"), dict) and payload["profiles"]:
        profiles = payload["profiles"]
        active_id = str(payload.get("active_profile_id") or payload.get("active_style") or "")
        source = profiles.get(active_id)
        if not isinstance(source, dict):
            source = next((v for v in profiles.values() if isinstance(v, dict)), None)
        if not isinstance(source, dict):
            raise ValueError(_IMPORT_FORMAT_HINT)
        return source, "library"
    if "format" not in payload and ("pipelines" in payload or any(key in payload for key in TEMPLATE_KEYS)):
        return payload, "bare"
    raise ValueError(_IMPORT_FORMAT_HINT)


def _unknown_variable_errors(errors: list[str]) -> list[str]:
    return [item for item in errors if "未知变量" in item]


def import_profile(payload: dict[str, Any], name_override: str = "") -> dict[str, Any]:
    source, origin = _normalize_import_source(payload)
    library = load_library()
    profile_id = f"custom-{uuid.uuid4().hex[:10]}"
    profile_data = copy.deepcopy(source)
    profile_data["id"] = profile_id
    original_name = str(profile_data.get("name") or "").strip()
    if name_override:
        profile_data["name"] = name_override
    elif original_name:
        profile_data["name"] = f"{original_name}（导入）"[:60]
    else:
        profile_data["name"] = "导入方案"
    profile_data["created_at"] = _now()
    profile_data["updated_at"] = _now()
    profile = _clean_profile(profile_data, profile_id)
    check = validate_profile(profile)
    if not check["valid"]:
        unknown = _unknown_variable_errors(check["errors"])
        if unknown:
            raise ValueError("导入文件包含未知变量，请对照导出文件中的 allowed_variables 清单修正：\n- " + "\n- ".join(unknown))
        raise ValueError("；".join(check["errors"]))
    library["profiles"][profile["id"]] = profile
    origin_label = {"wrapped": "标准导出包", "library": "整库导出文件", "bare": "裸方案对象"}.get(origin, "未知来源")
    library["revisions"].append(_revision(profile, "import", f"导入方案（{origin_label}）"))
    library["revisions"] = library["revisions"][-MAX_REVISIONS:]
    save_library(library)
    return profile


def _import_with_templates(source: dict[str, Any], name_override: str) -> dict[str, Any]:
    return import_profile({"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "profile": source}, name_override)


def active_profile() -> dict[str, Any]:
    return resolve_profile(get_profile(load_library()["active_profile_id"]))


# Backward-compatible aliases for policy snapshot & external modules
load_active_profile = active_profile
load_prompt_config = load_library
save_prompt_config = save_library


def all_profiles() -> list[dict[str, Any]]:
    library = load_library()
    profiles_map = copy.deepcopy(library["profiles"])
    result = []
    for pid in ("stable",):
        if pid in profiles_map:
            result.append(profiles_map.pop(pid))
        elif pid in PRESETS:
            preset = _clean_profile(copy.deepcopy(PRESETS[pid]), pid)
            preset["editable"] = True
            result.append(preset)
    result.extend(profiles_map.values())
    return result


def render_variables(text: str, context: dict[str, Any] | None = None) -> str:
    """Pure, single-pass substitution; never fetch account, news or memory data.

    Missing context means template preview. An explicit mapping means final
    rendering; unavailable inputs are markers, not evidence of an empty account.
    Replacement values are opaque and are never recursively interpreted.
    """
    if context is None:
        return text or ""
    values = {"timezone": "Asia/Shanghai", **context}

    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in ALLOWED_VARIABLES:
            return f"[UNKNOWN_VARIABLE:{key}]"
        if key not in values or values[key] is None:
            return f"[MISSING_CONTEXT:{key}]"
        return str(values[key])

    return _VAR_RE.sub(replace, text or "")


def _trading_user_parent_groups(base_modules: list[dict[str, Any]], layout_titles: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Associate nested live-value modules with the preceding editor-visible section."""
    groups: dict[str, list[dict[str, Any]]] = {}
    current_parent = ""
    for module in base_modules:
        title = module["title"]
        if title in layout_titles:
            current_parent = title
            groups.setdefault(title, []).append(module)
        elif current_parent:
            groups[current_parent].append(module)
    return groups


def apply_module_layout(base: str, profile: dict[str, Any], pipeline: str, label: str, context: dict[str, Any] | None = None) -> str:
    """Build a real message from base sections plus ordered profile modules.

    Modules whose source is ``base`` reference the live base section by id; custom
    and legacy modules carry editable content. Locked base modules stay visible.
    Trading-user runtime sections may contain many nested ``【...】`` labels. Those
    live values are merged back into their parent editor slot instead of being
    appended out of order or replaced by static placeholder text.
    Supports variable placeholders such as {{news_intelligence}}, {{trading_memory}},
    {{market_matrix}}, {{account_positions}}, etc.
    """
    base_modules = base_template_modules(base, pipeline)
    layout = ((profile.get("pipelines") or {}).get(pipeline) if isinstance(profile.get("pipelines"), dict) else None)
    if not isinstance(layout, list) or not any(item.get("source") == "base" for item in layout):
        custom = layout if isinstance(layout, list) else text_to_modules(str(profile.get(pipeline) or ""), "custom")
        return render_variables(compile_modules(base_modules + custom), context)

    if pipeline == "trading_system":
        # System pipeline: the profile layout is the authoritative single source of truth.
        # User configurations must never be silently overridden by hardcoded base strings or zombie modules.
        active_items = [
            item for item in layout
            if isinstance(item, dict) and item.get("enabled", True) and str(item.get("content") or "").strip()
        ]
        if not active_items:
            active_items = base_modules
        return render_variables(compile_modules(active_items), context)

    base_by_title = {item["title"]: item for item in base_modules}
    layout_titles = {str(item.get("title") or "") for item in layout if item.get("source") == "base"}
    runtime_groups = _trading_user_parent_groups(base_modules, layout_titles) if pipeline == "trading_user" else {}
    output: list[dict[str, Any]] = []
    matched: set[str] = set()

    for item in layout:
        if item.get("source") != "base":
            if item.get("enabled", True):
                content = str(item.get("content") or "")
                output.append({**item, "content": content})
            continue

        title = str(item.get("title") or "")
        live = base_by_title.get(title)
        if not live:
            continue

        group = runtime_groups.get(title, [live])
        matched.update(module["title"] for module in group)
        if not item.get("enabled", True):
            continue

        if pipeline == "trading_user":
            raw_content = str(item.get("content") or "")
            if _VAR_RE.search(raw_content):
                content = raw_content
            else:
                content = compile_modules(group)
        else:
            content = str(item.get("content") if item.get("content") is not None else live["content"])
        output.append({**live, "content": content})

    # Fail closed for user pipeline: preserve live sections that an older layout does not know.
    output.extend(module for module in base_modules if module["title"] not in matched)
    return render_variables(compile_modules(output), context)


def pipeline_view(base: str, profile: dict[str, Any], pipeline: str) -> list[dict[str, Any]]:
    base_modules = base_template_modules(base, pipeline)
    current = ((profile.get("pipelines") or {}).get(pipeline) if isinstance(profile.get("pipelines"), dict) else [])
    if isinstance(current, list) and current:
        view = copy.deepcopy(current)
        for m in view:
            m["locked"] = False
        return view
    return base_modules + (text_to_modules(str(profile.get(pipeline) or ""), "custom") if profile.get(pipeline) else [])


def append_layer(base: str, layer: str, label: str) -> str:
    layer = (layer or "").strip()
    return base if not layer else f"{base.rstrip()}\n\n======================= 【{label}】 =======================\n{layer}"

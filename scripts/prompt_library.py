"""Versioned prompt profile library used directly by Python trading processes.

## 双文件模型（2026-09 改造，起因是一次真实的数据丢失）

方案库分两个文件，**职责不重叠**：

| 文件 | 角色 | git | 谁写 |
|---|---|---|---|
| `BASELINE_FILE` `data/prompt_library.json` | 出厂基线 | **跟踪**（随发版更新） | 没人（运行期只读） |
| `LOCAL_FILE` `data/prompt_library.local.json` | 用户改动 | 忽略（`data/*.json`） | 本模块（唯一写入目标） |

`load_library()` = 基线 ⊕ 本地（**本地优先**）；`save_library()` 只写本地，
且**只写真差异**（与出厂逐字相同的预设不落本地，好让它继续跟随发版更新）。

### 为什么不这么改不行

改造前只有一个文件，而它**被 git 跟踪**。于是用户一改提示词，工作区就脏了，
而后台「更新」有一道「工作区存在未提交修改 ⇒ 409 拒绝更新」的闸
（`r20_backend/routers/system.py`）—— 用户为了更新只能丢弃改动，丢的正是自己的
提示词；`git pull` 再把仓库版本盖回来。表现出来就是用户报的那句
「更新后预设提示词覆盖了用户的预设提示词」。

**推论（别再把运行态文件加回 git）**：任何"应用会写 + git 跟踪"的文件都会
复现同一事故。`data/policy_archives/*` 同批停止跟踪，理由相同。
"""
from __future__ import annotations
import copy
import functools
import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 结构优化阶段 4·B3 第五十三刀：模板编译簇外提到 `scripts/prompt_templates.py`。
# ⚠️ 双模导入：本模块既可能以裸名 `prompt_library` 导入（`scripts/` 在
# sys.path），也可能以 `scripts.prompt_library` 导入（repo 根在 sys.path）——
# 后一种情况下裸名 `prompt_templates` **不在** sys.path。
# （第四十八刀在 local_lock 上踩过同一个坑，被 dashboard 用例当场抓住。）
try:  # repo 根在 sys.path
    from scripts.prompt_templates import (
        stable_base_module_id as _tpl_stable_base_module_id,
        _module as _tpl_module,
        text_to_modules as _tpl_text_to_modules,
        compile_modules as _tpl_compile_modules,
        base_template_modules as _tpl_base_template_modules,
        base_template_text as _tpl_base_template_text,
        align_pipeline_sources as _tpl_align_pipeline_sources,
        _inherit_module_tags as _tpl__inherit_module_tags,
        pipeline_view as _tpl_pipeline_view,
        append_layer as _tpl_append_layer,
    )
except ImportError:  # scripts/ 在 sys.path
    from prompt_templates import (
        stable_base_module_id as _tpl_stable_base_module_id,
        _module as _tpl_module,
        text_to_modules as _tpl_text_to_modules,
        compile_modules as _tpl_compile_modules,
        base_template_modules as _tpl_base_template_modules,
        base_template_text as _tpl_base_template_text,
        align_pipeline_sources as _tpl_align_pipeline_sources,
        _inherit_module_tags as _tpl__inherit_module_tags,
        pipeline_view as _tpl_pipeline_view,
        append_layer as _tpl_append_layer,
    )
# 结构优化阶段 4·B3 第四十八刀：本地锁兜底外提到 `scripts/local_lock.py`。
# ⚠️ 双模导入：本模块既可能以裸名 `prompt_library` 导入（`scripts/` 在 sys.path），
# 也可能以 `scripts.prompt_library` 导入（repo 根在 sys.path）——
# 后一种情况下裸名 `local_lock` **不在** sys.path，直接 import 会
# `ModuleNotFoundError`（实测被 tests/test_dashboard_bills_extraction 抓到）。
try:  # repo 根在 sys.path
    from scripts.local_lock import local_file_lock  # noqa: E402
except ImportError:  # scripts/ 在 sys.path
    from local_lock import local_file_lock  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
#: 出厂基线：随发版更新，**运行时只读**（应用从不写它）。
BASELINE_FILE = ROOT / "data" / "prompt_library.json"
#: 用户改动：应用**唯一的写入目标**，不纳入 git（`.gitignore` 的 `data/*.json` 已覆盖）。
#:
#: 为什么要分成两个文件（2026-09 实测事故）：单文件时代这个数据文件**被 git 跟踪**，
#: 于是用户一在后台改提示词就把工作区弄脏，而后台「更新」有一道
#: 「工作区存在未提交修改 ⇒ 409 拒绝更新」的闸（`r20_backend/routers/system.py`）。
#: 用户为了更新只能丢弃改动 —— 丢弃的正是自己的提示词；`git pull` 再把仓库版本盖回来。
#: 表现出来就是「更新后预设提示词覆盖了用户的预设提示词」。
#:
#: 分成两文件后：基线继续由 git 交付（新部署/新克隆拿得到真正的出厂预设，
#: 而不是代码里那份**残缺兜底** —— 实测 `evolution_system` 兜底只有 92 字符，
#: 而基线里有 1049），用户改动落在不跟踪文件里，**永远不会弄脏工作区**，
#: 也永远不会被 `git pull` 覆盖。
LOCAL_FILE = ROOT / "data" / "prompt_library.local.json"
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
        "sample": "# AstraQuant AI 交易大脑长期记忆与启发式心法\n1. [2026-09-04] 4H主升浪中回调即是做多机会，严禁盲目摸顶开空...",
    },
    {
        "key": "market_regime",
        "label": "全市场宏观体制",
        "category": "行情数据",
        "description": "注入全市场宏观体制自适应识别结果（单边趋势/宽幅震荡/窄幅低波/极端冲击、趋势强度、波动与震荡指数、操盘指导建议）",
        "sample": "【市场体制自适应识别】: 当前全市场宏观体制为【宽幅上下震荡】(高波动箱体 · 逆势防扫)。\n- 核心量化指标: 趋势强度=38.5/100 | 波动指数=76.2/100 | 震荡指数=82.0/100 | 主导方向=NEUTRAL\n- 操盘指导建议: 处于宽幅上下震荡箱体，建议箱体边界高抛低吸，拉宽止损至 2.0x ATR 防插针扫损，浮盈达 1.5R 及时保本或锁利。",
    },
    {
        "key": "market_matrix",
        "label": "标的行情数理矩阵",
        "category": "行情数据",
        "description": "注入标的池全部币种K线、现价、盘口买卖价、聪明钱流向、1H三大数理基石硬证据(v/a/j/I/E/A/VaR)",
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
        "key": "risk_budget",
        "label": "自适应风险预算",
        "category": "账户敞口",
        "description": "按当前实际可用余额自动推导的单笔保证金区间、单标的累计上限与当日亏损熔断线；小资金账户(如 80U)与大资金账户自动适配，彻底替代提示词中任何写死的绝对金额",
        "sample": "【本周期风险预算（示意样本，实际以运行期小节为准）｜按实际可用余额 80.00 USDT 自适应推导，严禁套用任何固定绝对金额】:\n- 常规单笔保证金: 2.4 ~ 9.6 USDT (可用余额 3%~12%)\n- 强信号单笔保证金上限: 16.0 USDT (20%)\n- 单标的累计保证金上限(含金字塔加仓): 24.0 USDT (30%)",
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
        "description": "当前 AstraQuant 交易引擎版本",
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
        "sample": "# AstraQuant AI 交易大脑长期记忆与启发式心法\n1. [2026-09-05] 4H 顺势回踩优先做多...",
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

PRESETS: dict[str, dict[str, Any]] = {
    "stable": {
        "id": "stable", "name": "全维度波段强化版", "description": "基于 1H~4H 宏观多空趋势、因果微积分、定积分能量、概率论高权重定价与智能加仓的量化决策方案（系统唯一主策略）。", "editable": True,
        "editor_mode": "modules",
        "pipelines": {
            "trading_system": [
                {"id": "custom-ts-style", "title": "全维度波段强化交易风格", "locked": False, "enabled": True, "source": "custom",
                 "content": "【交易风格：全维度波段强化（概率论权重提升·强开单·高胜率·高盈亏比·波段奔跑）】\n所有 P0 硬约束保持不变，不得把“稳健”解释为长期空仓。核心裁决由【概率论与期望值】优先定性：当条件延续/击穿概率具有优势且 R:R 达执行层底线时果断进场！杜绝频繁随意割肉与微利早跑：止损给足 1.8~2.2x ATR 彻底隔绝杂波插针扫损；三阶利润棘轮给足波段展开空间，浮盈达 1.5R 稳固波段才启动保本移损锁死胜率，杜绝浮盈变亏损；峰值回撤与动能耗散避免在正常微幅回踩中恐慌 CLOSE_MARKET，坚决让主升浪波段充分奔跑以兑现高盈亏比。多空对称顺势，形态契合时自信评定 78%~88% 积极开单进场！"},
            ],
            "trading_user": [
                {"id": "base-ts-time", "title": "当前决策时间戳与市场时效", "locked": True, "enabled": True, "source": "base",
                 # 审计 P1-1(2026-09-13)：代码预设此前漏了 {{risk_budget}}（线上库里有），
                 # 一旦 load_library 回退到预设，模型就完全收不到【本周期风险预算】小节，
                 # 而 SYSTEM PROMPT 却要求"一切金额类参数以该小节为准"——金额口径直接失锚。
                 "content": "======================= 【当前决策时间戳与市场时效】 =======================\n{{decision_timestamp}}\n{{account_balance}}\n{{risk_budget}}"},
                {"id": "base-ts-news", "title": "全网实时重大快讯与宏观情报", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【全网实时重大快讯与宏观情报】 =======================\n{{news_intelligence}}"},
                {"id": "base-ts-pos", "title": "账户当前持仓与风险敞口全景", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【账户当前持仓与风险敞口全景】 =======================\n{{account_positions}}"},
                {"id": "base-ts-pending", "title": "在途未成交限价挂单 (Pending Maker Orders)", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【在途未成交限价挂单 (Pending Maker Orders)】 =======================\n{{pending_orders}}"},
                {"id": "base-ts-memory", "title": "AstraQuant 启发式实战认知与长期记忆", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【AstraQuant 启发式实战认知与长期记忆】 =======================\n{{trading_memory}}"},
                {"id": "base-ts-matrix", "title": "全标的池原生行情、技术指标与筹码矩阵", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【全标的池原生行情、技术指标与筹码矩阵】 =======================\n{{market_matrix}}"},
                {"id": "base-ts-task", "title": "推演与决策任务", "locked": False, "enabled": True, "source": "base", "content": ""},
                {"id": "custom-tu-style", "title": "全维度波段强化裁决偏好（概率论高权重·多空对称·高胜率·防割肉体系）", "locked": False, "enabled": True, "source": "custom",
                 "content": "【全维度波段强化裁决偏好（概率论高权重·多空对称·高盈亏比·波段奔跑体系）】\n1. 概率期望优先：P续/P破 差值 ≥ 15% 即方向定论，只做概率优势一侧的回踩/承压入场，逆势一侧一律放弃；\n2. 强开单欲望：拒绝机械空仓。4H/1H 顺势多头找回踩低吸挂多，顺势空头找反弹承压挂空，箱体边界双向高抛低吸，半山腰坚决 WAIT；\n3. 盈亏比与防割肉平衡：止损一律给足 1.8~2.2x 1H ATR 呼吸空间；浮盈达 1.5R 稳固波段才执行 UPDATE_SL 移损保本，杜绝微利被 15M 杂波过早扫出；走势未破坏前坚决 HOLD，让大波段主升浪充分奔跑，严禁在正常微幅回调中恐慌砸盘；\n4. 敞口自律防踩踏：同向持仓达到 2 笔时自律收紧开仓门禁，杜绝在强相关币种上无节制同向堆叠单边敞口；\n5. 置信度标定：形态达标且空间充足果断给出 78~88，确保通过执行层门禁进场；仅当全部候选触发硬否决或优势不足时才全体 WAIT。"},
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
                {"id": "base-eu-ledger-hdr", "title": "AstraQuant 加密量化实盘战绩与历史交易台账", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【AstraQuant 加密量化实盘战绩与历史交易台账】 ======================="},
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
        "trading_system": """【交易风格：全维度波段强化（概率论权重提升·强开单·高胜率·高盈亏比·波段奔跑）】\n所有 P0 硬约束保持不变，不得把“稳健”解释为长期空仓。核心裁决由【概率论与期望值】优先定性：当条件延续/击穿概率具有优势且 R:R≥2.0 时果断进场！杜绝频繁随意割肉与微利早跑：止损给足 1.8~2.2x ATR 彻底隔绝杂波插针扫损；三阶利润棘轮给足波段展开空间，浮盈达 1.5R 稳固波段才启动保本移损锁死胜率，杜绝浮盈变亏损；峰值回撤与动能耗散避免在正常微幅回踩中恐慌 CLOSE_MARKET，坚决让主升浪波段充分奔跑以兑现高盈亏比。多空对称顺势，形态契合时自信评定 78%~88% 积极开单进场！""",
        "trading_user": """【全维度波段强化裁决偏好（概率论高权重·多空对称·高盈亏比·波段奔跑体系）】
1. 概率论最高权重决策：优先根据条件延续概率 P续 与击穿概率 P破 的数学期望定价；P续 占优专注做多回踩，P破 占优专注承压做空；
2. 强烈开单欲望：拒绝机械空仓观望，普通回抽优先作为限价入场定位，4H/1H 顺势多头找回踩低吸挂多，4H/1H 顺势空头找反弹承压挂空，箱体震荡边界双向高抛低吸；
3. 彻底拒绝随意割肉：止损距离外扩给足 1.8x~2.2x 1H ATR 呼吸空间，隔绝 15M/5M 噪音假动作；
4. 科学波段锁利：浮盈达 1.5R 稳固波段主动输出 UPDATE_SL 移至保本位，未破坏前坚决 HOLD 让主浪充分奔跑，杜绝微利恐慌早跑；
5. 严守同向敞口：同向已有 2 笔时审慎克制，防范单边系统性踩踏；
6. 形态确立且 R:R≥2.0 时，自信给出 78%~88% 置信度果断开单！""",
        "evolution_system": """【全维度波段复盘风格】\n优先识别回撤、过度交易、追价和低质量入场，但只使用真实可观测证据。小样本、数理快照缺失或因果不可辨时 NO_CHANGE；任何记忆都不得成为绕过硬风控的新阈值。""",
        "evolution_user": """【全维度波段进化任务】\n评估信号一致性、风险预算、手续费、入场与退出质量；只有多个独立样本支持时才沉淀新经验，否则保留旧记忆并提出需要补充的证据。""",
    },
    "wide_oscillation": {
        "id": "wide_oscillation", "name": "宽幅震荡箱体收割版", "description": "专为宽幅上下震荡与高波动无序箱体量身定制：箱体边际高抛低吸、拉宽止损隔绝假突破与插针扫损、严禁半山腰追价、浮盈1.5R稳固即保本锁定。", "editable": True,
        "editor_mode": "modules",
        "pipelines": {
            "trading_system": [
                {"id": "custom-ts-wide-style", "title": "宽幅震荡箱体收割风格", "locked": False, "enabled": True, "source": "custom",
                 "content": "【交易风格：宽幅震荡箱体收割（高波动箱体·边际高抛低吸·2.2x宽止损防插针·1.5R快锁保本）】\n所有 P0 硬约束保持不变。宽幅震荡行情核心在于箱体边际定价与防插针保护：严禁在箱体正中间（半山腰）追涨杀跌！开仓仅限于 1H/4H 箱体上轨阻力位承压做空，或箱体下轨支撑位企稳做多；止损给足 2.0~2.5x 1H ATR 宽阔空间并置于近期箱体摆动极值外，彻底杜绝日内假突破影线插针频繁扫损；箱体行情主浪空间受限，浮盈达 1.5R 稳固波段必须果断输出 UPDATE_SL 移至开仓成本保本位，锁死胜率，触及箱体对边或动能衰竭时主动锁利，杜绝浮盈变亏损；多空双向平权，形态触及箱体边际且盈亏比合规时，自信评定 78%~86% 果断挂单！"},
            ],
            "trading_user": [
                {"id": "base-ts-time", "title": "当前决策时间戳与市场时效", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【当前决策时间戳与市场时效】 =======================\n{{decision_timestamp}}\n{{account_balance}}\n{{risk_budget}}"},
                {"id": "base-ts-regime", "title": "全市场宏观体制自适应识别", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【全市场宏观体制自适应识别】 =======================\n{{market_regime}}"},
                {"id": "base-ts-news", "title": "全网实时重大快讯与宏观情报", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【全网实时重大快讯与宏观情报】 =======================\n{{news_intelligence}}"},
                {"id": "base-ts-pos", "title": "账户当前持仓与风险敞口全景", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【账户当前持仓与风险敞口全景】 =======================\n{{account_positions}}"},
                {"id": "base-ts-pending", "title": "在途未成交限价挂单 (Pending Maker Orders)", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【在途未成交限价挂单 (Pending Maker Orders)】 =======================\n{{pending_orders}}"},
                {"id": "base-ts-memory", "title": "AstraQuant 启发式实战认知与长期记忆", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【AstraQuant 启发式实战认知与长期记忆】 =======================\n{{trading_memory}}"},
                {"id": "base-ts-matrix", "title": "全标的池原生行情、技术指标与筹码矩阵", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【全标的池原生行情、技术指标与筹码矩阵】 =======================\n{{market_matrix}}"},
                {"id": "base-ts-task", "title": "推演与决策任务", "locked": False, "enabled": True, "source": "base", "content": ""},
                {"id": "custom-tu-wide-style", "title": "宽幅震荡箱体裁决偏好（箱体边界定位·动能耗散逆转·拉宽呼吸·快锁胜率）", "locked": False, "enabled": True, "source": "custom",
                 "content": "【宽幅震荡箱体裁决偏好（箱体边界定位·动能耗散逆转·拉宽呼吸·快锁胜率）】\n1. 箱体边缘入场原则：坚决拒绝在震荡区间正中央追多或追空。唯有当价格触及 4H/1H 箱体上轨承压（v 减速且 a < 0）挂限价做空，触及箱体下轨企稳（v 企稳且 a > 0）挂限价做多；\n2. 防插针宽止损体系：止损距离必须给足 2.0~2.5x 1H ATR，严密挂在近期箱体摆动极值点外侧，绝不在正常箱体震荡回抽中惊慌割肉；\n3. 阶梯式锁利防坐过山车：浮盈达 1.5R 稳固波段立即输出 UPDATE_SL 提损保本；价格接近箱体反向阻力位/支撑位时果断分批止盈退出，拒绝利润回吐；\n4. 动能反转微积分硬验证：重点参考一阶速度减速与加速度符号变向（如冲顶出现 v > 0 但 a < -0.10，探底出现 v < 0 但 a > 0.10），确认箱体动力学反转确立；\n5. 敞口自律防假突破：全账户同向在管持仓限制在 2 笔以内，防范假突破演变为单边异动踩踏；\n6. 边际形态达标且盈亏比满足要求时，自信给出 78%~86% 置信度果断开单！"},
            ],
            "evolution_system": [
                {"id": "custom-es-wide-style", "title": "宽幅震荡复盘风格", "locked": False, "enabled": True, "source": "custom",
                 "content": "【宽幅震荡复盘风格】\n重点排查半山腰盲目追价、止损空间过窄导致的假动作插针扫损、以及盈利后未及时移损导致的利润回吐。小样本、数理快照缺失或因果不可辨时 NO_CHANGE；任何记忆都不得成为绕过硬风控的新阈值。"},
            ],
            "evolution_user": [
                {"id": "base-eu-time-hdr", "title": "当前认知复盘基准时间", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【当前认知复盘基准时间】 ======================="},
                {"id": "base-eu-time", "title": "复盘基准时间", "locked": False, "enabled": True, "source": "base",
                 "content": "【复盘基准时间】: {{timestamp_beijing}}"},
                {"id": "base-eu-mem", "title": "当前系统已有的历史长期记忆库", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【当前系统已有的历史长期记忆库】 =======================\n{{existing_memory_markdown}}"},
                {"id": "base-eu-ledger-hdr", "title": "AstraQuant 加密量化实盘战绩与历史交易台账", "locked": True, "enabled": True, "source": "base",
                 "content": "======================= 【AstraQuant 加密量化实盘战绩与历史交易台账】 ======================="},
                {"id": "base-eu-stats", "title": "统计汇总", "locked": False, "enabled": True, "source": "base",
                 "content": "【统计汇总】:\n- 总平仓笔数: {{total}} 笔（胜 {{wins}} / 负 {{losses}} | 胜率 {{win_rate}}%）\n- 累计净盈亏: {{total_net}} USDT | 累计手续费: {{total_fees}} USDT\n- 当前聚焦标的池: {{target_instruments}}"},
                {"id": "base-eu-trades", "title": "逐笔历史交易明细 (按时间排序)", "locked": False, "enabled": True, "source": "base",
                 "content": "【逐笔历史交易明细】:\n{{closed_trades_json}}"},
                {"id": "base-eu-task", "title": "复盘与长期记忆进化任务", "locked": False, "enabled": True, "source": "base",
                 "content": "【复盘与长期记忆进化任务】:\n严格基于可观测台账证据复盘；没有交易发生时的微积分、定积分、概率与 VaR/CVaR 快照时，必须标记“数理快照不可观测”，不得事后编造。证据不足时输出 NO_CHANGE 并保留现有记忆。输出 change_status、diagnosis_insights、evolution_actions、ai_long_term_memory、memory_overwrites_reason 的严格 JSON。"},
                {"id": "custom-eu-wide-task", "title": "宽幅震荡进化任务", "locked": False, "enabled": True, "source": "custom",
                 "content": "【宽幅震荡进化任务】\n评估震荡区间识别准确度、箱体边际入场质量、止损抗插针有效性与手续费磨损；只有多个独立样本支持时才沉淀新经验，否则保留旧记忆并提出需要补充的证据。"},
            ],
        },
        "trading_system": """【交易风格：宽幅震荡箱体收割（高波动箱体·边际高抛低吸·2.2x宽止损防插针·1.5R快锁保本）】\n所有 P0 硬约束保持不变。宽幅震荡行情核心在于箱体边际定价与防插针保护：严禁在箱体正中间（半山腰）追涨杀跌！开仓仅限于 1H/4H 箱体上轨阻力位承压做空，或箱体下轨支撑位企稳做多；止损给足 2.0~2.5x 1H ATR 宽阔空间并置于近期箱体摆动极值外，彻底杜绝日内假突破影线插针频繁扫损；箱体行情主浪空间受限，浮盈达 1.5R 稳固波段必须果断输出 UPDATE_SL 移至开仓成本保本位，锁死胜率，触及箱体对边或动能衰竭时主动锁利，杜绝浮盈变亏损；多空双向平权，形态触及箱体边际且盈亏比合规时，自信评定 78%~86% 果断挂单！""",
        "trading_user": """【宽幅震荡箱体裁决偏好（箱体边界定位·动能耗散逆转·拉宽呼吸·快锁胜率）】
1. 箱体边缘入场原则：坚决拒绝在震荡区间正中央追多或追空。唯有当价格触及 4H/1H 箱体上轨承压（v 减速且 a < 0）挂限价做空，触及箱体下轨企稳（v 企稳且 a > 0）挂限价做多；
2. 防插针宽止损体系：止损距离必须给足 2.0~2.5x 1H ATR，严密挂在近期箱体摆动极值点外侧，绝不在正常箱体震荡回抽中惊慌割肉；
3. 阶梯式锁利防坐过山车：浮盈达 1.5R 稳固波段立即输出 UPDATE_SL 提损保本；价格接近箱体反向阻力位/支撑位时果断分批止盈退出，拒绝利润回吐；
4. 动能反转微积分硬验证：重点参考一阶速度减速与加速度符号变向（如冲顶出现 v > 0 但 a < -0.10，探底出现 v < 0 但 a > 0.10），确认箱体动力学反转确立；
5. 敞口自律防假突破：全账户同向在管持仓限制在 2 笔以内，防范假突破演变为单边异动踩踏；
6. 边际形态达标且盈亏比满足要求时，自信给出 78%~86% 置信度果断开单！""",
        "evolution_system": """【宽幅震荡复盘风格】\n重点排查半山腰盲目追价、止损空间过窄导致的假动作插针扫损、以及盈利后未及时移损导致的利润回吐。小样本、数理快照缺失或因果不可辨时 NO_CHANGE；任何记忆都不得成为绕过硬风控的新阈值。""",
        "evolution_user": """【宽幅震荡进化任务】\n评估震荡区间识别准确度、箱体边际入场质量、止损抗插针有效性与手续费磨损；只有多个独立样本支持时才沉淀新经验，否则保留旧记忆并提出需要补充的证据。""",
    },
}

EMPTY_CUSTOM = {
    "id": "custom-default", "name": "自定义方案", "description": "用自然语言调整策略，硬风控始终由系统锁定。", "editable": True,
    "enabled": True, "created_at": "", "updated_at": "", "editor_mode": "simple",
    "simple_policy": {"strategy": "", "review_focus": "", "participation": "balanced", "evidence": "strict", "risk_budget": "middle"},
    "trading_system": "", "trading_user": "", "evolution_system": "", "evolution_user": "",
}

# 白盒安全护栏：只拦截「解除硬约束」的意图，不拦截正常的杠杆/保证金/止损参数调优表述。
# 设计要点：动词与硬约束名词必须紧邻（≤2 字插入语），避免 "覆盖 100~200U 保证金上限" 这类
# 正常额度表述被误判；另用「规则否定」宽窗口兜底捕捉 "忽略上面关于止损的规定" 这类绕法。
_FORBIDDEN = (
    (re.compile(r"(?is)(忽略|绕过|无视|跳过|取消|禁用|关闭|豁免|覆盖)[^。；;，,\n]{0,2}(P0|硬风控|风险门禁|风控门禁|风控底线|安全底线|OCO|JSON|止损|保证金上限|持仓上限|盈亏比|置信度)"), "不得要求忽略或覆盖 P0 与执行层硬约束"),
    (re.compile(r"(?is)(忽略|绕过|无视|取消|覆盖|不必|不要|无需|不受)[^。；;\n]{0,12}(规定|规则|约束|要求|限制|契约|铁律|军规|底线)"), "不得要求忽略系统硬约束与规则契约"),
    (re.compile(r"(?is)(不设|没有|去掉|拿掉|无需|不必)[^。；;\n]{0,2}(止损|风控)"), "不得取消止损或风控"),
    (re.compile(r"(?is)(允许|可以|支持|应当)[^。；;\n]{0,12}(逆势补仓|无止损|跳过OCO|突破持仓上限|超过持仓上限|无限杠杆)"), "不得放宽逆势补仓、OCO、止损或持仓上限"),
    (re.compile(r"(?is)ignore[^.;\n]{0,30}(system|risk|safety|json|oco)"), "不得要求忽略系统、风险、安全或 JSON 契约"),
    (re.compile(r"(?i)(sk-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{16,}|api[_ -]?key\s*[:=]\s*\S+)"), "提示词中禁止写入 API Key 或密钥"),
)
# 命中前若出现这些否定前缀，说明文本本身是在「禁止」该行为，属于合规表述。
_BENIGN_PREFIX = re.compile(r"(不得|严禁|禁止|不可|不能|无法|杜绝|防止|避免)[^。；;\n]{0,10}$")


def scan_forbidden(value: str) -> list[str]:
    """返回命中的护栏告警（附带命中片段），供保存/导入/回滚三处校验复用。"""
    hits: list[str] = []
    text = str(value or "")
    for pattern, message in _FORBIDDEN:
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 12):match.start()]
            if _BENIGN_PREFIX.search(prefix):
                continue
            fragment = match.group(0).strip().replace("\n", " ")
            hits.append(f"{message}（命中片段：「{fragment[:40]}」）")
            break
    return hits
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


def stable_base_module_id(title: str) -> str:
    """薄壳：转调 `scripts/prompt_templates.py`（结构优化阶段 4·B3 第五十三刀）。"""
    return _tpl_stable_base_module_id(title)


def _module(module: dict[str, Any], index: int=0) -> dict[str, Any]:
    """薄壳：转调 `scripts/prompt_templates.py`（结构优化阶段 4·B3 第五十三刀）。

    ⚠️ `MAX_TEMPLATE_CHARS` 在**调用时**作为实参传入 —— 它留在门面，
    故仍可被 patch / 直接赋值；本模块不在 import 期烘焙它。
    """
    return _tpl_module(module, index, max_template_chars=MAX_TEMPLATE_CHARS)


# ── 管线 → 代码基座文本（审计批5 新发现：update_profile 的来源判定缺陷）──────────
# 旧行为：用扁平文本更新管线时无条件 `text_to_modules(text, "legacy")`——即使提交文本
# 与代码基座**逐字相同**，8 个模块也会全变成 legacy；下一轮 apply_module_layout 判定
# "无 base 模块"就把基座整段前置 → 线上提示词翻倍（实测 6454 → 12910 字符）。
# 这里提供「管线 → 基座文本」注册表 + 逐模块来源对齐：内容与基座逐字相同的模块
# 恢复 `source="base"`（连同基座 id 与 locked），只有真正被改写的模块才留在 legacy。
# 同一份代码在两种导入形态下都存在（`scripts.X` 与裸 `X`，见 risk_constants 的同族问题），
# 这里按「已导入的实例优先 → 点号形态 → 裸名」依次尝试，避免再引入第三份副本。
_BASE_TEMPLATE_SOURCES: dict[str, tuple[str, tuple[str, ...]]] = {
    "trading_system": ("SYSTEM_PROMPT", ("scripts.ai_brain_trader", "ai_brain_trader")),
    "trading_user": ("TRADING_USER_TEMPLATE", ("r20_backend.prompt_views",)),
    "evolution_system": ("EVOLUTION_SYSTEM_PROMPT",
                         ("scripts.self_improvement_engine", "self_improvement_engine")),
    "evolution_user": ("EVOLUTION_USER_TEMPLATE", ("r20_backend.prompt_views",)),
}
_BASE_TEMPLATE_CACHE: dict[str, str] = {}


def _base_text_resolver(pipeline: str) -> str:
    """在**调用时**把门面的 `_BASE_TEMPLATE_SOURCES` / `_BASE_TEMPLATE_CACHE`
    交给 `scripts/prompt_templates.py`（结构优化阶段 4·B3 第五十三刀）。

    ⚠️ 这两个名字都留在门面：前者是"管线 → 基座来源"注册表（配置面），
    后者是**可变**缓存（门面的 `register_base_template()` 会往里写）。
    子模块若在 import 期绑一份，登记的基座就永远读不到 —— 两处状态分叉。
    故用这个 resolver 在每次调用时现读门面全局。
    """
    return _tpl_base_template_text(
        pipeline,
        sources=_BASE_TEMPLATE_SOURCES,
        cache=_BASE_TEMPLATE_CACHE,
    )


def register_base_template(pipeline: str, text: str) -> None:
    """显式登记某条管线的代码基座文本（懒加载失败时的兜底入口）。"""
    if pipeline in TEMPLATE_KEYS:
        _BASE_TEMPLATE_CACHE[pipeline] = str(text or "")


def base_template_text(pipeline: str) -> str:
    """薄壳：转调 `scripts/prompt_templates.py`（结构优化阶段 4·B3 第五十三刀）。

    ⚠️ `_BASE_TEMPLATE_SOURCES` / `_BASE_TEMPLATE_CACHE` 都以**实参**传入，
    而不是让子模块 import：

    - `_BASE_TEMPLATE_CACHE` 是**可变** dict，且本模块的
      `register_base_template()` 会往它里面写。子模块若在 import 期绑一份，
      登记的基座就永远读不到（两处状态分叉）；
    - `_BASE_TEMPLATE_SOURCES` 是"管线 → 基座来源"注册表，属门面配置面。
    """
    return _tpl_base_template_text(
        pipeline,
        sources=_BASE_TEMPLATE_SOURCES,
        cache=_BASE_TEMPLATE_CACHE,
    )


def align_pipeline_sources(modules: list[dict[str, Any]], pipeline: str) -> list[dict[str, Any]]:
    """薄壳：转调 `scripts/prompt_templates.py`（结构优化阶段 4·B3 第五十三刀）。"""
    return _tpl_align_pipeline_sources(modules, pipeline,
                                       max_template_chars=MAX_TEMPLATE_CHARS,
                                       base_text_resolver=_base_text_resolver)


def text_to_modules(text: str, source: str='legacy', locked: bool=False) -> list[dict[str, Any]]:
    """薄壳：转调 `scripts/prompt_templates.py`（结构优化阶段 4·B3 第五十三刀）。"""
    return _tpl_text_to_modules(text, source, locked,
                                max_template_chars=MAX_TEMPLATE_CHARS)


def compile_modules(modules: list[dict[str, Any]]) -> str:
    """薄壳：转调 `scripts/prompt_templates.py`（结构优化阶段 4·B3 第五十三刀）。"""
    return _tpl_compile_modules(modules)


def base_template_modules(text: str, pipeline: str) -> list[dict[str, Any]]:
    """薄壳：转调 `scripts/prompt_templates.py`（结构优化阶段 4·B3 第五十三刀）。"""
    return _tpl_base_template_modules(text, pipeline,
                                      max_template_chars=MAX_TEMPLATE_CHARS,
                                      base_text_resolver=_base_text_resolver)


def _inherit_module_tags(submitted: list[Any], stored: Any) -> list[Any]:
    """薄壳：转调 `scripts/prompt_templates.py`（结构优化阶段 4·B3 第五十三刀）。"""
    return _tpl__inherit_module_tags(submitted, stored)


def _clean_pipelines(raw: Any, legacy: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    source=raw if isinstance(raw,dict) else {}
    # 审计 P1-2(2026-09-13)：本次提交**没提到**的管线必须原样保留已存定义。
    # 旧实现在这里用扁平文本 text_to_modules(..., "legacy") 重建 → source 从 base 变
    # legacy → 下一轮 apply_module_layout 判定「无 base 模块」→ 把 base 整段前置，
    # 于是「保存心法页」会让交易提示词 6452 → 12906 字符（×2.00，军规/JSON 契约各两遍）。
    stored_pipelines = legacy.get("pipelines") if isinstance(legacy, dict) else None
    stored_pipelines = stored_pipelines if isinstance(stored_pipelines, dict) else {}
    result={}
    for key in TEMPLATE_KEYS:
        if isinstance(source.get(key), list):
            modules = _inherit_module_tags(source[key], stored_pipelines.get(key))
        elif isinstance(stored_pipelines.get(key), list) and stored_pipelines.get(key):
            modules = stored_pipelines[key]
        else:
            modules=align_pipeline_sources(text_to_modules(str(legacy.get(key) or ""),"legacy"), key)
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


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    """读一个 JSON 对象；**缺失/损坏一律返回 None**（不抛、不猜）。

    「文件不在」在双文件模型里是**正常状态**（用户还没改过任何东西），
    所以这里不能把它当异常处理 —— 与 `load_library` 的兜底语义一致。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _profile_fingerprint(profile: dict[str, Any]) -> str:
    """方案**内容**指纹，**忽略时间戳**。

    用途：判定"用户到底改没改过这条方案"（见 `_is_user_owned`）。
    必须忽略 `created_at`/`updated_at` —— 它们每次 `_clean_profile` 都会被刷新，
    带上就等于"永远不相同"，纯净判定会失效（预设将永远收不到发版改进）。
    """
    comparable = {k: v for k, v in (profile or {}).items()
                  if k not in ("created_at", "updated_at")}
    blob = json.dumps(comparable, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _shipped_profiles(baseline: dict[str, Any]) -> dict[str, Any]:
    """"出厂"方案的权威内容 = 代码预设 ⊕ 基线文件（**基线优先**）。

    为什么基线优先：基线是发版交付的那份**完整**预设，而代码 `PRESETS` 只是
    基线缺失时的兜底 —— 实测二者并不相同（`evolution_system` 基线 1049 字符、
    兜底只有 92），所以基线在时必须盖住兜底。
    """
    shipped = {pid: _clean_profile(copy.deepcopy(PRESETS[pid]), pid) for pid in PRESETS}
    # ⚠️ 基线侧**也必须 `_clean_profile`**：传进来的可能是文件原样的 dict，
    # 而待判定那一侧（`save_library` 收到的 payload）已经规范化过。两边不同归
    # ⇒ 指纹永远不同 ⇒ **纯净预设定被判成"用户改过"而落进本地**，
    # 后果是把出厂基线永久钉死在旧版本上（发版改进再也进不来）。
    for pid, prof in (baseline.get("profiles") or {}).items():
        if isinstance(prof, dict):
            shipped[str(pid)] = _clean_profile(copy.deepcopy(prof), str(pid))
    return shipped


def _is_user_owned(profile_id: str, profile: dict[str, Any],
                   shipped: dict[str, Any]) -> bool:
    """这条方案该不该落进**本地**文件？

    只有两种情况该落：①基线里根本没有它（用户自建）；②基线里有、但内容与出厂不同
    （用户改过）。**与出厂逐字相同的不落** —— 这正是"没改过的预设能自动跟随发版
    改进"的实现：本地不留副本，读取时自然由基线供给。
    """
    if profile_id not in shipped:
        return True
    return _profile_fingerprint(profile) != _profile_fingerprint(shipped[profile_id])


def _merge_libraries(baseline_raw: dict[str, Any] | None,
                     local_raw: dict[str, Any] | None) -> dict[str, Any]:
    """基线 ⊕ 本地（**本地优先**）。

    - `profiles`：按 id 合并且本地覆盖基线；
    - `active_profile_id`：本地显式设过才覆盖（否则沿用基线）；
    - `revisions`：两边**按 id 去重**后合并（见下）。
    """
    baseline = _migrate(baseline_raw) if baseline_raw else _default()
    if not local_raw:
        return baseline
    local = _migrate(local_raw)
    merged = copy.deepcopy(baseline)
    merged["profiles"].update(copy.deepcopy(local["profiles"]))
    if local_raw.get("active_profile_id"):
        merged["active_profile_id"] = local["active_profile_id"]
    # 修订按 **id 去重**后合并：本地文件里存的是"合并后的历史"（save 时整体写入），
    # 若这里只做简单相加，基线的旧修订会在每次 load 时被重新前置 ⇒ 历史无限膨胀。
    seen: dict[str, dict[str, Any]] = {}
    for item in [*(baseline.get("revisions") or []), *(local.get("revisions") or [])]:
        if isinstance(item, dict) and item.get("id"):
            seen[str(item["id"])] = item
    if seen:
        merged["revisions"] = list(seen.values())[-MAX_REVISIONS:]
    return merged


def load_library() -> dict[str, Any]:
    """读方案库 = `BASELINE_FILE`（出厂基线）⊕ `LOCAL_FILE`（用户改动，本地优先）。

    两个文件都在时以本地为准；本地不在（用户还没改过任何东西）时**就等于基线**，
    行为与双文件改造前逐位相同。
    """
    payload = _merge_libraries(_read_json_dict(BASELINE_FILE), _read_json_dict(LOCAL_FILE))
    active = str(payload.get("active_profile_id") or "stable")
    if active not in PRESETS and active not in payload["profiles"]:
        active = "stable"
    payload["active_profile_id"] = active
    # Backward compatibility for old API/tests.
    payload["active_style"] = active if active in PRESETS else "custom"
    payload["custom"] = copy.deepcopy(payload["profiles"].get(active) or payload["profiles"].get("custom-default") or EMPTY_CUSTOM)
    return payload


def _library_lock():
    """跨进程互斥（审计 P2-6）：提示词方案库是 RMW 目标（管理页多次点击 / 导入 / 回滚 /
    采集脚本都会 load→改→save）。优先用可重入的后端锁，退化为本地 flock。

    ⚠️ 双文件模型（2026-09）后只锁 `LOCAL_FILE`：基线是**只读**的，没人跟它竞争；
    真正需要串行化的是"读两侧 → 改 → 写本地"这个 RMW 循环本身。

    兜底实现已移到 `scripts/local_lock.py`（结构优化阶段 4·B3 第四十八刀）——
    原先两处脚本各手写一份**不可重入**的裸 flock，而调用方存在嵌套
    （`mutate_instruments` → `save_instruments`），一旦走兜底分支会同线程自锁挂死。
    """
    try:
        from r20_backend.file_locks import file_lock
        return file_lock(LOCAL_FILE)
    except Exception:
        return local_file_lock(LOCAL_FILE)


def _locked_library(fn):
    """装饰器：整个 load→改→save 期间持锁（可重入，嵌套 save_library 不会自锁）。"""
    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        with _library_lock():
            return fn(*args, **kwargs)
    return _wrapper


@_locked_library
def save_library(payload: dict[str, Any]) -> None:
    # Accept the v1 shape used by older admin clients. If a caller changed only
    # active_style/custom while active_profile_id still equals the persisted value,
    # treat it as an intentional legacy update.
    legacy_update = "profiles" not in payload
    if not legacy_update and "active_style" in payload:
        try:
            persisted_active = load_library().get("active_profile_id", "stable")
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
    # ⚠️ 只写**本地**文件，且只写"真差异"（见 `_is_user_owned`）。
    # 基线是出厂交付物，应用**永不写它** —— 写它就等于把用户改动塞回 git 跟踪的文件，
    # 又会把工作区弄脏（那正是本次要消除的形态）。
    shipped = _shipped_profiles(_read_json_dict(BASELINE_FILE) or {})
    local = {
        "version": 2,
        "active_profile_id": normalized.get("active_profile_id", "stable"),
        "profiles": {pid: prof for pid, prof in normalized["profiles"].items()
                     if _is_user_owned(pid, prof, shipped)},
        "revisions": list(normalized.get("revisions") or [])[-MAX_REVISIONS:],
    }
    _atomic_write(LOCAL_FILE, local)


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
            for hit in scan_forbidden(value):
                errors.append(f"{pipeline}/{module.get('title', '模块')}：{hit}")
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
            for hit in scan_forbidden(value):
                errors.append(f"{label}：{hit}")
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
        for hit in scan_forbidden(value):
            errors.append(f"{key}：{hit}")
    total_chars = module_total if pipelines else total
    if total_chars > MAX_PROFILE_CHARS:
        errors.append(f"四类模板合计不得超过 {MAX_PROFILE_CHARS} 字符")
    if not total and not pipelines and profile.get("editor_mode") != "simple":
        warnings.append("当前方案四类附加模板均为空，将只使用基础提示词")
    return {"valid": not errors, "errors": list(dict.fromkeys(errors)), "warnings": warnings, "characters": total_chars}


def _revision(profile: dict[str, Any], action: str, note: str = "") -> dict[str, Any]:
    return {"id": f"rev-{uuid.uuid4().hex[:12]}", "profile_id": profile["id"], "action": action, "note": str(note)[:240], "created_at": _now(), "snapshot": copy.deepcopy(profile)}


@_locked_library
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


@_locked_library
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
    # 审计 P1-2 关键修复：调用方可以只提交一条管线（如心法页只提交 evolution_system），
    # 此时必须与**已存**管线逐键合并后再清理；否则未提到的管线会被扁平文本重建成
    # legacy 模块（丢掉 source=base 标签），下一轮 apply_module_layout 判定「无 base 模块」
    # 就把整段 base 前置 → 该管线提示词翻倍（实测保存心法页后交易提示词 ×2.00）。
    submitted_keys = {k for k in TEMPLATE_KEYS if isinstance((changes.get("pipelines") or {}).get(k), list)}
    if submitted_keys:
        stored_pipelines = current.get("pipelines") if isinstance(current.get("pipelines"), dict) else {}
        merged = copy.deepcopy(stored_pipelines)
        for key in submitted_keys:
            # 提交里漏了 source/locked 时从已存同 id/同标题模块继承（见 _inherit_module_tags）
            merged[key] = _inherit_module_tags(accepted["pipelines"][key], stored_pipelines.get(key))
        accepted["pipelines"] = merged
    flat_updates = [key for key in TEMPLATE_KEYS if key in accepted]
    submitted_keys |= set(flat_updates)
    if "pipelines" not in accepted and flat_updates:
        accepted["pipelines"] = copy.deepcopy(current.get("pipelines") or {})
        for key in flat_updates:
            # 批5：不再无条件标 legacy——与基座逐字相同的模块保留 source=base，
            # 否则下一次渲染会把基座整段前置（提示词翻倍）。
            accepted["pipelines"][key] = align_pipeline_sources(
                text_to_modules(str(accepted[key] or ""), "legacy"), key)
        accepted["editor_mode"] = "modules"
    elif "editor_mode" not in accepted and any(str(accepted.get(key) or "").strip() for key in TEMPLATE_KEYS): accepted["editor_mode"] = "advanced"
    updated = _clean_profile({**current, **accepted, "updated_at": _now()}, profile_id)
    check = validate_profile(updated)
    if not check["valid"]: raise ValueError("；".join(check["errors"]))
    # 硬闸（审计 P1-2）：本次没提交的管线，保存后渲染文本必须逐字节不变——
    # 任何「保存 A 页把 B 管线改了」都说明重建路径又复活了（旧实现在此把 B 管线
    # 全部降级成 legacy，下一轮布局就把 base 前置导致整段翻倍）。
    # submitted_keys 取自**原始请求**（上面的 merge 已把四条键补齐，不能拿合并后的判）。
    for key in TEMPLATE_KEYS:
        if key in submitted_keys: continue
        before = compile_modules((current.get("pipelines") or {}).get(key) or [])
        after = compile_modules((updated.get("pipelines") or {}).get(key) or [])
        if before.strip() != after.strip():
            raise ValueError(f"内部一致性错误：本次未提交 {key}，但其内容在保存过程中被改变（禁止静默改写其他管线）")
    library["profiles"][profile_id] = updated
    library["revisions"].append(_revision(updated, "update", note))
    library["revisions"] = library["revisions"][-MAX_REVISIONS:]
    save_library(library)
    return updated


@_locked_library
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


@_locked_library
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


@_locked_library
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


@_locked_library
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
    for pid in ("stable", "wide_oscillation"):
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
            if item.get("enabled", True):
                content = str(item.get("content") or "").strip()
                if content:
                    output.append({**item, "content": content})
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
        elif any(token in title for token in ("三重滤网裁决协议", "开仓与价格几何")):
            content = live["content"]
        else:
            content = str(item.get("content") if item.get("content") is not None else live["content"])
        output.append({**live, "content": content})

    # Fail closed: only for trading_user to preserve live runtime sections
    if pipeline == "trading_user":
        output.extend(module for module in base_modules if module["title"] not in matched)

    return render_variables(compile_modules(output), context)


def pipeline_view(base: str, profile: dict[str, Any], pipeline: str) -> list[dict[str, Any]]:
    """薄壳：转调 `scripts/prompt_templates.py`（结构优化阶段 4·B3 第五十三刀）。"""
    return _tpl_pipeline_view(base, profile, pipeline,
                              max_template_chars=MAX_TEMPLATE_CHARS,
                              base_text_resolver=_base_text_resolver)


def append_layer(base: str, layer: str, label: str) -> str:
    """薄壳：转调 `scripts/prompt_templates.py`（结构优化阶段 4·B3 第五十三刀）。"""
    return _tpl_append_layer(base, layer, label)

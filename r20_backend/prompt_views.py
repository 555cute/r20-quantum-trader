"""Base module skeletons and rendered prompt snapshots for the admin editor."""
from __future__ import annotations
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

EVOLUTION_USER_TEMPLATE = """======================= 【当前认知复盘基准时间】 =======================
【复盘基准时间】: {{timestamp_beijing}}

======================= 【当前系统已有的历史长期记忆库】 =======================
{{existing_memory_markdown}}

======================= 【AstraQuant 加密量化实盘战绩与历史交易台账】 =======================
【统计汇总】:
- 总平仓笔数: {{total}} 笔（胜 {{wins}} / 负 {{losses}} | 胜率 {{win_rate}}%）
- 累计净盈亏: {{total_net}} USDT | 累计手续费: {{total_fees}} USDT
- 当前聚焦标的池: {{target_instruments}}

【逐笔历史交易明细】:
{{closed_trades_json}}

【复盘与长期记忆进化任务】:
严格基于可观测台账证据复盘；没有交易发生时的微积分、定积分、概率与 VaR/CVaR 快照时，必须标记“数理快照不可观测”，不得事后编造。证据不足时输出 NO_CHANGE 并保留现有记忆。输出 change_status、diagnosis_insights、evolution_actions、ai_long_term_memory、memory_overwrites_reason 的严格 JSON。
"""

TRADING_USER_TEMPLATE = """======================= 【当前决策时间戳与市场时效】 =======================
实时插槽：北京时间、账户可用资金。

======================= 【全网实时重大快讯与宏观情报】 =======================
实时插槽：宏观环境与最新可验证资讯。

======================= 【账户当前持仓与风险敞口全景】 =======================
实时插槽：持仓概况、方向、均价、标记价、浮盈亏与动态止损。

======================= 【在途未成交限价挂单 (Pending Maker Orders)】 =======================
实时插槽：未成交订单、价格、数量、创建时间及云端止盈止损。

======================= 【AstraQuant 启发式实战认知与长期记忆】 =======================
实时插槽：自进化引擎沉淀的可审计长期记忆；无记忆时本模块省略。

======================= 【全标的池原生行情、技术指标与筹码矩阵】 =======================
实时插槽：各标的多周期 K 线、盘口、聪明钱、微积分动力学、定积分能量与概率风险。

【推演与决策任务】:
可编辑规则模块：持仓管理、挂单生命周期、开仓/顺势加仓裁决与严格 JSON 输出 Schema。P0 与执行层硬门禁仍由 System Prompt 和执行器锁定。
"""


def _split_snapshot(path: Path) -> dict[str, str]:
    if not path.exists(): return {"system": "", "user": "", "updated": ""}
    text = path.read_text(encoding="utf-8", errors="replace"); marker = "【USER PROMPT"; index = text.find(marker)
    system = text[:index].strip() if index >= 0 else ""; user = text[index:].strip() if index >= 0 else text.strip()
    return {"system": system, "user": user, "updated": str(int(path.stat().st_mtime))}


def rendered_snapshots() -> dict[str, Any]:
    return {"trading": _split_snapshot(DATA / "ai_brain_last_prompt.txt"), "evolution": _split_snapshot(DATA / "self_improvement_last_prompt.txt")}

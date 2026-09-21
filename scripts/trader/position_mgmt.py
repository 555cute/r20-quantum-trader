"""AI 持仓指令的执行器（B3 抽取第十二块）。

从 `scripts/ai_factor_trader.py` 搬出 `execute_ai_position_management`（95 行）：
读取主脑写下的 `ai_position_management.json`，把其中的持仓指令落到交易所。

## 为什么单独成为一块

它是**执行层**里少见的"策略判定 + 云端改单"复合体，且带两条硬安全语义：

1. **CLOSE_MARKET 需置信度 ≥ 85** 才执行（`AI_CLOSE_CONFIDENCE_MIN`）；
   低于阈值直接拒绝并在 `executed_actions` 里留痕 —— 宁可少平，不可误平。
2. **UPDATE_SL 必须先过 `ai_tightens_stop`**：只有"确实在收紧"的改单才放行。
   放松止损 = 账户裸奔，一律拒绝（判定数学见 `scripts/trader/protection.py`）。

另有两条 fail-closed 边界：文件不存在直接返回；指令 **超过 300 秒**视为过期不执行
（防止用上一轮的陈旧指令操作当前盘面）。

## 注入面（9 项，全部调用期）

| 依赖 | 说明 |
|---|---|
| `ai_position_management_file` | 门面 `AI_POSITION_MANAGEMENT_FILE`；测试会 patch 门面属性 |
| `ai_tightens_stop` | 来自 `scripts/trader/protection.py`，同一份判定 |
| `close_position_confirmed` / `okx_rest` | OKX 直下路径 |
| `venue_registry` / `current_environment` / `amend_venue_stop_loss` | 多所路径 |

全部**调用期注入**：门面会被 `pin_baseline_risk_env()` 原地重载，
import 期绑定会变成过期快照（`r20_backend/README.md` §5）。

> 平仓置信度阈值 **85 是原实现里的字面量**（不是 `risk_constants` 常量，全仓查无
> `AI_CLOSE_CONFIDENCE_MIN`）。本次搬运**刻意保持字面量不变** —— 重构不得改业务阈值。
> 若将来要把它配置化，那是独立的行为变更，需单独评审。
"""
import json
import os
import time


def execute_ai_position_management(real_pos_dict, trackers, timestamp_full, executed_actions, *,
                                 ai_position_management_file, ai_tightens_stop,
                                 close_position_confirmed, okx_rest, venue_registry,
                                 current_environment, amend_venue_stop_loss):
    """Execute only fresh, high-confidence and risk-reducing AI position instructions."""
    if not os.path.exists(ai_position_management_file):
        return
    try:
        with open(ai_position_management_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if int(time.time()) - int(payload.get("timestamp", 0) or 0) > 300:
            executed_actions.append("AI持仓指令已过期，未执行")
            return
    except Exception as e:
        executed_actions.append(f"AI持仓指令读取失败: {e}")
        return

    for instruction in payload.get("instructions", []):
        inst_id = str(instruction.get("instId", ""))
        action = str(instruction.get("action", "HOLD")).upper()
        confidence = float(instruction.get("confidence", 0) or 0)
        reason = str(instruction.get("reason", "AI持仓管理"))[:120]
        position = real_pos_dict.get(inst_id)
        if not position:
            # ⚠️ 第一百一十七刀：旧实现在这里**静默** `continue` —— AI 明明对一笔
            # **外所**持仓写了 CLOSE_MARKET / UPDATE_SL，面板与日志里毫无痕迹，
            # 看起来像"本轮无事可做"（假阴性）。
            #
            # 实测（2026-09-20）：AI 指令文件里唯一一条就是 `UNI-USDT-SWAP`（HOLD），
            # 而它正是 **binance** 的 UNI 空仓；`real_pos_dict` 由
            # `cycle_stages.fetch_positions_and_reconcile` 用 **OKX 直签链**的
            # `query_positions()` 构建 ⇒ 外所持仓**永远不在**这个字典里，
            # 这条缺口**实际可达**（只要 AI 把 HOLD 换成 CLOSE_MARKET/UPDATE_SL）。
            #
            # 本刀只**如实留痕**、不改任何交易行为：真正的执行能力（把外所持仓并入
            # 本路径管理）属改变实盘行为的改动，须单独决策。
            if action != "HOLD":
                _nm = inst_id.replace("-USDT-SWAP", "") or inst_id
                executed_actions.append(
                    f"[{_nm}] AI{action}指令未执行：{inst_id} 不在本路径持仓字典"
                    f"（该字典仅 OKX 直签链；外所持仓由云端保护腿链路管理）")
            continue
        if action == "HOLD":
            continue

        pos_side = str(position.get("posSide", "net")).lower()
        pos_venue = str(position.get("venue") or position.get("exchange") or "okx").lower()
        current_px = float(position.get("markPx", position.get("last", 0)) or 0)
        name = inst_id.replace("-USDT-SWAP", "")

        if action == "CLOSE_MARKET":
            if confidence < 85:
                executed_actions.append(f"[{name}] AI平仓置信度{confidence:.0f}<85，拒绝执行")
                continue
            closed, close_detail = close_position_confirmed(inst_id, pos_side, float(position.get("pos", 0) or 0), venue=pos_venue)
            if closed:
                executed_actions.append(f"[{name}] AI高置信度整仓退出 ({pos_venue.upper()}): {reason}")
                trackers.pop(f"{inst_id}_{pos_side}", None)
            else:
                executed_actions.append(f"[{name}] AI平仓请求未获交易所确认，仓位保持不变: {close_detail}")

        elif action == "UPDATE_SL":
            new_sl = float(instruction.get("suggested_sl_price", 0) or 0)
            # 反过早收紧判定见 scripts/trader/protection.py::ai_tightens_stop
            # （判定收紧方向；放松即账户裸奔）。三个阈值常量随函数搬去，值未改。
            tightens_risk = ai_tightens_stop(instruction, position)

            if not tightens_risk:
                executed_actions.append(f"[{name}] 浮盈空间不足或与现价缓冲过近({current_px} vs 拟调SL {new_sl})，拒绝过早收紧止损")
                continue

            amend_ok = False
            old_sl = 0.0
            if pos_venue != "okx":
                try:
                    from r20_backend.close_intent import adapter_environment as _sl_env
                    ad = venue_registry.get_adapter(pos_venue,
                        environment=_sl_env(pos_venue, str(current_environment().mode)))  # 审计 C2+C3
                    # 审计 C3（后半）：棘轮而非堆单——原生改单优先，回退先挂新再撤旧
                    _c3_ok, _c3_note = amend_venue_stop_loss(
                        ad, name, pos_side, float(new_sl), abs(float(position.get("pos", 0) or 0)))
                    amend_ok = bool(_c3_ok)
                    if not amend_ok:
                        executed_actions.append(f"[{name}] {pos_venue.upper()} 云端止损更新失败: {_c3_note}")
                        continue
                except Exception as vexc:
                    executed_actions.append(f"[{name}] {pos_venue.upper()} 云端止损更新失败: {vexc}")
                    continue
            else:
                try:
                    algo_orders = okx_rest.pending_algo_orders(inst_id)
                except Exception as exc:
                    executed_actions.append(f"[{name}] 云端止损收紧失败，原保护单保持不变（查询异常：{exc}）")
                    continue
                # 第一百八十六刀：同 cloud_protection —— 净持仓账户的云端单 `posSide` 是
                # `"net"`，精确相等会永远找不到 ⇒ 只会打印"未找到真实云端止损单"，
                # 云端止损上移静默不生效。统一为 net 容错。
                live_algo = next((o for o in algo_orders
                                  if str(o.get("state", "")).lower() == "live"
                                  and str(o.get("posSide", "net")).lower() in {pos_side, "net"}
                                  and o.get("slTriggerPx")), None)
                if not live_algo:
                    executed_actions.append(f"[{name}] 未找到真实云端止损单，无法更新")
                    continue
                old_sl = float(live_algo.get("slTriggerPx", 0) or 0)
                try:
                    okx_rest.amend_algo_sl(live_algo["algoId"], new_sl, inst_id=inst_id, new_sl_ord_px="-1")
                    amend_ok = True
                except Exception:
                    amend_ok = False

            if amend_ok:
                executed_actions.append(f"[{name}] 云端止损收紧至 {new_sl} ({pos_venue.upper()}): {reason}")
                tracker = trackers.get(f"{inst_id}_{pos_side}")
                if tracker:
                    tracker["trailingStopPx"] = new_sl
                try:
                    from qq_notifier import notify_sl_updated
                    notify_sl_updated(name, pos_side, old_sl, new_sl, reason)
                except Exception:
                    pass
            else:
                executed_actions.append(f"[{name}] 云端止损更新失败，原保护单保持不变")

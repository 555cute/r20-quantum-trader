"""B3（交易员侧第十二块）`scripts/trader/position_mgmt.py` 的抽取回归。

## 这个测试在守什么

`execute_ai_position_management`（95 行）从 `scripts/ai_factor_trader.py` 搬进
`scripts/trader/position_mgmt.py` —— 它是"主脑写下的持仓指令 → 落交易所"的执行器。

**该路径在重构前几乎零直接测试覆盖**，却握着两条硬安全语义：

1. **CLOSE_MARKET 需置信度 ≥ 85** 才执行。低于阈值必须拒绝 + 在 `executed_actions`
   留痕（宁可少平，不可误平）。阈值是原实现的**字面量 85**，本次搬运刻意不改。
2. **UPDATE_SL 必须先过 `ai_tightens_stop`**：只有"确实在收紧"的改单才放行。
   放松止损 = 账户裸奔，一律拒绝。

另有两条 fail-closed 边界：
- 指令文件不存在 → 直接返回（不报错、不动仓）；
- 指令 **超过 300 秒** → 视为过期，不执行（防用陈旧指令操作当前盘面）。

本文件用**假交易所**（记录调用）对拍"搬走前门面实现"与"搬后子模块"，覆盖
上述四条，并额外验证注入契约（9 项依赖必须调用期从门面取）。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.ai_factor_trader as aft
from scripts.trader import position_mgmt

FACADE = Path(__file__).resolve().parents[2] / "scripts" / "ai_factor_trader.py"
SUBMODULE = Path(__file__).resolve().parents[2] / "scripts" / "trader" / "position_mgmt.py"


class _FakeOkx:
    """记录调用的假 OKX 直下通道。"""

    def __init__(self, algo_orders=None, amend_ok=True, pending_raises=None):
        self.algo_orders = algo_orders if algo_orders is not None else []
        self.amend_ok = amend_ok
        self.pending_raises = pending_raises
        self.amend_calls = []
        self.pending_calls = []

    def pending_algo_orders(self, inst_id):
        self.pending_calls.append(inst_id)
        if self.pending_raises:
            raise self.pending_raises
        return self.algo_orders

    def amend_algo_sl(self, algo_id, new_sl, **kw):
        self.amend_calls.append((algo_id, new_sl, kw))
        if not self.amend_ok:
            raise RuntimeError("amend rejected")


def _legacy(real_pos_dict, trackers, timestamp_full, executed_actions, *,
            ai_position_management_file, ai_tightens_stop, close_position_confirmed,
            okx_rest, venue_registry, current_environment, amend_venue_stop_loss):
    """搬走前门面里的实现（逐字原样；仅把模块全局改成入参以便参数化）。"""
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
        if not position or action == "HOLD":
            continue

        pos_side = str(position.get("posSide", "net")).lower()
        pos_venue = str(position.get("venue") or position.get("exchange") or "okx").lower()
        current_px = float(position.get("markPx", position.get("last", 0)) or 0)
        name = inst_id.replace("-USDT-SWAP", "")

        if action == "CLOSE_MARKET":
            if confidence < 85:
                executed_actions.append(f"[{name}] AI平仓置信度{confidence:.0f}<85，拒绝执行")
                continue
            closed, close_detail = close_position_confirmed(
                inst_id, pos_side, float(position.get("pos", 0) or 0), venue=pos_venue)
            if closed:
                executed_actions.append(f"[{name}] AI高置信度整仓退出 ({pos_venue.upper()}): {reason}")
                trackers.pop(f"{inst_id}_{pos_side}", None)
            else:
                executed_actions.append(f"[{name}] AI平仓请求未获交易所确认，仓位保持不变: {close_detail}")

        elif action == "UPDATE_SL":
            new_sl = float(instruction.get("suggested_sl_price", 0) or 0)
            tightens_risk = ai_tightens_stop(instruction, position)

            if not tightens_risk:
                executed_actions.append(
                    f"[{name}] 浮盈空间不足或与现价缓冲过近({current_px} vs 拟调SL {new_sl})，拒绝过早收紧止损")
                continue

            amend_ok = False
            old_sl = 0.0
            if pos_venue != "okx":
                try:
                    from r20_backend.close_intent import adapter_environment as _sl_env
                    ad = venue_registry.get_adapter(
                        pos_venue, environment=_sl_env(pos_venue, str(current_environment().mode)))
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
                live_algo = next((o for o in algo_orders
                                  if o.get("state") == "live" and o.get("posSide") == pos_side
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


class _Harness:
    """把门面的 9 项依赖打包成一次调用，可分别跑 legacy / impl。"""

    def __init__(self, payload, positions, tracker=None, *, algo_orders=None,
                 amend_ok=True, pending_raises=None, timestamp=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ai_position_management.json"
        body = dict(payload)
        body.setdefault("timestamp", int(timestamp if timestamp is not None else time.time()))
        self.path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        self.positions = positions
        self.trackers = tracker if tracker is not None else {f"{k}_{v.get('posSide','net')}": {"trailingStopPx": 1.0}
                                                            for k, v in positions.items()}
        self.okx = _FakeOkx(algo_orders=algo_orders, amend_ok=amend_ok, pending_raises=pending_raises)
        self.close_calls = []
        self.amend_calls = []

        def _close(inst_id, side, sz, venue=None):
            self.close_calls.append((inst_id, side, sz, venue))
            return True, "ok"

        self.close = _close

    def run(self, fn):
        actions = []
        fn(self.positions, self.trackers, "2026-09-14 10:00:00", actions,
           ai_position_management_file=str(self.path),
           ai_tightens_stop=aft.ai_tightens_stop,
           close_position_confirmed=self.close,
           okx_rest=self.okx,
           venue_registry=None,
           current_environment=None,
           amend_venue_stop_loss=None)
        return actions

    @property
    def pending_raises(self):
        return self.okx.pending_raises

    def cleanup(self):
        self.tmp.cleanup()


class ImplementationMovedTest(unittest.TestCase):
    def test_impl_lives_in_submodule_not_facade(self):
        """门面只应留薄壳：用"门面里不再有循环体特征"判定，而不是只看某个字面量。

        注意：门面壳的 docstring 会提到子模块路径与语义，所以不能拿"某个字符串"
        做 assertNotIn —— 那会把注释也算进去。这里改为检查**两个只有实现体才会有的
        结构特征**：`for instruction in` 循环与 `if confidence <` 判定。
        """
        facade = FACADE.read_text(encoding="utf-8")
        sub = SUBMODULE.read_text(encoding="utf-8")
        self.assertIn("AI持仓指令已过期，未执行", sub, "实现体未搬入子模块")
        for marker in ("for instruction in payload.get", "if confidence < 85:"):
            self.assertIn(marker, sub, f"子模块缺少实现特征 {marker!r}")
            self.assertNotIn(marker, facade, f"门面仍留有实现体特征 {marker!r}")

    def test_facade_shell_injects_all_dependencies(self):
        facade = FACADE.read_text(encoding="utf-8")
        self.assertIn("_execute_ai_position_management_impl(", facade)
        for kw in ("ai_position_management_file=AI_POSITION_MANAGEMENT_FILE,",
                   "ai_tightens_stop=ai_tightens_stop,",
                   "close_position_confirmed=close_position_confirmed,",
                   "okx_rest=okx_rest,",
                   "venue_registry=venue_registry,",
                   "current_environment=current_environment,",
                   "amend_venue_stop_loss=amend_venue_stop_loss,"):
            self.assertIn(kw, facade, f"门面未注入 {kw}")

    def test_confidence_threshold_stays_a_literal(self):
        """平仓阈值 85 是原实现字面量，搬运不得改动它，也不得引入新常量。

        这条防的是"重构顺手把阈值配置化"——那属于行为变更，必须单独评审。
        """
        sub = SUBMODULE.read_text(encoding="utf-8")
        self.assertIn("if confidence < 85:", sub)
        self.assertNotIn("ai_close_confidence_min", sub)


class ParityTest(unittest.TestCase):
    """搬走前实现 vs 搬后子模块 —— 假交易所下逐条对拍。"""

    #: ⚠️ **文档化差异**（第一百一十七刀，2026-09-20）：本执行器只允许下面这一处
    #: 有意的改动 —— 当 AI 指令的 `instId` **不在** `real_pos_dict`（该字典由
    #: `cycle_stages.fetch_positions_and_reconcile` 用 **OKX 直签链**构建）且动作
    #: 非 HOLD 时，旧实现**静默 continue**，新实现**如实留痕**。
    #:
    #: 实测可达性（2026-09-20）：AI 指令文件里唯一一条是 `UNI-USDT-SWAP`（HOLD），
    #: 而它正是 **binance** 的 UNI 空仓 ⇒ 只要 AI 改成 CLOSE_MARKET/UPDATE_SL，
    #: 旧实现就"什么都没做、一个字也不说"。本刀只改**可见性**，不动交易行为
    #: （能力补齐属改变实盘行为的改动，须单独决策）。
    #:
    #: 差异用"从 got 里剥掉这些留痕后必须与 legacy 逐字一致"来表达：
    #: 于是**除留痕之外**的任何行为分叉仍会翻红。
    @staticmethod
    def _delta_lines(harness):
        out = []
        for ins in _read_instr(harness.path):
            inst = str(ins.get("instId") or "")
            act = str(ins.get("action") or "HOLD").upper()
            if inst and inst not in harness.positions and act != "HOLD":
                nm = inst.replace("-USDT-SWAP", "") or inst
                out.append(f"[{nm}] AI{act}指令未执行：{inst} 不在本路径持仓字典"
                           f"（该字典仅 OKX 直签链；外所持仓由云端保护腿链路管理）")
        return out

    def _both(self, harness):
        self.addCleanup(harness.cleanup)
        got_raw = harness.run(position_mgmt.execute_ai_position_management)
        got_env = ([list(a) for a in harness.close_calls], list(harness.okx.amend_calls),
                   dict(harness.trackers))

        h2 = self._clone(harness)
        exp = h2.run(_legacy)
        exp_env = ([list(a) for a in h2.close_calls], list(h2.okx.amend_calls), dict(h2.trackers))
        h2.cleanup()

        # 剥掉文档化差异后，必须与搬走前实现**逐字一致**
        delta = self._delta_lines(harness)
        remaining = list(delta)
        got = []
        for line in got_raw:
            if line in remaining:
                remaining.remove(line)      # 每条差异只抵扣一次
            else:
                got.append(line)
        self.assertEqual(remaining, [], "新实现缺少文档化差异留痕（或文案不一致）")
        return got, exp, got_env, exp_env

    def _clone(self, h):
        return _Harness({"instructions": _read_instr(h.path)}, h.positions,
                        tracker={k: dict(v) for k, v in h.trackers.items()},
                        algo_orders=h.okx.algo_orders, amend_ok=h.okx.amend_ok,
                        pending_raises=h.okx.pending_raises,
                        timestamp=_read_ts(h.path))

    def test_missing_file_is_a_noop(self):
        actions_got = []
        position_mgmt.execute_ai_position_management(
            {}, {}, "T", actions_got,
            ai_position_management_file="/nonexistent/nope.json",
            ai_tightens_stop=aft.ai_tightens_stop, close_position_confirmed=lambda *a, **k: (True, ""),
            okx_rest=_FakeOkx(), venue_registry=None, current_environment=None,
            amend_venue_stop_loss=None)
        self.assertEqual(actions_got, [])

    def test_stale_file_beyond_300s_is_skipped(self):
        h = _Harness({"instructions": [{"instId": "BTC-USDT-SWAP", "action": "CLOSE_MARKET",
                                        "confidence": 99, "reason": "r"}]},
                     {"BTC-USDT-SWAP": {"posSide": "long", "pos": 1, "markPx": 100}},
                     timestamp=time.time() - 400)
        got, exp, _, _ = self._both(h)
        self.assertEqual(got, exp)
        self.assertEqual(got, ["AI持仓指令已过期，未执行"])

    def test_close_market_parity_below_and_at_threshold(self):
        for conf in (0, 84, 84.9, 85, 90, 100):
            h = _Harness({"instructions": [{"instId": "BTC-USDT-SWAP", "action": "CLOSE_MARKET",
                                            "confidence": conf, "reason": "理由"}]},
                         {"BTC-USDT-SWAP": {"posSide": "long", "pos": 2, "markPx": 100}})
            got, exp, g_env, e_env = self._both(h)
            self.assertEqual(got, exp, f"confidence={conf} 文案分叉")
            self.assertEqual(g_env, e_env, f"confidence={conf} 副作用分叉")
            if conf < 85:
                self.assertIn("拒绝执行", got[0], "低于阈值必须拒绝")

    def test_unknown_position_is_reported_and_hold_is_silent(self):
        """第一百一十七刀改判：**非 HOLD** 的未知持仓指令必须留痕（旧实现静默）。

        实测的正是这个形状：AI 对 **binance** 的 `UNI-USDT-SWAP` 发文，而
        `real_pos_dict` 仅 OKX 直签链 ⇒ 旧实现一个字都不说，看起来像"无事可做"。
        HOLD 仍然静默（没要求动作，无需留痕）。
        """
        h = _Harness({"instructions": [
            {"instId": "NOPE-USDT-SWAP", "action": "CLOSE_MARKET", "confidence": 99, "reason": "x"},
            {"instId": "BTC-USDT-SWAP", "action": "HOLD", "confidence": 99, "reason": "x"},
        ]}, {"BTC-USDT-SWAP": {"posSide": "long", "pos": 1, "markPx": 100}})
        got, exp, g_env, _ = self._both(h)
        self.assertEqual(got, exp, "除留痕外必须与搬走前实现逐字一致")
        # 差异就是那一条留痕（已在 _both 里抵扣），故这里 got 为空
        self.assertEqual(got, [])
        # 且**真的**留痕了（原始输出里含它）
        raw = self._delta_lines(h)
        self.assertEqual(len(raw), 1)
        self.assertIn("不在本路径持仓字典", raw[0])
        self.assertIn("仅 OKX 直签链", raw[0])
        self.assertEqual(g_env[0], [], "未知持仓绝不得触发平仓")

    def test_binance_position_instruction_is_reported_not_silent(self):
        """真机形状：`real_pos_dict` 只有 OKX 仓，AI 却对 binance 仓发 CLOSE_MARKET。"""
        h = _Harness({"instructions": [
            {"instId": "UNI-USDT-SWAP", "action": "CLOSE_MARKET", "confidence": 99, "reason": "x"}],
        }, {})          # OKX 字典为空 = 与真机"持仓 OKX 0/9｜跨所 1 笔"同形
        got_raw = h.run(position_mgmt.execute_ai_position_management)
        self.addCleanup(h.cleanup)
        self.assertEqual(len(got_raw), 1, "必须留痕，不得静默")
        self.assertIn("UNI", got_raw[0])
        self.assertIn("CLOSE_MARKET", got_raw[0])
        self.assertEqual(h.close_calls, [], "外所仓不得被本路径平掉（能力未接线）")

    def test_unknown_position_update_sl_is_reported_not_silent(self):
        """UPDATE_SL 同样（且更危险：AI 以为收紧了止损，实际什么都没发生）。"""
        h = _Harness({"instructions": [
            {"instId": "DOGE-USDT-SWAP", "action": "UPDATE_SL", "confidence": 99,
             "suggested_sl_price": 1.0, "reason": "x"}], }, {})
        raw = h.run(position_mgmt.execute_ai_position_management)
        self.addCleanup(h.cleanup)
        self.assertEqual(len(raw), 1)
        self.assertIn("UPDATE_SL", raw[0])
        self.assertEqual(h.okx.amend_calls, [], "不得改单")

    def test_update_sl_rejected_when_not_tightening(self):
        """放松止损 / 空间不足 → 拒绝，且**不得触达交易所**。"""
        h = _Harness({"instructions": [{"instId": "BTC-USDT-SWAP", "action": "UPDATE_SL",
                                        "suggested_sl_price": 50, "confidence": 90, "reason": "放松"}]},
                     {"BTC-USDT-SWAP": {"posSide": "long", "pos": 1, "avgPx": 100, "markPx": 101,
                                        "atr_1h": 1.0}})
        got, exp, g_env, e_env = self._both(h)
        self.assertEqual(got, exp)
        self.assertEqual(g_env, e_env)
        # 拒绝会留下一条留痕（不是静默丢弃），但不许触达交易所
        self.assertEqual(len(got), 1, f"应恰有一条拒绝留痕，实际 {got}")
        self.assertIn("拒绝过早收紧止损", got[0])
        self.assertEqual(h.okx.pending_calls, [], "拒绝的指令不得查询/改单")
        self.assertEqual(h.okx.amend_calls, [], "拒绝的指令不得改单")

    def test_update_sl_accepted_when_tightening(self):
        h = _Harness({"instructions": [{"instId": "BTC-USDT-SWAP", "action": "UPDATE_SL",
                                        "suggested_sl_price": 116, "confidence": 90, "reason": "提损"}]},
                     {"BTC-USDT-SWAP": {"posSide": "long", "pos": 1, "avgPx": 100, "markPx": 120,
                                        "atr_1h": 2.0}},
                     algo_orders=[{"state": "live", "posSide": "long", "slTriggerPx": "110",
                                   "algoId": "A1"}])
        got, exp, g_env, e_env = self._both(h)
        self.assertEqual(got, exp, "文案分叉")
        self.assertEqual(g_env, e_env, "副作用分叉")
        self.assertTrue(any("云端止损收紧至" in a for a in got), f"实际 {got}")

    def test_update_sl_query_exception_is_fail_closed(self):
        h = _Harness({"instructions": [{"instId": "BTC-USDT-SWAP", "action": "UPDATE_SL",
                                        "suggested_sl_price": 116, "confidence": 90, "reason": "提损"}]},
                     {"BTC-USDT-SWAP": {"posSide": "long", "pos": 1, "avgPx": 100, "markPx": 120,
                                        "atr_1h": 2.0}},
                     pending_raises=RuntimeError("network"))
        got, exp, g_env, e_env = self._both(h)
        self.assertEqual(got, exp)
        self.assertEqual(g_env, e_env)
        self.assertTrue(any("查询异常" in a for a in got), f"实际 {got}")
        self.assertEqual(h.okx.amend_calls, [], "查询异常时绝不改单")

    def test_update_sl_no_live_algo_is_fail_closed(self):
        h = _Harness({"instructions": [{"instId": "BTC-USDT-SWAP", "action": "UPDATE_SL",
                                        "suggested_sl_price": 116, "confidence": 90, "reason": "提损"}]},
                     {"BTC-USDT-SWAP": {"posSide": "long", "pos": 1, "avgPx": 100, "markPx": 120,
                                        "atr_1h": 2.0}},
                     algo_orders=[])
        got, exp, _, _ = self._both(h)
        self.assertEqual(got, exp)
        self.assertTrue(any("未找到真实云端止损单" in a for a in got), f"实际 {got}")

    def test_amend_failure_is_reported(self):
        h = _Harness({"instructions": [{"instId": "BTC-USDT-SWAP", "action": "UPDATE_SL",
                                        "suggested_sl_price": 116, "confidence": 90, "reason": "提损"}]},
                     {"BTC-USDT-SWAP": {"posSide": "long", "pos": 1, "avgPx": 100, "markPx": 120,
                                        "atr_1h": 2.0}},
                     algo_orders=[{"state": "live", "posSide": "long", "slTriggerPx": "110",
                                   "algoId": "A1"}],
                     amend_ok=False)
        got, exp, g_env, e_env = self._both(h)
        self.assertEqual(got, exp)
        self.assertEqual(g_env, e_env)
        self.assertTrue(any("更新失败" in a for a in got), f"实际 {got}")

    def test_short_side_uses_short_tightening_rule(self):
        h = _Harness({"instructions": [{"instId": "ETH-USDT-SWAP", "action": "UPDATE_SL",
                                        "suggested_sl_price": 84, "confidence": 90, "reason": "提损"}]},
                     {"ETH-USDT-SWAP": {"posSide": "short", "pos": 1, "avgPx": 100, "markPx": 80,
                                        "atr_1h": 2.0}},
                     algo_orders=[{"state": "live", "posSide": "short", "slTriggerPx": "90",
                                   "algoId": "A2"}])
        got, exp, g_env, e_env = self._both(h)
        self.assertEqual(got, exp)
        self.assertEqual(g_env, e_env)
        self.assertTrue(any("云端止损收紧至" in a for a in got), f"实际 {got}")

    def test_non_okx_venue_routes_through_amend_venue_stop_loss(self):
        """多所路径必须走 `amend_venue_stop_loss`，且用 `venue` 字段选路。"""
        calls = []

        class _VR:
            def get_adapter(self, venue, environment=None):
                calls.append(("get_adapter", venue))
                return "ADAPTER"

        def _amend(ad, nm, side, sl, sz):
            calls.append(("amend", ad, nm, side, round(sl, 4), round(sz, 4)))
            return True, "ok"

        h = _Harness({"instructions": [{"instId": "BTC-USDT-SWAP", "action": "UPDATE_SL",
                                        "suggested_sl_price": 116, "confidence": 90, "reason": "提损"}]},
                     {"BTC-USDT-SWAP": {"posSide": "long", "pos": 1, "avgPx": 100, "markPx": 120,
                                        "atr_1h": 2.0, "venue": "binance"}})
        self.addCleanup(h.cleanup)
        actions = []
        with patch("r20_backend.close_intent.adapter_environment", lambda v, m: "demo"):
            h.positions["BTC-USDT-SWAP"]["venue"] = "binance"
            position_mgmt.execute_ai_position_management(
                h.positions, h.trackers, "T", actions,
                ai_position_management_file=str(h.path),
                ai_tightens_stop=aft.ai_tightens_stop,
                close_position_confirmed=h.close,
                okx_rest=h.okx,
                venue_registry=_VR(),
                current_environment=lambda: type("E", (), {"mode": "demo"})(),
                amend_venue_stop_loss=_amend)
        self.assertIn(("get_adapter", "binance"), calls, "多所路径未按 venue 选适配器")
        self.assertTrue(any(c[0] == "amend" for c in calls), "未调用 amend_venue_stop_loss")
        self.assertEqual(h.okx.pending_calls, [], "多所路径不得走 OKX 直下")


def _read_instr(path):
    return json.loads(path.read_text(encoding="utf-8"))["instructions"]


def _read_ts(path):
    return json.loads(path.read_text(encoding="utf-8"))["timestamp"]


class InjectionContractTest(unittest.TestCase):
    def test_file_path_and_close_seam_are_read_at_call_time(self):
        """门面上 patch 的两个缝都必须生效，且**绝不触达真实交易所**。

        这条最早踩过坑：只 patch 文件路径时，`CLOSE_MARKET` 会走到门面真实的
        `close_position_confirmed`（生产实现会打 OKX 接口 —— 实测返回
        `OKX 51023: Position doesn't exist.`）。测试不得触网/触所，
        故两个缝一起 patch，并断言"读到的是临时文件里的指令"。
        """
        h = _Harness({"instructions": [{"instId": "BTC-USDT-SWAP", "action": "CLOSE_MARKET",
                                        "confidence": 99, "reason": "r"}]},
                     {"BTC-USDT-SWAP": {"posSide": "long", "pos": 1, "markPx": 100}})
        self.addCleanup(h.cleanup)
        with patch.object(aft, "AI_POSITION_MANAGEMENT_FILE", str(h.path)), \
             patch.object(aft, "close_position_confirmed", h.close):
            actions = []
            aft.execute_ai_position_management(h.positions, h.trackers, "T", actions)
        self.assertTrue(any("整仓退出" in a for a in actions),
                        f"未读到 patch 的文件路径，实际 {actions}")
        self.assertEqual(len(h.close_calls), 1, "未走 patch 的 close 实现")
        self.assertEqual(h.close_calls[0][0], "BTC-USDT-SWAP")


if __name__ == "__main__":
    unittest.main()

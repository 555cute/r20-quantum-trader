"""QQ / 网关通知发布（`scripts/qq_notifier.py`）的残余分支收口 —— 第 327 刀。

本模块 380 行，是**所有对外通知的唯一出口**（开仓 / 平仓 / 移损 / 拦截 / 熔断 /
日报 / 自进化报告）。它本身不联网 —— 只把结构化事件交给 `r20_gateway.publisher`
持久化入队，因此**"返回 True = 已持久入队"而非"已同步送达"**（`send_qq_message`
的 docstring 明说此事）。

## 本刀立住的三条纪律

1. **通知绝不阻断交易主流程**：`_publish` 吞掉一切异常并返回 `False` ——
   网关挂了不能让一次平仓记录丢失。
2. **标的格式化不硬编码 `-SWAP`**：按场所约定输出（币安 `BTCUSDT 永续` /
   Gate `BTC_USDT 永续` / OKX `BTC-USDT-SWAP`）。
3. **平仓状态标签四态互斥且顺序敏感**：分批止盈 → 保本 → 盈利 → 风控止损，
   `is_partial_exit` 与 `is_be` 的判定**先于** `is_win`（否则 +0.01 U 会被报成"盈利落袋"）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.qq_notifier as qn  # noqa: E402


class _CapturePublish:
    """记录每次 `publish(...)` 的调用，并可注入异常。"""

    def __init__(self, *, exc=None):
        self.calls: list = []
        self.exc = exc

    def __call__(self, event_type, title, message, payload=None, priority=50):
        self.calls.append({"event_type": event_type, "title": title,
                           "message": message, "payload": payload,
                           "priority": priority})
        if self.exc is not None:
            raise self.exc


class _NotifierSandbox(unittest.TestCase):
    def setUp(self):
        self.capture = _CapturePublish()
        p = patch.object(qn, "publish", self.capture)
        p.start()
        self.addCleanup(p.stop)

    @property
    def call(self):
        assert len(self.capture.calls) == 1, self.capture.calls
        return self.capture.calls[0]


# ───────────────────── _publish ─────────────────────
class PublishHelperTests(_NotifierSandbox, unittest.TestCase):
    def test_a_successful_publish_returns_true(self):
        self.assertTrue(qn._publish("evt", "标题", "正文"))

    def test_the_default_priority_is_fifty(self):
        qn._publish("evt", "标题", "正文")
        self.assertEqual(self.call["priority"], 50)

    def test_a_gateway_failure_is_swallowed_and_returns_false(self):
        # ★ 第 18/19 行 —— 通知绝不阻断交易主流程
        self.capture.exc = RuntimeError("gateway down")
        self.assertFalse(qn._publish("evt", "标题", "正文"))

    def test_any_exception_type_is_swallowed(self):
        for exc in (OSError("disk"), ValueError("bad"), KeyError("k"),
                    TypeError("t")):
            with self.subTest(exc=type(exc).__name__):
                self.capture.exc = exc
                self.assertFalse(qn._publish("evt", "标题", "正文"))

    def test_the_payload_is_forwarded(self):
        qn._publish("evt", "标题", "正文", {"a": 1}, priority=7)
        self.assertEqual(self.call["payload"], {"a": 1})
        self.assertEqual(self.call["priority"], 7)


# ───────────────────── 标的格式化 ─────────────────────
class FormatSymbolTests(unittest.TestCase):
    def test_an_okx_id_stays_okx_shaped(self):
        self.assertEqual(qn._format_symbol("BTC-USDT-SWAP"), "BTC-USDT-SWAP")

    def test_binance_uses_the_binance_convention(self):
        self.assertEqual(qn._format_symbol("BTC-USDT-SWAP", "binance"), "BTCUSDT 永续")

    def test_gate_uses_the_gate_convention(self):
        self.assertEqual(qn._format_symbol("BTC-USDT-SWAP", "gate"), "BTC_USDT 永续")

    def test_the_venue_match_is_case_insensitive_and_substring_based(self):
        for venue in ("BINANCE", "Binance", "binance-us"):
            with self.subTest(venue=venue):
                self.assertEqual(qn._format_symbol("BTC-USDT-SWAP", venue), "BTCUSDT 永续")

    def test_a_bare_symbol_is_normalised(self):
        for raw in ("BTC", "BTCUSDT"):
            with self.subTest(raw=raw):
                self.assertEqual(qn._format_symbol(raw), "BTC-USDT-SWAP")

    def test_a_lowercase_swap_id_is_not_cleaned_at_all(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：第 27 行的四次 `.replace(...)` 都是
        #   **大小写敏感**的，而 `.upper()` 在**它们之后**才执行 ⇒ 小写输入一路不被清洗，
        #   最后被当成"币种名"再套一层后缀：
        #     `"btc-usdt-swap"` → `"btc-usdt-swap"` → upper → `"BTC-USDT-SWAP"`
        #     → `f"{clean}-USDT-SWAP"` ⇒ `"BTC-USDT-SWAP-USDT-SWAP"`
        #   调用方若传小写（REST 响应里 instId 常见小写形态）就会得到双后缀。
        self.assertEqual(qn._format_symbol("btc-usdt-swap"), "BTC-USDT-SWAP-USDT-SWAP")

    def test_the_underscore_form_keeps_a_residual_underscore(self):
        # ⚠️ 同族缺陷（与 `sync_full_ledger._other_venue_live_positions:570` 一字不差）：
        #   `.replace("USDT","").replace("_USDT","")` —— 前一步已摘掉 `USDT`，
        #   后一步的 `_USDT` 再也匹配不上 ⇒ `"BTC_USDT"` → `"BTC_"`。
        self.assertEqual(qn._format_symbol("BTC_USDT"), "BTC_-USDT-SWAP")

    def test_an_uppercase_swap_id_is_cleaned_correctly(self):
        # 对照：大写输入正常（说明缺陷只在大小写/下划线两种形态上）
        self.assertEqual(qn._format_symbol("BTC-USDT-SWAP"), "BTC-USDT-SWAP")

    def test_an_empty_symbol_returns_the_unknown_placeholder(self):
        # ★ 第 25/26 行
        for raw in ("", None, "   "):
            with self.subTest(raw=raw):
                self.assertEqual(qn._format_symbol(raw), "UNKNOWN-SWAP")

    def test_the_default_venue_is_okx(self):
        self.assertTrue(qn._format_symbol("BTC", "").endswith("-USDT-SWAP"))


class SendQqMessageTests(_NotifierSandbox, unittest.TestCase):
    def test_it_publishes_a_generic_notification(self):
        # ★ 第 36–38 行
        self.assertTrue(qn.send_qq_message("系统就绪"))
        self.assertEqual(self.call["event_type"], "notification.generic")
        self.assertEqual(self.call["message"], "系统就绪")
        self.assertEqual(self.call["priority"], 50)


# ───────────────────── 开仓通知 ─────────────────────
class TradeOpenTests(_NotifierSandbox, unittest.TestCase):
    def _open(self, **over):
        kw = {"inst": "BTC-USDT-SWAP", "side": "多", "sz": 5, "px": 100.0,
              "strategy": "低吸", "reason": "回踩不破"}
        kw.update(over)
        return qn.notify_trade_open(**kw)

    def test_a_long_open_publishes_the_trade_opened_event(self):
        self.assertTrue(self._open())
        self.assertEqual(self.call["event_type"], "trade.opened")
        self.assertIn("🟢 多单 BUY", self.call["message"])
        self.assertIn("BTC-USDT-SWAP", self.call["message"])

    def test_a_short_side_is_detected_from_the_english_spelling(self):
        self._open(side="SELL_SHORT")
        self.assertIn("🔴 空单 SELL", self.call["message"])

    def test_the_venue_name_is_upper_cased_in_the_header(self):
        self._open(venue="binance")
        self.assertIn("执行交易所：BINANCE", self.call["message"])
        self.assertIn("BTCUSDT 永续", self.call["message"])

    def test_a_policy_version_suppresses_the_strategy_line(self):
        self._open(policy_version="v7.9.2")
        self.assertIn("🏷️ 策略版本：v7.9.2", self.call["message"])
        self.assertNotIn("🎯 触发策略", self.call["message"])

    def test_the_strategy_line_is_used_without_a_policy_version(self):
        self._open()
        self.assertIn("🎯 触发策略：低吸", self.call["message"])

    def test_the_council_line_joins_role_and_confidence(self):
        self._open(council_role="技术席", confidence=88.5)
        self.assertIn("技术席提案 · 置信度 88.5%", self.call["message"])

    def test_the_council_line_works_with_only_a_role(self):
        self._open(council_role="宏观席")
        self.assertIn("宏观席提案", self.call["message"])

    def test_the_council_line_works_with_only_a_confidence(self):
        self._open(confidence=70.0)
        self.assertIn("置信度 70.0%", self.call["message"])

    def test_an_explicit_margin_is_used_when_positive(self):
        self._open(margin_usdt=25.5)
        self.assertIn("保证金 25.50 U", self.call["message"])

    def test_a_zero_margin_falls_back_to_the_estimate(self):
        # `margin_usdt and margin_usdt > 0` ⇒ 0 是 falsy ⇒ 走预估分支
        self._open(margin_usdt=0, sz=5, px=100.0, leverage=5)
        self.assertIn("预估保证金", self.call["message"])

    def test_the_notional_is_shown_when_positive(self):
        self._open(notional_usdt=1234.5)
        self.assertIn("货值 ~1234.5 U", self.call["message"])

    def test_the_long_rr_is_auto_deduced(self):
        self._open(px=100.0, tp_px=110.0, sl_px=95.0)
        self.assertIn("几何盈亏比：2.00 R", self.call["message"])

    def test_the_short_rr_is_auto_deduced(self):
        # ★ 第 110/111 行 —— (px - tp) / (sl - px) = (100-90)/(105-100) = 2.0
        self._open(side="空", px=100.0, tp_px=90.0, sl_px=105.0)
        self.assertIn("几何盈亏比：2.00 R", self.call["message"])

    def test_an_explicit_rr_string_is_used_verbatim(self):
        self._open(px=100.0, tp_px=110.0, sl_px=95.0, rr_ratio="N/A")
        self.assertIn("几何盈亏比：N/A", self.call["message"])

    def test_an_explicit_numeric_rr_is_formatted(self):
        self._open(px=100.0, tp_px=110.0, sl_px=95.0, rr_ratio=3)
        self.assertIn("几何盈亏比：3.00 R", self.call["message"])

    def test_no_rr_line_without_both_brackets(self):
        self._open(px=100.0)
        self.assertNotIn("几何盈亏比", self.call["message"])

    def test_the_long_tp1_is_auto_calculated(self):
        self._open(px=100.0, tp_px=110.0, sl_px=95.0)
        self.assertIn("首批止盈 (TP1", self.call["message"])

    def test_the_short_tp1_is_auto_calculated(self):
        # ★ 第 124–128 行
        self._open(side="空", px=100.0, tp_px=90.0, sl_px=105.0)
        self.assertIn("首批止盈 (TP1", self.call["message"])
        self.assertIn("(TP1 · 50%仓位)", self.call["message"])

    def test_an_explicit_tp1_wins(self):
        self._open(px=100.0, tp_px=110.0, sl_px=95.0, tp1_px=105.0)
        self.assertIn("105.0", self.call["message"])

    def test_a_non_positive_tp1_is_treated_as_absent(self):
        # `tp1_px is None or tp1_px <= 0` ⇒ 0 与负数都触发自动计算
        for bad in (0, -1):
            with self.subTest(bad=bad):
                self.capture.calls.clear()
                self._open(px=100.0, tp_px=110.0, sl_px=95.0, tp1_px=bad)
                self.assertIn("首批止盈 (TP1", self.call["message"])

    def test_the_scale_out_ratio_labels_the_first_batch(self):
        self._open(px=100.0, tp_px=110.0, sl_px=95.0, scale_out_ratio=0.30)
        self.assertIn("(TP1 · 30%仓位)", self.call["message"])

    def test_a_zero_scale_out_ratio_falls_back_to_fifty(self):
        # `if scale_out_ratio else 50`
        self._open(px=100.0, tp_px=110.0, sl_px=95.0, scale_out_ratio=0)
        self.assertIn("(TP1 · 50%仓位)", self.call["message"])

    def test_the_hard_stop_loss_line_shows_the_percentage(self):
        self._open(px=100.0, sl_px=95.0)
        self.assertIn("云端硬止损 (SL)：95.0 (-5.00%)", self.call["message"])

    def test_the_market_regime_is_shown(self):
        self._open(market_regime="趋势上行")
        self.assertIn("🌐 宏观体制：趋势上行", self.call["message"])

    def test_unknown_kwargs_are_tolerated(self):
        # `**kwargs: Any` —— 调用方加字段不许炸
        self.assertTrue(self._open(future_field=123))

    def test_the_payload_carries_the_structured_fields(self):
        self._open(venue="gate", leverage=7)
        self.assertEqual(self.call["payload"]["venue"], "gate")
        self.assertEqual(self.call["payload"]["instrument"], "BTC-USDT-SWAP")

    def test_a_publish_failure_returns_false(self):
        self.capture.exc = RuntimeError("boom")
        self.assertFalse(self._open())


# ───────────────────── 平仓通知 ─────────────────────
class TradeCloseTests(_NotifierSandbox, unittest.TestCase):
    def _close(self, **over):
        kw = {"inst": "BTC-USDT-SWAP", "pnl": 10.0, "stage": "止盈",
              "exit_px": 110.0}
        kw.update(over)
        return qn.notify_trade_close(**kw)

    def test_a_profit_close_is_tagged_as_profit(self):
        self._close()
        self.assertIn("🎉 【盈利落袋】", self.call["title"])

    def test_a_loss_close_is_tagged_as_stop_loss(self):
        # ★ 第 217–220 行
        self._close(pnl=-10.0, stage="止损")
        self.assertIn("🛡️ 【风控止损】", self.call["title"])
        self.assertIn("-10.0000 USDT", self.call["message"])

    def test_a_near_zero_result_is_tagged_as_breakeven(self):
        # ★ 第 210–212 行 —— |net| < 0.05
        self._close(pnl=0.01, stage="平仓")
        self.assertIn("⚖️ 【保本结清】", self.call["title"])
        self.assertIn("保本移损退出", self.call["message"])

    def test_an_exact_zero_is_breakeven(self):
        self._close(pnl=0.0)
        self.assertIn("⚖️ 【保本结清】", self.call["title"])

    def test_a_breakeven_stage_forces_the_breakeven_tag(self):
        # 金额不为零但 stage 含"保本" ⇒ 仍按保本报
        self._close(pnl=5.0, stage="保本移损")
        self.assertIn("⚖️ 【保本结清】", self.call["title"])

    def test_the_breakeven_boundary_is_exclusive(self):
        # |net| == 0.05 **不**算保本（`< 0.05`）
        self._close(pnl=0.05)
        self.assertIn("🎉 【盈利落袋】", self.call["title"])

    def test_a_partial_exit_is_tagged_as_scale_out(self):
        # ★ 第 206–209 行 —— 判定先于 is_be/is_win
        self._close(pnl=0.01, stage="分批止盈")
        self.assertIn("🎉 【阶梯止盈 TP1 达成】", self.call["title"])
        self.assertIn("首批 50% 利润落袋", self.call["message"])

    def test_the_partial_flag_alone_triggers_the_scale_out_tag(self):
        self._close(pnl=0.01, is_partial=True)
        self.assertIn("【阶梯止盈 TP1 达成】", self.call["title"])

    def test_a_partial_exit_dominates_over_breakeven(self):
        # 顺序敏感：is_partial_exit 在 is_be 之前
        self._close(pnl=0.0, stage="首批止盈")
        self.assertIn("【阶梯止盈 TP1 达成】", self.call["title"])

    def test_the_roi_is_appended_when_provided(self):
        self._close(roi_pct=12.34)
        self.assertIn("(+12.34%)", self.call["message"])

    def test_the_loss_roi_uses_a_plain_sign(self):
        # 第 219 行 —— 亏损侧不用 `+` 前缀
        self._close(pnl=-5.0, roi_pct=-3.5)
        self.assertIn("(-3.50%)", self.call["message"])

    def test_a_missing_roi_omits_the_suffix(self):
        self._close(roi_pct=None)
        self.assertNotIn("(%)", self.call["message"])

    def test_the_fee_deduction_is_shown_as_a_breakdown(self):
        # 第 239/240 行
        self._close(pnl=10.0, fee=0.5, net_pnl=None)
        self.assertIn("到手净利", self.call["message"])
        self.assertIn("交易手续费: -0.5000 U", self.call["message"])

    def test_an_explicit_net_pnl_overrides_the_fee_math(self):
        self._close(pnl=10.0, fee=0.5, net_pnl=7.0)
        self.assertIn("+7.0000 USDT", self.call["message"])

    def test_a_zero_fee_uses_the_plain_settlement_line(self):
        self._close(pnl=10.0, fee=0.0)
        self.assertIn("结算收益", self.call["message"])

    def test_the_side_line_is_only_shown_when_provided(self):
        self._close(side="空")
        self.assertIn("🔴 空单", self.call["message"])
        self.capture.calls.clear()
        self._close()
        self.assertNotIn("持仓方向", self.call["message"])

    def test_the_entry_price_is_shown_beside_the_exit(self):
        self._close(entry_px=100.0)
        self.assertIn("开仓均价: 100.0", self.call["message"])

    def test_a_zero_entry_price_omits_the_comparison(self):
        self._close(entry_px=0)
        self.assertIn("🏁 退出价格：110.0", self.call["message"])
        self.assertNotIn("开仓均价", self.call["message"])

    def test_the_duration_line_is_optional(self):
        self._close(duration_str="2时30分")
        self.assertIn("⏱️ 持仓时长：2时30分", self.call["message"])

    def test_a_partial_exit_explains_the_follow_up_protection(self):
        self._close(is_partial=True)
        self.assertIn("剩余 50% 仓位已自动收紧至保本止损位", self.call["message"])

    def test_a_non_okx_venue_prefixes_the_title(self):
        self._close(venue="binance")
        self.assertIn("[BINANCE] ", self.call["title"])

    def test_okx_does_not_prefix_the_title(self):
        self._close()
        self.assertNotIn("[OKX]", self.call["title"])

    def test_the_title_reports_the_signed_net_pnl(self):
        self._close(pnl=10.0, fee=0.5)
        self.assertIn("盈亏: +9.50 U", self.call["title"])

    def test_the_priority_is_high(self):
        self._close()
        self.assertEqual(self.call["priority"], 95)


# ───────────────────── 移损通知 ─────────────────────
class SlUpdatedTests(_NotifierSandbox, unittest.TestCase):
    def _sl(self, **over):
        kw = {"inst": "BTC-USDT-SWAP", "side": "多", "old_sl": 95.0, "new_sl": 100.0}
        kw.update(over)
        return qn.notify_sl_updated(**kw)

    def test_the_move_is_reported_with_an_arrow(self):
        self._sl()
        self.assertIn("止损上移：95.0 ➔ 100.0", self.call["message"])

    def test_the_current_price_line_shows_the_profit_percentage(self):
        # ★ 第 293/294 行
        self._sl(cur_px=105.0, profit_pct=5.0)
        self.assertIn("📈 当前市价：105.0 (+5.00%)", self.call["message"])

    def test_the_current_price_line_without_a_percentage(self):
        self._sl(cur_px=105.0, profit_pct=None)
        self.assertIn("📈 当前市价：105.0", self.call["message"])
        self.assertNotIn("(+", self.call["message"])

    def test_a_zero_current_price_omits_the_line(self):
        self._sl(cur_px=0, profit_pct=5.0)
        self.assertNotIn("当前市价", self.call["message"])

    def test_the_side_label_handles_the_english_spelling(self):
        self._sl(side="short")
        self.assertIn("🔴 空单", self.call["message"])

    def test_the_default_reason_mentions_the_breakeven_move(self):
        self._sl()
        self.assertIn("浮盈达标，启动保本移损锁死胜率", self.call["message"])

    def test_the_event_type_and_priority(self):
        self._sl()
        self.assertEqual(self.call["event_type"], "trade.sl_updated")
        self.assertEqual(self.call["priority"], 85)

    def test_a_non_okx_venue_prefixes_the_title(self):
        self._sl(venue="gate")
        self.assertIn("[GATE] ", self.call["title"])


# ───────────────────── 拦截 / 熔断 / 简报 / 复盘 ─────────────────────
class InterceptorBlockedTests(_NotifierSandbox, unittest.TestCase):
    def _blk(self, **over):
        kw = {"inst": "BTC-USDT-SWAP", "action": "BUY_LONG",
              "interceptor_name": "POSITION_CAP", "reason": "超出同向上限"}
        kw.update(over)
        return qn.notify_interceptor_blocked(**kw)

    def test_a_long_proposal_is_labelled(self):
        self._blk()
        self.assertIn("🟢 追多", self.call["message"])
        self.assertIn("POSITION_CAP", self.call["message"])

    def test_a_short_proposal_is_labelled(self):
        self._blk(action="SELL_SHORT")
        self.assertIn("🔴 追空", self.call["message"])

    def test_the_fail_closed_stance_is_stated(self):
        self._blk()
        self.assertIn("Fail-Closed 强制降级观望", self.call["message"])

    def test_the_event_type_and_priority(self):
        self._blk()
        self.assertEqual(self.call["event_type"], "risk.interceptor_blocked")
        self.assertEqual(self.call["priority"], 70)


class CircuitBreakerNoticeTests(_NotifierSandbox, unittest.TestCase):
    def test_the_critical_level_is_stated(self):
        qn.notify_circuit_breaker("交易所挤兑", "命中高危词汇")
        self.assertIn("CRITICAL 宏观异动", self.call["message"])
        self.assertIn("触发事件：交易所挤兑", self.call["message"])
        self.assertIn("详细成因：命中高危词汇", self.call["message"])

    def test_the_priority_is_the_highest(self):
        qn.notify_circuit_breaker("x", "y")
        self.assertEqual(self.call["event_type"], "risk.triggered")
        self.assertEqual(self.call["priority"], 100)


class DailySummaryTests(_NotifierSandbox, unittest.TestCase):
    def test_the_briefing_event_is_published(self):
        # ★ 第 361–363 行
        self.assertTrue(qn.notify_daily_summary("今日无交易"))
        self.assertEqual(self.call["event_type"], "briefing.ready")
        self.assertEqual(self.call["message"], "今日无交易")
        self.assertEqual(self.call["priority"], 40)


class EvolutionReportTests(_NotifierSandbox, unittest.TestCase):
    def test_the_sample_and_winrate_are_reported(self):
        # ★ 第 366–380 行
        self.assertTrue(qn.notify_evolution_report(62.5, 48, "收紧入场", "等待确认"))
        self.assertIn("复盘样本：最近 48 笔实盘平仓", self.call["message"])
        self.assertIn("样本胜率: 62.5%", self.call["message"])
        self.assertIn("演进方向：收紧入场", self.call["message"])
        self.assertIn("提炼心法：等待确认", self.call["message"])

    def test_the_title_and_payload(self):
        qn.notify_evolution_report(55.0, 20, "s", "l")
        self.assertEqual(self.call["event_type"], "evolution.completed")
        self.assertEqual(self.call["title"], "🧬 【AI 大脑自进化完成】胜率 55.0%")
        self.assertEqual(self.call["payload"], {"winrate": 55.0, "total_trades": 20})

    def test_the_priority_is_sixty(self):
        qn.notify_evolution_report(55.0, 20, "s", "l")
        self.assertEqual(self.call["priority"], 60)


class CrossCuttingTests(_NotifierSandbox, unittest.TestCase):
    """每个入口都要有「网关挂了也不抛、只返回 False」的同款保证。"""

    def test_every_entry_point_returns_false_when_the_gateway_is_down(self):
        self.capture.exc = RuntimeError("gateway down")
        entries = [
            lambda: qn.send_qq_message("t"),
            lambda: qn.notify_trade_open("BTC-USDT-SWAP", "多", 1, 100.0, "s", "r"),
            lambda: qn.notify_trade_close("BTC-USDT-SWAP", 1.0, "止盈", 101.0),
            lambda: qn.notify_sl_updated("BTC-USDT-SWAP", "多", 95.0, 100.0),
            lambda: qn.notify_interceptor_blocked("BTC-USDT-SWAP", "BUY_LONG", "I", "r"),
            lambda: qn.notify_circuit_breaker("e", "r"),
            lambda: qn.notify_daily_summary("s"),
            lambda: qn.notify_evolution_report(1.0, 1, "s", "l"),
        ]
        for i, entry in enumerate(entries):
            with self.subTest(entry=i):
                self.assertFalse(entry(), "通知失败绝不许抛给交易主流程")

    def test_the_module_imports_without_touching_the_network(self):
        # 本模块只做结构化入队，不自己联网
        src = Path(qn.__file__).read_text(encoding="utf-8")
        for forbidden in ("urllib.request", "requests.get", "requests.post", "socket."):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()

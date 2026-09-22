"""投委会 `/test` 推演：**上下文只许来自真实快照，缺什么就写"不可用"**（第二百五十一刀）。

先打印目标行再动笔。审计 P1-4c 的事故记录（注释原文）：旧实现在这里**整套编造**
（固定时间 2026-09-05、可用资金 1450U、BTC 多单 77200、挂单 ord_10283），而且按
`factor_data[sym]` 取值 —— 真实文件结构是 `{timestamp,time_str,instruments[]}`，
于是**每个字段都落到 fallback**（现价 $0、ADX 22.5、ATR 0）。而 UI 写着
「投委会正在全息审阅资金与行情并组织交易员辩论」。现在：**只用真实快照，缺什么就写"不可用"，不再编任何数字**。

| 语义 | 口径 |
|---|---|
| ★ 管理员给了演练文本 | `source="manual_mock"` + note，**完全不读**任何快照（`missing` 必为空）|
| ★ **"没有" ≠ "读不到"** | 因子快照**文件不存在** ⇒ 静默（不记 `missing`），文案「—（因子快照不可用）」；**读取抛错**才记 `missing`。持仓/挂单同理：`[]` ⇒ 「（当前无持仓）」，`None` ⇒ 记 `missing` + 「—（…不可用）」|
| ★ 如实回传 | 成功与失败**都**带 `market_context`（来源与缺口）；辩论抛错 ⇒ `status="error"`（**不冒泡**）|
| 渲染 | 只渲染前 8 条；非 dict 元素跳过；标的行含现价/ADX/RSI/ATR/CMF/聪明钱 |
| 兜底提示词 | 没有模块布局时用固定兜底（「你是 R20 Quantum Trader 首席量化官…」）|
"""

import json
import types
import unittest
from unittest import mock

from r20_backend.routers.strategy import council as C
from r20_backend.schemas import CouncilTestRequest


class CouncilTestRouteTest(unittest.TestCase):
    def setUp(self):
        self.debate_kwargs = {}
        for name in ("require_admin_header",):
            p = mock.patch.object(C, name, mock.Mock(), create=True)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("r20_backend.council_manager.load_council_config",
                       return_value={"timeout_seconds": 120.0, "roles": {}})
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("scripts.prompt_library.active_profile", return_value={"pipelines": {}})
        p.start()
        self.addCleanup(p.stop)
        self.saved_cache = None

    def _debate(self, **kw):
        self.debate_kwargs = dict(kw)
        return ({"decisions": []}, {"council_mode": True})

    def _run(self, *, mock_prompt=None, root=None, cache=None, debate=None):
        p = mock.patch.object(C, "ROOT", root) if root is not None else mock.patch.object(
            C, "ROOT", C.ROOT)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("r20_backend.council_manager.execute_council_debate",
                       side_effect=debate or self._debate)
        p.start()
        self.addCleanup(p.stop)
        saved = __import__("r20_backend.dashboard_cache", fromlist=["x"]).CACHE_DATA
        self.addCleanup(setattr, __import__("r20_backend.dashboard_cache", fromlist=["x"]),
                        "CACHE_DATA", saved)
        __import__("r20_backend.dashboard_cache", fromlist=["x"]).CACHE_DATA = cache
        payload = CouncilTestRequest(**({"mock_market_prompt": mock_prompt}
                                        if mock_prompt else {}))
        return C.admin_test_council_debate(payload, x_r20_session="t")

    def _temp_root(self, snapshot=None):
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "data").mkdir(parents=True, exist_ok=True)
        if snapshot is not None:
            (root / "data" / "factor_library_snapshot.json").write_text(
                snapshot if isinstance(snapshot, str) else json.dumps(snapshot),
                encoding="utf-8")
        return root

    # ── 管理员显式文本 ────────────────────────────────────
    def test_manual_mock_never_touches_any_snapshot(self):
        out = self._run(mock_prompt="我自己写的行情", root=self._temp_root(),
                        cache=None)
        ctx = out["market_context"]
        self.assertEqual(ctx["source"], "manual_mock")
        self.assertEqual(ctx["missing"], [], "给了演练文本就**不读**任何快照")
        self.assertEqual(ctx["note"], "管理员显式提供的演练文本")
        self.assertEqual(self.debate_kwargs["market_prompt"], "我自己写的行情")

    # ── "没有" ≠ "读不到" ────────────────────────────────
    def test_missing_factor_file_is_not_an_error_but_says_unavailable(self):
        out = self._run(root=self._temp_root(), cache={"account": {}})
        ctx = out["market_context"]
        self.assertEqual(ctx["factor_time"], None)
        self.assertEqual(ctx["instruments"], 0)
        self.assertNotIn("因子快照读取失败", " ".join(ctx["missing"]),
                         "**文件不存在**是「没有」，不记缺口")
        prompt = self.debate_kwargs["market_prompt"]
        self.assertIn("—（因子快照不可用）", prompt)
        self.assertIn("禁止臆造行情", prompt)

    def test_corrupt_factor_file_is_recorded_as_a_gap(self):
        out = self._run(root=self._temp_root(snapshot="{ 坏 json"), cache={"account": {}})
        ctx = out["market_context"]
        self.assertTrue(any("因子快照读取失败" in m for m in ctx["missing"]),
                        "**读不到**才是缺口，要如实登记")

    def test_real_snapshot_is_rendered_field_by_field(self):
        snap = {"time_str": "2026-09-21 10:00:00",
                "instruments": [{"instId": "BTC-USDT-SWAP", "price": 70000, "chg24h": 1.5,
                                 "trend_momentum": {"adx_1h": 30, "rsi_14": 60,
                                                    "trend_regime": "多头"},
                                 "volatility_channel": {"atr_1h": 500},
                                 "volume_money_flow": {"cmf_1h": 0.2},
                                 "smart_money_derivatives": {"weighted_long_pct": 55}}]}
        out = self._run(root=self._temp_root(snapshot=snap), cache={"account": {}})
        ctx = out["market_context"]
        self.assertEqual(ctx["factor_time"], "2026-09-21 10:00:00")
        self.assertEqual(ctx["instruments"], 1)
        prompt = self.debate_kwargs["market_prompt"]
        for token in ("BTC-USDT-SWAP", "70000", "ADX=30", "RSI=60", "ATR=500", "CMF=0.2",
                      "55%"):
            self.assertIn(token, prompt)

    # ── dashboard 缓存三态 ───────────────────────────────
    def test_no_cache_at_all_is_recorded(self):
        out = self._run(root=self._temp_root(), cache=None)
        self.assertIn("dashboard 缓存不可用", out["market_context"]["missing"])

    def test_absent_sections_are_gaps_but_empty_lists_are_just_empty(self):
        """★★ 本刀最重要的一条：**「读不到」与「没有」文案必须不同**。"""
        # ⚠️ 我第一版给了 `positions: []` 却断言它被记为**缺口** —— 自相矛盾：
        # 空列表恰恰是「**没有**」（下一条用例管这个）。要测「读不到」，必须**不给这些键**。
        out = self._run(root=self._temp_root(), cache={"account": {}})
        ctx = out["market_context"]
        for gap in ("账户快照", "持仓快照", "挂单快照"):
            self.assertIn(gap, ctx["missing"], "缺字段 ⇒ 记为缺口")
        prompt = self.debate_kwargs["market_prompt"]
        self.assertIn("—（账户快照不可用）", prompt)
        self.assertIn("—（持仓快照不可用）", prompt)
        self.assertIn("—（挂单快照不可用）", prompt)

    def test_empty_lists_render_as_no_holdings_not_as_unavailable(self):
        out = self._run(root=self._temp_root(),
                        cache={"account": {"avail_eq": 1000, "total_eq": 1200},
                               "positions": [], "pending_orders": []})
        ctx = out["market_context"]
        self.assertNotIn("持仓快照", ctx["missing"], "空列表 = **没有**，不是读不到")
        self.assertNotIn("挂单快照", ctx["missing"])
        prompt = self.debate_kwargs["market_prompt"]
        self.assertIn("（当前无持仓）", prompt)
        self.assertIn("（当前无挂单）", prompt)
        self.assertIn("1000", prompt, "有账户就写真实数字")

    def test_rows_are_truncated_and_non_dict_entries_skipped(self):
        positions = [{"instId": f"I{i}", "venue": "okx", "posSide": "long", "pos": i,
                      "upl": 0} for i in range(10)] + ["坏行"]
        out = self._run(root=self._temp_root(),
                        cache={"account": {"avail_eq": 1}, "positions": positions,
                               "pending_orders": []})
        prompt = self.debate_kwargs["market_prompt"]
        self.assertIn("共 11 笔", prompt)
        self.assertNotIn("I8", prompt, "只渲染前 8 条")
        self.assertIn("I7", prompt)

    # ── 成功 / 失败都回传 context ─────────────────────────
    def test_success_returns_transcript_and_context(self):
        out = self._run(root=self._temp_root(), cache={"account": {}})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(sorted(out), ["brain_output", "market_context", "status",
                                       "transcript"])

    def test_debate_failure_returns_error_status_not_an_exception(self):
        """★ 辩论炸了 ⇒ `status="error"` + **仍然**带上 `market_context`（不冒泡成 500）。"""
        def _boom(**kw):
            raise RuntimeError("引擎炸了")
        out = self._run(root=self._temp_root(), cache={"account": {}}, debate=_boom)
        self.assertEqual(out["status"], "error")
        self.assertIn("引擎炸了", out["error"])
        self.assertIn("missing", out["market_context"], "失败也要说清上下文来源")

    def test_runtime_context_uses_the_trading_side_names(self):
        """★ 审计 P1-4d：席位提示词变量用与**交易侧同名**的上下文渲染。"""
        self._run(root=self._temp_root(), cache={"account": {}})
        ctx = self.debate_kwargs["runtime_context"]
        self.assertEqual(sorted(ctx), ["market_matrix", "profile_name", "trading_memory"])
        self.assertEqual(self.debate_kwargs["timeout"], 120.0, "超时取配置里的值")


if __name__ == "__main__":
    unittest.main()

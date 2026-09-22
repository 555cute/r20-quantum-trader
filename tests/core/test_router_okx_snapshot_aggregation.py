"""`/api/v1/admin/okx/account-snapshot` 的**多所聚合**（第二百四十八刀）。

先打印实现再动笔（403-500）。要点：

| 语义 | 口径 |
|---|---|
| ★ **所失败显式化（审计 C6）** | OKX 段抛错 ⇒ 记进 `venue_errors["okx"]`，**不再抹平为"完整"**；`OKXNotConfigured` 例外 ⇒ **503**（fail-closed）|
| ★ **逐所互不牵连** | binance / gate 各自 `try` ⇒ 一所失败只影响它自己那一格 `venue_errors` |
| ★ **fail-closed 兜底** | `env.configured` 为假且三所都空 ⇒ **503**，detail 写明「V5 直签是唯一私有通道（fail-closed，无 CLI 回退）」|
| ★ **令牌钉住凭证（审计 B3）** | 平仓意图创建时带 `credential_fingerprint`（`_current_credential_fp(venue, adapter_env)`）—— 换过凭证的旧令牌不可复用 |
| ★ **保证金口径** | 优先交易所给的 `margin`；缺失时用 `notional / leverage` 推（**两者都来自交易所实况**）；**都拿不到就是 0** —— 注释原话：「前端据此回落显示原生张数，**不显示捏造的数字**」|
| 方向/零仓 | `size_signed` 符号定 `long`/`short`；`|amt| < 1e-12` 或空 symbol ⇒ 跳过（不产生平仓意图）|
| 标注 | 挂单 `setdefault("venue", ...)`（已有 venue 的不覆盖）|
"""

import types
import unittest
from unittest import mock

from fastapi import HTTPException

from r20_backend.routers import exchanges as R


def _env(configured=True, mode="demo", fingerprint="fp"):
    return types.SimpleNamespace(configured=configured, mode=mode, fingerprint=fingerprint)


class SnapshotAggregationTest(unittest.TestCase):
    def setUp(self):
        self.intents = []
        for name in ("require_admin_header",):
            p = mock.patch.object(R, name, mock.Mock(), create=True)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("scripts.okx_runtime.current_environment", return_value=_env())
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("r20_backend.close_intent.adapter_environment", return_value="demo")
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("r20_backend.close_intent._current_credential_fp",
                       return_value="cred-fp")
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("r20_backend.close_intent.create",
                       side_effect=self._intent)
        p.start()
        self.addCleanup(p.stop)

    def _intent(self, **kw):
        self.intents.append(kw)
        return ("tok-" + kw["venue"], "CONFIRM " + kw["venue"])

    def _okx(self, payload=None, exc=None):
        def _fn():
            if exc is not None:
                raise exc
            return payload or {"positions": [], "orders": []}
        p = mock.patch.object(R, "app_attr", lambda name, default: _fn, create=True)
        p.start()
        self.addCleanup(p.stop)

    def _adapters(self, adapters):
        p = mock.patch("r20_backend.exchanges.get_adapter",
                       side_effect=lambda v, environment=None: adapters[v])
        p.start()
        self.addCleanup(p.stop)

    def _call(self):
        try:
            return R.admin_okx_account_snapshot(x_r20_admin_token="t"), None
        except HTTPException as exc:
            return None, exc

    # ── 所失败显式化 / fail-closed ─────────────────────────────
    def test_okx_failure_is_recorded_not_smoothed_over(self):
        self._okx(exc=RuntimeError("OKX 51001"))
        self._adapters({"binance": types.SimpleNamespace(),
                        "gate": types.SimpleNamespace()})
        out, exc = self._call()
        self.assertIsNone(exc, "OKX 失败不该让整个接口炸（除非是未配置）")
        self.assertIn("okx", out["venue_errors"])
        self.assertIn("51001", out["venue_errors"]["okx"])

    def test_okx_not_configured_is_fail_closed_503(self):
        # ⚠️ 正解：类来自 `scripts.okx_rest`（模块顶部 `from scripts.okx_rest import OKXNotConfigured`）。
        # 我第一次改成 `import r20_backend.okx_rest` —— **那个模块根本不存在**（第三次栽在
        # "没确认导入路径就写进用例"上）。这次先打印模块导入块，再照抄它的来源。
        from scripts.okx_rest import OKXNotConfigured as not_configured
        self._okx(exc=not_configured("没配"))
        self._adapters({"binance": types.SimpleNamespace(),
                        "gate": types.SimpleNamespace()})
        _out, exc = self._call()
        self.assertIsNotNone(exc)
        self.assertEqual(exc.status_code, 503)

    def test_unconfigured_and_empty_is_fail_closed_503(self):
        p = mock.patch("scripts.okx_runtime.current_environment",
                       return_value=_env(configured=False))
        p.start()
        self.addCleanup(p.stop)
        self._okx()
        self._adapters({"binance": types.SimpleNamespace(),
                        "gate": types.SimpleNamespace()})
        _out, exc = self._call()
        self.assertIsNotNone(exc, "没有私有通道又没有仓位/挂单 ⇒ 必须拒绝，不能假装完整")
        self.assertEqual(exc.status_code, 503)
        self.assertIn("唯一私有通道", exc.detail)

    def test_one_venue_failing_does_not_hide_another(self):
        self._okx()
        bad = types.SimpleNamespace(positions=lambda: (_ for _ in ()).throw(
            RuntimeError("binance 崩了")), open_orders=lambda: [])
        good = types.SimpleNamespace(
            positions=lambda: [{"size_signed": 1, "symbol": "ETH-USDT-SWAP"}],
            open_orders=lambda: [])
        self._adapters({"binance": bad, "gate": good})
        out, _exc = self._call()
        self.assertIn("binance", out["venue_errors"], "失败的那所要留痕")
        self.assertNotIn("gate", out["venue_errors"], "另一所不受牵连")
        self.assertTrue(any(p["venue"] == "gate" for p in out["positions"]),
                        "gate 的仓位照样出现在结果里")

    # ── 保证金口径（金句）────────────────────────────────────
    def _single_position(self, pos):
        self._okx()
        self._adapters({"binance": types.SimpleNamespace(positions=lambda: [pos],
                                                         open_orders=lambda: []),
                        "gate": types.SimpleNamespace(positions=lambda: [],
                                                      open_orders=lambda: [])})
        out, _exc = self._call()
        return out["positions"][0]

    def test_margin_from_the_exchange_wins(self):
        row = self._single_position({"size_signed": 2, "symbol": "BTC", "margin": 12.5})
        self.assertEqual(row["margin"], 12.5)

    def test_margin_is_derived_from_notional_over_leverage(self):
        row = self._single_position({"size_signed": 2, "symbol": "BTC",
                                     "notional": 1000.0, "leverage": 20})
        self.assertEqual(row["margin"], 50.0, "名义额/杠杆（两个数都来自交易所实况）")

    def test_margin_is_zero_when_nothing_is_known(self):
        """★ 都拿不到 ⇒ **0**（前端据此回落显示原生张数，**不显示捏造的数字**）。"""
        row = self._single_position({"size_signed": 2, "symbol": "BTC"})
        self.assertEqual(row["margin"], 0, "不许编造保证金")

    def test_direction_and_symbol_normalisation(self):
        row = self._single_position({"size_signed": -3, "symbol": "ETH-USDT-SWAP"})
        self.assertEqual(row["posSide"], "short", "负号 ⇒ 空头")
        self.assertEqual(row["instId"], "ETH-USDT-SWAP")
        # ⚠️ 我原以为这里是 "3" —— 实测是 **"3.0"**（`str(abs(float))`）。字段是**字符串**，
        # 所以断言按数值语义写，避免把"格式化细节"当成契约。
        self.assertEqual(float(row["pos"]), 3.0, "仓位大小取绝对值（方向另有字段）")
        self.assertIsInstance(row["pos"], str, "协议上是字符串字段")

    def test_close_token_is_pinned_to_the_credential_fingerprint(self):
        """★ 审计 B3：令牌必须钉住**凭证身份**，换过凭证的旧令牌不可复用。"""
        self._single_position({"size_signed": 1, "symbol": "BTC"})
        self.assertEqual(len(self.intents), 1)
        kw = self.intents[0]
        self.assertEqual(kw["credential_fingerprint"], "cred-fp")
        self.assertEqual(kw["environment"], "demo")
        self.assertEqual(kw["expected_size"], 1)
        self.assertEqual(kw["pos_side"], "long")

    def test_zero_size_and_blank_symbol_are_skipped(self):
        self._okx()
        self._adapters({"binance": types.SimpleNamespace(
            positions=lambda: [{"size_signed": 0, "symbol": "BTC"},
                               {"size_signed": 1, "symbol": ""}],
            open_orders=lambda: []),
            "gate": types.SimpleNamespace(positions=lambda: [], open_orders=lambda: [])})
        out, _exc = self._call()
        self.assertEqual(out["positions"], [], "零仓与空标的都不产生仓位")
        self.assertEqual(self.intents, [], "也不产生平仓意图")

    def test_orders_are_tagged_without_overwriting_an_existing_venue(self):
        self._okx()
        self._adapters({"binance": types.SimpleNamespace(
            positions=lambda: [], open_orders=lambda: [{"id": 1}, {"id": 2, "venue": "别处"}]),
            "gate": types.SimpleNamespace(positions=lambda: [], open_orders=lambda: [])})
        out, _exc = self._call()
        by_id = {o["id"]: o for o in out["orders"]}
        self.assertEqual(by_id[1]["venue"], "binance")
        self.assertEqual(by_id[2]["venue"], "别处", "已有 venue 的不覆盖")

    # ── OKX 段的 imr 推导 ────────────────────────────────────
    def test_okx_margin_is_derived_when_imr_is_missing(self):
        self._okx({"positions": [{"instId": "BTC-USDT-SWAP", "notionalUsd": 900.0,
                                  "lever": 30}], "orders": []})
        self._adapters({"binance": types.SimpleNamespace(),
                        "gate": types.SimpleNamespace()})
        out, _exc = self._call()
        row = out["positions"][0]
        self.assertEqual(row["margin"], 30.0, "900/30")
        self.assertEqual(row["venue"], "okx")


if __name__ == "__main__":
    unittest.main()

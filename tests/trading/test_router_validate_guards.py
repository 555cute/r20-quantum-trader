"""开仓前置校验与「跨所敞口不可核算 ⇒ fail-closed」分支（第二百零九刀）。

覆盖率探针发现 `open_protected_position` 里这几条**在发单之前**的守卫从未被执行：

- 决策缺 `asset` ⇒ `validate` 拒绝；
- 数值字段非数字 ⇒ `validate` 拒绝；
- `margin`/`leverage`/`entry` 非有限正数 ⇒ `validate` 拒绝；
- 跨所敞口的持仓**读不到** ⇒ 不是"当作没有敞口"，而是**拒开**（fail-closed），
  并写明统计了哪些场所、哪些没计及原因。
"""

import math
import unittest
from unittest.mock import patch

from r20_backend import execution_router as router


class _Ad:
    """最小适配器桩：只提供 `open_protected_position` 前置校验会读到的属性。"""

    environment = "demo"

    class capabilities:
        venue = "gate"

    def positions(self):
        return []


class OpenPositionValidateGuardTest(unittest.TestCase):
    def setUp(self):
        # 校验发生在 require_execution / 所池门禁**之前**，但仍统一桩掉，避免用例受环境开关影响
        self._p1 = patch.object(router, "require_execution", lambda *a, **k: None)
        self._p2 = patch.object(router, "_load_venue_pool_soft", lambda v: {})
        self._p1.start(); self._p2.start()
        self.addCleanup(self._p1.stop); self.addCleanup(self._p2.stop)
        self.ad = _Ad()

    def _decide(self, **kw):
        base = {"asset": "BTC", "action": "BUY_LONG", "venue": "gate",
                "margin_usdt": 100, "leverage": 5, "entry_price": 70000,
                "take_profit_price": 72000, "stop_loss_price": 68000}
        base.update(kw)
        return base

    def test_missing_asset_is_rejected_at_validate(self):
        r = router.open_protected_position(self._decide(asset=""), adapter=self.ad)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "validate")
        self.assertIn("缺少 asset", r["detail"], f"缺币种必须在校验阶段就拒绝：{dict(r)}")

    def test_non_numeric_fields_are_rejected(self):
        for field in ("margin_usdt", "leverage", "entry_price", "take_profit_price", "stop_loss_price"):
            with self.subTest(field=field):
                r = router.open_protected_position(self._decide(**{field: "不是数字"}), adapter=self.ad)
                self.assertFalse(r["ok"], f"{field} 非数字必须拒绝")
                self.assertEqual(r["stage"], "validate")
                self.assertIn("数值字段非法", r["detail"])

    def test_non_positive_or_non_finite_numbers_are_rejected(self):
        bad = [0, -1, float("inf"), float("nan")]
        for field in ("margin_usdt", "leverage", "entry_price"):
            for value in bad:
                with self.subTest(field=field, value=value):
                    r = router.open_protected_position(self._decide(**{field: value}), adapter=self.ad)
                    self.assertFalse(r["ok"], f"{field}={value} 必须拒绝（有限正数是硬前提）")
                    self.assertEqual(r["stage"], "validate")
                    self.assertIn("有限正数", r["detail"])

    def test_baseline_decision_passes_validation(self):
        """非真空自检：上面几条用的是**同一个**合法决策、只改一个字段 ⇒ 必须证明
        合法决策**不会**停在校验阶段（否则那几条可能只是"什么决策都被拒"的假阳）。

        这里让敞口闸门直接返回失败来"截停"流程：能走到 `exposure` 阶段 ⇒ 说明校验放行了。
        """
        with patch.object(router, "_check_total_exposure",
                          lambda **k: router._fail("exposure", "截停：只看是否走过校验")):
            r = router.open_protected_position(self._decide(), adapter=self.ad)
        self.assertEqual(r["stage"], "exposure",
                         f"合法决策必须过校验、走到敞口阶段：{dict(r)}")


class CrossVenueExposureFailClosedTest(unittest.TestCase):
    """跨所敞口核算：读不到就**拒开**（不是"当作没有"），并留痕统计范围。"""

    def setUp(self):
        self._p1 = patch.object(router, "require_execution", lambda *a, **k: None)
        self._p2 = patch.object(router, "_load_venue_pool_soft", lambda v: {})
        self._p1.start(); self._p2.start()
        self.addCleanup(self._p1.stop); self.addCleanup(self._p2.stop)

    def test_unreadable_other_venue_is_fail_closed_with_a_trace(self):
        class _Boom:
            environment = "demo"

            def positions(self):
                raise RuntimeError("positions down")

        # ⚠️ 不依赖环境常量：套件运行环境里 `TOTAL_EXPOSURE_CAP` 实测为 0（=闸门不启用），
        # 而本判据只在 cap>0 时才存在 ⇒ 显式给一个正值，让"读不到就拒开"这条路径真的被走到。
        with patch.object(router, "TOTAL_EXPOSURE_CAP", 3000.0), \
             patch.object(router, "_exposure_venues",
                          lambda venue, env: (["gate", "binance"], ["okx(凭证未配置)"])), \
             patch.object(router, "get_adapter",
                          lambda v, environment=None: _Ad() if v == "gate" else _Boom()):
            r = router.open_protected_position(
                {"asset": "BTC", "action": "BUY_LONG", "venue": "gate", "environment": "demo",
                 "margin_usdt": 100, "leverage": 5, "entry_price": 70000,
                 "take_profit_price": 72000, "stop_loss_price": 68000})
        self.assertFalse(r["ok"], "读不到别的场所持仓 ⇒ 必须拒开（宁可不开，不可超敞口）")
        self.assertEqual(r["stage"], "exposure")
        self.assertIn("跨所敞口不可核算", r["detail"], f"要指名是哪一所读不到：{dict(r)}")
        self.assertIn("fail-closed", r["detail"])
        # 留痕：风控说"跨所"，就必须让人看得见"跨"到了哪几所（这里一所都没数成 ⇒ 如实为空，
        # 不谎报成"数过了"）。⚠️ 已知限制：读失败发生在缓存写入**之前** ⇒ `skipped` 也一并丢失，
        # detail 里退化成 `统计范围=—`（宁可显示"没数成"，也不显示假的场所清单）。
        self.assertIn("统计范围", r["detail"])
        self.assertEqual(r.get("counted_venues"), [], f"一所都没数成时不许谎报：{dict(r)}")
        self.assertEqual(r.get("skipped_venues"), [])

    def test_over_cap_reports_counted_and_skipped_venues(self):
        """敞口**真的超限**时的留痕：统计了哪几所、哪些没计及原因（都要报出来）。"""
        with patch.object(router, "TOTAL_EXPOSURE_CAP", 1.0), \
             patch.object(router, "_exposure_venues",
                          lambda venue, env: (["gate", "binance"], ["okx(凭证未配置)"])), \
             patch.object(router, "get_adapter",
                          lambda v, environment=None: _Ad()):
            r = router.open_protected_position(
                {"asset": "BTC", "action": "BUY_LONG", "venue": "gate", "environment": "demo",
                 "margin_usdt": 100, "leverage": 5, "entry_price": 70000,
                 "take_profit_price": 72000, "stop_loss_price": 68000})
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "exposure")
        self.assertIn("gate", r.get("counted_venues") or [], f"数成的场所必须报出：{dict(r)}")
        self.assertTrue(any("okx" in x for x in (r.get("skipped_venues") or [])),
                        f"未计场所与原因必须留痕：{dict(r)}")
        self.assertIn("未计=", r["detail"])


if __name__ == "__main__":
    unittest.main()

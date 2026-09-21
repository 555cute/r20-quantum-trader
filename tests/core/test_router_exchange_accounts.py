"""交易所读接口：**读不到 ≠ 没有**，且**名字必须说真话**（第二百二十一刀）。

| 语义 | 口径 |
|---|---|
| ★ 未知账户 | `_venue_account_unknown` 把四个账户字段与同步时间**全部置 `None`**（**不是 0**）——0 会被读成「账户是空的」 |
| ★ 名字即语义 | 时间字段名是 `last_sync_ms`（**值一直是毫秒**）；旧名 `last_sync_ts` **在说谎**，已弃用（第一百六十九刀）|
| 三态分明 | `positions`：未配置凭证 ⇒ **503 + 人话 NOT READY**；读取失败 ⇒ **502**；成功 ⇒ 带 `source` 的载荷 |
| 鉴权 | 先过 `require_admin_header`（未过不会触碰交易所）|
"""

import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from r20_backend.routers import exchanges as R

FIELDS = ("equity", "available", "positions_count", "open_orders_count")


class VenueAccountUnknownTest(unittest.TestCase):
    def test_every_account_field_is_none_not_zero(self):
        """★ 读不到 ⇒ `None`：0 会被下游读成「账户是空的」，那是**另一个事实**。"""
        out = R._venue_account_unknown("unknown", "凭证缺失")
        self.assertEqual(out["status"], "unknown")
        self.assertEqual(out["reason"], "凭证缺失")
        for field in FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, out)
                self.assertIsNone(out[field], f"{field} 读不到必须是 None，不是 0")
        self.assertIsNone(out["last_sync_ms"])

    def test_the_time_field_is_named_ms_and_the_lying_name_is_gone(self):
        """★ 第一百六十九刀：值一直是**毫秒**，旧名 `last_sync_ts` 在**说谎** ⇒ 必须不存在。"""
        out = R._venue_account_unknown("unknown", "x")
        self.assertIn("last_sync_ms", out)
        self.assertNotIn("last_sync_ts", out, "旧名不得再出现（名字即语义）")

    def test_helper_covers_exactly_the_declared_field_set(self):
        out = R._venue_account_unknown("unknown", "x")
        for field in R._VENUE_ACCOUNT_FIELDS:
            self.assertIn(field, out)
        self.assertEqual(set(out) - set(R._VENUE_ACCOUNT_FIELDS),
                         {"status", "reason", "last_sync_ms"},
                         "输出只多出状态、原因与同步时间三个元字段")

    def test_listing_env_map_knows_the_venue_specific_naming(self):
        """沙箱档位**各所叫法不同**（gate 是 sandbox，另两所是 demo）—— 混用会把实盘当沙箱。"""
        self.assertEqual(R._LISTING_ENV_MAP["gate"]["demo"], "sandbox")
        self.assertEqual(R._LISTING_ENV_MAP["okx"]["demo"], "demo")
        self.assertEqual(R._LISTING_ENV_MAP["binance"]["live"], "live")


class PositionsRouteTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(R, "require_admin_header")
        self.auth = p.start()
        self.addCleanup(p.stop)

    def _env(self, configured):
        return type("E", (), {"configured": configured, "mode": "demo"})()

    def test_unconfigured_credentials_are_503_with_a_human_sentence(self):
        with patch("scripts.okx_runtime.current_environment", return_value=self._env(False)):
            with self.assertRaises(HTTPException) as ctx:
                R.positions(None)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("NOT READY", ctx.exception.detail)
        self.assertIn("API Key", ctx.exception.detail, "要告诉运维缺什么")

    def test_read_failure_is_502_not_503(self):
        """★ 三态分明：**读失败（502）** 与 **没配置（503）** 不是一回事。"""
        fake_okx = MagicMock()
        fake_okx.positions.side_effect = RuntimeError("网关超时")
        with patch("scripts.okx_runtime.current_environment", return_value=self._env(True)), \
             patch("r20_backend.dependencies.okx", fake_okx):
            with self.assertRaises(HTTPException) as ctx:
                R.positions(None)
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("OKX account request failed", ctx.exception.detail)

    def test_not_configured_exception_maps_to_503(self):
        from scripts.okx_rest import OKXNotConfigured
        fake_okx = MagicMock()
        fake_okx.positions.side_effect = OKXNotConfigured("三件套不全")
        with patch("scripts.okx_runtime.current_environment", return_value=self._env(True)), \
             patch("r20_backend.dependencies.okx", fake_okx):
            with self.assertRaises(HTTPException) as ctx:
                R.positions(None)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.detail, "三件套不全", "原始说明直接给出去")

    def test_success_payload_carries_positions_and_source(self):
        fake_okx = MagicMock()
        fake_okx.positions.return_value = [{"instId": "BTC-USDT-SWAP"}]
        with patch("scripts.okx_runtime.current_environment", return_value=self._env(True)), \
             patch("r20_backend.dependencies.okx", fake_okx):
            out = R.positions(None)
        self.assertEqual(out["positions"], [{"instId": "BTC-USDT-SWAP"}])
        self.assertEqual(out["source"], "OKX REST", "数据来源必须标明")
        self.assertTrue(self.auth.called, "先过鉴权")


if __name__ == "__main__":
    unittest.main()

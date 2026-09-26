"""注册/返佣通道的**公开只读**接口（2026-09）。

## 这个门在防什么

这三条邀请链接原先**硬编码在前端** `dashboard/AboutModal.vue` 里，与后端
`r20_backend/config.py` 的 `okx_invite_url` / `gate_invite_url` 是两份各说各话的
字面量。后果不是"显示错了"，而是**"用环境变量把通道换成自己的"这个能力，在用户
唯一看得见的那一处完全失效** —— 后端改了，界面照旧显示旧链接（Binance 更是压根
没出现在界面上）。

现在唯一事实源是后端：`GET /api/v1/referral-channels`。本文件钉住四件事：

1. 它**不需要任何鉴权**（用户看的看板是公开的，带鉴权就等于没人看得见）；
2. 三条通道**都出得来**（含此前界面上缺失的 Binance）；
3. `code` 是从链接里**抽**出来的展示短码，抽不到就空串 —— 不编造；
4. 环境变量覆盖**真的生效**（这正是上一版失效的那条）。

⚠️ 与订单上的经纪商 `tag` 是两件事：`tag` 随每笔订单发出、与用户是否点过这些
链接**无关**；本接口这几条只是给用户**开户**的入口。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import r20_backend.app as app_module  # noqa: E402
import r20_backend.routers.system as system_router  # noqa: E402


class InviteCodeTest(unittest.TestCase):
    """短码抽取是纯函数，逐形状钉住。"""

    def test_query_param_wins_over_path_tail(self):
        """币安把码放在 `?ref=`：路径末段是 `CPA`（无意义），必须以查询参数为准。"""
        self.assertEqual(
            system_router._invite_code(
                "https://www.bsmkweb.cc/activity/referral-entry/CPA?ref=CPA_00N8UVQ2OG"),
            "CPA_00N8UVQ2OG")

    def test_path_tail_is_used_when_no_query(self):
        self.assertEqual(
            system_router._invite_code("https://www.mitxcqvwnhj.com/join/48039151"),
            "48039151")
        self.assertEqual(
            system_router._invite_code("https://www.gatesites.net/share/MCHDBKYF"),
            "MCHDBKYF")

    def test_degenerate_inputs_yield_empty_not_garbage(self):
        """抽不到就空串（前端据此只显示链接），**绝不编造**一个码出来。"""
        for url in ("", "   ", "https://example.com/", "https://example.com/promo.html",
                    "not-a-url"):
            self.assertEqual(system_router._invite_code(url), "", url)


class ReferralChannelsApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)

    def test_is_public_no_auth_required(self):
        """用户看的看板是公开的 ⇒ 这个接口不能要鉴权，否则等于没人看得见。"""
        res = self.client.get("/api/v1/referral-channels")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn("detail", res.json())

    def test_three_channels_are_served_with_codes(self):
        body = self.client.get("/api/v1/referral-channels").json()
        keys = [c["key"] for c in body["channels"]]
        self.assertEqual(keys, ["okx", "gate", "binance"],
                         "三条都要出（Binance 此前在用户界面上完全缺失）")
        for ch in body["channels"]:
            if ch["invite_url"]:
                self.assertTrue(ch["code"], f"{ch['key']} 配了链接却没抽出短码")
                self.assertTrue(ch["invite_url"].startswith("http"), ch["invite_url"])

    def test_env_override_is_actually_honoured(self):
        """这条正是上一版失效的能力：覆盖必须**真的**反映到用户可见的那份数据。"""
        original = system_router.settings.okx_invite_url
        try:
            system_router.settings.okx_invite_url = "https://my-own.example/join/999888"
            with patch.object(system_router, "refresh_settings"):  # 别把 .env 读回来盖掉
                body = self.client.get("/api/v1/referral-channels").json()
            okx = next(c for c in body["channels"] if c["key"] == "okx")
            self.assertEqual(okx["invite_url"], "https://my-own.example/join/999888")
            self.assertEqual(okx["code"], "999888")
        finally:
            system_router.settings.okx_invite_url = original

    def test_unset_channel_is_reported_empty_not_broken(self):
        """没配的通道照常出条目、链接为空 —— 前端据此不渲染空卡片。"""
        original = system_router.settings.binance_invite_url
        try:
            system_router.settings.binance_invite_url = ""
            with patch.object(system_router, "refresh_settings"):
                body = self.client.get("/api/v1/referral-channels").json()
            binance = next(c for c in body["channels"] if c["key"] == "binance")
            self.assertEqual(binance["invite_url"], "")
            self.assertEqual(binance["code"], "")
        finally:
            system_router.settings.binance_invite_url = original


class AdminReferralChannelsApiTest(unittest.TestCase):
    """管理员版多给一个 OKX 经纪商 code；公开版**刻意不给**。

    存在的理由：后台 `SecurityPage` 曾把 code 与两条链接硬编码在模板里（第三份副本）。
    现由这里出值，且必须与**实发订单上的 tag 同源**。
    """

    def setUp(self):
        self.client = TestClient(app_module.app)

    def test_requires_admin_auth(self):
        """鉴权那一层单独钉：不带会话必须被拒。"""
        res = self.client.get("/api/v1/admin/referral-channels")
        self.assertIn(res.status_code, (401, 403), res.text)

    def test_broker_code_matches_the_wire_value(self):
        """展示用的 code 必须与**真正挂到订单上的那个值同源**（同一个函数）。

        否则就会出现"页面显示一个 code、订单带另一个" —— 而展示那份是给操作员
        用来核对订单的，漂移了就等于在骗自己。
        """
        import scripts.okx_rest as okx_rest
        # 本用例测的是**载荷语义**，不是鉴权（鉴权由上面那条单独钉）。
        # 故把鉴权闸替成放行 —— 免去在本用例里造管理员会话（那会去碰真库）。
        with patch.object(system_router, "require_admin_header", lambda *a, **k: None):
            body = self.client.get("/api/v1/admin/referral-channels").json()
        okx = next(c for c in body["channels"] if c["key"] == "okx")
        self.assertEqual(okx["broker_code"], okx_rest.effective_broker_tag())
        self.assertEqual(okx["broker_code"], okx_rest.DEFAULT_OKX_BROKER_TAG)

    def test_public_variant_deliberately_omits_the_broker_code(self):
        """公开版不该把经纪商 code 发出去（少一个公开面就少一分被冒用）。"""
        public = self.client.get("/api/v1/referral-channels").json()
        for ch in public["channels"]:
            self.assertNotIn("broker_code", ch, f"{ch['key']} 泄露了经纪商 code")


if __name__ == "__main__":
    unittest.main()

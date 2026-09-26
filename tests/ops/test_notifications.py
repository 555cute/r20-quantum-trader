import unittest
from unittest.mock import patch, MagicMock

from r20_backend import notifications


class NotificationsTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "R20_NOTIFY_WEBHOOK_ENABLED": "1",
            "R20_NOTIFICATION_WEBHOOK": "https://oapi.dingtalk.com/robot/send?access_token=mock",
            "R20_NOTIFY_WECHAT_ENABLED": "1",
            "R20_WECHAT_WEBHOOK": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=mock",
            "R20_NOTIFY_TELEGRAM_ENABLED": "1",
            "R20_TELEGRAM_BOT_TOKEN": "bot123456:mocktoken",
            "R20_TELEGRAM_CHAT_ID": "12345678",
            "R20_TELEGRAM_API_BASE": "https://custom-tg-proxy.example.com",
            "R20_NOTIFY_QQ_ENABLED": "1",
            "R20_QQ_APP_ID": "1905549905",
            "R20_QQ_CLIENT_SECRET": "mocksecret",
            "R20_QQ_OPENID": "MOCK_USER_OPENID",
        }

    def test_diagnose_ready_and_incomplete(self):
        diag = notifications.diagnose_channel("qq", self.env)
        self.assertEqual(diag["status"], "ready")

        incomplete_env = dict(self.env)
        incomplete_env["R20_QQ_OPENID"] = ""
        diag2 = notifications.diagnose_channel("qq", incomplete_env)
        self.assertEqual(diag2["status"], "incomplete")
        self.assertIn("自动获取 OpenID", diag2["detail"])

    def test_send_webhook_smart_payload_dingtalk(self):
        with patch("r20_backend.notifications.validate_outbound_url", return_value="https://oapi.dingtalk.com/robot/send?access_token=mock"), \
             patch("r20_backend.notifications._post_json", return_value=(True, "HTTP 200", {"errcode": 0})) as mock_post:
            ok, detail = notifications.send_channel("webhook", "钉钉测试消息", self.env)
            self.assertTrue(ok)
            self.assertIn("accepted", detail)
            args, _ = mock_post.call_args
            self.assertEqual(args[1], {"msgtype": "text", "text": {"content": "钉钉测试消息"}})

    def test_send_webhook_smart_payload_feishu(self):
        env = dict(self.env)
        env["R20_NOTIFICATION_WEBHOOK"] = "https://open.feishu.cn/open-apis/bot/v2/hook/mock"
        with patch("r20_backend.notifications.validate_outbound_url", return_value=env["R20_NOTIFICATION_WEBHOOK"]), \
             patch("r20_backend.notifications._post_json", return_value=(True, "HTTP 200", {"code": 0})) as mock_post:
            ok, detail = notifications.send_channel("webhook", "飞书测试消息", env)
            self.assertTrue(ok)
            args, _ = mock_post.call_args
            self.assertEqual(args[1], {"msg_type": "text", "content": {"text": "飞书测试消息"}})

    def test_send_webhook_smart_payload_discord(self):
        env = dict(self.env)
        env["R20_NOTIFICATION_WEBHOOK"] = "https://discord.com/api/webhooks/mock"
        with patch("r20_backend.notifications.validate_outbound_url", return_value=env["R20_NOTIFICATION_WEBHOOK"]), \
             patch("r20_backend.notifications._post_json", return_value=(True, "HTTP 200", {})) as mock_post:
            ok, detail = notifications.send_channel("webhook", "Discord消息", env)
            self.assertTrue(ok)
            args, _ = mock_post.call_args
            self.assertEqual(args[1], {"content": "Discord消息"})

    def test_send_telegram_uses_custom_api_base(self):
        # 审计修复A6后 telegram 也过 validate_outbound_url（与 webhook/wechat 对齐）；
        # 假域名按本文件既有惯例打恒等补丁，真实拒绝路径由批1审计回归测试覆盖。
        with patch("r20_backend.notifications.validate_outbound_url", side_effect=lambda u, **k: u), \
             patch("r20_backend.notifications._post_json", return_value=(True, "HTTP 200", {"ok": True, "result": {"message_id": 999}})) as mock_post:
            ok, detail = notifications.send_channel("telegram", "Telegram测试", self.env)
            self.assertTrue(ok)
            self.assertIn("accepted", detail)
            args, _ = mock_post.call_args
            self.assertTrue(args[0].startswith("https://custom-tg-proxy.example.com/botbot123456:mocktoken/sendMessage"))

    def test_send_qq_token_and_message_success(self):
        with patch("r20_backend.notifications._post_json") as mock_post:
            mock_post.side_effect = [
                (True, "HTTP 200", {"access_token": "valid_token_xyz"}),
                (True, "HTTP 200", {"id": "msg-12345"}),
            ]
            ok, detail = notifications.send_channel("qq", "QQ测试", self.env)
            self.assertTrue(ok)
            self.assertIn("accepted: id=msg-12345", detail)
            self.assertEqual(mock_post.call_count, 2)
            first_url = mock_post.call_args_list[0][0][0]
            second_url = mock_post.call_args_list[1][0][0]
            self.assertEqual(first_url, notifications.QQ_TOKEN_URL)
            self.assertIn("/v2/users/MOCK_USER_OPENID/messages", second_url)

    def test_send_qq_handles_11255_gracefully(self):
        with patch("r20_backend.notifications._post_json") as mock_post:
            mock_post.side_effect = [
                (True, "HTTP 200", {"access_token": "valid_token_xyz"}),
                (False, "HTTP 400 Bad Request", {"code": 11255, "message": "请求的资源不存在"}),
            ]
            ok, detail = notifications.send_channel("qq", "QQ测试", self.env)
            self.assertFalse(ok)
            self.assertIn("11255", detail)
            self.assertIn("自动获取 OpenID", detail)

    def test_send_webhook_dingtalk_with_secret_signature(self):
        env = dict(self.env)
        env["R20_DINGTALK_SECRET"] = "SEC_test_secret_key"
        with patch("r20_backend.notifications.validate_outbound_url", side_effect=lambda u, **k: u), \
             patch("r20_backend.notifications._post_json", return_value=(True, "HTTP 200", {"errcode": 0})) as mock_post:
            ok, detail = notifications.send_channel("webhook", "加签钉钉消息", env)
            self.assertTrue(ok)
            url_called, payload = mock_post.call_args[0]
            self.assertIn("&timestamp=", url_called)
            self.assertIn("&sign=", url_called)
            self.assertEqual(payload, {"msgtype": "text", "text": {"content": "加签钉钉消息"}})

    def test_send_webhook_feishu_with_secret_signature(self):
        env = dict(self.env)
        env["R20_NOTIFICATION_WEBHOOK"] = "https://open.feishu.cn/open-apis/bot/v2/hook/mock"
        env["R20_FEISHU_SECRET"] = "FS_test_secret_key"
        with patch("r20_backend.notifications.validate_outbound_url", side_effect=lambda u, **k: u), \
             patch("r20_backend.notifications._post_json", return_value=(True, "HTTP 200", {"code": 0})) as mock_post:
            ok, detail = notifications.send_channel("webhook", "加签飞书消息", env)
            self.assertTrue(ok)
            _, payload = mock_post.call_args[0]
            self.assertIn("timestamp", payload)
            self.assertIn("sign", payload)
            self.assertEqual(payload["msg_type"], "text")
            self.assertEqual(payload["content"]["text"], "加签飞书消息")

    def test_send_webhook_bark(self):
        env = dict(self.env)
        env["R20_NOTIFICATION_WEBHOOK"] = "https://api.day.app/mock-key/"
        with patch("r20_backend.notifications.validate_outbound_url", side_effect=lambda u, **k: u), \
             patch("r20_backend.notifications._post_json", return_value=(True, "HTTP 200", {})) as mock_post:
            ok, detail = notifications.send_channel("webhook", "Bark测试消息", env)
            self.assertTrue(ok)
            _, payload = mock_post.call_args[0]
            self.assertEqual(payload["body"], "Bark测试消息")
            self.assertEqual(payload["group"], "AstraQuant-Trade")

    def test_modern_notifier_double_tp_and_three_venues(self):
        import scripts.qq_notifier as notifier
        with patch("scripts.qq_notifier._publish", return_value=True) as mock_pub:
            # Test Binance multi-venue + double TP1/TP2
            res = notifier.notify_trade_open(
                inst="ETH",
                side="多",
                sz=10,
                px=3250.0,
                strategy="全维度波段强化版",
                reason="1H加速度突破",
                tp_px=3450.0,
                sl_px=3180.0,
                leverage=5,
                tp1_px=3320.0,
                scale_out_ratio=0.50,
                venue="binance",
                margin_usdt=325.0,
                rr_ratio=2.45,
                confidence=88.0,
                market_regime="单边主升",
                council_role="进攻官",
            )
            self.assertTrue(res)
            event_type, title, msg, payload = mock_pub.call_args[0][:4]
            self.assertEqual(event_type, "trade.opened")
            self.assertIn("[BINANCE]", title)
            self.assertIn("BINANCE", msg)
            self.assertIn("ETHUSDT 永续", msg)
            self.assertIn("首批止盈 (TP1 · 50%仓位)：3320.0", msg)
            self.assertIn("终极波段 (TP2 · 剩余仓位)：3450.0", msg)
            self.assertIn("几何盈亏比：2.45 R", msg)
            self.assertIn("保证金 325.00 U", msg)
            self.assertEqual(payload["tp1"], 3320.0)

            # Test legacy caller without tp1_px: auto-deduces TP1 & geometric RR
            res_legacy = notifier.notify_trade_open(
                inst="BTC",
                side="多",
                sz=2,
                px=90000.0,
                strategy="默认策略",
                reason="突破",
                tp_px=95000.0,
                sl_px=88000.0,
                leverage=3,
            )
            self.assertTrue(res_legacy)
            _, _, msg_leg, _ = mock_pub.call_args[0][:4]
            self.assertIn("首批止盈 (TP1 · 50%仓位)", msg_leg)
            self.assertIn("终极波段 (TP2 · 剩余仓位)：95000.0", msg_leg)
            self.assertIn("几何盈亏比：2.50 R", msg_leg)
            self.assertIn("预估保证金", msg_leg)

    def test_modern_notifier_partial_close_and_fees(self):
        import scripts.qq_notifier as notifier
        with patch("scripts.qq_notifier._publish", return_value=True) as mock_pub:
            res = notifier.notify_trade_close(
                inst="SOL",
                pnl=15.0,
                stage="首批分批止盈50%",
                exit_px=185.0,
                side="多",
                entry_px=175.0,
                fee=1.2,
                venue="gate",
                is_partial=True,
            )
            self.assertTrue(res)
            _, title, msg, payload = mock_pub.call_args[0][:4]
            self.assertIn("阶梯止盈 TP1 达成", title)
            self.assertIn("GATE", msg)
            self.assertIn("SOL_USDT 永续", msg)
            self.assertIn("到手净利", msg)
            self.assertIn("+13.8000 USDT", msg)
            self.assertIn("交易手续费: -1.2000 U", msg)
            self.assertIn("零风险放飞", msg)
            self.assertEqual(payload["pnl"], 13.8)

    def test_modern_notifier_interceptor_blocked(self):
        import scripts.qq_notifier as notifier
        with patch("scripts.qq_notifier._publish", return_value=True) as mock_pub:
            res = notifier.notify_interceptor_blocked(
                inst="DOGE",
                action="BUY_LONG",
                interceptor_name="4H 宏观顺势铁律",
                reason="4H 均线空头承压",
                venue="okx",
            )
            self.assertTrue(res)
            event_type, title, msg, payload = mock_pub.call_args[0][:4]
            self.assertEqual(event_type, "risk.interceptor_blocked")
            self.assertIn("物理硬风控拦截", title)
            self.assertIn("4H 宏观顺势铁律", msg)
            self.assertIn("4H 均线空头承压", msg)
            self.assertIn("Fail-Closed", msg)


if __name__ == "__main__":
    unittest.main()

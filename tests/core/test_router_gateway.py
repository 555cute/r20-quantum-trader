"""网关域路由（渠道/作业/通知）：**掩码回写=未改动、成败由子进程回执推出、按频道给具体原因**（第二百六十一刀，开新面 routers/gateway/）。

先打印 4 个子模块（`_shared` 14 + `channels` 102 + `gateway_ops` 140 + `notifications` 259 行）再动笔。
`backups.py`（501 行，含 tar 抽取安全）留作独立一刀。

| 语义 | 口径 |
|---|---|
| ★ **掩码回写 = 未改动** | `is_masked()` 命中的凭证字段在写回前被置 `None`（`readiness` 自然回退 `_env()`），**绝不把 `********` 落盘** |
| ★ **密钥进密文库、env 只留开关** | 整页 PUT 与 toggle 对称：`webhook_url`/`wechat_webhook`/`telegram_bot_token`/`qq_client_secret` 走 `save_secrets` 并从 env **拔除**；纯空串字段则 `remove_env` 后从 `env_update` 删除 |
| ★ **成败由回执推出** | `run_gateway_job`：`TimeoutExpired` ⇒ **504**+审计 `timeout`；退出码非 0 ⇒ **502**+审计 `failed`；只有 0 才 success。**调用了 ≠ 成功** |
| ★ **按频道给具体原因** | 开启但未就绪时不是笼统一句：QQ 分「缺 OpenID」与「缺 App ID/Secret」两种，微信/Webhook/Telegram 各自点名 |
| 确认短语 | `REPLAY {id}` / `SEND TEST {CHANNEL}` / `BACKUP` 类短语逐字校验 ⇒ 400 |
| 调度时间 | `HH:MM` 校验（非法 ⇒ 400），成功后**规范化补零 + 去重 + 排序** |

⚠️ 如实登记：`channels.toggle_channel` 第 98 行的兜底 400（"凭证或目标未配置完整"）在
`keys`/`readiness`/if-elif 链都是本地字面量且四频道全覆盖的前提下**结构上不可达** ——
只钉住可达的三条分支，不硬凑绿。
"""

import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from r20_backend.dependencies import ROOT as DEP_ROOT
from r20_backend.routers.gateway import _shared as SH
from r20_backend.routers.gateway import channels as CH
from r20_backend.routers.gateway import gateway_ops as GO
from r20_backend.routers.gateway import notifications as NT
from r20_backend.schemas import (
    ChannelToggleRequest,
    GatewayReplayRequest,
    NotificationConfigUpdate,
    NotificationScheduleUpdate,
    NotificationTestRequest,
    QQOpenIDCaptureStartRequest,
)


class SharedRootTests(unittest.TestCase):
    def test_root_comes_from_the_app_attr_seam(self):
        with mock.patch("r20_backend.routers.gateway._shared.app_attr",
                        return_value="/custom/root"):
            self.assertEqual(SH._get_root(), Path("/custom/root"))

    def test_root_falls_back_to_the_dependency_root(self):
        with mock.patch("r20_backend.routers.gateway._shared.app_attr",
                        side_effect=lambda name, default=None: default):
            self.assertEqual(SH._get_root(), Path(DEP_ROOT))


class _GatewayBase(unittest.TestCase):
    def _start(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher


class ChannelToggleTests(_GatewayBase):
    def setUp(self):
        self.audits = []
        self.secrets = []
        self.env_saved = []
        self.env_removed = []
        self.admin = mock.Mock()
        self._start(mock.patch.object(CH, "refresh_settings"))
        self._start(mock.patch.object(CH, "require_admin_header", self.admin))
        self._start(mock.patch.object(CH, "audit_record",
                                      lambda *a, **k: self.audits.append(a)))
        self._start(mock.patch.object(CH, "update_env",
                                      lambda d: self.env_saved.append(d)))
        self._start(mock.patch.object(CH, "remove_env",
                                      lambda d: self.env_removed.append(d)))
        self._start(mock.patch.object(CH, "save_secrets",
                                      lambda d: self.secrets.append(d)))
        # ⚠️ toggle_channel 里 `from r20_gateway.secrets import save_secrets` 是**局部导入**
        self._start(mock.patch("r20_gateway.secrets.save_secrets",
                               lambda d: self.secrets.append(d)))
        self.notify_env = mock.Mock(return_value={})
        self._start(mock.patch.object(CH, "notification_env", self.notify_env))

    def test_unknown_channel_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            CH.toggle_channel("slack", ChannelToggleRequest(enabled=False))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_wechat_webhook_is_saved_into_the_secret_store_not_env(self):
        CH.toggle_channel("wechat",
                          ChannelToggleRequest(enabled=False,
                                               wechat_webhook="https://qy?key=abc"))
        self.assertEqual(self.secrets, [{"R20_WECHAT_WEBHOOK": "https://qy?key=abc"}])
        self.assertEqual(self.env_removed, [{"R20_WECHAT_WEBHOOK"}])

    def test_masked_credential_is_treated_as_unchanged(self):
        CH.toggle_channel("wechat",
                          ChannelToggleRequest(enabled=False,
                                               wechat_webhook="key=" + "*" * 8))
        self.assertEqual(self.secrets, [], "掩码串绝不落盘")
        self.assertEqual(self.env_removed, [])

    def test_webhook_and_telegram_and_qq_write_paths(self):
        CH.toggle_channel("webhook",
                          ChannelToggleRequest(enabled=False,
                                               webhook_url="https://hook"))
        self.assertEqual(self.secrets[-1], {"R20_NOTIFICATION_WEBHOOK": "https://hook"})

        CH.toggle_channel("telegram", ChannelToggleRequest(
            enabled=False, telegram_bot_token="tok", telegram_chat_id="123",
            telegram_api_base="https://api"))
        self.assertEqual(self.secrets[-1], {"R20_TELEGRAM_BOT_TOKEN": "tok"})
        self.assertIn({"R20_TELEGRAM_CHAT_ID": "123",
                       "R20_TELEGRAM_API_BASE": "https://api"}, self.env_saved)

        CH.toggle_channel("qq", ChannelToggleRequest(
            enabled=False, qq_client_secret="sec", qq_app_id="app", qq_openid="oid"))
        self.assertEqual(self.secrets[-1], {"R20_QQ_CLIENT_SECRET": "sec"})
        self.assertIn({"R20_QQ_APP_ID": "app", "R20_QQ_OPENID": "oid"}, self.env_saved)
        self.assertEqual(self.env_saved[-1], {"R20_NOTIFY_QQ_ENABLED": "0"},
                         "最后一次写的是开关本身")

    def test_enabling_without_credentials_names_the_channel_and_the_gap(self):
        cases = [
            ("wechat", {}, "企业微信尚未配置 Webhook URL"),
            ("webhook", {}, "通用 Webhook 尚未配置 URL"),
            ("telegram", {}, "Telegram 缺少 Bot Token 或 Chat ID"),
            ("qq", {}, "QQ 缺少目标用户 OpenID"),
            ("qq", {"R20_QQ_OPENID": "o"}, "QQ App ID 或 Client Secret 尚未配置完整"),
        ]
        for channel, env, expected in cases:
            self.notify_env.return_value = env
            with self.assertRaises(HTTPException) as ctx:
                CH.toggle_channel(channel, ChannelToggleRequest(enabled=True))
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn(expected, ctx.exception.detail)

    def test_enabling_a_ready_channel_writes_the_flag_and_audits(self):
        self.notify_env.return_value = {"R20_WECHAT_WEBHOOK": "https://x"}
        out = CH.toggle_channel("wechat", ChannelToggleRequest(enabled=True),
                                x_r20_admin_token="tok")
        self.assertEqual(self.env_saved[-1], {"R20_NOTIFY_WECHAT_ENABLED": "1"})
        self.assertEqual(self.audits[-1][0], "channel.toggle")
        self.assertTrue(self.audits[-1][2]["enabled"])
        self.assertIn("开启", out["message"])

    def test_disabling_skips_the_readiness_gate(self):
        out = CH.toggle_channel("qq", ChannelToggleRequest(enabled=False))
        self.assertEqual(self.env_saved[-1], {"R20_NOTIFY_QQ_ENABLED": "0"})
        self.assertTrue(out["enabled"] is False)
        self.assertIn("关闭", out["message"])


class GatewayOpsTests(_GatewayBase):
    def setUp(self):
        self.audits = []
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-gw-ops-"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.admin = mock.Mock(return_value={"id": 1, "username": "root"})
        self._start(mock.patch.object(GO, "refresh_settings"))
        self._start(mock.patch.object(GO, "require_admin_header", self.admin))
        self._start(mock.patch.object(GO, "audit_record",
                                      lambda *a, **k: self.audits.append((a, k))))
        self._start(mock.patch.object(GO, "SCRIPTS_DIR", self.tmp))
        self._start(mock.patch.object(GO, "ROOT", self.tmp))
        self.store = mock.Mock()
        self.store.stats.return_value = {"s": 1}
        self.store.event_health.return_value = {"e": 1}
        self.store.recent.return_value = [{"id": 9}]
        self._start(mock.patch.object(GO, "GatewayStore", return_value=self.store))
        self._start(mock.patch.object(GO, "scheduler_snapshot", return_value={"sch": 1}))
        self._start(mock.patch.object(GO, "read_pid", return_value=4321))
        self._start(mock.patch.object(GO, "process_running", return_value=True))

    def _script(self, name):
        path = self.tmp / name
        path.write_text("# stub", encoding="utf-8")
        return path

    def test_status_reports_the_store_and_pid(self):
        self.store.replay_dead.return_value = True
        out = GO.gateway_status(x_r20_admin_token="tok", limit=7)
        self.assertTrue(out["running"])
        self.assertEqual(out["pid"], 4321)
        self.assertEqual(out["deliveries"], [{"id": 9}])
        self.assertEqual(out["scheduler"], {"sch": 1})
        self.store.recent.assert_called_once_with(7)

    def test_status_without_a_pid_reports_none(self):
        self._start(mock.patch.object(GO, "read_pid", return_value=None))
        self._start(mock.patch.object(GO, "process_running", return_value=False))
        out = GO.gateway_status(x_r20_admin_token="tok")
        self.assertIsNone(out["pid"])
        self.assertFalse(out["running"])

    def test_replay_phrase_then_dead_state(self):
        with self.assertRaises(HTTPException) as ctx:
            GO.replay_gateway_delivery(5, GatewayReplayRequest(confirmation="REPLAY 6"),
                                       x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 400)

        self.store.replay_dead.return_value = False
        with self.assertRaises(HTTPException) as ctx:
            GO.replay_gateway_delivery(5, GatewayReplayRequest(confirmation="replay 5"),
                                       x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 409, "非 dead 状态不得重放")

        self.store.replay_dead.return_value = True
        out = GO.replay_gateway_delivery(5, GatewayReplayRequest(confirmation="replay 5"),
                                         x_r20_admin_token="tok")
        self.assertEqual(out, {"accepted": True, "delivery_id": 5, "status": "pending"})
        self.assertEqual(self.audits[-1][0][1], "accepted")

    def test_unknown_job_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            GO.run_gateway_job("nope", {}, x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_missing_script_is_500(self):
        with self.assertRaises(HTTPException) as ctx:
            GO.run_gateway_job("news", {}, x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("不存在", ctx.exception.detail)

    def test_timeout_is_504_and_audited_as_timeout(self):
        self._script("news_sentiment_harvester.py")
        self._start(mock.patch.object(
            GO.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=120)))
        with self.assertRaises(HTTPException) as ctx:
            GO.run_gateway_job("news", {}, x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 504)
        self.assertEqual(self.audits[-1][0][1], "timeout")
        self.assertEqual(self.audits[-1][0][2]["timeout"], 120)

    def test_nonzero_exit_is_502_and_audited_as_failed(self):
        self._script("news_sentiment_harvester.py")
        self._start(mock.patch.object(GO.subprocess, "run", return_value=mock.Mock(
            returncode=2, stdout="", stderr="boom")))
        with self.assertRaises(HTTPException) as ctx:
            GO.run_gateway_job("news", {}, x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("boom", ctx.exception.detail)
        self.assertEqual(self.audits[-1][0][1], "failed")

    def test_success_passes_configured_args_and_returns_the_tail(self):
        self._script("self_improvement_engine.py")
        proc = mock.Mock(returncode=0, stdout="ok-output", stderr="")
        runner = mock.Mock(return_value=proc)
        self._start(mock.patch.object(GO.subprocess, "run", runner))
        out = GO.run_gateway_job("self_improvement", {}, x_r20_admin_token="tok")
        cmd = runner.call_args[0][0]
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(Path(cmd[1]).name, "self_improvement_engine.py")
        self.assertEqual(cmd[2], "--force")
        self.assertEqual(runner.call_args.kwargs["timeout"], 180)
        self.assertTrue(out["completed"])
        self.assertEqual(out["output"], "ok-output")
        self.assertEqual(self.audits[-1][0][1], "success")


class NotificationsTests(_GatewayBase):
    def setUp(self):
        self.audits = []
        self.secrets = []
        self.env_saved = []
        self.env_removed = []
        self.admin = mock.Mock()
        self.super = mock.Mock(return_value={"id": 1, "username": "root",
                                             "role": "superadmin"})
        self._start(mock.patch.object(NT, "refresh_settings"))
        self._start(mock.patch.object(NT, "require_admin_header", self.admin))
        self._start(mock.patch.object(NT, "require_superadmin", self.super))
        self._start(mock.patch.object(NT, "audit_record",
                                      lambda *a, **k: self.audits.append((a, k))))
        self._start(mock.patch.object(NT, "update_env",
                                      lambda d: self.env_saved.append(d)))
        self._start(mock.patch.object(NT, "remove_env",
                                      lambda d: self.env_removed.append(d)))
        self._start(mock.patch.object(NT, "save_secrets",
                                      lambda d: self.secrets.append(d)))
        self._start(mock.patch.object(NT, "app_attr",
                                      side_effect=lambda name, default=None: default))
        self.notify_env = mock.Mock(return_value={})
        self._start(mock.patch.object(NT, "notification_env", self.notify_env))

    # ── GET ──────────────────────────────────────────────
    def test_get_config_masks_credentials_and_reports_enabled(self):
        self.notify_env.return_value = {
            "R20_NOTIFY_WEBHOOK_ENABLED": "1",
            "R20_NOTIFICATION_WEBHOOK": "https://hook?key=abcdefghij",
            "R20_TELEGRAM_BOT_TOKEN": "1234567890:secret",
            "R20_QQ_CLIENT_SECRET": "abcdefghijkl",
            "R20_QQ_APP_ID": "app",
        }
        out = NT.admin_notifications(x_r20_admin_token="tok")
        self.assertTrue(out["webhook"]["enabled"])
        self.assertNotIn("abcdefghij", out["webhook"]["url"], "URL 里的密钥必须脱敏")
        self.assertIn("*" * 8, out["telegram"]["bot_token"])
        self.assertIn("*" * 8, out["qq"]["client_secret"])
        self.assertEqual(out["qq"]["app_id"], "app", "非密钥字段原样返回")

    # ── PUT ──────────────────────────────────────────────
    def test_masked_values_are_skipped_not_written_back(self):
        payload = NotificationConfigUpdate(
            webhook_url="https://x?key=" + "*" * 8,
            telegram_bot_token="*" * 12)
        NT.admin_update_notifications(payload, x_r20_session="s")
        self.assertEqual(self.secrets, [], "掩码值=未改动，不落密文库")
        self.assertNotIn("R20_NOTIFICATION_WEBHOOK", self.env_saved[-1])
        self.assertNotIn("R20_TELEGRAM_BOT_TOKEN", self.env_saved[-1])

    def test_secret_fields_go_to_the_secret_store_and_leave_env(self):
        payload = NotificationConfigUpdate(webhook_url="https://hook?key=abc",
                                           qq_client_secret="sec")
        NT.admin_update_notifications(payload, x_r20_session="s")
        self.assertEqual(self.secrets, [{"R20_NOTIFICATION_WEBHOOK": "https://hook?key=abc",
                                         "R20_QQ_CLIENT_SECRET": "sec"}])
        self.assertIn(["R20_NOTIFICATION_WEBHOOK", "R20_QQ_CLIENT_SECRET"],
                      self.env_removed)
        self.assertNotIn("R20_NOTIFICATION_WEBHOOK", self.env_saved[-1])
        self.assertNotIn("R20_QQ_CLIENT_SECRET", self.env_saved[-1])

    def test_enabling_without_readiness_downgrades_and_warns(self):
        payload = NotificationConfigUpdate(telegram_enabled=True,
                                           qq_enabled=True,
                                           webhook_enabled=True,
                                           wechat_enabled=True)
        out = NT.admin_update_notifications(payload, x_r20_session="s")
        env = self.env_saved[-1]
        self.assertEqual(env["R20_NOTIFY_TELEGRAM_ENABLED"], "0")
        self.assertEqual(env["R20_NOTIFY_QQ_ENABLED"], "0")
        self.assertEqual(env["R20_NOTIFY_WEBHOOK_ENABLED"], "0")
        self.assertEqual(env["R20_NOTIFY_WECHAT_ENABLED"], "0")
        self.assertEqual(len(out["warnings"]), 4)
        self.assertIn("提示", out["message"])
        self.assertEqual(self.audits[-1][0][1], "success")

    def test_blank_values_are_removed_from_env(self):
        payload = NotificationConfigUpdate(telegram_chat_id="", telegram_api_base="")
        NT.admin_update_notifications(payload, x_r20_session="s")
        self.assertIn({"R20_TELEGRAM_CHAT_ID"}, self.env_removed)
        self.assertIn({"R20_TELEGRAM_API_BASE"}, self.env_removed)
        self.assertNotIn("R20_TELEGRAM_CHAT_ID", self.env_saved[-1])

    def test_telegram_token_is_routed_to_the_secret_store(self):
        payload = NotificationConfigUpdate(telegram_bot_token="123:abc")
        NT.admin_update_notifications(payload, x_r20_session="s")
        self.assertIn({"R20_TELEGRAM_BOT_TOKEN": "123:abc"}, self.secrets)
        self.assertIn(["R20_TELEGRAM_BOT_TOKEN"], self.env_removed)
        self.assertNotIn("R20_TELEGRAM_BOT_TOKEN", self.env_saved[-1])

    # ── diagnose / test ──────────────────────────────────
    def test_diagnose_never_claims_it_sent_anything(self):
        self._start(mock.patch.object(NT, "diagnose_channel",
                                      return_value={"status": "ok"}))
        out = NT.diagnose_notification(NotificationTestRequest(channel="qq"),
                                       x_r20_admin_token="tok")
        self.assertEqual(out["result"], {"status": "ok"})
        self.assertFalse(out["sent"])
        self.assertEqual(self.audits[-1][0][0], "notifications.diagnose")

    def test_send_test_phrase_is_mandatory_and_meaning_is_honest(self):
        with self.assertRaises(HTTPException) as ctx:
            NT.send_notification_test(NotificationTestRequest(channel="qq",
                                                              confirmation="SEND"),
                                      x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)

        self._start(mock.patch.object(NT, "test_channel", return_value={"ok": True}))
        out = NT.send_notification_test(
            NotificationTestRequest(channel="qq", confirmation="send test QQ"),
            x_r20_session="s")
        self.assertTrue(out["sent"])
        self.assertIn("已受理", out["meaning"])
        self.assertIn("不等于", out["meaning"])

    # ── QQ OpenID capture ────────────────────────────────
    def test_capture_start_defaults_and_error_mapping(self):
        import r20_backend.qq_bind as QB
        start = mock.Mock(return_value={"capture_id": "c1", "app_id": "a1"})
        self._start(mock.patch.object(QB, "start_openid_capture", start))
        out = NT.qq_capture_openid_start(None, x_r20_session="s")
        start.assert_called_once_with(app_id=None, client_secret=None, timeout=60)
        self.assertEqual(out["capture_id"], "c1")

        start.side_effect = ValueError("缺 app_id")
        with self.assertRaises(HTTPException) as ctx:
            NT.qq_capture_openid_start(QQOpenIDCaptureStartRequest(), x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)

        start.side_effect = RuntimeError("网关起不来")
        with self.assertRaises(HTTPException) as ctx:
            NT.qq_capture_openid_start(QQOpenIDCaptureStartRequest(), x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 502)

    def test_capture_poll_audits_only_on_capture(self):
        import r20_backend.qq_bind as QB
        poll = mock.Mock(return_value={"status": "waiting"})
        self._start(mock.patch.object(QB, "poll_openid_capture", poll))
        NT.qq_capture_openid_poll("c1", x_r20_session="s")
        self.assertEqual(self.audits, [], "未捕获不留 complete 审计")

        poll.return_value = {"status": "captured", "openid": "oid"}
        out = NT.qq_capture_openid_poll("c1", x_r20_session="s")
        self.assertEqual(out["openid"], "oid")
        self.assertEqual(self.audits[-1][0][0], "qq.capture_openid.complete")

    # ── QQ bind ──────────────────────────────────────────
    def test_bind_start_failure_is_audited_and_502(self):
        import r20_backend.qq_bind as QB
        self._start(mock.patch.object(QB, "create_bind_task",
                                      side_effect=RuntimeError("boom")))
        with self.assertRaises(HTTPException) as ctx:
            NT.qq_bind_start(x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(self.audits[-1][0][1], "failed")

    def test_bind_start_renders_a_qr_when_segno_is_available(self):
        import r20_backend.qq_bind as QB
        self._start(mock.patch.object(QB, "create_bind_task", return_value={
            "task_id": "t1", "connect_url": "https://connect", "expires_in": 300}))
        fake = types.SimpleNamespace(
            make=mock.Mock(return_value=mock.Mock(
                png_data_uri=mock.Mock(return_value="data:image/png;base64,AAA"))))
        with mock.patch.dict(sys.modules, {"segno": fake}):
            out = NT.qq_bind_start(x_r20_session="s")
        self.assertEqual(out["qr_data_uri"], "data:image/png;base64,AAA")
        self.assertEqual(out["task_id"], "t1")
        self.assertEqual(self.audits[-1][0][1], "success")

    def test_bind_start_survives_a_missing_qr_library(self):
        import r20_backend.qq_bind as QB
        self._start(mock.patch.object(QB, "create_bind_task", return_value={
            "task_id": "t1", "connect_url": "https://connect", "expires_in": 300}))
        with mock.patch.dict(sys.modules, {"segno": None}):
            out = NT.qq_bind_start(x_r20_session="s")
        self.assertEqual(out["qr_data_uri"], "", "二维码库缺失不阻断绑定任务")
        self.assertEqual(out["connect_url"], "https://connect")

    def test_bind_poll_expiry_is_410_and_bound_is_audited(self):
        import r20_backend.qq_bind as QB
        poll = mock.Mock(side_effect=RuntimeError("已过期"))
        self._start(mock.patch.object(QB, "poll_bind_task", poll))
        with self.assertRaises(HTTPException) as ctx:
            NT.qq_bind_poll("t1", x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 410)

        poll.side_effect = None
        poll.return_value = {"status": "awaiting_message", "app_id": "a1",
                             "openid": ""}
        out = NT.qq_bind_poll("t1", x_r20_session="s")
        self.assertEqual(out["status"], "awaiting_message")
        self.assertEqual(self.audits[-1][0][0], "qq.bind.complete")

    # ── schedule ─────────────────────────────────────────
    def test_schedule_get_returns_the_saved_shape_with_notes(self):
        self._start(mock.patch.object(NT, "load_schedule",
                                      return_value={"briefing_times": ["08:00"],
                                                    "enabled": True}))
        out = NT.notification_schedule(x_r20_admin_token="tok")
        self.assertEqual(out["briefing_times"], ["08:00"])
        self.assertIn("不受每日简报时间限制", out["event_notifications"])
        self.assertIn("60 秒", out["restart_note"])

    def test_schedule_put_normalises_dedupes_and_sorts(self):
        saved = []
        self._start(mock.patch.object(NT, "load_schedule",
                                      return_value={"briefing_times": []}))
        self._start(mock.patch.object(NT, "save_schedule", lambda s: saved.append(s)))
        out = NT.update_notification_schedule(
            NotificationScheduleUpdate(briefing_times=["23:59", "7:5", "07:05"]),
            x_r20_admin_token="tok")
        self.assertEqual(out["briefing_times"], ["07:05", "23:59"])
        self.assertEqual(saved[-1]["briefing_times"], ["07:05", "23:59"])
        self.assertEqual(self.audits[-1][0][0], "notifications.schedule")

    def test_schedule_put_rejects_invalid_times(self):
        self._start(mock.patch.object(NT, "load_schedule",
                                      return_value={"briefing_times": []}))
        save = mock.Mock()
        self._start(mock.patch.object(NT, "save_schedule", save))
        with self.assertRaises(HTTPException) as ctx:
            NT.update_notification_schedule(
                NotificationScheduleUpdate(briefing_times=["25:00"]),
                x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("HH:MM", ctx.exception.detail)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()

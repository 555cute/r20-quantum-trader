"""通知扇出：**密钥入日志前必须打码、业务拒绝不许当成功、测试只测指定通道**（第二百六十五刀，开新面 notifications.py）。

先打印整个文件（263 行）再动笔。四条口径：

| 语义 | 口径 |
|---|---|
| ★ **密钥不许进日志** | `_post_json` 的异常文本一律过 `_scrub_url_secret`（`bot<id>:<secret>` 与 URL userinfo 两种形态都要打码）—— 否则含 token 的完整 URL 会被写进 uvicorn.log |
| ★ **HTTP 200 ≠ 送达** | 钉钉/飞书之外的通用 Webhook 还要看业务体（`success is False`、`errcode`、`code` 非 0 都算失败）；企业微信 `errcode != 0` 失败；Telegram `ok is not True` 失败；QQ `code` 非 0 失败 |
| ★ **测试只测指定通道** | `test_channel` 只返回该通道一条结果（别的通道挂了不许掩盖它）；`diagnose_channel` **只读配置、绝不发消息** |
| ★ **配置诊断分三态** | `ready` / `incomplete`（点名缺哪些键）/ `failed`（未知通道）；QQ「只缺 OpenID」有专门的自动绑定指引文案 |
| 适配器 | 按 URL 主机名分流 8 种 payload（钉钉加签 HMAC-SHA256、飞书加签、Bark、Discord、企业微信、Server 酱、PushDeer、通用四字段）|
| 兼容符号 | `send_qq_message` 保留给老策略脚本：任一通道 accepted 即为 True |

⚠️ 如实登记一处**刻意不覆盖**的行：`_env` 第 **37** 行（真实
`r20_gateway.secrets.load_secrets()`）。原因是 `tests/__init__.py` 的会话沙箱会把
`notifications.ROOT` 改写成临时配置目录，于是第 35 行的
`ROOT == Path(__file__).resolve().parents[1]` 在测试里**恒为假**；要走到第 37 行，
只能把 ROOT 掰回代码根，而那会**读线上 `.env` 与 `data/r20_secrets.enc`** ——
实测会触发本仓的「生产配置依赖」守卫告警（严格模式 `R20_TESTS_STRICT_READS=1` 下直接失败）：

    [tests] ⚠️ 生产配置依赖：… 读了线上 .env（读取点 notifications.py:27）
            —— 它的内容会塑造决策 ⇒ 结果随线上配置漂移。

本刀的用例**只断言"结果是个 dict"**、并不依赖其内容，但为守住"测试不依赖线上运维配置"
这条纪律，仍选择不覆盖该行（notifications.py 因此 174/175）。这条沙箱副作用值得记下来：
`notifications.ROOT` 在测试里**不是**代码根。
"""

import base64
import hashlib
import hmac
import json
import re
import shutil
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from r20_backend import notifications as N


class _Resp:
    def __init__(self, status, body: bytes):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class EnvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-notify-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_dotenv_is_parsed_and_comments_and_blank_lines_skipped(self):
        (self.tmp / ".env").write_text(
            "# a comment\n\nR20_NOTIFY_WEBHOOK_ENABLED=1\nSPACED = value \nNOEQUALS\n",
            encoding="utf-8")
        with mock.patch.object(N, "ROOT", self.tmp), \
                mock.patch.object(N, "SECRET_LOADER", lambda: {}):
            env = N._env()
        self.assertEqual(env["R20_NOTIFY_WEBHOOK_ENABLED"], "1")
        self.assertIn("SPACED", env)
        self.assertNotIn("NOEQUALS", env)

    def test_encrypted_secrets_override_dotenv_and_environment(self):
        (self.tmp / ".env").write_text("R20_QQ_APP_ID=from-dotenv\n", encoding="utf-8")
        with mock.patch.object(N, "ROOT", self.tmp), \
                mock.patch.dict("os.environ", {"R20_QQ_APP_ID": "from-environ"}), \
                mock.patch.object(N, "SECRET_LOADER",
                                  lambda: {"R20_QQ_APP_ID": "from-secrets"}):
            env = N._env()
        self.assertEqual(env["R20_QQ_APP_ID"], "from-secrets",
                         "动态密文必须压过 .env 与继承的环境变量")

    def test_secret_loader_failure_is_swallowed(self):
        (self.tmp / ".env").write_text("A=1\n", encoding="utf-8")

        def boom():
            raise RuntimeError("密文库损坏")

        with mock.patch.object(N, "ROOT", self.tmp), \
                mock.patch.object(N, "SECRET_LOADER", boom):
            env = N._env()
        self.assertEqual(env["A"], "1", "密文库读不出来也要能继续读 .env")

    def test_sandbox_redirects_root_away_from_the_code_root(self):
        """钉住那条沙箱副作用本身：测试期 `notifications.ROOT` **不是**代码根，
        所以第 35–37 行（真实 `load_secrets`）不该被用例意外打开。"""
        code_root = Path(N.__file__).resolve().parents[1]
        self.assertNotEqual(N.ROOT, code_root,
                            "若某天沙箱不再改写 ROOT，请重新评估第 37 行要不要覆盖")
        self.assertTrue(N.ROOT.exists())


class ScrubTests(unittest.TestCase):
    def test_telegram_bot_token_in_a_path_is_redacted(self):
        text = "HTTPError: https://api.telegram.org/bot123456789:AAF-xyz_secret/sendMessage"
        scrubbed = N._scrub_url_secret(text)
        self.assertNotIn("AAF-xyz_secret", scrubbed)
        self.assertIn("bot***REDACTED***", scrubbed)

    def test_url_userinfo_is_redacted(self):
        scrubbed = N._scrub_url_secret("failed https://user:pa55word@hook.example/x")
        self.assertNotIn("pa55word", scrubbed)
        self.assertIn("://***:***@", scrubbed)


class PostJsonTests(unittest.TestCase):
    def test_success_parses_json_body(self):
        with mock.patch.object(N, "safe_urlopen",
                               return_value=_Resp(200, b'{"ok": true}')):
            ok, detail, data = N._post_json("https://x.example/h", {"a": 1})
        self.assertTrue(ok)
        self.assertEqual(detail, "HTTP 200")
        self.assertEqual(data, {"ok": True})

    def test_non_json_and_empty_bodies_are_tolerated(self):
        with mock.patch.object(N, "safe_urlopen", return_value=_Resp(200, b"plain")):
            ok, _, data = N._post_json("https://x.example/h", {})
        self.assertTrue(ok)
        self.assertEqual(data, {"raw": "plain"})

        with mock.patch.object(N, "safe_urlopen", return_value=_Resp(204, b"")):
            ok, _, data = N._post_json("https://x.example/h", {})
        self.assertTrue(ok)
        self.assertEqual(data, {})

    def test_http_error_status_is_not_ok_but_keeps_the_body(self):
        with mock.patch.object(N, "safe_urlopen",
                               return_value=_Resp(500, b'{"code": 1}')):
            ok, detail, data = N._post_json("https://x.example/h", {})
        self.assertFalse(ok)
        self.assertEqual(detail, "HTTP 500")
        self.assertEqual(data, {"code": 1})

    def test_exception_text_is_scrubbed_before_returning(self):
        with mock.patch.object(N, "safe_urlopen", side_effect=ValueError(
                "unknown url type: https://api.telegram.org/bot123456789:AAF-xyz_secret/sendMessage")):
            ok, detail, data = N._post_json("https://x.example/h", {})
        self.assertFalse(ok)
        self.assertNotIn("AAF-xyz_secret", detail)
        self.assertEqual(data, {})

    def test_headers_are_merged_over_defaults(self):
        captured = {}

        def _capture(request, timeout=None):
            captured["headers"] = dict(request.headers)
            return _Resp(200, b"{}")

        with mock.patch.object(N, "safe_urlopen", side_effect=_capture):
            N._post_json("https://x.example/h", {"a": 1},
                         {"Authorization": "QQBot tok"})
        self.assertIn("Authorization", captured["headers"])
        self.assertIn("Content-type", captured["headers"])


class EnabledAndDiagnoseTests(unittest.TestCase):
    def test_only_explicitly_enabled_channels_are_listed_in_order(self):
        env = {"R20_NOTIFY_WEBHOOK_ENABLED": "1", "R20_NOTIFY_TELEGRAM_ENABLED": "1",
               "R20_NOTIFY_WECHAT_ENABLED": "0"}
        self.assertEqual(N.enabled_channels(env), ["webhook", "telegram"])

    def test_unknown_channel_is_failed_not_incomplete(self):
        self.assertEqual(N.diagnose_channel("nope", {})["status"], "failed")

    def test_missing_keys_are_named(self):
        out = N.diagnose_channel("telegram", {"R20_TELEGRAM_BOT_TOKEN": "t"})
        self.assertEqual(out["status"], "incomplete")
        self.assertEqual(out["missing"], ["R20_TELEGRAM_CHAT_ID"])

    def test_qq_with_only_openid_missing_gets_the_binding_hint(self):
        out = N.diagnose_channel("qq", {"R20_QQ_APP_ID": "a",
                                        "R20_QQ_CLIENT_SECRET": "s"})
        self.assertEqual(out["status"], "incomplete")
        self.assertIn("自动获取 OpenID", out["detail"])

    def test_complete_config_is_ready_and_never_sends(self):
        with mock.patch.object(N, "_post_json") as poster:
            out = N.diagnose_channel("webhook", {"R20_NOTIFICATION_WEBHOOK": "https://h"})
        self.assertEqual(out["status"], "ready")
        poster.assert_not_called()


class SendChannelWebhookTests(unittest.TestCase):
    def _send(self, env, url="https://hook.example/x", response=None, ok=True):
        with mock.patch.object(N, "validate_outbound_url", side_effect=lambda u, **k: u), \
                mock.patch.object(N, "_post_json",
                                  return_value=(ok, "HTTP 200", response if response is not None else {})) as poster:
            result = N.send_channel("webhook", "hello", dict(env, R20_NOTIFICATION_WEBHOOK=url))
        return result, poster

    def test_unconfigured_and_invalid_urls_fail_before_any_request(self):
        with mock.patch.object(N, "_post_json") as poster:
            self.assertFalse(N.send_channel("webhook", "m", {})[0])
            poster.assert_not_called()
        self.assertFalse(N.send_channel("webhook", "m",
                                        {"R20_NOTIFICATION_WEBHOOK": "x"})[0])

    def test_invalid_url_reports_the_validation_error(self):
        with mock.patch.object(N, "validate_outbound_url",
                               side_effect=ValueError("内网地址被拒绝")):
            ok, detail = N.send_channel("webhook", "m",
                                        {"R20_NOTIFICATION_WEBHOOK": "http://127.0.0.1"})
        self.assertFalse(ok)
        self.assertIn("内网地址被拒绝", detail)

    def test_dingtalk_payload_and_signature(self):
        result, poster = self._send({"R20_DINGTALK_SECRET": "s3cret"},
                                    url="https://oapi.dingtalk.com/robot/send")
        self.assertTrue(result[0])
        url, payload = poster.call_args[0][0], poster.call_args[0][1]
        self.assertEqual(payload["msgtype"], "text")
        self.assertIn("timestamp=", url)
        self.assertIn("sign=", url)
        self.assertNotIn("s3cret", url, "签名值不得是明文密钥")

    def test_dingtalk_signature_matches_the_documented_hmac(self):
        with mock.patch.object(N, "time") as clock, \
                mock.patch.object(N, "validate_outbound_url", side_effect=lambda u, **k: u), \
                mock.patch.object(N, "_post_json",
                                  return_value=(True, "HTTP 200", {})) as poster:
            clock.time.return_value = 1_700_000_000.0
            N.send_channel("webhook", "m", {"R20_NOTIFICATION_WEBHOOK":
                                            "https://oapi.dingtalk.com/r", 
                                            "R20_DINGTALK_SECRET": "k"})
        ts = 1_700_000_000_000
        expected = urllib.parse.quote_plus(base64.b64encode(hmac.new(
            b"k", f"{ts}\nk".encode(), hashlib.sha256).digest()).decode())
        self.assertIn(f"timestamp={ts}", poster.call_args[0][0])
        self.assertIn(f"sign={expected}", poster.call_args[0][0])

    def test_feishu_signature_goes_into_the_payload(self):
        result, poster = self._send({"R20_FEISHU_SECRET": "fk"},
                                    url="https://open.feishu.cn/open-apis/bot/v2/hook/x")
        self.assertTrue(result[0])
        payload = poster.call_args[0][1]
        self.assertEqual(payload["msg_type"], "text")
        self.assertIn("timestamp", payload)
        self.assertIn("sign", payload)

    def test_each_adapter_gets_its_own_payload_shape(self):
        cases = [
            ("https://api.day.app/KEY", {"title", "body", "group"}),
            ("https://discord.com/api/webhooks/x", {"content"}),
            ("https://qyapi.weixin.qq.com/cgi-bin/webhook/send", {"msgtype", "text"}),
            ("https://sctapi.ftqq.com/SCTKEY.send", {"title", "desp"}),
            ("https://api2.pushdeer.com/message/push", {"text", "desp"}),
            ("https://example.com/hook", {"source", "message", "text", "content"}),
        ]
        for url, expected_keys in cases:
            with self.subTest(url=url):
                result, poster = self._send({}, url=url)
                self.assertTrue(result[0])
                self.assertEqual(set(poster.call_args[0][1]), expected_keys)
                self.assertIn("m", json.dumps(poster.call_args[0][1], ensure_ascii=False)
                              .replace("hello", "m"))

    def test_http_failure_is_reported_with_the_body(self):
        result, _ = self._send({}, ok=False, response={"code": 9})
        self.assertFalse(result[0])
        self.assertIn("HTTP 200", result[1])

    def test_business_level_rejection_is_not_treated_as_success(self):
        for body in ({"success": False}, {"errcode": 40013}, {"code": "1"}):
            with self.subTest(body=body):
                result, _ = self._send({}, response=body)
                self.assertFalse(result[0], f"{body} 是业务拒绝，不能算成功")

    def test_zero_like_business_codes_are_accepted(self):
        for body in ({}, {"success": True}, {"errcode": 0}, {"code": "0"},
                     {"errcode": None}):
            with self.subTest(body=body):
                result, _ = self._send({}, response=body)
                self.assertTrue(result[0], f"{body} 应视为正常")


class SendChannelWechatTests(unittest.TestCase):
    def _send(self, env, response):
        with mock.patch.object(N, "validate_outbound_url", side_effect=lambda u, **k: u), \
                mock.patch.object(N, "_post_json",
                                  return_value=(True, "HTTP 200", response)):
            return N.send_channel("wechat", "m",
                                  dict(env, R20_WECHAT_WEBHOOK="https://qyapi.weixin.qq.com/x"))

    def test_missing_and_invalid_config(self):
        self.assertFalse(N.send_channel("wechat", "m", {})[0])
        with mock.patch.object(N, "validate_outbound_url",
                               side_effect=ValueError("非公网")):
            ok, detail = N.send_channel("wechat", "m",
                                        {"R20_WECHAT_WEBHOOK": "http://10.0.0.1"})
        self.assertFalse(ok)
        self.assertIn("非公网", detail)

    def test_errcode_must_be_zero(self):
        self.assertFalse(self._send({}, {"errcode": 93000})[0])
        self.assertTrue(self._send({}, {"errcode": 0})[0])
        self.assertFalse(self._send({}, {})[0], "缺 errcode 视为 -1 ⇒ 拒绝")

    def test_transport_failure_is_reported_with_the_body(self):
        with mock.patch.object(N, "validate_outbound_url", side_effect=lambda u, **k: u), \
                mock.patch.object(N, "_post_json",
                                  return_value=(False, "HTTP 500", {"errcode": 1})):
            ok, detail = N.send_channel("wechat", "m", {
                "R20_WECHAT_WEBHOOK": "https://qyapi.weixin.qq.com/x"})
        self.assertFalse(ok)
        self.assertIn("HTTP 500", detail)


class SendChannelTelegramTests(unittest.TestCase):
    _OK_BODY = {"ok": True, "result": {"message_id": 1}}

    def _send(self, env, response=None, ok=True, detail="HTTP 200"):
        with mock.patch.object(N, "validate_outbound_url", side_effect=lambda u, **k: u), \
                mock.patch.object(N, "_post_json",
                                  return_value=(ok, detail,
                                                self._OK_BODY if response is None else response)) as poster:
            result = N.send_channel("telegram", "m", dict(
                env, R20_TELEGRAM_BOT_TOKEN="123456789:TOK",
                R20_TELEGRAM_CHAT_ID="42"))
        return result, poster

    def test_missing_config_and_invalid_base_url(self):
        self.assertFalse(N.send_channel("telegram", "m", {})[0])
        with mock.patch.object(N, "validate_outbound_url",
                               side_effect=ValueError("api.telegram.org 不可达")):
            ok, detail = N.send_channel("telegram", "m", {
                "R20_TELEGRAM_BOT_TOKEN": "t", "R20_TELEGRAM_CHAT_ID": "1"})
        self.assertFalse(ok)
        self.assertIn("Telegram API Base URL 无效", detail)

    def test_custom_base_url_is_normalised(self):
        result, poster = self._send({"R20_TELEGRAM_API_BASE": "https://proxy.example/"})
        self.assertTrue(result[0])
        self.assertTrue(poster.call_args[0][0].startswith("https://proxy.example/bot"))

    def test_network_keywords_get_a_china_proxy_hint(self):
        result, _ = self._send({}, ok=False, detail="<urlopen error timed out>")
        self.assertFalse(result[0])
        self.assertIn("反代", result[1])

    def test_business_rejection_requires_ok_true(self):
        rejected, _ = self._send({}, response={"ok": False,
                                               "description": "chat not found"})
        self.assertFalse(rejected[0])
        accepted, _ = self._send({}, response={"ok": True,
                                               "result": {"message_id": 7}})
        self.assertTrue(accepted[0])
        self.assertIn("message_id=7", accepted[1])


class SendChannelQQTests(unittest.TestCase):
    def test_missing_config_is_reported(self):
        ok, detail = N._send_qq({}, "m")
        self.assertFalse(ok)
        self.assertIn("未完整配置", detail)

    def test_token_failure_is_reported(self):
        with mock.patch.object(N, "_post_json", return_value=(
                False, "HTTP 401", {"code": "100007", "message": "bad secret"})):
            ok, detail = N._send_qq({"R20_QQ_APP_ID": "a", "R20_QQ_CLIENT_SECRET": "s",
                                     "R20_QQ_OPENID": "u"}, "m")
        self.assertFalse(ok)
        self.assertIn("access token 获取失败", detail)
        self.assertIn("100007", detail)

    def test_group_and_c2c_endpoints_are_selected_by_openid_shape(self):
        for openid, expected in (("group_openid_x", "/v2/groups/"),
                                 ("A" * 41, "/v2/groups/"),
                                 ("user_openid_short", "/v2/users/")):
            with self.subTest(openid=openid):
                with mock.patch.object(N, "_post_json", side_effect=[
                        (True, "HTTP 200", {"access_token": "tok"}),
                        (True, "HTTP 200", {"id": "msg-1"})]) as poster:
                    ok, detail = N._send_qq({"R20_QQ_APP_ID": "a",
                                             "R20_QQ_CLIENT_SECRET": "s",
                                             "R20_QQ_OPENID": openid}, "m")
                self.assertTrue(ok)
                self.assertIn(expected, poster.call_args_list[1][0][0])
                self.assertIn("msg-1", detail)

    def test_11255_gets_the_dedicated_rebinding_message(self):
        with mock.patch.object(N, "_post_json", side_effect=[
                (True, "HTTP 200", {"access_token": "tok"}),
                (True, "HTTP 200", {"code": 11255})]):
            ok, detail = N._send_qq({"R20_QQ_APP_ID": "a", "R20_QQ_CLIENT_SECRET": "s",
                                     "R20_QQ_OPENID": "u"}, "m")
        self.assertFalse(ok)
        self.assertIn("11255", detail)
        self.assertIn("自动获取 OpenID", detail)

    def test_11255_on_the_transport_failure_path_also_gets_the_hint(self):
        """11255 也可能以非 2xx 回来 ⇒ 传输失败分支单独再判一次（两条分支不能只覆盖一条）。"""
        with mock.patch.object(N, "_post_json", side_effect=[
                (True, "HTTP 200", {"access_token": "tok"}),
                (False, "HTTP 400", {"code": 11255})]):
            ok, detail = N._send_qq({"R20_QQ_APP_ID": "a", "R20_QQ_CLIENT_SECRET": "s",
                                     "R20_QQ_OPENID": "u"}, "m")
        self.assertFalse(ok)
        self.assertIn("11255", detail)
        self.assertIn("自动获取 OpenID", detail)

    def test_transport_failure_and_business_rejection(self):
        with mock.patch.object(N, "_post_json", side_effect=[
                (True, "HTTP 200", {"access_token": "tok"}),
                (False, "HTTP 500", {})]):
            self.assertFalse(N._send_qq({"R20_QQ_APP_ID": "a",
                                         "R20_QQ_CLIENT_SECRET": "s",
                                         "R20_QQ_OPENID": "u"}, "m")[0])

        with mock.patch.object(N, "_post_json", side_effect=[
                (True, "HTTP 200", {"access_token": "tok"}),
                (True, "HTTP 200", {"code": "40054", "message": "rejected"})]):
            ok, detail = N._send_qq({"R20_QQ_APP_ID": "a", "R20_QQ_CLIENT_SECRET": "s",
                                     "R20_QQ_OPENID": "u"}, "m")
        self.assertFalse(ok)
        self.assertIn("40054", detail)

    def test_message_id_is_reported_and_message_without_id_is_a_rejection(self):
        with mock.patch.object(N, "_post_json", side_effect=[
                (True, "HTTP 200", {"access_token": "tok"}),
                (True, "HTTP 200", {"id": "m9"})]):
            ok, detail = N._send_qq({"R20_QQ_APP_ID": "a", "R20_QQ_CLIENT_SECRET": "s",
                                     "R20_QQ_OPENID": "u"}, "m")
        self.assertTrue(ok)
        self.assertIn("m9", detail)

        # 有 message 却无 id ⇒ 视为业务拒绝（`or` 分支）
        with mock.patch.object(N, "_post_json", side_effect=[
                (True, "HTTP 200", {"access_token": "tok"}),
                (True, "HTTP 200", {"message": "accepted-ish"})]):
            rejected, detail = N._send_qq({"R20_QQ_APP_ID": "a",
                                           "R20_QQ_CLIENT_SECRET": "s",
                                           "R20_QQ_OPENID": "u"}, "m")
        self.assertFalse(rejected)
        self.assertIn("业务拒绝", detail)


class RoutingTests(unittest.TestCase):
    def test_qq_channel_delegates_to_send_qq(self):
        with mock.patch.object(N, "_send_qq", return_value=(True, "accepted: id=1")) as qq:
            self.assertEqual(N.send_channel("qq", "m", {}), (True, "accepted: id=1"))
        qq.assert_called_once()

    def test_unknown_channel_is_rejected(self):
        ok, detail = N.send_channel("nope", "m", {})
        self.assertFalse(ok)
        self.assertIn("未知通知通道", detail)

    def test_notify_marks_each_enabled_channel(self):
        env = {"R20_NOTIFY_WEBHOOK_ENABLED": "1", "R20_NOTIFY_QQ_ENABLED": "1"}
        with mock.patch.object(N, "_env", return_value=env), \
                mock.patch.object(N, "send_channel",
                                  side_effect=[(True, "HTTP 200"), (False, "boom")]), \
                mock.patch.object(N, "datetime") as clock:
            clock.datetime.now.return_value.strftime.return_value = "2026-09-22 12:00:00"
            out = N.notify("  hello  ")
        self.assertEqual(out["webhook"], "accepted: HTTP 200")
        self.assertEqual(out["qq"], "failed: boom")

    def test_notify_strips_text_and_stamps_beijing_time(self):
        with mock.patch.object(N, "_env", return_value={}), \
                mock.patch.object(N, "enabled_channels", return_value=[]):
            self.assertEqual(N.notify("  x  "), {}, "没有启用通道 ⇒ 空结果，不报错")

    def test_send_qq_message_is_true_when_any_channel_accepted(self):
        with mock.patch.object(N, "notify",
                               return_value={"qq": "failed: x", "webhook": "accepted: y"}):
            self.assertTrue(N.send_qq_message("m"))
        with mock.patch.object(N, "notify", return_value={"qq": "failed: x"}):
            self.assertFalse(N.send_qq_message("m"))

    def test_test_channel_returns_only_the_selected_channel(self):
        with mock.patch.object(N, "_env", return_value={}), \
                mock.patch.object(N, "send_channel",
                                  return_value=(True, "accepted: HTTP 200")) as sender:
            out = N.test_channel("telegram")
        self.assertEqual(list(out), ["telegram"])
        self.assertTrue(out["telegram"].startswith("accepted:"))
        self.assertIn("TELEGRAM", sender.call_args[0][1])

    def test_test_channel_reports_failure_for_that_channel_only(self):
        with mock.patch.object(N, "_env", return_value={}), \
                mock.patch.object(N, "send_channel", return_value=(False, "boom")):
            out = N.test_channel("webhook")
        self.assertEqual(out, {"webhook": "failed: boom"})


class NoSecretLeakTests(unittest.TestCase):
    def test_channel_failure_detail_never_contains_the_bot_token(self):
        with mock.patch.object(N, "validate_outbound_url", side_effect=lambda u, **k: u), \
                mock.patch.object(N, "safe_urlopen", side_effect=ValueError(
                    "unknown url type: https://api.telegram.org/bot123456789:AAF-xyz_secret/sendMessage")):
            ok, detail = N.send_channel("telegram", "m", {
                "R20_TELEGRAM_BOT_TOKEN": "123456789:AAF-xyz_secret",
                "R20_TELEGRAM_CHAT_ID": "1"})
        self.assertFalse(ok)
        self.assertNotIn("AAF-xyz_secret", detail)
        self.assertRegex(detail, re.compile(r"bot\*+REDACTED\*+"))


if __name__ == "__main__":
    unittest.main()

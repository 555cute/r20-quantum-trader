"""系统健康/审计/概览/日志/配置路由：**公开面只说外壳、敏感面不泄密、更新先自证干净**（第二百五十九刀，开新面 system.py）。

先打印整个文件（507 行）再动笔。6 个助手 + 14 个处理器，按三条主线钉住：

| 语义 | 口径 |
|---|---|
| ★ **读不到 ≠ 新鲜** | `file_health` 缺文件 ⇒ `exists:False, age:None, fresh:False`（**不填 0 年龄冒充新鲜**）；`runtime_overview` 任一文件不新鲜 ⇒ `overall:"STALE"` |
| ★ **公开面只说外壳** | `/api/v1/status` 是**无鉴权**公开面，只返回 mode 与脚本存在性 —— 审计修复后**不再**吐出 tracker/decisions 底牌 |
| ★ **敏感面不泄密** | `admin_runtime` 的 `llm_runtime` 只取**白名单四键**，`get_active_llm_runtime()` 回包里的明文 `api_key`/`base_url` **绝不整包入响应**；调用失败也有兜底 |
| ★ **更新先自证干净** | `POST /admin/update`：确认短语 `UPDATE R20` 逐字校验 ⇒ 400；`update_status().error` ⇒ 502；工作区 dirty ⇒ **409**；读不到 remote ⇒ 502；`git pull` 失败 ⇒ 502；**只有 local 变了才算 updated** 并触发前端构建 |
| 写配置分权 | 含 `okx_*` 或 `manual_close_enabled` 的载荷 ⇒ **超管**，其余 ⇒ 管理员；URL 协议校验 ⇒ 400；切换 OKX 环境要抢交易锁，抢不到 ⇒ **409** |
| 日志来源 | 白名单外 ⇒ **400**（不是静默空内容）|

⚠️ 如实记录两处**不一致**（列待议，未擅自改）：

1. `ADMIN_LOG_SOURCES` 把 `scheduler` 与 `gateway` 都映到 `r20_gateway.log`，而
   `runtime_overview()` 的日志块却读 `r20_scheduler.log` —— 同一页面两条路径口径不同；
2. `admin_config` 用**局部导入**再取 `app_attr`（309 行），因此「打模块级别
   `routers.system.app_attr`」的测试缝对它无效，只有 `r20_backend.app` 上的覆盖才生效
   —— 表现为**隔离跑绿、全量跑红**（全量里 `r20_backend.app` 已导入 ⇒ 真 `app_attr`
   命中 app 的属性，读到生产 `account_initial_state.json`）。本刀在两处 seam 都钉住。
"""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse

from r20_backend.routers import system as A
from r20_backend.schemas import AdminConfigUpdate, UpdateRequest


class _Base(unittest.TestCase):
    def setUp(self):
        self.audits = []

        def _patch(target, new=mock.DEFAULT, **kwargs):
            patcher = mock.patch.object(A, target, new, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

        def _start(patcher):
            patcher.start()
            self.addCleanup(patcher.stop)
            return patcher

        self._start = _start

        _patch("audit_record", lambda *a, **k: self.audits.append((a, k)))
        _patch("app_attr", side_effect=lambda name, default=None: default)
        # ⚠️ admin_config 里 `from r20_backend.dependencies import app_attr` 是**局部导入**，
        # 打模块级别的 A.app_attr 管不到它 —— 必须同时钉住 dependencies 上的那一份，
        # 否则全量套件里 r20_backend.app 已被导入 ⇒ 真 app_attr 命中 app.load_account_baseline
        # 而读到生产 account_initial_state.json（实测：隔离跑绿、全量跑红，initial_capital 5000≠4061）。
        self._start(mock.patch("r20_backend.dependencies.app_attr",
                               side_effect=lambda name, default=None: default))
        _patch("refresh_settings")
        self.admin = mock.Mock()
        _patch("require_admin_header", self.admin)
        self.superadmin = mock.Mock(return_value={"id": 1, "username": "root",
                                                  "role": "superadmin"})
        _patch("require_superadmin", self.superadmin)

        self.settings = mock.Mock()
        s = self.settings
        s.llm_base_url = ""
        s.llm_model = "m"
        s.llm_reasoning_effort = "high"
        s.llm_api_key = "sk-llm"
        s.notification_webhook = ""
        s.qq_bot_app_id = None
        s.tg_bot_token = None
        s.wechat_webhook = None
        s.okx_simulated = False
        s.okx_live_configured = True
        s.okx_demo_configured = False
        s.okx_api_key = "a"
        s.okx_secret_key = "b"
        s.okx_passphrase = "c"
        s.manual_close_enabled = True
        s.okx_environment = "live"
        _patch("settings", s)

        self.admin_auth = mock.Mock()
        self.admin_auth.has_users.return_value = True
        _patch("admin_auth", self.admin_auth)

    def _rec(self, index=0):
        return self.audits[index][0]


class FileHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-sys-health-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.object(A, "DATA_DIR", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_file_is_not_reported_as_fresh(self):
        out = A.file_health("nope.json", 60)
        self.assertEqual(out, {"name": "nope.json", "exists": False,
                               "age_seconds": None, "fresh": False})

    def test_fresh_and_stale_are_decided_by_double_the_interval(self):
        path = self.tmp / "x.json"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (time.time(), time.time()))
        fresh = A.file_health("x.json", 60)
        self.assertTrue(fresh["exists"])
        self.assertTrue(fresh["fresh"])
        self.assertLessEqual(fresh["age_seconds"], 2)
        self.assertIn("bytes", fresh)

        os.utime(path, (time.time() - 1000, time.time() - 1000))
        stale = A.file_health("x.json", 60)
        self.assertFalse(stale["fresh"], "1000s > 2×60s ⇒ 必须判 STALE")
        self.assertGreaterEqual(stale["age_seconds"], 999)


class LogTailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-sys-logs-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.object(A, "ROOT", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.tmp / "logs").mkdir(parents=True, exist_ok=True)

    def test_missing_log_says_so_instead_of_empty_string(self):
        self.assertEqual(A.log_tail("nope.log"), "暂无日志")

    def test_tail_is_clamped_to_between_one_and_two_hundred_lines(self):
        path = self.tmp / "logs" / "x.log"
        path.write_text("\n".join(str(i) for i in range(300)), encoding="utf-8")
        self.assertEqual(A.log_tail("x.log", 30).splitlines(), [str(i) for i in range(270, 300)])
        self.assertEqual(A.log_tail("x.log", 0).splitlines(), ["299"])
        self.assertEqual(len(A.log_tail("x.log", 999).splitlines()), 200)


class DecisionSummaryTests(_Base):
    def test_dict_payload_maps_rows_and_defaults_missing_fields(self):
        self._start(mock.patch.object(A, "read_json", return_value={
            "BTC-USDT-SWAP": {"decision": {"action": "BUY", "confidence": 90,
                                           "summary_reason": "突破"},
                              "time_str": "t1"},
            "ETH-USDT-SWAP": "坏行",
        }))
        rows = {r["instId"]: r for r in A.decision_summary()}
        self.assertEqual(rows["BTC-USDT-SWAP"]["action"], "BUY")
        self.assertEqual(rows["BTC-USDT-SWAP"]["confidence"], 90)
        self.assertEqual(rows["BTC-USDT-SWAP"]["summary"], "突破")
        self.assertEqual(rows["BTC-USDT-SWAP"]["updated_at"], "t1")
        self.assertEqual(rows["ETH-USDT-SWAP"]["action"], "WAIT")
        self.assertEqual(rows["ETH-USDT-SWAP"]["confidence"], 0)

    def test_non_dict_payload_yields_empty_list(self):
        self._start(mock.patch.object(A, "read_json", return_value=[1, 2]))
        self.assertEqual(A.decision_summary(), [])


class AdminConfigurationTests(_Base):
    def setUp(self):
        super().setUp()
        self._start(mock.patch.object(A, "load_account_baseline",
                                      return_value={"initial_capital": 1234.5}))
        self.rt = mock.Mock(return_value={"model": "rt-model",
                                          "reasoning_effort": "max"})
        self._start(mock.patch.object(A, "get_active_llm_runtime", self.rt))

    def test_runtime_model_and_effort_drive_the_editable_summary(self):
        out = A.get_admin_configuration()
        self.assertEqual(out["LLM 决策主脑"], "rt-model")
        self.assertEqual(out["LLM 思考强度"], "MAX")
        self.assertEqual(out["初始本金基准"], "1,234.50 USDT")
        self.assertEqual(out["管理员系统"], "账号密码 + 服务端会话")
        self.assertEqual(out["应急平仓机制"], "一次性 Token 复核已就绪")
        self.assertEqual(out["OKX 当前环境"], "实盘 LIVE")
        self.assertIn("实盘 LIVE", out["交易场所与路由"])

    def test_runtime_failure_falls_back_to_static_settings(self):
        self.rt.side_effect = RuntimeError("LLM 配置坏了")
        out = A.get_admin_configuration()
        self.assertEqual(out["LLM 决策主脑"], "m")
        self.assertEqual(out["LLM 思考强度"], "HIGH")

    def test_preferred_venue_is_loaded_and_defaults_to_auto(self):
        with mock.patch("r20_backend.exchanges.routing_policy.load_preferred_venue",
                        return_value="binance"):
            self.assertIn("选所模式: BINANCE", A.get_admin_configuration()["交易场所与路由"])
        with mock.patch("r20_backend.exchanges.routing_policy.load_preferred_venue",
                        side_effect=RuntimeError("policy bad")):
            self.assertIn("选所模式: AUTO", A.get_admin_configuration()["交易场所与路由"])

    def test_notification_channel_and_disabled_close_are_reported_honestly(self):
        self.settings.notification_webhook = "https://hook"
        self.assertEqual(A.get_admin_configuration()["通知告警通道"], "已配置多通道")
        self.settings.notification_webhook = ""
        self.settings.manual_close_enabled = False
        out = A.get_admin_configuration()
        self.assertEqual(out["通知告警通道"], "未配置")
        self.assertEqual(out["应急平仓机制"], "已禁用")

    def test_demo_mode_and_simulated_configuration_strings(self):
        self.settings.okx_simulated = True
        self.settings.okx_live_configured = False
        self.settings.okx_demo_configured = True
        out = A.get_admin_configuration()
        self.assertIn("模拟盘 DEMO", out["交易场所与路由"])
        self.assertEqual(out["OKX 当前环境"], "模拟盘 DEMO")
        self.assertEqual(out["OKX 实盘凭证"], "未配置")
        self.assertEqual(out["OKX 模拟盘凭证"], "已配置")


class RuntimeOverviewTests(_Base):
    def setUp(self):
        super().setUp()
        self.health_files = mock.Mock(return_value={"name": "f", "exists": True,
                                                    "age_seconds": 1, "fresh": True})
        self._start(mock.patch.object(A, "file_health", self.health_files))
        self._start(mock.patch.object(A, "read_json", return_value={"BTC": 1, "ETH": 2}))
        self._start(mock.patch.object(A, "log_tail", return_value="tail"))
        self._start(mock.patch.object(A, "recent_audit", return_value=[{"id": 1}]))
        self._start(mock.patch.object(A, "get_admin_configuration", return_value={"k": "v"}))
        self._start(mock.patch.object(A, "decision_summary", return_value=[{"instId": "BTC"}]))

    def test_all_fresh_is_live_and_trackers_are_counted(self):
        out = A.runtime_overview()
        self.assertEqual(out["data_health"]["overall"], "LIVE")
        self.assertEqual(len(out["data_health"]["files"]), 4)
        self.assertEqual(out["trackers"], 2)
        self.assertEqual(out["configuration"], {"k": "v"})
        self.assertEqual(out["decisions"], [{"instId": "BTC"}])
        self.assertEqual(out["logs"], {"trader": "tail", "backend": "tail", "scheduler": "tail"})
        self.assertEqual(out["audit"], [{"id": 1}])
        self.assertTrue(out["credentials"]["okx"])
        self.assertTrue(out["credentials"]["llm"])

    def test_any_stale_file_makes_the_whole_overview_stale(self):
        self.health_files.side_effect = [
            {"fresh": True}, {"fresh": False}, {"fresh": True}, {"fresh": True}]
        self.assertEqual(A.runtime_overview()["data_health"]["overall"], "STALE")

    def test_non_dict_trackers_count_as_zero(self):
        self._start(mock.patch.object(A, "read_json", return_value=[1, 2, 3]))
        self.assertEqual(A.runtime_overview()["trackers"], 0)


class GitHelperTests(_Base):
    def test_successful_git_strips_stdout(self):
        self._start(mock.patch.object(A.subprocess, "run", return_value=mock.Mock(
            returncode=0, stdout=" main \n", stderr="")))
        self.assertEqual(A.git(["branch", "--show-current"]), "main")

    def test_failed_git_raises_with_stderr(self):
        self._start(mock.patch.object(A.subprocess, "run", return_value=mock.Mock(
            returncode=128, stdout="", stderr="fatal: not a repo")))
        with self.assertRaises(RuntimeError) as ctx:
            A.git(["status"])
        self.assertIn("not a repo", str(ctx.exception))

    def test_timeout_is_reported_as_a_timeout(self):
        exc = A.subprocess.TimeoutExpired(cmd="git", timeout=30)
        self._start(mock.patch.object(A.subprocess, "run", side_effect=exc))
        with self.assertRaises(RuntimeError) as ctx:
            A.git(["fetch"])
        self.assertIn("timed out", str(ctx.exception))


class UpdateStatusTests(_Base):
    def _git_ok(self):
        mapping = {
            ("rev-parse", "--short", "HEAD"): "abc123",
            ("branch", "--show-current"): "main",
            ("status", "--porcelain", "-uno"): "",
            ("rev-parse", "--short", "origin/main"): "def456",
            ("rev-list", "--left-right", "--count", "HEAD...origin/main"): "1\t2",
        }

        def _run(cmd):
            key = tuple(cmd)
            if cmd[0] == "fetch":
                return "fetched"
            return mapping[key]
        return mock.Mock(side_effect=_run)

    def test_parses_ahead_behind_and_dirty(self):
        g = self._git_ok()
        self._start(mock.patch.object(A, "git", g))
        out = A.update_status()
        self.assertEqual(out, {"branch": "main", "local": "abc123", "remote": "def456",
                               "behind": 2, "ahead": 1, "dirty": False})

    def test_fetch_failure_is_tolerated_without_an_error_key(self):
        def _run(cmd):
            if cmd[0] == "fetch":
                raise RuntimeError("no network")
            if cmd[0] == "rev-parse" and cmd[-1] == "HEAD":
                return "abc123"
            if cmd[0] == "branch":
                return "main"
            if cmd[0] == "status":
                return " M x"
            raise RuntimeError("should not be reached")
        self._start(mock.patch.object(A, "git", mock.Mock(side_effect=_run)))
        out = A.update_status()
        self.assertNotIn("error", out)
        self.assertEqual(out["remote"], "")
        self.assertEqual((out["ahead"], out["behind"]), (0, 0))
        self.assertTrue(out["dirty"])

    def test_outer_failure_returns_an_error_payload(self):
        self._start(mock.patch.object(A, "git", side_effect=RuntimeError("git missing")))
        out = A.update_status()
        self.assertEqual(out["error"], "git missing")
        self.assertEqual(out["local"], "")


class HealthRouteTests(_Base):
    def test_llm_host_is_derived_and_credentials_reflect_settings(self):
        self.settings.llm_base_url = "https://api.example.com/v1"
        out = A.health()
        self.assertEqual(out["data_flow"]["llm_endpoint_host"], "api.example.com")
        self.assertTrue(out["credentials"]["okx_configured"])
        self.assertTrue(out["credentials"]["llm_configured"])
        self.assertEqual(out["status"], "ok")

    def test_env_fallback_and_not_configured(self):
        self.settings.llm_base_url = ""
        with mock.patch.dict(os.environ, {"LLM_BASE_URL": "https://env.example.com"},
                             clear=False):
            self.assertEqual(A.health()["data_flow"]["llm_endpoint_host"], "env.example.com")
        with mock.patch.dict(os.environ, {"LLM_BASE_URL": "", "OPENAI_BASE_URL": ""},
                             clear=False):
            self.assertEqual(A.health()["data_flow"]["llm_endpoint_host"], "NOT_CONFIGURED")
        self.settings.okx_api_key = ""
        self.assertFalse(A.health()["credentials"]["okx_configured"])

    def test_unparseable_base_url_degrades_to_not_configured(self):
        self.settings.llm_base_url = "https://ok.example"
        with mock.patch.object(A, "urlparse", side_effect=ValueError("bad url")):
            out = A.health()
        self.assertEqual(out["data_flow"]["llm_endpoint_host"], "NOT_CONFIGURED")


class StatusRouteTests(_Base):
    def test_public_status_is_only_a_harmless_shell(self):
        self._start(mock.patch.object(A, "script_state", side_effect=lambda n: {"name": n}))
        out = A.status()
        self.assertEqual(set(out), {"version", "mode", "scripts"})
        self.assertNotIn("trackers", out)
        self.assertNotIn("last_decisions", out)
        self.assertEqual(out["mode"], "read_only_control_plane")
        self.assertEqual(len(out["scripts"]), 5)


class AdminReadRoutesTests(_Base):
    def test_overview_requires_admin_and_returns_runtime_overview(self):
        self._start(mock.patch.object(A, "runtime_overview", return_value={"ok": 1}))
        self.assertEqual(A.admin_overview(x_r20_admin_token="tok"), {"ok": 1})
        self.admin.assert_called_once_with("tok")

    def test_audit_route_forwards_the_limit(self):
        rec = mock.Mock(return_value=[{"id": 9}])
        self._start(mock.patch.object(A, "recent_audit", rec))
        self.assertEqual(A.admin_audit(x_r20_admin_token="tok", limit=7),
                         {"records": [{"id": 9}]})
        rec.assert_called_once_with(7)

    def test_metrics_defaults_to_prometheus_text(self):
        import r20_backend.metrics as metrics_mod
        self._start(mock.patch.object(metrics_mod, "build_snapshot", return_value={"a": 1}))
        self._start(mock.patch.object(metrics_mod, "render_prometheus",
                                      return_value="r20_x 1\n"))
        out = A.admin_metrics(x_r20_admin_token="tok")
        self.assertIsInstance(out, PlainTextResponse)
        self.assertEqual(out.body, b"r20_x 1\n")
        self.assertIn("version=0.0.4", out.media_type)

    def test_metrics_json_format_returns_the_raw_snapshot(self):
        import r20_backend.metrics as metrics_mod
        self._start(mock.patch.object(metrics_mod, "build_snapshot", return_value={"a": 1}))
        self._start(mock.patch.object(metrics_mod, "render_prometheus"))
        self.assertEqual(A.admin_metrics(format="JSON", x_r20_admin_token="tok"),
                         {"a": 1})
        metrics_mod.render_prometheus.assert_not_called()

    def test_logs_reject_unknown_sources_instead_of_returning_empty(self):
        with self.assertRaises(HTTPException) as ctx:
            A.admin_logs(source="nope", x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("trader", ctx.exception.detail)

    def test_logs_map_a_known_source_to_its_real_file(self):
        self._start(mock.patch.object(A, "log_tail", return_value="line"))
        out = A.admin_logs(source="backend", lines=5, x_r20_admin_token="tok")
        self.assertEqual(out, {"source": "backend", "file": "uvicorn.log", "content": "line"})


class AdminRuntimeRouteTests(_Base):
    def setUp(self):
        super().setUp()
        self._start(mock.patch.object(A, "runtime_overview", return_value={"base": 1}))

    def test_llm_runtime_is_whitelisted_and_never_leaks_the_key(self):
        self._start(mock.patch.object(A, "get_active_llm_runtime", return_value={
            "model": "gpt", "provider_name": "P", "reasoning_effort": "low",
            "api_format": "anthropic_messages", "api_key": "sk-SECRET",
            "base_url": "https://secret.example.com"}))
        self._start(mock.patch.object(A, "read_json", return_value={}))
        out = A.admin_runtime(x_r20_admin_token="tok")
        self.assertEqual(set(out["llm_runtime"]),
                         {"model", "provider_name", "reasoning_effort", "api_format"})
        self.assertNotIn("sk-SECRET", str(out))
        self.assertNotIn("secret.example.com", str(out))

    def test_llm_runtime_failure_falls_back_to_defaults(self):
        self._start(mock.patch.object(A, "get_active_llm_runtime",
                                      side_effect=RuntimeError("boom")))
        self._start(mock.patch.object(A, "read_json", return_value={}))
        self.assertEqual(A.admin_runtime(x_r20_admin_token="tok")["llm_runtime"],
                         {"model": "", "provider_name": "默认",
                          "reasoning_effort": "high", "api_format": "openai_chat"})

    def test_full_decisions_normalise_confidence_and_fall_back_per_field(self):
        self._start(mock.patch.object(A, "get_active_llm_runtime", return_value={}))
        self._start(mock.patch.object(A, "read_json", return_value={
            "BTC-USDT-SWAP": {"decision": {"action": "BUY", "confidence": 90,
                                           "summary_reason": ""},
                              "thought_process": {"market_structure": "结构上行"},
                              "time_str": "t1"},
            "ETH-USDT-SWAP": {"action": "SELL", "confidence": 0.5, "reason": "平",
                              "timestamp": 123},
        }))
        rows = {r["instId"]: r for r in A.admin_runtime(x_r20_admin_token="tok")["full_decisions"]}
        self.assertEqual(rows["BTC-USDT-SWAP"]["confidence"], 0.9,
                         ">1 的置信度按百分比折成 0-1")
        self.assertEqual(rows["BTC-USDT-SWAP"]["reason"], "结构上行")
        self.assertEqual(rows["ETH-USDT-SWAP"]["confidence"], 0.5)
        self.assertEqual(rows["ETH-USDT-SWAP"]["timestamp"], "123")

    def test_full_decisions_accepts_list_payload_and_other_types(self):
        self._start(mock.patch.object(A, "get_active_llm_runtime", return_value={}))
        self._start(mock.patch.object(A, "read_json", return_value=[{"instId": "BTC"}]))
        self.assertEqual(A.admin_runtime(x_r20_admin_token="tok")["full_decisions"],
                         [{"instId": "BTC"}])
        self._start(mock.patch.object(A, "read_json", return_value="garbage"))
        self.assertEqual(A.admin_runtime(x_r20_admin_token="tok")["full_decisions"], [])


class AdminConfigRouteTests(_Base):
    def setUp(self):
        super().setUp()
        self._start(mock.patch.object(A, "load_account_baseline",
                                      return_value={"initial_capital": 4061.04,
                                                    "reset_time": "t"}))
        self._start(mock.patch.object(A, "get_active_llm_runtime",
                                      return_value={"model": "m"}))

    def test_get_config_exposes_editable_snapshot(self):
        out = A.admin_config(x_r20_admin_token="tok")
        self.assertEqual(out["authentication_mode"], "account-password")
        self.assertEqual(out["editable"]["okx_environment"], "live")
        self.assertEqual(out["editable"]["initial_capital"], 4061.04)
        self.assertEqual(out["editable"]["initial_capital_reset_time"], "t")

    def test_missing_baseline_falls_back_to_the_documented_default(self):
        self._start(mock.patch.object(A, "load_account_baseline", return_value={}))
        self.assertEqual(A.admin_config(x_r20_admin_token="tok")["editable"]["initial_capital"],
                         4061.04)


class UpdateAdminConfigTests(_Base):
    def setUp(self):
        super().setUp()
        self._start(mock.patch.object(A, "load_account_baseline",
                                      return_value={"initial_capital": 1.0,
                                                    "reset_time": ""}))
        self._start(mock.patch.object(A, "get_active_llm_runtime",
                                      return_value={"model": "m"}))
        self.update_env = mock.Mock()
        self._start(mock.patch.object(A, "update_env", self.update_env))
        self.save_secrets = mock.Mock()
        self._start(mock.patch.object(A, "save_secrets", self.save_secrets))
        # 必须沙箱化：真实 init/save_llm_config 会读**并写**生产 data/llm_models.json
        self.init_llm = mock.Mock(return_value={"active_model_id": "", "models": [],
                                                "providers": []})
        self._start(mock.patch.object(A, "init_llm_providers", self.init_llm))
        self.save_llm = mock.Mock()
        self._start(mock.patch.object(A, "save_llm_config", self.save_llm))
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-sys-cfg-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._start(mock.patch.object(A, "DATA_DIR", self.tmp))

    def test_non_sensitive_payload_uses_admin_not_superadmin(self):
        out = A.update_admin_config(AdminConfigUpdate(llm_model="m2"),
                                    x_r20_admin_token="tok")
        # 授权只用管理员（末尾的 admin_config 也会再调一次，故不断言"仅一次"）
        self.assertIn(mock.call("tok"), self.admin.call_args_list)
        self.superadmin.assert_not_called()
        self.update_env.assert_called_once()
        self.assertEqual(out["authentication_mode"], "account-password")

    def test_any_okx_or_close_flag_field_requires_superadmin(self):
        A.update_admin_config(AdminConfigUpdate(okx_simulated=False),
                              x_r20_admin_token="tok", x_r20_session="s")
        self.superadmin.assert_called_once_with("s")

    def test_url_protocol_validation_blocks_bad_endpoints(self):
        with self.assertRaises(HTTPException) as ctx:
            A.update_admin_config(AdminConfigUpdate(llm_base_url="ftp://x"),
                                  x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 400)
        with self.assertRaises(HTTPException) as ctx:
            A.update_admin_config(AdminConfigUpdate(notification_webhook="nope"),
                                  x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 400)
        self.update_env.assert_not_called()

    def test_environment_switch_grabs_the_trading_lock_and_409_when_busy(self):
        with mock.patch("fcntl.flock", side_effect=BlockingIOError()):
            with self.assertRaises(HTTPException) as ctx:
                A.update_admin_config(AdminConfigUpdate(okx_environment="demo"),
                                      x_r20_admin_token="tok", x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 409)
        self.update_env.assert_not_called()

    def test_same_environment_does_not_touch_the_lock(self):
        calls = []
        with mock.patch("fcntl.flock", side_effect=lambda *a, **k: calls.append(a)):
            A.update_admin_config(AdminConfigUpdate(okx_environment="live"),
                                  x_r20_admin_token="tok", x_r20_session="s")
        self.assertEqual(calls, [], "环境没变 ⇒ 不该抢锁")

    def test_secrets_are_saved_without_empty_values_and_env_is_written(self):
        A.update_admin_config(AdminConfigUpdate(okx_live_api_key="K",
                                                okx_live_secret_key=""),
                              x_r20_admin_token="tok", x_r20_session="s")
        self.save_secrets.assert_called_once_with({"OKX_LIVE_API_KEY": "K"})
        env_values = self.update_env.call_args[0][0]
        self.assertIsNone(env_values["LLM_BASE_URL"])
        self.assertIsNone(env_values["R20_OKX_ENV"])

    def test_llm_updates_rewrite_the_provider_and_model_entries(self):
        self.init_llm.return_value = {
            "active_model_id": "m9", "models": [{"id": "m9", "provider_id": "p1"}],
            "providers": [{"id": "p1", "base_url": "https://old", "api_key": "old"}]}
        A.update_admin_config(AdminConfigUpdate(llm_base_url="https://new/",
                                                llm_api_key="sk-new",
                                                llm_model="m9",
                                                llm_reasoning_effort="low"),
                              x_r20_admin_token="tok")
        cfg = self.save_llm.call_args[0][0]
        self.assertEqual(cfg["providers"][0]["base_url"], "https://new")
        self.assertEqual(cfg["providers"][0]["api_key"], "sk-new")
        self.assertEqual(cfg["models"][0]["base_url"], "https://new")
        self.assertEqual(cfg["active_model_id"], "m9")
        self.assertEqual(cfg["active_reasoning_effort"], "low")

    def test_llm_config_sync_failure_is_swallowed(self):
        self.init_llm.side_effect = RuntimeError("坏配置")
        out = A.update_admin_config(AdminConfigUpdate(llm_model="m2"),
                                    x_r20_admin_token="tok")
        self.assertEqual(out["authentication_mode"], "account-password")


class AdminOpsRoutesTests(_Base):
    def test_agents_route_reads_gateway_store(self):
        store = mock.Mock()
        store.job_runs.return_value = [{"id": 1}]
        store.model_stats.return_value = {"x": 1}
        store.model_calls.return_value = [{"id": 2}]
        self._start(mock.patch.object(A, "GatewayStore", return_value=store))
        self._start(mock.patch.object(A, "agent_statuses", return_value=["a"]))
        self._start(mock.patch.object(A, "secret_store_status", return_value={"ok": True}))
        out = A.admin_agents(x_r20_admin_token="tok")
        self.assertEqual(out["agents"], ["a"])
        self.assertEqual(out["model_stats"], {"x": 1})
        store.job_runs.assert_called_once_with(100)
        store.model_calls.assert_called_once_with(50)

    def test_plugins_are_builtin_only(self):
        self._start(mock.patch.object(A, "plugin_statuses", return_value=[{"name": "p"}]))
        out = A.admin_plugins(x_r20_admin_token="tok")
        self.assertEqual(out["installation_policy"], "builtin-only")
        self.assertEqual(out["plugins"], [{"name": "p"}])

    def test_about_reports_gateway_health_and_repository_identity(self):
        store = mock.Mock()
        store.stats.return_value = {"s": 1}
        store.event_health.return_value = {"e": 1}
        self._start(mock.patch.object(A, "GatewayStore", return_value=store))
        self._start(mock.patch.object(A, "read_pid", return_value=4242))
        self._start(mock.patch.object(A, "process_running", return_value=True))
        self._start(mock.patch.object(A, "scheduler_snapshot", return_value={"sch": 1}))
        self._start(mock.patch.object(A, "get_version", return_value="9.9.9"))
        self._start(mock.patch.object(A, "git", side_effect=lambda cmd: {
            ("branch", "--show-current"): "main",
            ("rev-parse", "--short", "HEAD"): "abc",
        }[tuple(cmd)]))
        self._start(mock.patch.object(A, "update_status", return_value={"behind": 0}))
        out = A.admin_about(x_r20_admin_token="tok")
        self.assertEqual(out["product"]["version"], "9.9.9")
        self.assertTrue(out["runtime"]["gateway"]["running"])
        self.assertEqual(out["runtime"]["gateway"]["pid"], 4242)
        self.assertEqual(out["repository"]["branch"], "main")
        self.assertEqual(out["repository"]["commit"], "abc")
        self.assertEqual(out["update"], {"behind": 0})
        self.assertEqual(out["security"]["plugin_policy"], "builtin-only")
        self.assertEqual(len(out["components"]), 3)

    def test_about_tolerates_a_stopped_gateway(self):
        store = mock.Mock()
        self._start(mock.patch.object(A, "GatewayStore", return_value=store))
        self._start(mock.patch.object(A, "read_pid", return_value=None))
        self._start(mock.patch.object(A, "process_running", return_value=False))
        self._start(mock.patch.object(A, "scheduler_snapshot", return_value={}))
        self._start(mock.patch.object(A, "git", side_effect=lambda cmd: ""))
        self._start(mock.patch.object(A, "update_status", return_value={}))
        out = A.admin_about(x_r20_admin_token="tok")
        self.assertFalse(out["runtime"]["gateway"]["running"])
        self.assertIsNone(out["runtime"]["gateway"]["pid"])


class UpdateApplicationTests(_Base):
    def test_confirmation_phrase_is_mandatory(self):
        with self.assertRaises(HTTPException) as ctx:
            A.update_application(UpdateRequest(confirmation="update"),
                                 x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_dirty_worktree_blocks_the_update(self):
        self._start(mock.patch.object(A, "update_status", return_value={
            "branch": "main", "local": "a", "remote": "b", "dirty": True}))
        self._start(mock.patch.object(A, "git"))
        with self.assertRaises(HTTPException) as ctx:
            A.update_application(UpdateRequest(confirmation="UPDATE R20"),
                                 x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_status_error_and_missing_remote_are_502(self):
        self._start(mock.patch.object(A, "update_status", return_value={"error": "git 坏了"}))
        with self.assertRaises(HTTPException) as ctx:
            A.update_application(UpdateRequest(confirmation="UPDATE R20"),
                                 x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 502)

        self._start(mock.patch.object(A, "update_status", return_value={
            "branch": "main", "local": "a", "remote": "", "dirty": False}))
        with self.assertRaises(HTTPException) as ctx:
            A.update_application(UpdateRequest(confirmation="UPDATE R20"),
                                 x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 502)

    def test_git_pull_failure_is_502(self):
        self._start(mock.patch.object(A, "update_status", return_value={
            "branch": "main", "local": "a", "remote": "b", "dirty": False}))
        self._start(mock.patch.object(A, "git", side_effect=RuntimeError("non-fast-forward")))
        with self.assertRaises(HTTPException) as ctx:
            A.update_application(UpdateRequest(confirmation="UPDATE R20"),
                                 x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("non-fast-forward", ctx.exception.detail)

    def test_updated_runs_the_frontend_build_and_asks_for_restart(self):
        self._start(mock.patch.object(A, "update_status", side_effect=[
            {"branch": "main", "local": "aaaa", "remote": "bbbb", "dirty": False},
            {"branch": "main", "local": "cccc", "remote": "bbbb", "dirty": False}]))
        self._start(mock.patch.object(A, "git", return_value="Already up to date."))
        npm = mock.Mock()
        self._start(mock.patch.object(A.subprocess, "run", npm))
        out = A.update_application(UpdateRequest(confirmation="UPDATE R20"),
                                   x_r20_admin_token="tok")
        self.assertTrue(out["updated"])
        self.assertTrue(out["restart_required"])
        self.assertIn("重启", out["restart_note"])
        self.assertEqual(self._rec()[0], "application.update")
        self.assertEqual(self._rec()[2]["before"], "aaaa")
        self.assertEqual(self._rec()[2]["after"], "cccc")
        npm.assert_called_once()
        self.assertEqual(npm.call_args[0][0][:2], ["npm", "run"])

    def test_no_change_skips_the_build_and_the_restart(self):
        same = {"branch": "main", "local": "aaaa", "remote": "bbbb", "dirty": False}
        self._start(mock.patch.object(A, "update_status", return_value=dict(same)))
        self._start(mock.patch.object(A, "git", return_value="Already up to date."))
        npm = mock.Mock()
        self._start(mock.patch.object(A.subprocess, "run", npm))
        out = A.update_application(UpdateRequest(confirmation="UPDATE R20"),
                                   x_r20_admin_token="tok")
        self.assertFalse(out["updated"])
        self.assertFalse(out["restart_required"])
        npm.assert_not_called()

    def test_frontend_build_failure_does_not_fail_the_update(self):
        self._start(mock.patch.object(A, "update_status", side_effect=[
            {"branch": "main", "local": "aaaa", "remote": "bbbb", "dirty": False},
            {"branch": "main", "local": "cccc", "remote": "bbbb", "dirty": False}]))
        self._start(mock.patch.object(A, "git", return_value="ok"))
        self._start(mock.patch.object(A.subprocess, "run",
                                      side_effect=OSError("npm 不在 PATH")))
        out = A.update_application(UpdateRequest(confirmation="UPDATE R20"),
                                   x_r20_admin_token="tok")
        self.assertTrue(out["updated"], "构建失败只吞掉，更新本身已成功")

    def test_auxiliary_update_status_routes_delegate(self):
        self._start(mock.patch.object(A, "update_status", return_value={"behind": 3}))
        self.assertEqual(A.admin_update_status(x_r20_admin_token="tok"), {"behind": 3})


if __name__ == "__main__":
    unittest.main()

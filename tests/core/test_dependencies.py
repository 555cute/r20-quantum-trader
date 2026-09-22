"""共享依赖与鉴权边界：**令牌常量时间比对、legacy 通道只在零用户时开、superadmin 必须显式**（第二百七十四刀，开新面 dependencies.py）。

先打印整个文件（111 行）再动笔。这是**所有管理端点的安全边界**（本会话前面收口的
routers 全都经它鉴权），也是 `routers/system.py` 里那个"局部 import app_attr"陷阱的源头。

| 语义 | 口径 |
|---|---|
| ★ **令牌比对必须常量时间** | `require_admin_token` 走 `hmac.compare_digest`（用例断言它被调用）⇒ 不能退化成 `==` 泄露前缀信息 |
| ★ **未配置令牌 = 503，错令牌 = 403** | 两者语义不同（一个是运维没配、一个是攻击者猜错）|
| ★ **legacy 令牌通道只在"零用户"时开** | `has_users()` 为真时，光凭 admin token **不再能登录**（否则等于留了永久后门）；返回的 legacy 用户角色是 `legacy` |
| ★ **superadmin 必须显式** | 会话有效但角色不是 `superadmin` ⇒ **403**；无会话 ⇒ **401** |
| ★ **app 模块可覆盖鉴权** | `r20_backend.app` 上若挂了**不同**的 `require_admin_header`/`require_superadmin`，一律委派给它（并容忍 1 参数旧签名）|
| ★ **损坏 ≠ 缺失（但都不炸）** | `read_json` 两种都返回默认值，但**损坏必须往 stderr 吼 CRITICAL**（旧实现静默 ⇒ 前端把"数据损坏"渲染成"确实没有"）|
"""

import io
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from r20_backend import dependencies as DEPS


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def _settings(self, admin_token="", setup_token=""):
        return self._start(mock.patch.object(
            DEPS, "settings",
            types.SimpleNamespace(admin_token=admin_token, setup_token=setup_token)))


class ModuleConstantTests(unittest.TestCase):
    def test_paths_and_context_var_defaults(self):
        self.assertEqual(DEPS.DATA_DIR, DEPS.ROOT / "data")
        self.assertEqual(DEPS.SCRIPTS_DIR, DEPS.ROOT / "scripts")
        self.assertEqual(DEPS.VUE_DIST, DEPS.ROOT / "frontend" / "dist")
        self.assertTrue(DEPS.BACKUP_LOG_FILE.name.endswith(".log"))
        self.assertEqual(DEPS.REQUEST_SESSION.get(), "")

    def test_pool_bounds_are_integers_and_ordered(self):
        self.assertIsInstance(DEPS.MAX_POOL_SIZE, int)
        self.assertIsInstance(DEPS.MIN_POOL_SIZE, int)
        self.assertLess(DEPS.MIN_POOL_SIZE, DEPS.MAX_POOL_SIZE)


class AppAttrTests(unittest.TestCase):
    def test_missing_app_module_returns_the_default(self):
        with mock.patch.dict(sys.modules, {"r20_backend.app": None}):
            self.assertEqual(DEPS.app_attr("WHATEVER", "fallback"), "fallback")

    def test_attribute_present_is_returned(self):
        fake = types.SimpleNamespace(MARKER="value")
        with mock.patch.dict(sys.modules, {"r20_backend.app": fake}):
            self.assertEqual(DEPS.app_attr("MARKER", "fallback"), "value")

    def test_missing_attribute_returns_the_default(self):
        with mock.patch.dict(sys.modules, {"r20_backend.app": types.SimpleNamespace()}):
            self.assertIsNone(DEPS.app_attr("NOPE"))
            self.assertEqual(DEPS.app_attr("NOPE", 7), 7)

    def test_none_default(self):
        with mock.patch.dict(sys.modules, {"r20_backend.app": None}):
            self.assertIsNone(DEPS.app_attr("ANY"))


class AuthStoreResolutionTests(unittest.TestCase):
    def test_app_level_store_wins_when_present(self):
        store = mock.Mock()
        fake = types.SimpleNamespace(admin_auth=store)
        with mock.patch.dict(sys.modules, {"r20_backend.app": fake}):
            self.assertIs(DEPS.get_auth_store(), store)

    def test_module_level_store_is_the_fallback(self):
        with mock.patch.dict(sys.modules, {"r20_backend.app": types.SimpleNamespace()}):
            self.assertIs(DEPS.get_auth_store(), DEPS.admin_auth)

    def test_no_app_module_falls_back(self):
        with mock.patch.dict(sys.modules, {"r20_backend.app": None}):
            self.assertIs(DEPS.get_auth_store(), DEPS.admin_auth)


class RequireAdminTokenTests(_Base):
    def test_unconfigured_tokens_yield_503(self):
        self._settings()
        with self.assertRaises(HTTPException) as ctx:
            DEPS.require_admin_token("anything")
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("R20_SETUP_TOKEN", ctx.exception.detail)

    def test_admin_token_matches(self):
        self._settings(admin_token="secret")
        self.assertIsNone(DEPS.require_admin_token("secret"))

    def test_setup_token_is_the_fallback_when_admin_token_is_unset(self):
        self._settings(setup_token="boot")
        self.assertIsNone(DEPS.require_admin_token("boot"))

    def test_admin_token_takes_precedence_over_setup_token(self):
        self._settings(admin_token="real", setup_token="boot")
        self.assertIsNone(DEPS.require_admin_token("real"))
        with self.assertRaises(HTTPException) as ctx:
            DEPS.require_admin_token("boot")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_wrong_token_is_403(self):
        self._settings(admin_token="real")
        with self.assertRaises(HTTPException) as ctx:
            DEPS.require_admin_token("guess")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("无效", ctx.exception.detail)

    def test_comparison_goes_through_hmac_compare_digest(self):
        self._settings(admin_token="real")
        comparer = self._start(mock.patch.object(DEPS.hmac, "compare_digest",
                                                 return_value=True))
        DEPS.require_admin_token("whatever")
        comparer.assert_called_once_with("whatever", "real")


class CurrentAdminTests(_Base):
    def setUp(self):
        super().setUp()
        self.store = mock.Mock()
        self._start(mock.patch.object(DEPS, "get_auth_store", return_value=self.store))

    def test_valid_session_returns_the_user(self):
        user = {"id": 1, "username": "ops", "role": "admin"}
        self.store.validate_session.return_value = user
        self.assertIs(DEPS.current_admin("sess"), user)
        self.store.validate_session.assert_called_once_with("sess")

    def test_missing_session_and_token_is_401(self):
        self.store.validate_session.return_value = None
        with self.assertRaises(HTTPException) as ctx:
            DEPS.current_admin()
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("重新登录", ctx.exception.detail)

    def test_legacy_token_works_only_while_there_are_no_users(self):
        self.store.validate_session.return_value = None
        self.store.has_users.return_value = False
        self._settings(admin_token="boot-token")
        user = DEPS.current_admin(x_r20_admin_token="boot-token")
        self.assertEqual(user, {"id": 0, "username": "legacy-token",
                                "role": "legacy", "enabled": 1})

    def test_a_populated_store_closes_the_legacy_door(self):
        self.store.validate_session.return_value = None
        self.store.has_users.return_value = True
        self._settings(admin_token="boot-token")
        with self.assertRaises(HTTPException) as ctx:
            DEPS.current_admin(x_r20_admin_token="boot-token")
        self.assertEqual(ctx.exception.status_code, 401,
                         "已有用户时，光凭 admin token 不得再登录")

    def test_legacy_path_still_validates_the_token(self):
        self.store.validate_session.return_value = None
        self.store.has_users.return_value = False
        self._settings(admin_token="real")
        with self.assertRaises(HTTPException) as ctx:
            DEPS.current_admin(x_r20_admin_token="wrong")
        self.assertEqual(ctx.exception.status_code, 403)


class RequireAdminHeaderTests(_Base):
    def test_no_app_override_delegates_to_current_admin(self):
        self._start(mock.patch.dict(sys.modules, {"r20_backend.app": None}))
        current = self._start(mock.patch.object(DEPS, "current_admin",
                                                return_value={"id": 5}))
        out = DEPS.require_admin_header("tok", "sess")
        current.assert_called_once_with("sess", "tok")
        self.assertEqual(out, {"id": 5})

    def test_non_string_headers_are_dropped_and_the_context_var_is_used(self):
        self._start(mock.patch.dict(sys.modules, {"r20_backend.app": None}))
        current = self._start(mock.patch.object(DEPS, "current_admin",
                                                return_value={"id": 5}))
        token = DEPS.REQUEST_SESSION.set("ctx-session")
        self.addCleanup(DEPS.REQUEST_SESSION.reset, token)
        DEPS.require_admin_header(object(), object())
        current.assert_called_once_with("ctx-session", None)

    def test_app_override_is_used_when_it_is_a_different_callable(self):
        override = mock.Mock(return_value={"id": 9})
        fake = types.SimpleNamespace(require_admin_header=override)
        with mock.patch.dict(sys.modules, {"r20_backend.app": fake}):
            out = DEPS.require_admin_header("tok", "sess")
        override.assert_called_once_with("tok", "sess")
        self.assertEqual(out, {"id": 9})

    def test_legacy_single_argument_override_falls_back(self):
        override = mock.Mock(side_effect=[TypeError("takes 1 positional argument"), {"id": 9}])
        fake = types.SimpleNamespace(require_admin_header=override)
        with mock.patch.dict(sys.modules, {"r20_backend.app": fake}):
            out = DEPS.require_admin_header("tok", "sess")
        self.assertEqual(override.call_args_list[1], mock.call("tok"))
        self.assertEqual(out, {"id": 9})

    def test_the_same_function_object_is_not_treated_as_an_override(self):
        fake = types.SimpleNamespace(require_admin_header=DEPS.require_admin_header)
        current = self._start(mock.patch.object(DEPS, "current_admin",
                                                return_value={"id": 1}))
        with mock.patch.dict(sys.modules, {"r20_backend.app": fake}):
            DEPS.require_admin_header("tok", "sess")
        current.assert_called_once_with("sess", "tok")


class RequireSuperadminTests(_Base):
    def setUp(self):
        super().setUp()
        self.store = mock.Mock()
        self._start(mock.patch.object(DEPS, "get_auth_store", return_value=self.store))
        self._start(mock.patch.dict(sys.modules, {"r20_backend.app": None}))

    def test_superadmin_passes(self):
        user = {"id": 1, "role": "superadmin"}
        self.store.validate_session.return_value = user
        self.assertIs(DEPS.require_superadmin("sess"), user)

    def test_plain_admin_is_403(self):
        self.store.validate_session.return_value = {"id": 2, "role": "admin"}
        with self.assertRaises(HTTPException) as ctx:
            DEPS.require_superadmin("sess")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("超级管理员", ctx.exception.detail)

    def test_no_session_is_401(self):
        self.store.validate_session.return_value = None
        with self.assertRaises(HTTPException) as ctx:
            DEPS.require_superadmin("sess")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_context_var_session_is_used_when_header_is_not_a_string(self):
        self.store.validate_session.return_value = {"id": 3, "role": "superadmin"}
        token = DEPS.REQUEST_SESSION.set("ctx")
        self.addCleanup(DEPS.REQUEST_SESSION.reset, token)
        DEPS.require_superadmin(object())
        self.store.validate_session.assert_called_once_with("ctx")

    def test_app_override_wins(self):
        override = mock.Mock(return_value={"id": 9, "role": "superadmin"})
        fake = types.SimpleNamespace(require_superadmin=override)
        with mock.patch.dict(sys.modules, {"r20_backend.app": fake}):
            out = DEPS.require_superadmin("sess")
        override.assert_called_once_with("sess")
        self.assertEqual(out["id"], 9)


class ReadJsonTests(_Base):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-deps-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._start(mock.patch.object(DEPS, "DATA_DIR", self.tmp))

    def _capture_stderr(self):
        return self._start(mock.patch.object(sys, "stderr", io.StringIO()))

    def test_valid_file_is_parsed(self):
        (self.tmp / "ok.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
        self.assertEqual(DEPS.read_json("ok.json", {}), {"a": 1})

    def test_missing_file_returns_the_default_silently(self):
        stderr = self._capture_stderr()
        self.assertEqual(DEPS.read_json("nope.json", {"fallback": True}),
                         {"fallback": True})
        self.assertEqual(stderr.getvalue(), "", "缺失是常态，不该刷日志")

    def test_corrupt_file_returns_the_default_but_shouts(self):
        (self.tmp / "bad.json").write_text("{not json", encoding="utf-8")
        stderr = self._capture_stderr()
        self.assertEqual(DEPS.read_json("bad.json", "default"), "default")
        self.assertIn("CRITICAL", stderr.getvalue())
        self.assertIn("bad.json", stderr.getvalue())

    def test_unreadable_file_returns_the_default_with_a_warning(self):
        (self.tmp / "dir.json").mkdir()
        stderr = self._capture_stderr()
        self.assertEqual(DEPS.read_json("dir.json", "default"), "default")
        self.assertIn("warn", stderr.getvalue())
        self.assertIn("dir.json", stderr.getvalue())

    def test_default_is_returned_verbatim(self):
        sentinel = object()
        stderr = self._capture_stderr()
        self.assertIs(DEPS.read_json("missing.json", sentinel), sentinel)


class ScriptStateTests(_Base):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-deps-scripts-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._start(mock.patch.object(DEPS, "SCRIPTS_DIR", self.tmp))

    def test_existing_script(self):
        (self.tmp / "ai_factor_trader.py").write_text("x", encoding="utf-8")
        state = DEPS.script_state("ai_factor_trader.py")
        self.assertEqual(state["name"], "ai_factor_trader.py")
        self.assertTrue(state["exists"])
        self.assertEqual(state["path"], str(self.tmp / "ai_factor_trader.py"))

    def test_missing_script_reports_exists_false(self):
        state = DEPS.script_state("ghost.py")
        self.assertFalse(state["exists"])
        self.assertIn("ghost.py", state["path"])


if __name__ == "__main__":
    unittest.main()

"""管理员鉴权路由：**限速先于记数、登出先取证、改密先验旧密码**（第二百五十六刀，开新面 auth.py）。

先打印整个文件（142 行）再动笔。八条路由的骨架一致，权限与失败映射如下：

| 路由 | 权限 | 失败映射 |
|---|---|---|
| GET  status | 公开 | 只报 `initialized`（有没有账号）与固定会话时长 |
| POST login | 公开 | 限速 ⇒ **429 + `Retry-After`**；凭证错 ⇒ **401** |
| POST logout | 公开 | **永不 401**（幂等）；有会话才留痕 |
| GET  me | 登录态 | `legacy` 令牌 ⇒ **401**（要求走账号密码）|
| GET/POST users | **超管** | 建号 `ValueError` ⇒ 400 |
| PUT  enabled | **超管** | `ValueError` ⇒ **409** |
| POST unlock | **超管** | 确认短语不符 ⇒ 400；查无此人 ⇒ **404** |
| PUT  password | 本人 / 超管 | 旧密码错或越权 ⇒ **403**；口令不合规 ⇒ 400 |

三条顺序语义是本刀的重点（顺序错了语义就反了）：

1. **限速判定在「记数」之前** —— 被 429 拦下的请求**不**进尝试窗口（否则封锁会被自己的
   重试无限续期），也不触达凭证校验；
2. **登出先校验、后撤销** —— 身份必须在 `logout()` 抹掉会话**之前**取到，否则审计日志
   记不出「是谁登出的」（这正是失败语义手册反复出现的「先取证再动手」）；
3. **改密先验旧密码、再落盘** —— 本人改密时旧密码不对 ⇒ 403 且**一次写都没发生**。

审计只记**事实**：登录成功记回包里的**规范**用户名（不是请求里的大小写变体），
所有分支的审计载荷都**不含密码**（`success`/`failed`/`rate_limited` 一视同仁）。
"""

import unittest
from unittest import mock

from fastapi import HTTPException

from r20_backend.routers import auth as A
from r20_backend.schemas import (
    AdminCreateRequest,
    AdminEnabledRequest,
    AdminLoginRequest,
    AdminPasswordRequest,
    AdminUnlockRequest,
)


class AuthRoutesTest(unittest.TestCase):
    def setUp(self):
        self.audits = []
        self.rec_audit = lambda *a, **k: self.audits.append((a, k))

        def _patch(target, new=mock.DEFAULT, **kwargs):
            patcher = mock.patch.object(A, target, new, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

        _patch("app_attr", return_value=self.rec_audit)
        self.store = mock.Mock()
        _patch("get_auth_store", return_value=self.store)
        _patch("resolve_client_ip", return_value="203.0.113.7")
        _patch("resolve_user_agent", return_value="pytest-UA")
        self.guard = mock.Mock()
        self.guard.check.return_value = (True, 0)
        _patch("login_guard", self.guard)
        self.current_admin = mock.Mock(return_value={"id": 1, "username": "root",
                                                     "role": "admin"})
        _patch("current_admin", self.current_admin)
        self.superadmin = mock.Mock(return_value={"id": 1, "username": "root",
                                                  "role": "superadmin"})
        _patch("require_superadmin", self.superadmin)

    def _rec(self, index=0):
        return self.audits[index][0]

    # ── GET /auth/status ──────────────────────────────────
    def test_status_reports_initialized_and_fixed_session_window(self):
        self.store.has_users.return_value = True
        self.assertEqual(A.admin_auth_status(),
                         {"initialized": True, "mode": "account-password",
                          "session_hours": 12})
        self.store.has_users.return_value = False
        self.assertIs(A.admin_auth_status()["initialized"], False)

    # ── POST /login ───────────────────────────────────────
    def test_login_success_audits_canonical_username_and_resolved_origin(self):
        self.store.login.return_value = {"user": {"username": "Root"}, "session": "tok"}
        out = A.admin_login(mock.Mock(), AdminLoginRequest(username="root", password="pw"))
        # 回包原样透传
        self.assertEqual(out["session"], "tok")
        # 计数走的是解析后的 IP，不是请求里伪造的头
        self.guard.note_attempt.assert_called_once_with("203.0.113.7")
        args, kwargs = self.audits[0]
        self.assertEqual(args[0], "admin.login")
        self.assertEqual(args[1], "success")
        self.assertEqual(args[2]["username"], "Root", "记规范用户名，不记请求里的大小写变体")
        self.assertEqual(kwargs["ip"], "203.0.113.7")
        self.assertEqual(kwargs["user_agent"], "pytest-UA")
        self.assertNotIn("password", args[2])

    def test_rate_limited_is_429_with_retry_after_and_counts_no_attempt(self):
        self.guard.check.return_value = (False, 600)
        with self.assertRaises(HTTPException) as ctx:
            A.admin_login(mock.Mock(), AdminLoginRequest(username="root", password="pw"))
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.headers["Retry-After"], "600")
        # 被拦下的请求不进窗口，也不触达凭证校验
        self.guard.note_attempt.assert_not_called()
        self.store.login.assert_not_called()
        args, _ = self.audits[0]
        self.assertEqual(args[1], "rate_limited")
        self.assertEqual(args[2]["username"], "root")
        self.assertEqual(args[2]["ip"], "203.0.113.7")

    def test_login_failure_is_401_counts_a_failure_and_audits_failed(self):
        self.store.login.side_effect = PermissionError("用户名或密码错误")
        with self.assertRaises(HTTPException) as ctx:
            A.admin_login(mock.Mock(), AdminLoginRequest(username="root", password="pw"))
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "用户名或密码错误")
        self.guard.note_attempt.assert_called_once_with("203.0.113.7")
        self.guard.note_failure.assert_called_once_with("203.0.113.7")
        args, _ = self.audits[0]
        self.assertEqual(args[1], "failed")
        self.assertNotIn("password", args[2])

    def test_password_never_reaches_any_audit_branch(self):
        for status in ("success", "failed", "rate_limited"):
            self.audits.clear()
            if status == "success":
                self.store.login.return_value = {"user": {"username": "root"}, "session": "t"}
                self.store.login.side_effect = None
                A.admin_login(mock.Mock(), AdminLoginRequest(username="root",
                                                             password="S3cret-Pw!"))
            elif status == "failed":
                self.store.login.side_effect = PermissionError("x")
                with self.assertRaises(HTTPException):
                    A.admin_login(mock.Mock(), AdminLoginRequest(username="root",
                                                                 password="S3cret-Pw!"))
            else:
                self.guard.check.return_value = (False, 1)
                with self.assertRaises(HTTPException):
                    A.admin_login(mock.Mock(), AdminLoginRequest(username="root",
                                                                 password="S3cret-Pw!"))
                self.guard.check.return_value = (True, 0)
            for args, kwargs in self.audits:
                self.assertNotIn("S3cret-Pw!", str(args) + str(kwargs))

    # ── POST /logout ──────────────────────────────────────
    def test_logout_takes_identity_before_revoking_the_session(self):
        order = []
        self.store.validate_session.side_effect = lambda tok: (
            order.append("validate") or {"username": "root"})
        self.store.logout.side_effect = lambda tok: order.append("logout")
        out = A.admin_logout(mock.Mock(), x_r20_session="tok")
        self.assertEqual(out, {"logged_out": True})
        self.assertEqual(order, ["validate", "logout"], "先取证，后撤销")
        self.store.logout.assert_called_once_with("tok")
        args, kwargs = self.audits[0]
        self.assertEqual(args[0], "admin.logout")
        self.assertEqual(args[1], "success")
        self.assertEqual(args[2]["username"], "root")

    def test_logout_without_a_session_still_reports_logged_out_and_audits_nothing(self):
        self.store.validate_session.return_value = None
        self.assertEqual(A.admin_logout(mock.Mock(), x_r20_session=None), {"logged_out": True})
        # 缺失令牌按空串处理，撤销照发（幂等），但无人可记 ⇒ 不留痕
        self.store.logout.assert_called_once_with("")
        self.assertEqual(self.audits, [])

    # ── GET /auth/me ──────────────────────────────────────
    def test_me_rejects_legacy_token_with_actionable_message(self):
        self.current_admin.return_value = {"id": 0, "username": "legacy-token",
                                           "role": "legacy"}
        with self.assertRaises(HTTPException) as ctx:
            A.admin_me(x_r20_session="tok")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("账号密码", ctx.exception.detail)

    def test_me_returns_the_user_object(self):
        self.current_admin.return_value = {"id": 3, "username": "alice", "role": "admin"}
        self.assertEqual(A.admin_me(x_r20_session="tok"),
                         {"user": {"id": 3, "username": "alice", "role": "admin"}})

    # ── users ─────────────────────────────────────────────
    def test_users_list_requires_superadmin_and_reports_current_id(self):
        self.store.list_users.return_value = [{"id": 1, "username": "root"}]
        out = A.admin_users(x_r20_session="tok")
        self.superadmin.assert_called_once_with("tok")
        self.assertEqual(out, {"users": [{"id": 1, "username": "root"}], "current_user_id": 1})

    def test_create_user_admin_only_not_audited_on_rejection(self):
        self.store.create_user.side_effect = ValueError("口令太弱")
        with self.assertRaises(HTTPException) as ctx:
            A.create_admin_user(AdminCreateRequest(username="bob",
                                                   password="longpassword123"),
                                x_r20_session="tok")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.audits, [], "失败**不**记 success 审计")

    def test_create_user_success_audits_actor_and_target_without_password(self):
        self.store.create_user.return_value = {"id": 2, "username": "bob", "role": "admin"}
        out = A.create_admin_user(AdminCreateRequest(username="bob",
                                                     password="longpassword123",
                                                     role="admin"),
                                  x_r20_session="tok")
        self.assertEqual(out, {"created": {"id": 2, "username": "bob", "role": "admin"}})
        args, _ = self.audits[0]
        self.assertEqual(args[0], "admin.user.create")
        self.assertEqual(args[2]["actor"], "root")
        self.assertEqual(args[2]["username"], "bob")
        self.assertNotIn("password", args[2])

    def test_enable_toggle_conflict_is_409(self):
        self.store.set_enabled.side_effect = ValueError("不能停用最后一个超管")
        with self.assertRaises(HTTPException) as ctx:
            A.update_admin_enabled(2, AdminEnabledRequest(enabled=False), x_r20_session="tok")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.audits, [])

    def test_enable_toggle_success_audits_and_returns_fresh_user(self):
        self.store.get_user.return_value = {"id": 2, "enabled": 1}
        out = A.update_admin_enabled(2, AdminEnabledRequest(enabled=True), x_r20_session="tok")
        self.store.set_enabled.assert_called_once_with(2, True, 1)
        self.assertEqual(out, {"user": {"id": 2, "enabled": 1}})
        self.assertEqual(self.audits[0][0][0], "admin.user.enabled")

    # ── unlock ────────────────────────────────────────────
    def test_unlock_requires_the_exact_confirmation_phrase(self):
        with self.assertRaises(HTTPException) as ctx:
            A.unlock_admin_user(7, AdminUnlockRequest(confirmation="UNLOCK ADMIN 8"),
                                x_r20_session="tok")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("UNLOCK ADMIN 7", ctx.exception.detail)
        # 短语不符 ⇒ 一次解锁都没发生，也不留痕
        self.store.unlock_user.assert_not_called()
        self.assertEqual(self.audits, [])

    def test_unlock_accepts_the_phrase_after_strip_upper_normalisation(self):
        self.store.get_user.return_value = {"id": 7, "enabled": 1}
        out = A.unlock_admin_user(7, AdminUnlockRequest(confirmation="  unlock admin 7  "),
                                  x_r20_session="tok")
        self.store.unlock_user.assert_called_once_with(7)
        self.assertEqual(out["user"]["id"], 7)
        self.assertEqual(self.audits[0][0][0], "admin.user.unlock")

    def test_unlock_unknown_user_is_404(self):
        self.store.unlock_user.side_effect = ValueError("查无此人")
        with self.assertRaises(HTTPException) as ctx:
            A.unlock_admin_user(99, AdminUnlockRequest(confirmation="UNLOCK ADMIN 99"),
                                x_r20_session="tok")
        self.assertEqual(ctx.exception.status_code, 404)

    # ── password ──────────────────────────────────────────
    def test_legacy_token_cannot_change_any_password(self):
        self.current_admin.return_value = {"id": 0, "username": "legacy-token",
                                           "role": "legacy"}
        with self.assertRaises(HTTPException) as ctx:
            A.update_admin_password(0, AdminPasswordRequest(current_password="old",
                                                            new_password="longpassword123"),
                                    x_r20_session="tok")
        self.assertEqual(ctx.exception.status_code, 401)
        self.store.change_password.assert_not_called()

    def test_non_superadmin_cannot_change_someone_else_password(self):
        self.current_admin.return_value = {"id": 1, "username": "alice", "role": "admin"}
        with self.assertRaises(HTTPException) as ctx:
            A.update_admin_password(2, AdminPasswordRequest(current_password="old",
                                                            new_password="longpassword123"),
                                    x_r20_session="tok")
        self.assertEqual(ctx.exception.status_code, 403)
        self.store.change_password.assert_not_called()

    def test_self_change_verifies_the_current_password_before_writing(self):
        self.current_admin.return_value = {"id": 1, "username": "alice", "role": "admin"}
        self.store.verify_password.return_value = False
        with self.assertRaises(HTTPException) as ctx:
            A.update_admin_password(1, AdminPasswordRequest(current_password="wrong",
                                                            new_password="longpassword123"),
                                    x_r20_session="tok")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("当前密码", ctx.exception.detail)
        self.store.change_password.assert_not_called()
        self.assertEqual(self.audits, [])

    def test_superadmin_resets_another_password_without_the_current_one(self):
        self.current_admin.return_value = {"id": 1, "username": "root", "role": "superadmin"}
        out = A.update_admin_password(2, AdminPasswordRequest(new_password="longpassword123"),
                                      x_r20_session="tok")
        # 超管改别人的密码：不索要旧密码 ⇒ 一次 verify 都不该有
        self.store.verify_password.assert_not_called()
        self.store.change_password.assert_called_once_with(2, "longpassword123")
        self.assertEqual(out, {"changed": True, "reauthenticate": True})
        self.assertEqual(self.audits[0][0][0], "admin.password.update")
        self.assertNotIn("new_password", self.audits[0][0][2])
        # 本路由用 current_admin 判身份 + 用 role 判超管，不经过 require_superadmin
        self.superadmin.assert_not_called()

    def test_password_policy_violation_is_400(self):
        self.current_admin.return_value = {"id": 1, "username": "root", "role": "superadmin"}
        self.store.change_password.side_effect = ValueError("口令不合规")
        with self.assertRaises(HTTPException) as ctx:
            A.update_admin_password(1, AdminPasswordRequest(current_password="old",
                                                            new_password="longpassword123"),
                                    x_r20_session="tok")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.audits, [])


if __name__ == "__main__":
    unittest.main()

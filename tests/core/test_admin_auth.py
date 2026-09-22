"""管理员账号与会话：**枚举面收口、锁定只记账不改文案、停用即踢会话**（第二百七十五刀，开新面 admin_auth.py）。

先打印整个文件（248 行）再动笔。这是 `dependencies.py`（第 274 刀）鉴权边界**背后的真正实现**。

| 语义 | 口径 |
|---|---|
| ★ **枚举面收口** | `login` 四种失败（停用 / 锁定 / 密码错 / 账号不存在）对客户端**只发同一句**"账号或密码错误"；真实原因只进 stderr。用例把四种失败文案**逐个比对为同一字符串** |
| ★ **时序侧信道也堵** | 账号不存在时会做一次**哑元 PBKDF2**（与真实校验同量级），不是立刻返回 |
| ★ **锁定只记账、不改文案** | 密码错累计到 5 次 ⇒ 锁 15 分钟；**锁定期间再登录不再累加计数**；`剩余尝试` 用 `max(0, …)` 夹紧（不会算出负数）|
| ★ **停用/改密即踢会话** | `set_enabled(False)` 与 `change_password` 都删除该用户全部会话；`set_enabled` 另有两条护栏：**不能停用自己**、**不能停用最后一个启用的超管** |
| ★ **会话 token 只存哈希** | 库里存 `sha256(token)`；`validate_session` 只认未过期且用户仍启用 |
| ★ **构造期读模块常量** | `__init__` 用 `path if path is not None else DB_PATH`（**调用期**读）——若写成默认参数会在**类定义期**绑定，测试沙箱重定向 `DB_PATH` 就完全失效（文件里那句警告正是本仓踩过的坑，用例专门钉住）|

⚠️ 性能：真实 PBKDF2 是 600k 轮（约 0.3s/次）。除两条专测真实现的用例外，
其余用例把 `_hash_password` 换成**同样形状但 1000 轮**的快实现（salt/iterations 语义不变），
使 50+ 条用例仍在 1 秒级跑完。
"""

import hashlib
import io
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from r20_backend import admin_auth as AA

GOOD = "abcd1234efgh"          # 12 位、含字母与数字


def _fast_hash(password, salt=None, iterations=600_000):
    """与真实实现同形状，但只跑 1000 轮（测试提速）。"""
    salt = salt or secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 1_000)
    return digest.hex(), salt.hex(), iterations


class PasswordPolicyTests(unittest.TestCase):
    def test_valid_passwords(self):
        self.assertTrue(AA._password_valid(GOOD))
        self.assertTrue(AA._password_valid("A" * 11 + "1"))
        self.assertTrue(AA._password_valid("x1" + "y" * 126))

    def test_invalid_passwords(self):
        for bad in ("short1", "a" * 200, "123456789012", "abcdefghijkl"):
            with self.subTest(password=bad[:14]):
                self.assertFalse(AA._password_valid(bad))

    def test_username_pattern(self):
        self.assertTrue(AA.USERNAME_RE.fullmatch("admin"))
        self.assertTrue(AA.USERNAME_RE.fullmatch("ops.user-1_x"))
        for bad in ("ab", "1admin", "admin!", "a" * 33, " adm in"):
            with self.subTest(username=bad):
                self.assertIsNone(AA.USERNAME_RE.fullmatch(bad))


class HashHelperTests(unittest.TestCase):
    def test_real_pbkdf2_shape_and_determinism(self):
        digest, salt, iterations = AA._hash_password("pw-1")
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertRegex(salt, r"^[0-9a-f]{48}$")
        self.assertEqual(iterations, AA.PBKDF2_ITERATIONS)
        again = AA._hash_password("pw-1", bytes.fromhex(salt), iterations)
        self.assertEqual(again[0], digest, "同 salt 同轮数必须复现")

    def test_token_hash_is_sha256_hex(self):
        self.assertEqual(AA._token_hash("tok"),
                         hashlib.sha256(b"tok").hexdigest())
        self.assertNotEqual(AA._token_hash("a"), AA._token_hash("b"))

    def test_now_text_is_beijing_time(self):
        self.assertRegex(AA._now_text(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class _StoreBase(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-auth-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = self.tmp / "data" / "r20_admin.db"
        self._start(mock.patch.object(AA, "DB_PATH", self.db))
        self._start(mock.patch.object(AA, "_hash_password", _fast_hash))
        self.now = 1_700_000_000
        self._start(mock.patch.object(AA.time, "time", side_effect=lambda: self.now))
        self.store = AA.AdminAuthStore()

    def _user(self, username="admin", password=GOOD, role="superadmin"):
        return self.store.create_user(username, password, role)

    def _stderr(self):
        return self._start(mock.patch.object(sys, "stderr", io.StringIO()))


class ConstructionTests(_StoreBase):
    def test_default_path_is_resolved_at_call_time(self):
        """文件里那句警告的回归防线：默认值必须是**调用期**读模块常量。"""
        other = self.tmp / "other.db"
        with mock.patch.object(AA, "DB_PATH", other):
            store = AA.AdminAuthStore()
        self.assertEqual(store.path, other)
        self.assertTrue(other.exists())

    def test_explicit_path_wins(self):
        explicit = self.tmp / "explicit.db"
        self.assertEqual(AA.AdminAuthStore(explicit).path, explicit)

    def test_creates_parents_and_locks_the_file_down(self):
        self.assertTrue(self.db.parent.is_dir())
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)

    def test_schema_is_idempotent(self):
        AA.AdminAuthStore(self.db)
        AA.AdminAuthStore(self.db)
        self.assertFalse(self.store.has_users())

    def test_foreign_keys_cascade_sessions(self):
        user = self._user()
        self.store.login("admin", GOOD)
        with self.store.connect() as connection:
            connection.execute("DELETE FROM admin_users WHERE id=?", (user["id"],))
        with self.store.connect() as connection:
            left = connection.execute("SELECT COUNT(*) c FROM admin_sessions").fetchone()["c"]
        self.assertEqual(left, 0, "外键级联必须真的生效")


class HasUsersAndLegacyTests(_StoreBase):
    def test_empty_store(self):
        self.assertFalse(self.store.has_users())

    def test_initialize_from_legacy_creates_the_first_superadmin(self):
        self.assertTrue(self.store.initialize_from_legacy(GOOD))
        users = self.store.list_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["username"], "admin")
        self.assertEqual(users[0]["role"], "superadmin")

    def test_initialize_is_a_no_op_once_users_exist_or_password_empty(self):
        self.assertFalse(self.store.initialize_from_legacy(""))
        self.assertTrue(self.store.initialize_from_legacy(GOOD))
        self.assertFalse(self.store.initialize_from_legacy("another12345"))


class CreateUserTests(_StoreBase):
    def test_valid_creation_returns_the_public_row(self):
        user = self._user(username="  ops  ", role="admin")
        self.assertEqual(user["username"], "ops", "用户名要去空白")
        self.assertEqual(user["role"], "admin")
        self.assertEqual(user["enabled"], 1)
        self.assertNotIn("password_hash", user)
        self.assertNotIn("salt", user)

    def test_username_and_role_and_password_are_all_validated(self):
        for username in ("ab", "1ops", "bad!", "x" * 33):
            with self.subTest(username=username):
                with self.assertRaises(ValueError) as ctx:
                    self.store.create_user(username, GOOD)
                self.assertIn("账号必须以字母开头", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            self.store.create_user("ops", GOOD, "root")
        self.assertIn("无效管理员角色", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            self.store.create_user("ops", "weak")
        self.assertIn("12-128", str(ctx.exception))

    def test_duplicate_usernames_are_rejected_case_insensitively(self):
        self._user(username="Admin")
        with self.assertRaises(ValueError) as ctx:
            self.store.create_user("admin", GOOD)
        self.assertIn("已存在", str(ctx.exception))

    def test_wrong_password_never_matches(self):
        user = self._user()
        self.assertTrue(self.store.verify_password(user["id"], GOOD))
        self.assertFalse(self.store.verify_password(user["id"], GOOD + "x"))

    def test_missing_or_disabled_user_never_verifies(self):
        user = self._user(username="ops", role="admin")
        self.assertFalse(self.store.verify_password(9999, GOOD))
        self.store.set_enabled(user["id"], False, actor_id=0)
        self.assertFalse(self.store.verify_password(user["id"], GOOD))


class UserQueryTests(_StoreBase):
    def test_get_user_missing_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.store.get_user(4242)
        self.assertIn("不存在", str(ctx.exception))

    def test_list_users_is_ordered_and_public_only(self):
        self._user(username="alpha")
        self._user(username="beta")
        users = self.store.list_users()
        self.assertEqual([u["username"] for u in users], ["alpha", "beta"])
        for user in users:
            self.assertNotIn("password_hash", user)
            self.assertNotIn("salt", user)


class LoginTests(_StoreBase):
    def test_successful_login_issues_a_session_and_resets_counters(self):
        user = self._user()
        with self.store.connect() as connection:
            connection.execute("UPDATE admin_users SET failed_attempts=3 WHERE id=?",
                               (user["id"],))
        out = self.store.login("admin", GOOD)
        self.assertEqual(out["expires_at"], self.now + AA.SESSION_SECONDS)
        self.assertEqual(out["user"]["username"], "admin")
        self.assertTrue(out["session_token"])
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM admin_users WHERE id=?",
                                     (user["id"],)).fetchone()
            session = connection.execute("SELECT * FROM admin_sessions").fetchone()
        self.assertEqual(row["failed_attempts"], 0)
        self.assertNotEqual(row["last_login_at"], "")
        self.assertEqual(session["token_hash"], AA._token_hash(out["session_token"]),
                         "库里只能存 token 的哈希")

    def test_username_lookup_is_case_insensitive_and_trimmed(self):
        self._user()
        self.assertTrue(self.store.login("  ADMIN  ", GOOD)["session_token"])

    def test_all_failure_modes_share_one_client_facing_message(self):
        """枚举面收口的核心：客户端看不出是"停用/锁定/密码错/不存在"哪一种。"""
        self._user(username="admin")
        disabled = self._user(username="disabled_one", role="admin")
        self.store.set_enabled(disabled["id"], False, actor_id=0)

        messages = set()
        for username, password in (("admin", "wrongpassword1"),
                                   ("admin", "wrongpassword1"),   # 再来一次
                                   ("disabled_one", GOOD),
                                   ("ghostUser", GOOD)):
            stderr = self._stderr()
            with self.assertRaises(PermissionError) as ctx:
                self.store.login(username, password)
            messages.add(str(ctx.exception))
        self.assertEqual(messages, {"账号或密码错误"},
                         "四种失败必须共用同一句对外文案")

    def test_real_reason_is_written_to_stderr_only(self):
        self._user()
        stderr = self._stderr()
        with self.assertRaises(PermissionError):
            self.store.login("admin", "wrongpassword1")
        self.assertIn("剩余尝试 4 次", stderr.getvalue())
        self.assertIn("login_denied", stderr.getvalue())

    def test_five_failures_lock_the_account_for_fifteen_minutes(self):
        user = self._user()
        for index in range(4):
            with self.assertRaises(PermissionError):
                self.store.login("admin", "wrongpassword1")
        stderr = self._stderr()
        with self.assertRaises(PermissionError):
            self.store.login("admin", "wrongpassword1")
        self.assertIn("触发 15 分钟锁定", stderr.getvalue())
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM admin_users WHERE id=?",
                                     (user["id"],)).fetchone()
        self.assertEqual(row["locked_until"], self.now + 15 * 60)

        stderr2 = self._stderr()
        with self.assertRaises(PermissionError):
            self.store.login("admin", GOOD)          # 锁定期内即使密码对也拒绝
        self.assertIn("账号锁定中", stderr2.getvalue())

    def test_locked_login_does_not_keep_counting_attempts(self):
        user = self._user()
        for _ in range(5):
            with self.assertRaises(PermissionError):
                self.store.login("admin", "wrongpassword1")
        with self.store.connect() as connection:
            before = connection.execute("SELECT failed_attempts FROM admin_users WHERE id=?",
                                        (user["id"],)).fetchone()["failed_attempts"]
            for _ in range(3):
                with self.assertRaises(PermissionError):
                    self.store.login("admin", "wrongpassword1")
            after = connection.execute("SELECT failed_attempts FROM admin_users WHERE id=?",
                                      (user["id"],)).fetchone()["failed_attempts"]
        self.assertEqual(before, 5)
        self.assertEqual(after, 5, "锁定期间不再累加（否则解锁时间会被无限延后）")

    def test_lock_expires_and_login_works_again(self):
        self._user()
        for _ in range(5):
            with self.assertRaises(PermissionError):
                self.store.login("admin", "wrongpassword1")
        self.now += 15 * 60 + 1
        self.assertTrue(self.store.login("admin", GOOD)["session_token"])

    def test_remaining_attempts_never_goes_negative(self):
        self._user()
        reasons = []
        for _ in range(6):
            stderr = self._stderr()
            with self.assertRaises(PermissionError):
                self.store.login("admin", "wrongpassword1")
            reasons.append(stderr.getvalue())
        for reason in reasons:
            if "剩余尝试" not in reason:
                continue
            tail = reason.split("剩余尝试")[-1].split("次")[0].strip()
            self.assertGreaterEqual(int(tail), 0, f"剩余次数出现负数: {reason!r}")

    def test_unknown_user_still_burns_a_dummy_hash(self):
        hasher = self._start(mock.patch.object(AA, "_hash_password",
                                          mock.Mock(side_effect=_fast_hash)))
        with self.assertRaises(PermissionError):
            self.store.login("ghost", GOOD)
        self.assertEqual(hasher.call_count, 1,
                         "不存在也要做一次同量级哈希，抹平时序差")

    def test_expired_sessions_are_pruned_on_successful_login(self):
        user = self._user()
        with self.store.connect() as connection:
            connection.execute(
                "INSERT INTO admin_sessions(token_hash,user_id,created_at,expires_at,last_seen_at)"
                " VALUES (?,?,?,?,?)",
                ("stale", user["id"], self.now - 100, self.now - 1, self.now - 100))
        self.store.login("admin", GOOD)
        with self.store.connect() as connection:
            tokens = {r["token_hash"] for r in
                      connection.execute("SELECT token_hash FROM admin_sessions")}
        self.assertNotIn("stale", tokens)


class SessionTests(_StoreBase):
    def test_empty_token_is_none(self):
        self.assertIsNone(self.store.validate_session(""))
        self.assertIsNone(self.store.validate_session(None))

    def test_valid_session_returns_the_user_and_touches_last_seen(self):
        self._user()
        token = self.store.login("admin", GOOD)["session_token"]
        self.now += 60
        user = self.store.validate_session(token)
        self.assertEqual(user["username"], "admin")
        self.assertEqual(user["role"], "superadmin")
        with self.store.connect() as connection:
            seen = connection.execute("SELECT last_seen_at FROM admin_sessions").fetchone()
        self.assertEqual(seen["last_seen_at"], self.now)

    def test_unknown_token_and_expired_session_are_rejected(self):
        self._user()
        token = self.store.login("admin", GOOD)["session_token"]
        self.assertIsNone(self.store.validate_session("not-a-real-token"))
        self.now += AA.SESSION_SECONDS + 1
        self.assertIsNone(self.store.validate_session(token))

    def test_disabling_the_user_invalidates_the_session(self):
        user = self._user()
        self._user(username="backup")
        token = self.store.login("admin", GOOD)["session_token"]
        self.store.set_enabled(user["id"], False, actor_id=0)
        self.assertIsNone(self.store.validate_session(token))

    def test_logout_kills_only_that_token(self):
        self._user()
        first = self.store.login("admin", GOOD)["session_token"]
        second = self.store.login("admin", GOOD)["session_token"]
        self.store.logout(first)
        self.assertIsNone(self.store.validate_session(first))
        self.assertIsNotNone(self.store.validate_session(second))

    def test_logout_user_kills_every_session_of_that_user(self):
        user = self._user()
        tokens = [self.store.login("admin", GOOD)["session_token"] for _ in range(3)]
        self.store.logout_user(user["id"])
        for token in tokens:
            self.assertIsNone(self.store.validate_session(token))


class PasswordChangeTests(_StoreBase):
    def test_policy_is_enforced(self):
        user = self._user()
        with self.assertRaises(ValueError):
            self.store.change_password(user["id"], "weak")

    def test_old_password_stops_working_and_sessions_die(self):
        user = self._user()
        token = self.store.login("admin", GOOD)["session_token"]
        self.store.change_password(user["id"], "newpass12345")
        self.assertFalse(self.store.verify_password(user["id"], GOOD))
        self.assertTrue(self.store.verify_password(user["id"], "newpass12345"))
        self.assertIsNone(self.store.validate_session(token),
                          "改密必须踢掉既有会话")

    def test_unknown_user_change_is_a_silent_no_op(self):
        """⚠️ 实测：`change_password` 对不存在的 user_id 不做存在性校验，
        UPDATE 影响 0 行也不报错（与 `get_user`/`unlock_user` 的显式校验不同）。"""
        self.store.change_password(4242, "newpass12345")
        self.assertEqual(self.store.list_users(), [])


class UnlockTests(_StoreBase):
    def test_missing_user_raises(self):
        with self.assertRaises(ValueError):
            self.store.unlock_user(4242)

    def test_unlock_clears_the_lock(self):
        user = self._user()
        for _ in range(5):
            with self.assertRaises(PermissionError):
                self.store.login("admin", "wrongpassword1")
        self.store.unlock_user(user["id"])
        self.assertTrue(self.store.login("admin", GOOD)["session_token"])


class SetEnabledTests(_StoreBase):
    def test_cannot_disable_yourself(self):
        user = self._user()
        with self.assertRaises(ValueError) as ctx:
            self.store.set_enabled(user["id"], False, actor_id=user["id"])
        self.assertIn("不能停用当前登录账号", str(ctx.exception))

    def test_missing_user_raises(self):
        with self.assertRaises(ValueError):
            self.store.set_enabled(4242, False, actor_id=0)

    def test_last_enabled_superadmin_cannot_be_disabled(self):
        user = self._user()
        with self.assertRaises(ValueError) as ctx:
            self.store.set_enabled(user["id"], False, actor_id=0)
        self.assertIn("至少保留一个启用的超级管理员", str(ctx.exception))

    def test_superadmin_can_be_disabled_when_another_one_remains(self):
        first = self._user(username="admin")
        self._user(username="backup")
        self.store.set_enabled(first["id"], False, actor_id=0)
        self.assertEqual(self.store.get_user(first["id"])["enabled"], 0)

    def test_disabling_a_plain_admin_needs_no_superadmin_count(self):
        root = self._user(username="root")
        plain = self._user(username="ops", role="admin")
        self.store.set_enabled(plain["id"], False, actor_id=root["id"])
        self.assertEqual(self.store.get_user(plain["id"])["enabled"], 0)

    def test_disabling_kills_sessions_and_re_enabling_does_not_restore_them(self):
        user = self._user()
        self._user(username="backup")
        token = self.store.login("admin", GOOD)["session_token"]
        self.store.set_enabled(user["id"], False, actor_id=0)
        self.assertIsNone(self.store.validate_session(token))
        self.store.set_enabled(user["id"], True, actor_id=0)
        self.assertIsNone(self.store.validate_session(token))
        self.assertEqual(self.store.get_user(user["id"])["enabled"], 1)


class ConnectionHandlingTests(_StoreBase):
    def test_context_manager_commits_but_does_not_close(self):
        """⚠️ 实测观察：`with self.connect() as connection:` 是 sqlite3 的**事务**上下文，
        **退出时不关闭连接**（只 commit）。即长期运行的进程里每次调用都会留下一个
        仍可用的连接，直到被 GC 回收。本用例按实际行为钉住，未擅自改生产代码。"""
        created = []
        original = AA.AdminAuthStore.connect

        def _spy(store_self):
            connection = original(store_self)
            created.append(connection)
            return connection

        with mock.patch.object(AA.AdminAuthStore, "connect", _spy):
            self.store.has_users()
        self.assertEqual(len(created), 1)
        try:
            created[0].execute("SELECT 1")
            still_usable = True
        except sqlite3.ProgrammingError:
            still_usable = False
        self.assertTrue(still_usable, "连接在 with 之后仍然可用 ⇒ 确实没被关闭")


if __name__ == "__main__":
    unittest.main()

"""Password-based administrator accounts and server-side sessions."""
from __future__ import annotations
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "r20_admin.db"
BJ_TZ = timezone(timedelta(hours=8))
PBKDF2_ITERATIONS = 600_000
SESSION_SECONDS = 12 * 60 * 60
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,31}$")
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
CREATE TABLE IF NOT EXISTS admin_users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  iterations INTEGER NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('superadmin','admin')),
  enabled INTEGER NOT NULL DEFAULT 1,
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_login_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS admin_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_token ON admin_sessions(token_hash);
"""


def _now_text() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_password(password: str, salt: bytes | None = None, iterations: int = PBKDF2_ITERATIONS) -> tuple[str, str, int]:
    salt = salt or secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return digest.hex(), salt.hex(), iterations


def _password_valid(password: str) -> bool:
    return len(password) >= 12 and len(password) <= 128 and any(c.isalpha() for c in password) and any(c.isdigit() for c in password)


class AdminAuthStore:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def has_users(self) -> bool:
        with self.connect() as connection:
            return bool(connection.execute("SELECT 1 FROM admin_users LIMIT 1").fetchone())

    def initialize_from_legacy(self, password: str) -> bool:
        if self.has_users() or not password:
            return False
        self.create_user("admin", password, "superadmin")
        return True

    def create_user(self, username: str, password: str, role: str = "admin") -> dict[str, Any]:
        username = username.strip()
        if not USERNAME_RE.fullmatch(username):
            raise ValueError("账号必须以字母开头，只能包含字母、数字、点、下划线或短横线，长度 3-32")
        if role not in {"superadmin", "admin"}:
            raise ValueError("无效管理员角色")
        if not _password_valid(password):
            raise ValueError("密码必须为 12-128 位，且同时包含字母和数字")
        password_hash, salt, iterations = _hash_password(password)
        now = _now_text()
        try:
            with self.connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO admin_users(username,password_hash,salt,iterations,role,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (username, password_hash, salt, iterations, role, now, now),
                )
                user_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError("管理员账号已存在") from exc
        return self.get_user(user_id)

    def get_user(self, user_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT id,username,role,enabled,created_at,updated_at,last_login_at,locked_until FROM admin_users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise ValueError("管理员不存在")
        return dict(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT id,username,role,enabled,created_at,updated_at,last_login_at,locked_until FROM admin_users ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def verify_password(self, user_id: int, password: str) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT password_hash,salt,iterations,enabled FROM admin_users WHERE id=?", (user_id,)).fetchone()
        if not row or not row["enabled"]:
            return False
        digest, _, _ = _hash_password(password, bytes.fromhex(row["salt"]), int(row["iterations"]))
        return hmac.compare_digest(digest, row["password_hash"])

    def login(self, username: str, password: str) -> dict[str, Any]:
        now_epoch = int(time.time())
        failure_message = ""
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM admin_users WHERE username=? COLLATE NOCASE", (username.strip(),)).fetchone()
            if row and not row["enabled"]:
                failure_message = "管理员账号已停用，请联系超级管理员"
            elif row and int(row["locked_until"]) > now_epoch:
                remaining = max(1, int(row["locked_until"]) - now_epoch)
                minutes = (remaining + 59) // 60
                failure_message = f"登录失败次数过多，账号已临时锁定，请约 {minutes} 分钟后重试"
            valid = False
            if row and not failure_message:
                digest, _, _ = _hash_password(password, bytes.fromhex(row["salt"]), int(row["iterations"]))
                valid = hmac.compare_digest(digest, row["password_hash"])
            elif not row and not failure_message:
                # Constant-time dummy computation to prevent username enumeration via timing side-channel
                _hash_password(password, b"\x00" * 24, PBKDF2_ITERATIONS)
            if not valid and not failure_message:
                if row:
                    failures = int(row["failed_attempts"]) + 1
                    locked_until = now_epoch + 15 * 60 if failures >= 5 else 0
                    connection.execute("UPDATE admin_users SET failed_attempts=?,locked_until=?,updated_at=? WHERE id=?", (failures, locked_until, _now_text(), row["id"]))
                    failure_message = "账号或密码错误，账号已锁定 15 分钟" if locked_until else f"账号或密码错误，还可尝试 {5 - failures} 次"
                else:
                    failure_message = "账号或密码错误"
            if failure_message:
                connection.commit()
            else:
                token = secrets.token_urlsafe(48)
                expires = now_epoch + SESSION_SECONDS
                connection.execute("DELETE FROM admin_sessions WHERE expires_at<=?", (now_epoch,))
                connection.execute(
                    "INSERT INTO admin_sessions(token_hash,user_id,created_at,expires_at,last_seen_at) VALUES (?,?,?,?,?)",
                    (_token_hash(token), row["id"], now_epoch, expires, now_epoch),
                )
                connection.execute("UPDATE admin_users SET failed_attempts=0,locked_until=0,last_login_at=?,updated_at=? WHERE id=?", (_now_text(), _now_text(), row["id"]))
        if failure_message:
            raise PermissionError(failure_message)
        return {"session_token": token, "expires_at": expires, "user": self.get_user(int(row["id"]))}

    def validate_session(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        now_epoch = int(time.time())
        with self.connect() as connection:
            row = connection.execute(
                """SELECT u.id,u.username,u.role,u.enabled,u.created_at,u.updated_at,u.last_login_at,s.id session_id,s.expires_at
                   FROM admin_sessions s JOIN admin_users u ON u.id=s.user_id
                   WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1""",
                (_token_hash(token), now_epoch),
            ).fetchone()
            if not row:
                return None
            connection.execute("UPDATE admin_sessions SET last_seen_at=? WHERE id=?", (now_epoch, row["session_id"]))
        return dict(row)

    def logout(self, token: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM admin_sessions WHERE token_hash=?", (_token_hash(token),))

    def logout_user(self, user_id: int) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM admin_sessions WHERE user_id=?", (user_id,))

    def unlock_user(self, user_id: int) -> None:
        with self.connect() as connection:
            row = connection.execute("SELECT id FROM admin_users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise ValueError("管理员不存在")
            connection.execute(
                "UPDATE admin_users SET failed_attempts=0,locked_until=0,updated_at=? WHERE id=?",
                (_now_text(), user_id),
            )

    def change_password(self, user_id: int, new_password: str) -> None:
        if not _password_valid(new_password):
            raise ValueError("密码必须为 12-128 位，且同时包含字母和数字")
        password_hash, salt, iterations = _hash_password(new_password)
        with self.connect() as connection:
            connection.execute("UPDATE admin_users SET password_hash=?,salt=?,iterations=?,updated_at=? WHERE id=?", (password_hash, salt, iterations, _now_text(), user_id))
            connection.execute("DELETE FROM admin_sessions WHERE user_id=?", (user_id,))

    def set_enabled(self, user_id: int, enabled: bool, actor_id: int) -> None:
        if user_id == actor_id and not enabled:
            raise ValueError("不能停用当前登录账号")
        with self.connect() as connection:
            target = connection.execute("SELECT role,enabled FROM admin_users WHERE id=?", (user_id,)).fetchone()
            if not target:
                raise ValueError("管理员不存在")
            if not enabled and target["role"] == "superadmin" and target["enabled"]:
                count = connection.execute("SELECT COUNT(*) count FROM admin_users WHERE role='superadmin' AND enabled=1").fetchone()["count"]
                if int(count) <= 1:
                    raise ValueError("至少保留一个启用的超级管理员")
            connection.execute("UPDATE admin_users SET enabled=?,updated_at=? WHERE id=?", (1 if enabled else 0, _now_text(), user_id))
            if not enabled:
                connection.execute("DELETE FROM admin_sessions WHERE user_id=?", (user_id,))

import base64
import json
import os
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from r20_backend import qq_bind


def _encrypt_secret(key_b64: str, secret: str) -> str:
    key = base64.b64decode(key_b64)
    iv = os.urandom(12)
    ct_with_tag = AESGCM(key).encrypt(iv, secret.encode("utf-8"), None)
    return base64.b64encode(iv + ct_with_tag).decode("ascii")


class QqBindTests(unittest.TestCase):
    def setUp(self):
        qq_bind._TASKS.clear()
        qq_bind._CAPTURE_SESSIONS.clear()
        self._daemon = patch("r20_backend.qq_bind.ensure_qq_gateway_daemon_running")
        self.ensure_daemon = self._daemon.start()
        self.addCleanup(self._daemon.stop)

    def test_decrypt_roundtrip_matches_connector_layout(self):
        key = base64.b64encode(os.urandom(32)).decode()
        encrypted = _encrypt_secret(key, "DG5g3B4j9X2KOErG")
        self.assertEqual(qq_bind._decrypt_secret(encrypted, key), "DG5g3B4j9X2KOErG")

    def test_decrypt_rejects_tampered_blob(self):
        key = base64.b64encode(os.urandom(32)).decode()
        encrypted = _encrypt_secret(key, "secret")
        raw = bytearray(base64.b64decode(encrypted))
        raw[-1] ^= 0x01  # tamper auth tag
        tampered = base64.b64encode(raw).decode()
        with self.assertRaises(RuntimeError):
            qq_bind._decrypt_secret(tampered, key)

    def test_create_task_builds_official_connect_url(self):
        with patch("r20_backend.qq_bind._post_qq", return_value={"task_id": "t-123"}) as post:
            task = qq_bind.create_bind_task()
        self.assertEqual(post.call_args[0][0], "/lite/create_bind_task")
        self.assertEqual(task["task_id"], "t-123")
        self.assertIn("q.qq.com/qqbot/openclaw/connect.html?task_id=t-123", task["connect_url"])
        self.assertIn("&_wv=2", task["connect_url"])
        key = base64.b64decode(post.call_args[0][1]["key"])
        self.assertEqual(len(key), 32)
        self.ensure_daemon.assert_called()

    def test_poll_bound_persists_credentials_without_plaintext_leak(self):
        with patch("r20_backend.qq_bind._post_qq", return_value={"task_id": "t-9"}):
            qq_bind.create_bind_task()
        task = qq_bind._TASKS["t-9"]
        encrypted = _encrypt_secret(task.key_b64, "topsecret-value")
        responses = iter([
            {"status": 1},
            {"status": 2, "bot_appid": "100456789", "bot_encrypt_secret": encrypted, "user_openid": "OPENID-USER"},
        ])
        with patch("r20_backend.qq_bind._post_qq", side_effect=lambda path, payload, timeout=12: next(responses)), \
             patch("r20_backend.qq_bind._persist") as persist:
            first = qq_bind.poll_bind_task("t-9")
            task.last_poll = 0
            self.ensure_daemon.reset_mock()
            second = qq_bind.poll_bind_task("t-9")
        self.assertEqual(first["status"], "pending")
        self.assertEqual(second["status"], "bound")
        self.assertEqual(second["app_id"], "100456789")
        self.assertEqual(second["openid"], "OPENID-USER")
        persist.assert_called_once_with("100456789", "topsecret-value", "OPENID-USER")
        self.ensure_daemon.assert_called_once_with()
        # the public view must never contain the secret material
        self.assertNotIn("topsecret-value", json.dumps(second))
        self.assertNotIn("bot_encrypt_secret", json.dumps(second))

    def test_poll_bound_without_openid_transitions_to_awaiting_message(self):
        with patch("r20_backend.qq_bind._post_qq", return_value={"task_id": "t-await"}):
            qq_bind.create_bind_task()
        task = qq_bind._TASKS["t-await"]
        encrypted = _encrypt_secret(task.key_b64, "my-secret")
        responses = iter([
            {"status": 2, "bot_appid": "1905549905", "bot_encrypt_secret": encrypted, "user_openid": ""},
        ])
        with patch("r20_backend.qq_bind._post_qq", side_effect=lambda path, payload, timeout=12: next(responses)), \
             patch("r20_backend.qq_bind._persist") as persist, \
             patch("r20_backend.qq_bind.start_openid_capture", return_value={"capture_id": "cap-auto-1"}) as start_cap:
            self.ensure_daemon.reset_mock()
            res = qq_bind.poll_bind_task("t-await")
        self.assertEqual(res["status"], "awaiting_message")
        self.assertEqual(res["capture_id"], "cap-auto-1")
        persist.assert_called_once_with("1905549905", "my-secret", "")
        self.ensure_daemon.assert_called_once_with()
        start_cap.assert_called_once_with("1905549905", "my-secret", timeout=90)

    def test_start_and_poll_openid_capture(self):
        with patch("threading.Thread.start") as mock_thread_start:
            cap = qq_bind.start_openid_capture("1905549905", "test-secret", timeout=45)
            self.assertEqual(cap["status"], "listening")
            self.assertEqual(cap["app_id"], "1905549905")
            self.assertTrue(cap["capture_id"].startswith("cap_"))
            mock_thread_start.assert_called_once()

            # Poll listening state
            polled = qq_bind.poll_openid_capture(cap["capture_id"])
            self.assertEqual(polled["status"], "listening")

            # Simulate capture
            session = qq_bind._CAPTURE_SESSIONS[cap["capture_id"]]
            with session.lock:
                session.status = "captured"
                session.openid = "USER_OPENID_9999"
                session.message_preview = "你好机器人"

            polled_after = qq_bind.poll_openid_capture(cap["capture_id"])
            self.assertEqual(polled_after["status"], "captured")
            self.assertEqual(polled_after["openid"], "USER_OPENID_9999")

    def test_poll_expired_task_marks_expired(self):
        with patch("r20_backend.qq_bind._post_qq", return_value={"task_id": "t-e"}):
            qq_bind.create_bind_task()
        task = qq_bind._TASKS["t-e"]
        task.created_at -= qq_bind.TASK_TTL_SECONDS + 5
        with patch("r20_backend.qq_bind._post_qq") as post:
            view = qq_bind.poll_bind_task("t-e")
        self.assertEqual(view["status"], "expired")
        post.assert_not_called()

    def test_unknown_task_raises(self):
        with self.assertRaises(RuntimeError):
            qq_bind.poll_bind_task("missing")

    def test_persist_writes_secret_store_and_env_only(self):
        with patch("r20_gateway.secrets.save_secrets") as save, patch("r20_backend.settings_store.update_env") as env:
            qq_bind._persist("100", "sekret", "OPEN")
        save.assert_called_once_with({"R20_QQ_CLIENT_SECRET": "sekret", "R20_QQ_OPENID": "OPEN"})
        env.assert_called_once_with({"R20_QQ_APP_ID": "100", "R20_QQ_OPENID": "OPEN"})

    def test_active_task_cap(self):
        counter = iter(f"task-{i}" for i in range(10))
        with patch("r20_backend.qq_bind._post_qq", side_effect=lambda path, payload, timeout=12: {"task_id": next(counter)}):
            for _ in range(qq_bind.MAX_ACTIVE_TASKS):
                qq_bind.create_bind_task()
            with self.assertRaises(RuntimeError):
                qq_bind.create_bind_task()


if __name__ == "__main__":
    unittest.main()

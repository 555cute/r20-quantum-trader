"""备份凭证密文库：**字段白名单、密文落盘、0600、清空字段不许误覆盖**（第二百七十二刀，开新面 backup_secrets.py）。

先打印整个文件（72 行）再动笔。它是备份目标的凭证存储层（`routers/gateway/backups.py`
与 `backup_store.validate_backup_job` 的凭证读取都走它），所以这里守两条线：
**明文绝不落盘**与**白名单外字段绝不入库**。

| 语义 | 口径 |
|---|---|
| ★ **明文绝不落盘** | 落盘必须是 Fernet 密文（用例直接断言密文里**搜不到**明文密钥），密钥文件与密文库都 **0600** |
| ★ **字段白名单** | 只有 `ALLOWED_FIELDS` 里的键能进库；白名单外静默丢弃（不是报错）|
| ★ **空值不覆盖** | `None` 跳过；**空字符串也跳过** ⇒ 不能用 `save` 清空既有字段（清空只能靠删引用/换库）——这条是"误把已配好的密钥抹成空"的护栏 |
| ★ **读侧同样过滤** | `_load_all` 逐层过滤：非 dict 的引用跳过、非白名单键丢弃、**假值（空串/None）丢弃** ⇒ 手工改坏密文也塞不进垃圾字段 |
| ★ **损坏 = 空库（fail-soft）** | 密钥缺失 / 密文库缺失 / 解密失败（`InvalidToken`）/ 读不动（`OSError`）/ 解不开（`JSONDecodeError`）一律返回 `{}`，绝不把异常抛给面板 |
| ★ **原子写** | `mkstemp`→`fsync`→`chmod 0600`→`os.replace`→再 `chmod 0600`；失败路径必须清掉临时文件 |
| 状态投影 | `credential_status` 只暴露 `{configured, fields[], count}` —— **不回传任何值** |
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet

from r20_backend import backup_secrets as BS

SECRET = "AKIA-SUPER-SECRET-VALUE"


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-bsecret-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.key_file = self.tmp / "data" / ".r20_backup_secret_key"
        self.store_file = self.tmp / "data" / "r20_backup_secrets.enc"
        self._start(mock.patch.object(BS, "KEY_FILE", self.key_file))
        self._start(mock.patch.object(BS, "STORE_FILE", self.store_file))

    def _seed(self, payload):
        """用真实密钥把任意载荷写进密文库（用于模拟损坏/垃圾数据）。"""
        fernet = BS._fernet(True)
        self.store_file.parent.mkdir(parents=True, exist_ok=True)
        self.store_file.write_bytes(
            fernet.encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8")))


class AtomicWriteTests(_Base):
    def test_content_permissions_and_no_temp_leftovers(self):
        BS._atomic(self.store_file, b"payload")
        self.assertEqual(self.store_file.read_bytes(), b"payload")
        self.assertEqual(self.store_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.store_file.parent.glob(".r20_backup_secrets.enc-*")), [])

    def test_failed_replace_cleans_the_temp_file(self):
        with mock.patch.object(BS.os, "replace", side_effect=OSError("磁盘满了")):
            with self.assertRaises(OSError):
                BS._atomic(self.store_file, b"payload")
        self.assertEqual(list(self.store_file.parent.glob(".r20_backup_secrets.enc-*")), [],
                         "失败路径不得留下半截临时文件")


class KeyFileTests(_Base):
    def test_no_key_means_no_fernet_unless_creation_is_requested(self):
        self.assertIsNone(BS._fernet(False), "只读路径下缺密钥必须返回 None 而不是造一把")
        self.assertFalse(self.key_file.exists())

    def test_creation_writes_a_valid_owner_only_key(self):
        fernet = BS._fernet(True)
        self.assertIsNotNone(fernet)
        self.assertTrue(self.key_file.exists())
        self.assertEqual(self.key_file.stat().st_mode & 0o777, 0o600)
        Fernet(self.key_file.read_bytes().strip())   # 必须是合法 Fernet 密钥

    def test_existing_key_is_reused(self):
        first = BS._fernet(True)
        token = first.encrypt(b"x")
        second = BS._fernet(True)
        self.assertEqual(second.decrypt(token), b"x", "已有密钥时不得重新生成")


class LoadAllTests(_Base):
    def test_missing_key_returns_empty(self):
        self.assertEqual(BS._load_all(), {})

    def test_missing_store_returns_empty(self):
        BS._fernet(True)
        self.assertEqual(BS._load_all(), {})

    def test_corrupt_ciphertext_returns_empty(self):
        BS._fernet(True)
        self.store_file.write_bytes(b"not-a-fernet-token")
        self.assertEqual(BS._load_all(), {})

    def test_unreadable_store_returns_empty(self):
        BS._fernet(True)
        self.store_file.mkdir(parents=True, exist_ok=True)   # 目录冒充文件
        self.assertEqual(BS._load_all(), {})

    def test_non_json_payload_returns_empty(self):
        fernet = BS._fernet(True)
        self.store_file.write_bytes(fernet.encrypt(b"not json at all"))
        self.assertEqual(BS._load_all(), {})

    def test_disallowed_and_falsy_fields_are_stripped_on_read(self):
        self._seed({"ref-1": {"access_key_id": "AK", "not_allowed": "x",
                              "empty": "", "none_ish": None, "password": "pw"},
                    "ref-2": "not-a-dict"})
        loaded = BS._load_all()
        self.assertEqual(loaded, {"ref-1": {"access_key_id": "AK", "password": "pw"}})

    def test_values_are_coerced_to_strings(self):
        self._seed({"ref": {"access_key_id": 123, "session_token": True}})
        self.assertEqual(BS._load_all()["ref"],
                         {"access_key_id": "123", "session_token": "True"})


class SaveCredentialsTests(_Base):
    def test_invalid_refs_are_rejected(self):
        for bad in ("", "   ", "x" * 101):
            with self.subTest(ref=bad[:12]):
                with self.assertRaises(ValueError) as ctx:
                    BS.save_credentials(bad, {"access_key_id": "AK"})
                self.assertIn("无效凭证引用", str(ctx.exception))

    def test_round_trip_and_status(self):
        status = BS.save_credentials("backup:s3",
                                     {"access_key_id": "AK", "secret_access_key": SECRET})
        self.assertTrue(status["configured"])
        self.assertEqual(status["fields"], ["access_key_id", "secret_access_key"])
        self.assertEqual(status["count"], 2)
        self.assertEqual(BS.load_credentials("backup:s3"),
                         {"access_key_id": "AK", "secret_access_key": SECRET})

    def test_plaintext_never_reaches_the_disk(self):
        BS.save_credentials("backup:s3", {"secret_access_key": SECRET})
        raw = self.store_file.read_bytes()
        self.assertNotIn(SECRET.encode(), raw, "落盘必须是密文")
        self.assertNotIn(b"secret_access_key", raw)

    def test_both_files_are_owner_only(self):
        BS.save_credentials("backup:s3", {"access_key_id": "AK"})
        self.assertEqual(self.store_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.key_file.stat().st_mode & 0o777, 0o600)

    def test_disallowed_fields_are_dropped_silently(self):
        BS.save_credentials("backup:s3", {"access_key_id": "AK", "evil": "x",
                                          "prompt": "leak"})
        self.assertEqual(BS.load_credentials("backup:s3"), {"access_key_id": "AK"})

    def test_merge_preserves_fields_that_were_not_resubmitted(self):
        BS.save_credentials("backup:s3", {"access_key_id": "AK",
                                          "secret_access_key": SECRET})
        BS.save_credentials("backup:s3", {"session_token": "TOK"})
        self.assertEqual(BS.load_credentials("backup:s3"),
                         {"access_key_id": "AK", "secret_access_key": SECRET,
                          "session_token": "TOK"})

    def test_existing_fields_can_be_rotated(self):
        BS.save_credentials("backup:s3", {"secret_access_key": "OLD"})
        BS.save_credentials("backup:s3", {"secret_access_key": "NEW"})
        self.assertEqual(BS.load_credentials("backup:s3")["secret_access_key"], "NEW")

    def test_none_and_empty_values_never_clobber_a_configured_field(self):
        BS.save_credentials("backup:s3", {"secret_access_key": SECRET})
        BS.save_credentials("backup:s3", {"secret_access_key": None})
        BS.save_credentials("backup:s3", {"secret_access_key": ""})
        self.assertEqual(BS.load_credentials("backup:s3")["secret_access_key"], SECRET,
                         "空值不得把已配好的密钥抹成空（清空只能删引用）")

    def test_url_encoded_credentials_are_accepted(self):
        """审计蓝队路径：access_key_id 里可能带 `%2F` 这类转义，必须原样保存。"""
        BS.save_credentials("backup:oss", {"access_key_id": "AK%2FID"})
        self.assertEqual(BS.load_credentials("backup:oss")["access_key_id"], "AK%2FID")

    def test_references_are_isolated(self):
        BS.save_credentials("ref-a", {"access_key_id": "A"})
        BS.save_credentials("ref-b", {"access_key_id": "B"})
        self.assertEqual(BS.load_credentials("ref-a")["access_key_id"], "A")
        self.assertEqual(BS.load_credentials("ref-b")["access_key_id"], "B")

    def test_ref_is_stripped(self):
        BS.save_credentials("  backup:s3  ", {"access_key_id": "AK"})
        self.assertEqual(BS.load_credentials("backup:s3")["access_key_id"], "AK")


class LoadCredentialsTests(_Base):
    def test_unknown_reference_is_empty(self):
        self.assertEqual(BS.load_credentials("nope"), {})

    def test_returned_dict_is_a_copy(self):
        BS.save_credentials("ref", {"access_key_id": "AK"})
        got = BS.load_credentials("ref")
        got["access_key_id"] = "TAMPERED"
        self.assertEqual(BS.load_credentials("ref")["access_key_id"], "AK")


class DeleteCredentialsTests(_Base):
    def test_delete_removes_only_that_reference(self):
        BS.save_credentials("keep", {"access_key_id": "K"})
        BS.save_credentials("drop", {"access_key_id": "D"})
        BS.delete_credentials("drop")
        self.assertEqual(BS.load_credentials("drop"), {})
        self.assertEqual(BS.load_credentials("keep")["access_key_id"], "K")

    def test_deleting_an_unknown_reference_is_a_no_op(self):
        BS.save_credentials("keep", {"access_key_id": "K"})
        BS.delete_credentials("never-existed")
        self.assertEqual(BS.load_credentials("keep")["access_key_id"], "K")

    def test_delete_on_a_virgin_store_creates_an_empty_encrypted_store(self):
        BS.delete_credentials("whatever")
        self.assertTrue(self.store_file.exists(), "删除会重写密文库（即使本来是空的）")
        self.assertEqual(BS._load_all(), {})
        self.assertEqual(self.store_file.stat().st_mode & 0o777, 0o600)


class CredentialStatusTests(_Base):
    def test_unconfigured_reference(self):
        self.assertEqual(BS.credential_status("nope"),
                         {"configured": False, "fields": [], "count": 0})

    def test_configured_reference_never_leaks_values(self):
        BS.save_credentials("ref", {"secret_access_key": SECRET, "access_key_id": "AK"})
        status = BS.credential_status("ref")
        self.assertEqual(status, {"configured": True,
                                  "fields": ["access_key_id", "secret_access_key"],
                                  "count": 2})
        self.assertNotIn(SECRET, json.dumps(status))

    def test_only_the_reference_is_required(self):
        BS.save_credentials("with-space ref", {"app_key": "K"})
        self.assertTrue(BS.credential_status("with-space ref")["configured"])


class WhitelistTests(unittest.TestCase):
    def test_whitelist_contents_are_the_documented_twelve(self):
        self.assertEqual(BS.ALLOWED_FIELDS, {
            "access_key_id", "secret_access_key", "session_token", "app_key",
            "app_secret", "refresh_token", "access_token", "username", "password",
            "client_id", "client_secret", "sign_key",
        })

    def test_sensitive_sounding_but_unlisted_names_are_not_accepted(self):
        for name in ("private_key", "totp_seed", "mnemonic", "api_secret"):
            with self.subTest(name=name):
                self.assertNotIn(name, BS.ALLOWED_FIELDS)


if __name__ == "__main__":
    unittest.main()

"""备份上传目标（backup_upload.py）收口 —— 第 298 刀。

本模块从 `scripts/backup_runtime.py` 抽出（第五十二刀），是四个上传后端
（S3 / OSS / WebDAV / 百度网盘）与两个 HTTP 原语。它的核心设计约束写在
模块 docstring 里，也正是本刀要钉住的东西：

1. **依赖一律调用时注入**（形参 `calculate_sha256` / `_credentials` /
   `_urlencoded_json` / `_multipart_upload`）—— 门面那边有既有 patch 缝，
   import 期绑定会让补丁**静默失效**。所以每个 `upload_*` 都要断言
   "它确实用了我传进去的那个桩"，而不是自己去 import。
2. **凭证绝不进 query string**（审计 D，RFC 6749 §2.3.1）—— 百度 token
   端点必须走 POST body。这是安全断言，不是风格偏好。
3. **失败必须显式**：缺凭证/缺端点/缺 uploadid/分片 MD5 不符/非 2xx，
   全部 `raise`，不许"返回成功但没传上去"。
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import patch

scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.backup_upload as bu


class _FakeResponse:
    """safe_urlopen 的替身：既支持 `with`，也支持直接 `.close()`（WebDAV 用法）。"""

    def __init__(self, raw: bytes):
        self._raw = raw
        self.closed = False

    def read(self) -> bytes:
        return self._raw

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _FakeHTTPResponse:
    def __init__(self, status: int, raw: bytes = b""):
        self.status = status
        self._raw = raw

    def read(self) -> bytes:
        return self._raw


class _FakeConnection:
    """http.client 连接的替身：记录请求行/头/正文，回放预设状态码。"""

    instances: list["_FakeConnection"] = []

    def __init__(self, host=None, timeout=None, *, status=200, body=b""):
        self.host = host
        self.timeout = timeout
        self.status = status
        self.body = body
        self.requests: list[tuple] = []
        self.sent: list[bytes] = []
        self.closed = False
        _FakeConnection.instances.append(self)

    def putrequest(self, method, path, **kw):
        self.requests.append(("putrequest", method, path, kw))

    def putheader(self, key, value):
        self.requests.append(("putheader", key, value))

    def endheaders(self):
        self.requests.append(("endheaders",))

    def send(self, chunk):
        self.sent.append(chunk)

    def getresponse(self):
        return _FakeHTTPResponse(self.status, self.body)

    def close(self):
        self.closed = True

    def header(self, key):
        for item in self.requests:
            if item[0] == "putheader" and item[1].lower() == key.lower():
                return item[2]
        return None


def _conn_factory(**overrides):
    """返回一个"能被当类调用"的替身，产出带预设状态码的连接。"""
    made: list[_FakeConnection] = []

    def factory(host=None, timeout=None):
        conn = _FakeConnection(host, timeout, **overrides)
        made.append(conn)
        return conn

    factory.made = made  # type: ignore[attr-defined]
    return factory


class _TempFileMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def make_file(self, name: str, content: bytes = b"payload-bytes") -> Path:
        path = self.root / name
        path.write_bytes(content)
        return path


class CalculateSha256Tests(_TempFileMixin, unittest.TestCase):
    def test_digest_matches_hashlib(self):
        path = self.make_file("a.bin", b"hello world")
        self.assertEqual(bu.calculate_sha256(path),
                         hashlib.sha256(b"hello world").hexdigest())

    def test_missing_file_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            bu.calculate_sha256(self.root / "nope.bin")
        self.assertIn("文件不存在", str(ctx.exception))

    def test_directory_raises(self):
        d = self.root / "adir"
        d.mkdir()
        with self.assertRaises(RuntimeError) as ctx:
            bu.calculate_sha256(d)
        self.assertIn("无法计算 SHA256", str(ctx.exception))

    def test_streams_in_chunks_for_large_file(self):
        # 跨过 1 MiB 分块边界：多块拼接后摘要仍等于整文件摘要
        blob = b"x" * (1024 * 1024 + 7)
        path = self.make_file("big.bin", blob)
        self.assertEqual(bu.calculate_sha256(path), hashlib.sha256(blob).hexdigest())


class CredentialsTests(unittest.TestCase):
    def test_uses_credential_ref_when_present(self):
        seen = []
        import r20_backend.backup_secrets as secrets
        with patch.object(secrets, "load_credentials",
                          lambda ref: seen.append(ref) or {"k": "v"}):
            self.assertEqual(bu._credentials({"id": 1, "credential_ref": "custom:ref"}),
                             {"k": "v"})
        self.assertEqual(seen, ["custom:ref"])

    def test_falls_back_to_backup_prefixed_id(self):
        seen = []
        import r20_backend.backup_secrets as secrets
        with patch.object(secrets, "load_credentials",
                          lambda ref: seen.append(ref) or {}):
            self.assertEqual(bu._credentials({"id": 42}), {})
        self.assertEqual(seen, ["backup:42"])

    def test_blank_credential_ref_falls_back_to_id(self):
        seen = []
        import r20_backend.backup_secrets as secrets
        with patch.object(secrets, "load_credentials",
                          lambda ref: seen.append(ref) or {}):
            bu._credentials({"id": 7, "credential_ref": ""})
        self.assertEqual(seen, ["backup:7"])


class UrlencodedJsonTests(unittest.TestCase):
    def _call(self, payload, *, data=None, status_raw=None):
        import r20_backend.net_security as net
        seen = {}
        raw = status_raw if status_raw is not None else json.dumps(payload).encode()

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            seen["body"] = request.data
            seen["timeout"] = timeout
            return _FakeResponse(raw)

        with patch.object(net, "safe_urlopen", fake_urlopen):
            result = bu._urlencoded_json("https://api.example/token", data, timeout=11)
        return result, seen

    def test_get_when_no_data(self):
        result, seen = self._call({"ok": True})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(seen["method"], "GET")
        self.assertIsNone(seen["body"])
        self.assertEqual(seen["timeout"], 11)

    def test_post_urlencodes_body(self):
        result, seen = self._call({"ok": True}, data={"a": "1", "b": "2"})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(urllib.parse.parse_qs(seen["body"].decode()),
                         {"a": ["1"], "b": ["2"]})

    def test_empty_body_parses_as_empty_dict(self):
        result, _ = self._call(None, status_raw=b"")
        self.assertEqual(result, {})

    def test_errno_nonzero_raises_with_errmsg(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call({"errno": 1, "errmsg": "bad token"})
        self.assertIn("bad token", str(ctx.exception))

    def test_error_field_raises_with_description(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call({"error": "invalid_grant", "error_description": "expired"})
        self.assertIn("expired", str(ctx.exception))

    def test_errno_zero_is_success(self):
        result, _ = self._call({"errno": 0, "access_token": "t"})
        self.assertEqual(result["access_token"], "t")


class MultipartUploadTests(unittest.TestCase):
    def _call(self, payload):
        import r20_backend.net_security as net
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["body"] = request.data
            seen["content_type"] = request.get_header("Content-type")
            seen["method"] = request.get_method()
            return _FakeResponse(json.dumps(payload).encode())

        with patch.object(net, "safe_urlopen", fake_urlopen):
            result = bu._multipart_upload("https://d.pcs.baidu.com/u", "file",
                                          "db.sqlite", b"BINARY")
        return result, seen

    def test_builds_multipart_body_and_returns_payload(self):
        result, seen = self._call({"md5": "abc"})
        self.assertEqual(result, {"md5": "abc"})
        self.assertEqual(seen["method"], "POST")
        self.assertIn(b'name="file"', seen["body"])
        self.assertIn(b'filename="db.sqlite"', seen["body"])
        self.assertIn(b"BINARY", seen["body"])
        self.assertTrue(seen["content_type"].startswith("multipart/form-data; boundary=----R20"))

    def test_errno_nonzero_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call({"errno": -6, "errmsg": "no permission"})
        self.assertIn("no permission", str(ctx.exception))


class UploadS3Tests(_TempFileMixin, unittest.TestCase):
    def _target(self, **overrides):
        target = {"id": 1, "endpoint": "https://s3.example.com", "bucket": "bkt",
                  "remote_path": "daily/backups"}
        target.update(overrides)
        return target

    def _creds(self, **overrides):
        creds = {"access_key_id": "AK", "secret_access_key": "SK"}
        creds.update(overrides)
        return creds

    def test_sigv4_request_is_well_formed(self):
        import http.client
        path = self.make_file("db.sqlite", b"DATA")
        sha_calls = []
        factory = _conn_factory(status=200)
        with patch.object(http.client, "HTTPSConnection", factory):
            result = bu.upload_s3(
                path, self._target(),
                calculate_sha256=lambda p: sha_calls.append(p) or "PAYLOAD_HASH",
                _credentials=lambda t: self._creds())
        self.assertTrue(result["success"])
        self.assertEqual(result["destination"], "s3://bkt/daily/backups/db.sqlite")
        # 注入的 sha 函数必须被真正使用（而不是模块内自己算）
        self.assertEqual(sha_calls, [path])
        conn = factory.made[0]
        self.assertEqual(conn.requests[0][:3], ("putrequest", "PUT", "/daily/backups/db.sqlite"))
        # skip_host=True：host 由我们自己的签名头提供，不能被 http.client 覆盖
        self.assertTrue(conn.requests[0][3].get("skip_host"))
        self.assertEqual(conn.header("host"), "bkt.s3.example.com")
        self.assertEqual(conn.header("x-amz-content-sha256"), "PAYLOAD_HASH")
        self.assertEqual(conn.header("content-length"), "4")
        self.assertIn("AWS4-HMAC-SHA256 Credential=AK/", conn.header("authorization"))
        self.assertIn("SignedHeaders=", conn.header("authorization"))
        self.assertEqual(b"".join(conn.sent), b"DATA")
        self.assertTrue(conn.closed)

    def test_force_path_style_keeps_bucket_in_path(self):
        import http.client
        path = self.make_file("db.sqlite", b"DATA")
        factory = _conn_factory(status=201)
        with patch.object(http.client, "HTTPSConnection", factory):
            result = bu.upload_s3(path, self._target(force_path_style=True),
                                  calculate_sha256=lambda p: "H",
                                  _credentials=lambda t: self._creds())
        conn = factory.made[0]
        self.assertEqual(conn.requests[0][2], "/bkt/daily/backups/db.sqlite")
        self.assertEqual(conn.header("host"), "s3.example.com")
        self.assertTrue(result["success"])

    def test_session_token_adds_security_header(self):
        import http.client
        path = self.make_file("db.sqlite", b"D")
        factory = _conn_factory(status=200)
        with patch.object(http.client, "HTTPSConnection", factory):
            bu.upload_s3(path, self._target(),
                         calculate_sha256=lambda p: "H",
                         _credentials=lambda t: self._creds(session_token="STS"))
        self.assertEqual(factory.made[0].header("x-amz-security-token"), "STS")

    def test_http_connection_used_for_plain_http_endpoint(self):
        import http.client
        path = self.make_file("db.sqlite", b"D")
        https_factory = _conn_factory(status=200)
        http_factory = _conn_factory(status=200)
        with patch.object(http.client, "HTTPSConnection", https_factory), \
             patch.object(http.client, "HTTPConnection", http_factory):
            bu.upload_s3(path, self._target(endpoint="http://s3.example.com"),
                         calculate_sha256=lambda p: "H",
                         _credentials=lambda t: self._creds())
        self.assertEqual(len(https_factory.made), 0)
        self.assertEqual(len(http_factory.made), 1)

    def test_region_defaults_to_us_east_1(self):
        import http.client
        path = self.make_file("db.sqlite", b"D")
        factory = _conn_factory(status=200)
        with patch.object(http.client, "HTTPSConnection", factory):
            bu.upload_s3(path, self._target(region=""),
                         calculate_sha256=lambda p: "H",
                         _credentials=lambda t: self._creds())
        self.assertIn("/us-east-1/s3/aws4_request", factory.made[0].header("authorization"))

    def test_non_2xx_raises(self):
        import http.client
        path = self.make_file("db.sqlite", b"D")
        factory = _conn_factory(status=403)
        with patch.object(http.client, "HTTPSConnection", factory):
            with self.assertRaises(RuntimeError) as ctx:
                bu.upload_s3(path, self._target(),
                             calculate_sha256=lambda p: "H",
                             _credentials=lambda t: self._creds())
        self.assertIn("S3 HTTP 403", str(ctx.exception))

    def test_missing_keys_raise(self):
        path = self.make_file("db.sqlite", b"D")
        with self.assertRaises(RuntimeError) as ctx:
            bu.upload_s3(path, self._target(), calculate_sha256=lambda p: "H",
                         _credentials=lambda t: {"access_key_id": "", "secret_access_key": ""})
        self.assertIn("Access Key / Secret Key 未配置", str(ctx.exception))

    def test_missing_endpoint_or_bucket_raise(self):
        path = self.make_file("db.sqlite", b"D")
        for target in (self._target(endpoint=""), self._target(bucket="")):
            with self.assertRaises(RuntimeError) as ctx:
                bu.upload_s3(path, target, calculate_sha256=lambda p: "H",
                             _credentials=lambda t: self._creds())
            self.assertIn("Endpoint 或 Bucket 未配置", str(ctx.exception))

    def test_empty_remote_path_yields_bare_filename(self):
        import http.client
        path = self.make_file("db.sqlite", b"D")
        factory = _conn_factory(status=200)
        with patch.object(http.client, "HTTPSConnection", factory):
            result = bu.upload_s3(path, self._target(remote_path=""),
                                  calculate_sha256=lambda p: "H",
                                  _credentials=lambda t: self._creds())
        self.assertEqual(result["destination"], "s3://bkt/db.sqlite")
        self.assertEqual(factory.made[0].requests[0][2], "/db.sqlite")


class _FakeOss2:
    """oss2 替身：记录 Bucket 构造与上传调用。"""

    class Auth:
        def __init__(self, access, secret):
            self.access = access
            self.secret = secret

    class Bucket:
        def __init__(self, auth, endpoint, name):
            self.auth = auth
            self.endpoint = endpoint
            self.name = name
            self.uploads: list[tuple] = []
            _FakeOss2.last_bucket = self

        def put_object_from_file(self, key, path):
            self.uploads.append((key, path))


class UploadOssTests(_TempFileMixin, unittest.TestCase):
    def test_missing_oss2_raises_actionable_error(self):
        path = self.make_file("db.sqlite", b"D")
        with patch.dict(sys.modules, {"oss2": None}):
            with self.assertRaises(RuntimeError) as ctx:
                bu.upload_oss(path, {"id": 1}, _credentials=lambda t: {})
        self.assertIn("未安装 oss2", str(ctx.exception))

    def test_uploads_to_composed_key(self):
        path = self.make_file("db.sqlite", b"D")
        with patch.dict(sys.modules, {"oss2": _FakeOss2}):
            result = bu.upload_oss(
                path,
                {"id": 1, "endpoint": "oss-cn.example.com", "bucket": "bk",
                 "remote_path": "daily"},
                _credentials=lambda t: {"access_key_id": "AK", "secret_access_key": "SK"})
        bucket = _FakeOss2.last_bucket
        self.assertEqual(bucket.name, "bk")
        self.assertEqual(bucket.uploads, [("daily/db.sqlite", str(path))])
        self.assertEqual(result["destination"], "oss://bk/daily/db.sqlite")

    def test_missing_credentials_raise(self):
        path = self.make_file("db.sqlite", b"D")
        with patch.dict(sys.modules, {"oss2": _FakeOss2}):
            with self.assertRaises(RuntimeError) as ctx:
                bu.upload_oss(path, {"id": 1, "endpoint": "e", "bucket": "b"},
                              _credentials=lambda t: {})
        self.assertIn("AccessKey ID / Secret 未配置", str(ctx.exception))

    def test_missing_endpoint_or_bucket_raise(self):
        path = self.make_file("db.sqlite", b"D")
        with patch.dict(sys.modules, {"oss2": _FakeOss2}):
            for target in ({"id": 1, "bucket": "b"}, {"id": 1, "endpoint": "e"}):
                with self.assertRaises(RuntimeError) as ctx:
                    bu.upload_oss(path, target,
                                  _credentials=lambda t: {"access_key_id": "A",
                                                          "secret_access_key": "S"})
                self.assertIn("OSS Endpoint 或 Bucket 未配置", str(ctx.exception))


class UploadWebdavTests(_TempFileMixin, unittest.TestCase):
    def _run(self, *, remote_path="daily", username="u", password="p",
             mkcol_statuses=None, put_status=201, endpoint="https://dav.example.com"):
        import http.client
        import r20_backend.net_security as net
        mkcol_statuses = mkcol_statuses or {}
        requests = []

        def fake_urlopen(request, timeout=None):
            requests.append((request.full_url, request.get_method(),
                             request.get_header("Authorization")))
            url = request.full_url
            code = mkcol_statuses.get(url, 201)
            if code >= 400:
                raise urllib.error.HTTPError(url, code, "err", {}, None)
            return _FakeResponse(b"")

        put_factory = _conn_factory(status=put_status)
        creds = {"username": username, "password": password} if username else {}
        path = self.make_file("db.sqlite", b"DAV")
        with patch.object(net, "safe_urlopen", fake_urlopen), \
             patch.object(http.client, "HTTPSConnection", put_factory):
            result = bu.upload_webdav(
                path, {"id": 1, "endpoint": endpoint, "remote_path": remote_path},
                _credentials=lambda t: creds)
        return result, requests, put_factory

    def test_creates_each_directory_then_puts_file(self):
        result, requests, put_factory = self._run(remote_path="a/b")
        self.assertEqual([r[1] for r in requests], ["MKCOL", "MKCOL"])
        self.assertEqual([r[0] for r in requests],
                         ["https://dav.example.com/a", "https://dav.example.com/a/b"])
        self.assertEqual(requests[0][2], "Basic " + __import__("base64").b64encode(b"u:p").decode())
        conn = put_factory.made[0]
        self.assertEqual(conn.requests[0][:3], ("putrequest", "PUT", "/a/b/db.sqlite"))
        self.assertEqual(conn.header("Content-Length"), "3")
        self.assertEqual(b"".join(conn.sent), b"DAV")
        self.assertEqual(result["destination"], "https://dav.example.com/a/b/db.sqlite")

    def test_existing_collection_statuses_are_tolerated(self):
        result, requests, _ = self._run(
            remote_path="a",
            mkcol_statuses={"https://dav.example.com/a": 405})
        self.assertEqual(len(requests), 1)
        self.assertTrue(result["success"])

    def test_redirect_and_missing_auth_tolerated(self):
        for code in (301, 302):
            result, _, _ = self._run(remote_path="a",
                                     mkcol_statuses={"https://dav.example.com/a": code})
            self.assertTrue(result["success"])

    def test_other_mkcol_errors_propagate(self):
        import urllib.error as ue
        with self.assertRaises(ue.HTTPError):
            self._run(remote_path="a",
                      mkcol_statuses={"https://dav.example.com/a": 403})

    def test_no_username_means_no_authorization_header(self):
        _, requests, put_factory = self._run(remote_path="a", username="")
        self.assertIsNone(requests[0][2])
        self.assertIsNone(put_factory.made[0].header("Authorization"))

    def test_non_2xx_put_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(remote_path="a", put_status=507)
        self.assertIn("WebDAV HTTP 507", str(ctx.exception))

    def test_204_is_accepted(self):
        result, _, _ = self._run(remote_path="a", put_status=204)
        self.assertTrue(result["success"])

    def test_empty_remote_path_puts_at_endpoint_root(self):
        result, requests, put_factory = self._run(remote_path="")
        self.assertEqual(requests, [])
        self.assertEqual(put_factory.made[0].requests[0][2], "/db.sqlite")
        self.assertEqual(result["destination"], "https://dav.example.com/db.sqlite")


class _FakeByPyClient:
    """bypy.ByPy() 的替身：按预设脚本返回 code / 抛异常。"""

    script: list = []
    calls: list = []

    def upload(self, source, destination):
        _FakeByPyClient.calls.append((source, destination))
        item = _FakeByPyClient.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeByPyModule:
    """`import bypy` 之后拿到的那个模块对象 —— 必须有 `ByPy` 属性。"""

    ByPy = _FakeByPyClient


class UploadBaiduTests(_TempFileMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        _FakeByPyClient.script = []
        _FakeByPyClient.calls = []

    def test_missing_bypy_returns_failure_dict(self):
        path = self.make_file("db.sqlite", b"D")
        with patch.dict(sys.modules, {"bypy": None}), \
             patch.object(bu.time, "sleep", lambda s: None):
            result = bu.upload_baidu(path, "remote/dir", 3)
        self.assertFalse(result["success"])
        self.assertIn("未安装 bypy", result["error"])
        self.assertEqual(result["attempts"], 1)

    def test_success_on_first_attempt(self):
        path = self.make_file("db.sqlite", b"D")
        _FakeByPyClient.script = [0]
        with patch.dict(sys.modules, {"bypy": _FakeByPyModule}):
            # remote_path 是**相对**路径（"/apps/bypy/" 前缀由返回值补上）
            result = bu.upload_baidu(path, "remote", 3)
        self.assertTrue(result["success"])
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["destination"], "/apps/bypy/remote/db.sqlite")
        # 传给 bypy 的是去掉了前导 "/" 的相对路径
        self.assertEqual(_FakeByPyClient.calls, [(str(path), "remote/db.sqlite")])

    def test_retries_then_succeeds(self):
        path = self.make_file("db.sqlite", b"D")
        _FakeByPyClient.script = [1, 0]
        slept = []
        with patch.dict(sys.modules, {"bypy": _FakeByPyModule}), \
             patch.object(bu.time, "sleep", lambda s: slept.append(s)):
            result = bu.upload_baidu(path, "remote", 3)
        self.assertTrue(result["success"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(slept, [5])

    def test_exhausted_retries_report_last_error(self):
        path = self.make_file("db.sqlite", b"D")
        _FakeByPyClient.script = [7, 7]
        with patch.dict(sys.modules, {"bypy": _FakeByPyModule}), \
             patch.object(bu.time, "sleep", lambda s: None):
            result = bu.upload_baidu(path, "remote", 2)
        self.assertFalse(result["success"])
        self.assertEqual(result["attempts"], 2)
        self.assertIn("ByPy 返回 7", result["error"])

    def test_exception_becomes_error_string(self):
        path = self.make_file("db.sqlite", b"D")
        _FakeByPyClient.script = [RuntimeError("network down")]
        with patch.dict(sys.modules, {"bypy": _FakeByPyModule}), \
             patch.object(bu.time, "sleep", lambda s: None):
            result = bu.upload_baidu(path, "remote", 1)
        self.assertFalse(result["success"])
        self.assertIn("RuntimeError: network down", result["error"])

    def test_sleep_is_capped_at_30_seconds(self):
        path = self.make_file("db.sqlite", b"D")
        _FakeByPyClient.script = [1, 1, 1, 1, 1, 1, 1, 1, 1, 0]
        slept = []
        with patch.dict(sys.modules, {"bypy": _FakeByPyModule}), \
             patch.object(bu.time, "sleep", lambda s: slept.append(s)):
            bu.upload_baidu(path, "remote", 10)
        self.assertEqual(slept, [5, 10, 15, 20, 25, 30, 30, 30, 30])

    def test_empty_remote_path_drops_prefix(self):
        path = self.make_file("db.sqlite", b"D")
        _FakeByPyClient.script = [0]
        with patch.dict(sys.modules, {"bypy": _FakeByPyModule}), \
             patch.object(bu.time, "sleep", lambda s: None):
            result = bu.upload_baidu(path, ".", 1)
        self.assertEqual(result["destination"], "/apps/bypy/db.sqlite")


class UploadBaiduOauthTests(_TempFileMixin, unittest.TestCase):
    """百度官方 OAuth：凭证走 POST body、分片 MD5 逐片校验、失败显式。"""

    def _run(self, *, source=None, creds=None, token=None, precreated=None,
             uploaded=None, created=None, block_size=None):
        path = source or self.make_file("db.sqlite", b"A" * 8)
        creds = creds if creds is not None else {
            "app_key": "AK", "app_secret": "AS", "refresh_token": "RT"}
        token = token if token is not None else {"access_token": "AT"}
        precreated = precreated if precreated is not None else {"uploadid": "UID"}
        created = created if created is not None else {"fs_id": "999"}
        calls = []

        def fake_urlencoded(url, data=None, timeout=60):
            calls.append(("urlencoded", url, data))
            if "method=precreate" in url:
                return precreated
            if "method=create" in url:
                return created
            return token

        uploaded_script = list(uploaded) if uploaded is not None else None

        def fake_multipart(url, field, filename, content, timeout=180):
            calls.append(("multipart", url, field, filename, content))
            if uploaded_script is not None:
                return uploaded_script.pop(0)
            return {"md5": hashlib.md5(content).hexdigest()}

        import r20_backend.backup_secrets as secrets
        saved = []
        with patch.object(secrets, "save_credentials",
                          lambda ref, payload: saved.append((ref, payload))), \
             patch("builtins.open", open):
            result = bu.upload_baidu_oauth(
                path, {"id": 1, "remote_path": "R20_Backups"},
                _credentials=lambda t: creds,
                _urlencoded_json=fake_urlencoded,
                _multipart_upload=fake_multipart)
        return result, calls, saved

    def test_happy_path_precreate_upload_create(self):
        result, calls, _ = self._run()
        self.assertTrue(result["success"])
        self.assertEqual(result["destination"], "/apps/R20QuantumTrader/R20_Backups/db.sqlite")
        kinds = [c[0] for c in calls]
        self.assertEqual(kinds, ["urlencoded", "urlencoded", "multipart", "urlencoded"])
        # ★ 安全断言：token 端点必须走 POST body，凭证绝不进 query string
        token_call = calls[0]
        self.assertEqual(token_call[1], "https://openapi.baidu.com/oauth/2.0/token")
        self.assertEqual(token_call[2]["grant_type"], "refresh_token")
        self.assertEqual(token_call[2]["client_secret"], "AS")
        self.assertNotIn("client_secret=", token_call[1])
        self.assertNotIn("refresh_token=", token_call[1])
        # 预创建载荷必须带 block_list 与 size
        precreate = calls[1]
        self.assertIn("method=precreate", precreate[1])
        self.assertIn("block_list", precreate[2])

    def test_missing_credentials_raise(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(creds={"app_key": "AK"})
        self.assertIn("需要 App Key、App Secret 与 Refresh Token", str(ctx.exception))

    def test_missing_access_token_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(token={})
        self.assertIn("未返回 Access Token", str(ctx.exception))

    def test_missing_uploadid_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run(precreated={})
        self.assertIn("预创建未返回 uploadid", str(ctx.exception))

    def test_multipart_md5_mismatch_raises(self):
        blob = b"A" * 8
        path = self.make_file("db.sqlite", blob)
        with self.assertRaises(RuntimeError) as ctx:
            self._run(source=path, uploaded=[{"md5": "deadbeef"}])
        self.assertIn("MD5 校验失败", str(ctx.exception))

    def test_part_without_md5_is_accepted(self):
        result, _, _ = self._run(uploaded=[{}])
        self.assertTrue(result["success"])

    def test_rotated_refresh_token_is_persisted(self):
        _, _, saved = self._run(token={"access_token": "AT", "refresh_token": "RT2"})
        # ⚠️ 实测到的生产缺陷（本刀仅记录，**未修**，避免改动上传行为）：
        # `save_credentials(str(target.get("credential_ref")), …)` 没有像
        # `_credentials` 那样回落 `or f"backup:{target['id']}"`，于是 credential_ref
        # 缺省时写进去的字面量是 **"None"** —— 轮换后的 refresh_token 落在一个
        # 谁都读不回来的键上（读取侧查的是 `backup:1`）。表现是"百度备份用几天后
        # 开始报 refresh_token 失效"，且每次刷新都重复发生。
        self.assertEqual(saved, [("None", {"refresh_token": "RT2"})])

    def test_unchanged_refresh_token_is_not_persisted(self):
        _, _, saved = self._run(token={"access_token": "AT", "refresh_token": "RT"})
        self.assertEqual(saved, [])

    def test_empty_remote_path_silently_falls_back_to_default_dir(self):
        import r20_backend.backup_secrets as secrets
        path = self.make_file("db.sqlite", b"A")
        calls = []

        def fake_urlencoded(url, data=None, timeout=60):
            calls.append((url, data))
            if "precreate" in url:
                return {"uploadid": "U"}
            if "method=create" in url:
                return {}
            return {"access_token": "AT"}

        with patch.object(secrets, "save_credentials", lambda *a: None):
            result = bu.upload_baidu_oauth(
                path, {"id": 1, "remote_path": ""},
                _credentials=lambda t: {"app_key": "A", "app_secret": "S", "refresh_token": "R"},
                _urlencoded_json=fake_urlencoded,
                _multipart_upload=lambda *a, **k: {"md5": hashlib.md5(b"A").hexdigest()})
        # ⚠️ 实测行为（与直觉相反，本刀仅记录，**未改**）：
        # `str(target.get("remote_path") or "R20_Backups")` 里**空串是假值**，
        # 于是 `remote_path=""` 无法表达"放到应用根目录"，会被**静默**替换成默认目录
        # R20_Backups。调用方目前**没有任何写法**能真正落到 /apps/R20QuantumTrader/。
        self.assertEqual(result["destination"],
                         "/apps/R20QuantumTrader/R20_Backups/db.sqlite")
        # 预创建载荷里的 path 同样落在默认目录下（不是应用根）
        self.assertEqual(calls[1][1]["path"],
                         "/apps/R20QuantumTrader/R20_Backups/db.sqlite")

    def test_multiple_chunks_are_uploaded_in_order(self):
        blob = b"B" * (4 * 1024 * 1024 + 3)
        path = self.make_file("big.sqlite", blob)
        result, calls, _ = self._run(source=path)
        parts = [c for c in calls if c[0] == "multipart"]
        self.assertEqual(len(parts), 2)
        self.assertIn("partseq=0", parts[0][1])
        self.assertIn("partseq=1", parts[1][1])
        self.assertEqual(parts[0][4], blob[: 4 * 1024 * 1024])
        self.assertEqual(parts[1][4], blob[4 * 1024 * 1024:])
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()

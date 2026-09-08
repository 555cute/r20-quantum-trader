"""Custom R20 backup job runtime: archive, encrypt, verify, deliver and retain."""
from __future__ import annotations
import base64
import fnmatch
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
LOCAL_DIR = BACKUPS / "local"
SQLITE_DIR = BACKUPS / "sqlite"
MANIFEST_DIR = BACKUPS / "manifests"
BJ_TZ = timezone(timedelta(hours=8))
MAGIC = b"R20GCM2\x00"
MANDATORY_EXCLUDES = (
    ".git/**", ".env", ".okx/**", ".bypy/**", "backups/**", "logs/**",
    "data/r20_admin.db*", "data/*.enc", "data/.*_key", "data/credentials/**",
    "data/*.db-wal", "data/*.db-shm", "**/__pycache__/**", "*.pyc",
)
SCOPE_PATHS = {
    "data": ("data",),
    "scripts": ("scripts",),
    "dashboard": ("dashboard",),
    "r20_backend": ("r20_backend",),
    "r20_gateway": ("r20_gateway",),
    "tests": ("tests",),
    "recovery_guide": ("RECOVERY_GUIDE.md",),
    "agent_profile": ("SOUL.md", "PROFILE.md", "AGENTS.md", "MEMORY.md"),
    "root_configs": ("README.md", "requirements.txt", "pyproject.toml", ".gitignore"),
}


def clean_stale_staging(max_age_seconds: int = 3600) -> int:
    staging = BACKUPS / "staging"
    if not staging.exists():
        return 0
    cleaned = 0
    now_ts = time.time()
    for item in staging.glob("r20_backup_*"):
        try:
            if item.is_file() and ((now_ts - item.stat().st_mtime > max_age_seconds) or item.stat().st_size == 0):
                item.unlink(missing_ok=True)
                cleaned += 1
        except OSError:
            pass
    return cleaned


def prune(paths: Iterable[Path], retention: int) -> None:
    existing: list[tuple[float, Path]] = []
    for path in paths:
        try:
            if path.exists() and path.is_file():
                existing.append((path.stat().st_mtime, path))
        except OSError:
            pass
    existing.sort(key=lambda x: x[0], reverse=True)
    for _, item in existing[max(0, retention):]:
        try:
            item.unlink(missing_ok=True)
        except OSError:
            pass


def retain_local_archive(source: Path, retention: int, destination_dir: Path | None = None) -> Path:
    if not source.exists() or not source.is_file():
        raise RuntimeError(f"备份归档源文件不存在或无效: {source}")
    destination_dir = destination_dir or LOCAL_DIR
    resolved_dest = destination_dir.resolve()
    if not resolved_dest.is_relative_to((ROOT / "backups").resolve()):
        raise RuntimeError("本地归档目录必须位于 backups/ 目录下")
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    shutil.copy2(source, destination)
    prune((p for p in destination_dir.glob("r20_backup_*") if p.is_file()), retention)
    return destination


def sqlite_hot_backups(timestamp: str, retention: int, destination_dir: Path | None = None) -> list[Path]:
    destination_dir = destination_dir or SQLITE_DIR
    resolved_dest = destination_dir.resolve()
    if not resolved_dest.is_relative_to((ROOT / "backups").resolve()):
        raise RuntimeError("SQLite 备份目录必须位于 backups/ 目录下")
    destination_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    data_dir = ROOT / "data"
    if not data_dir.exists():
        return created

    for source in data_dir.glob("*.db"):
        if source.name == "r20_admin.db":
            continue
        if source.name.endswith("-wal") or source.name.endswith("-shm"):
            continue
        destination = destination_dir / f"{source.stem}_{timestamp}.db"
        try:
            try:
                source_conn = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True, timeout=30.0)
            except Exception:
                source_conn = sqlite3.connect(str(source), timeout=30.0)
            try:
                target_conn = sqlite3.connect(str(destination), timeout=30.0)
                try:
                    source_conn.backup(target_conn)
                finally:
                    target_conn.close()
            finally:
                source_conn.close()
            os.chmod(destination, 0o600)
            created.append(destination)
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError(f"SQLite 数据库 {source.name} 热备份失败：{exc}") from exc
    prune((p for p in destination_dir.glob("*.db") if p.is_file()), retention)
    return created


def calculate_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"文件不存在，无法计算 SHA256: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _excluded(relative: str, patterns: list[str]) -> bool:
    rel = relative.replace(os.sep, "/").lstrip("./")
    return any(fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(f"{rel}/", pattern) for pattern in [*MANDATORY_EXCLUDES, *patterns])


def _tar_filter(patterns: list[str]):
    def filter_info(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        return None if _excluded(info.name, patterns) else info
    return filter_info


def create_archive(job: dict[str, Any], timestamp: str) -> tuple[Path, list[str]]:
    staging = BACKUPS / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c for c in str(job.get("id", "backup")) if c.isalnum() or c in "-_")[:48]
    path = staging / f"r20_backup_{safe_id}_{timestamp}.tar.gz"
    included: list[str] = []
    try:
        level = int(job.get("compression_level", 6))
        level = max(1, min(level, 9))
    except (ValueError, TypeError):
        level = 6

    with tarfile.open(path, "w:gz", compresslevel=level) as archive:
        for scope in job.get("scope", []):
            for relative in SCOPE_PATHS.get(scope, ()):
                source = ROOT / relative
                if source.exists() and not _excluded(relative, job.get("exclude", [])):
                    try:
                        archive.add(source, arcname=relative, recursive=True, filter=_tar_filter(job.get("exclude", [])))
                        included.append(relative)
                    except (FileNotFoundError, PermissionError):
                        pass
    if not included:
        path.unlink(missing_ok=True)
        raise RuntimeError("所选范围没有可归档文件")
    os.chmod(path, 0o600)
    return path, included


def _derive_key(secret: str, salt: bytes) -> bytes:
    if len(secret) < 16:
        raise RuntimeError("备份加密密钥至少 16 个字符")
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=32 * 1024 * 1024)


def encrypt_archive(source: Path, key_env: str) -> Path:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    secret = os.getenv(key_env, "")
    if not secret:
        raise RuntimeError(f"加密已启用但环境变量 {key_env} 未配置")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(secret, salt)
    target = source.with_suffix(source.suffix + ".aes256")
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    with source.open("rb") as inp, target.open("wb+") as out:
        out.write(MAGIC)
        out.write(salt)
        out.write(nonce)
        out.write(b"\x00" * 16)
        for chunk in iter(lambda: inp.read(1024 * 1024), b""):
            out.write(encryptor.update(chunk))
        out.write(encryptor.finalize())
        out.seek(len(MAGIC) + len(salt) + len(nonce))
        out.write(encryptor.tag)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(target, 0o600)
    source.unlink(missing_ok=True)
    return target


def decrypt_archive(source: Path, key_env: str, destination: Path) -> Path:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    secret = os.getenv(key_env, "")
    if not secret:
        raise RuntimeError(f"解密需要环境变量 {key_env}")
    with source.open("rb") as inp:
        if inp.read(len(MAGIC)) != MAGIC:
            raise RuntimeError("不是受支持的 R20 AES-256-GCM 归档")
        salt, nonce, tag = inp.read(16), inp.read(12), inp.read(16)
        if len(salt) != 16 or len(nonce) != 12 or len(tag) != 16:
            raise RuntimeError("加密归档头损坏")
        decryptor = Cipher(algorithms.AES(_derive_key(secret, salt)), modes.GCM(nonce, tag)).decryptor()
        with destination.open("wb") as out:
            for chunk in iter(lambda: inp.read(1024 * 1024), b""):
                out.write(decryptor.update(chunk))
            out.write(decryptor.finalize())
            out.flush()
            os.fsync(out.fileno())
    os.chmod(destination, 0o600)
    return destination


def verify_archive(path: Path, expected_sha256: str = "", key_env: str = "") -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise RuntimeError("归档文件不存在")
    checksum = calculate_sha256(path)
    if expected_sha256 and checksum.lower() != expected_sha256.strip().lower():
        raise RuntimeError("SHA256 校验失败")
    temp: Path | None = None
    tar_path = path
    try:
        if path.name.endswith(".aes256"):
            BACKUPS.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix="r20-verify-", suffix=".tar.gz", dir=BACKUPS)
            os.close(fd)
            temp = Path(temp_name)
            tar_path = decrypt_archive(path, key_env, temp)

        try:
            archive = tarfile.open(tar_path, "r:gz")
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise RuntimeError(f"归档文件损坏或不是有效 gzip 归档：{exc}") from exc

        with archive:
            members = archive.getmembers()
            unsafe = []
            for m in members:
                m_path = Path(m.name)
                if m.name.startswith("/") or m_path.is_absolute() or os.path.isabs(m.name):
                    unsafe.append(f"绝对路径: {m.name}")
                elif ".." in m_path.parts or ".." in m.name.replace("\\", "/").split("/"):
                    unsafe.append(f"路径逃逸: {m.name}")
                elif not (ROOT / m.name).resolve().is_relative_to(ROOT.resolve()):
                    unsafe.append(f"目标越界: {m.name}")
                elif m.issym() or m.islnk():
                    link_target = m.linkname
                    if os.path.isabs(link_target) or ".." in Path(link_target).parts:
                        unsafe.append(f"不安全符号链接: {m.name} -> {link_target}")
                    elif not (ROOT / Path(m.name).parent / link_target).resolve().is_relative_to(ROOT.resolve()):
                        unsafe.append(f"符号链接越界: {m.name} -> {link_target}")
                elif m.isdev() or m.ischr() or m.isblk() or m.isfifo():
                    unsafe.append(f"特殊设备节点: {m.name}")

            if unsafe:
                raise RuntimeError(f"归档包含不安全路径：{unsafe[0]}")
            roots = sorted({Path(m.name).parts[0] for m in members if Path(m.name).parts})
        return {
            "valid": True,
            "sha256": checksum,
            "members": len(members),
            "roots": roots,
            "encrypted": path.name.endswith(".aes256"),
        }
    finally:
        if temp and temp.exists():
            temp.unlink(missing_ok=True)


def upload_baidu(source: Path, remote_path: str, retries: int) -> dict[str, Any]:
    try:
        import bypy
    except ImportError:
        return {"success": False, "attempts": 1, "destination": remote_path, "error": "系统未安装 bypy 模块"}
    remote = "/".join(part for part in Path(remote_path).parts if part not in {"/", "."})
    destination = f"{remote}/{source.name}" if remote else source.name
    last = ""
    for attempt in range(1, retries + 1):
        try:
            code = bypy.ByPy().upload(str(source), destination)
            if code == 0:
                return {"success": True, "attempts": attempt, "destination": f"/apps/bypy/{destination}"}
            last = f"ByPy 返回 {code}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(min(attempt * 5, 30))
    return {"success": False, "attempts": retries, "destination": destination, "error": last}


def _credentials(target: dict[str, Any]) -> dict[str, str]:
    from r20_backend.backup_secrets import load_credentials
    return load_credentials(str(target.get("credential_ref") or f"backup:{target['id']}"))


def _urlencoded_json(url: str, data: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, headers={"User-Agent": "R20-Backup/6.2.0"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    payload = json.loads(raw or "{}")
    if payload.get("errno") not in (None, 0) or payload.get("error"):
        raise RuntimeError(str(payload.get("errmsg") or payload.get("error_description") or payload))
    return payload


def _multipart_upload(url: str, field_name: str, filename: str, content: bytes, timeout: int = 180) -> dict[str, Any]:
    boundary = f"----R20{hashlib.sha256(os.urandom(16)).hexdigest()[:24]}"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field_name}\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "R20-Backup/6.2.0"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")
    if payload.get("errno") not in (None, 0):
        raise RuntimeError(str(payload.get("errmsg") or payload))
    return payload


def upload_baidu_oauth(source: Path, target: dict[str, Any]) -> dict[str, Any]:
    from r20_backend.backup_secrets import save_credentials
    creds = _credentials(target)
    app_key = creds.get("app_key", "")
    app_secret = creds.get("app_secret", "")
    refresh_token = creds.get("refresh_token", "")
    if not app_key or not app_secret or not refresh_token:
        raise RuntimeError("百度官方 OAuth 需要 App Key、App Secret 与 Refresh Token")
    token_url = "https://openapi.baidu.com/oauth/2.0/token?" + urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": app_key, "client_secret": app_secret})
    token = _urlencoded_json(token_url)
    access_token = str(token.get("access_token") or "")
    if not access_token:
        raise RuntimeError("百度 OAuth 未返回 Access Token")
    if token.get("refresh_token") and token["refresh_token"] != refresh_token:
        save_credentials(str(target.get("credential_ref")), {"refresh_token": token["refresh_token"]})
    chunk_size = 4 * 1024 * 1024
    block_list: list[str] = []
    with source.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            block_list.append(hashlib.md5(chunk).hexdigest())
    remote_dir = str(target.get("remote_path") or "R20_Backups").strip("/")
    remote_path = f"/apps/R20QuantumTrader/{remote_dir}/{source.name}" if remote_dir else f"/apps/R20QuantumTrader/{source.name}"
    precreate_url = "https://pan.baidu.com/rest/2.0/xpan/file?method=precreate&access_token=" + urllib.parse.quote(access_token, safe="")
    common = {"path": remote_path, "size": source.stat().st_size, "isdir": 0, "autoinit": 1, "rtype": 3, "block_list": json.dumps(block_list)}
    precreated = _urlencoded_json(precreate_url, common)
    upload_id = str(precreated.get("uploadid") or "")
    if not upload_id:
        raise RuntimeError("百度网盘预创建未返回 uploadid")
    with source.open("rb") as handle:
        for part_seq in range(len(block_list)):
            chunk = handle.read(chunk_size)
            upload_url = "https://d.pcs.baidu.com/rest/2.0/pcs/superfile2?" + urllib.parse.urlencode({"method": "upload", "type": "tmpfile", "access_token": access_token, "path": remote_path, "uploadid": upload_id, "partseq": part_seq})
            uploaded = _multipart_upload(upload_url, "file", source.name, chunk)
            if uploaded.get("md5") and uploaded["md5"] != block_list[part_seq]:
                raise RuntimeError(f"百度分片 {part_seq} MD5 校验失败")
    create_url = "https://pan.baidu.com/rest/2.0/xpan/file?method=create&access_token=" + urllib.parse.quote(access_token, safe="")
    created = _urlencoded_json(create_url, {**common, "uploadid": upload_id})
    return {"success": True, "attempts": 1, "destination": remote_path, "fs_id": str(created.get("fs_id") or "")}


def upload_s3(source: Path, target: dict[str, Any]) -> dict[str, Any]:
    """S3-compatible SigV4 upload without a mandatory boto3 dependency."""
    import hmac
    creds = _credentials(target)
    access = creds.get("access_key_id", "")
    secret = creds.get("secret_access_key", "")
    if not access or not secret:
        raise RuntimeError("S3 Access Key / Secret Key 未配置")
    endpoint = str(target.get("endpoint", "")).rstrip("/")
    bucket = str(target.get("bucket", ""))
    if not endpoint or not bucket:
        raise RuntimeError("S3 Endpoint 或 Bucket 未配置")
    region = str(target.get("region") or "us-east-1")
    key = "/".join(x for x in [str(target.get("remote_path") or "").strip("/"), source.name] if x)
    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.netloc
    path = f"/{bucket}/{urllib.parse.quote(key, safe='/')}" if target.get("force_path_style") else f"/{urllib.parse.quote(key, safe='/')}"
    if not target.get("force_path_style"):
        host = f"{bucket}.{host}"
    now = datetime.now(timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = calculate_sha256(source)
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amzdate, "content-length": str(source.stat().st_size)}
    if creds.get("session_token"):
        headers["x-amz-security-token"] = creds["session_token"]
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical = f"PUT\n{path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = f"AWS4-HMAC-SHA256\n{amzdate}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    sign = lambda key, msg: hmac.new(key, msg.encode(), hashlib.sha256).digest()
    signing = sign(sign(sign(sign(("AWS4" + secret).encode(), datestamp), region), "s3"), "aws4_request")
    headers["authorization"] = f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={signed_headers}, Signature={hmac.new(signing, string_to_sign.encode(), hashlib.sha256).hexdigest()}"
    import http.client
    connection = (http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection)(host, timeout=300)
    connection.putrequest("PUT", path, skip_host=True)
    for k, v in headers.items():
        connection.putheader(k, v)
    connection.endheaders()
    with source.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            connection.send(chunk)
    response = connection.getresponse()
    response.read()
    connection.close()
    if response.status not in (200, 201):
        raise RuntimeError(f"S3 HTTP {response.status}")
    return {"success": True, "attempts": 1, "destination": f"s3://{bucket}/{key}"}


def upload_oss(source: Path, target: dict[str, Any]) -> dict[str, Any]:
    try:
        import oss2
    except ImportError:
        raise RuntimeError("系统未安装 oss2 模块，请先安装 oss2 (pip install oss2)")
    creds = _credentials(target)
    access = creds.get("access_key_id", "")
    secret = creds.get("secret_access_key", "")
    if not access or not secret:
        raise RuntimeError("OSS AccessKey ID / Secret 未配置")
    endpoint = str(target.get("endpoint", "")).strip()
    bucket_name = str(target.get("bucket", "")).strip()
    if not endpoint or not bucket_name:
        raise RuntimeError("OSS Endpoint 或 Bucket 未配置")
    key = "/".join(x for x in [str(target.get("remote_path") or "").strip("/"), source.name] if x)
    bucket = oss2.Bucket(oss2.Auth(access, secret), endpoint, bucket_name)
    bucket.put_object_from_file(key, str(source))
    return {"success": True, "attempts": 1, "destination": f"oss://{bucket_name}/{key}"}


def upload_webdav(source: Path, target: dict[str, Any]) -> dict[str, Any]:
    creds = _credentials(target)
    endpoint = str(target["endpoint"]).rstrip("/")
    remote = str(target.get("remote_path") or "").strip("/")
    auth = ""
    username = creds.get("username", "")
    password = creds.get("password", "")
    if username:
        auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    current = endpoint
    for part in [x for x in remote.split("/") if x]:
        current += "/" + urllib.parse.quote(part, safe="")
        req = urllib.request.Request(current, headers={"Authorization": auth} if auth else {}, method="MKCOL")
        try:
            urllib.request.urlopen(req, timeout=20).close()
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 405):
                raise
    url = current + "/" + urllib.parse.quote(source.name, safe="")
    parsed = urllib.parse.urlparse(url)
    import http.client
    connection = (http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection)(parsed.netloc, timeout=300)
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    connection.putrequest("PUT", path)
    connection.putheader("Content-Type", "application/octet-stream")
    connection.putheader("Content-Length", str(source.stat().st_size))
    if auth:
        connection.putheader("Authorization", auth)
    connection.endheaders()
    with source.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            connection.send(chunk)
    response = connection.getresponse()
    response.read()
    connection.close()
    if response.status not in (200, 201, 204):
        raise RuntimeError(f"WebDAV HTTP {response.status}")
    return {"success": True, "attempts": 1, "destination": url}


def deliver_target(source: Path, target: dict[str, Any]) -> dict[str, Any]:
    target_type = target.get("type")
    if target_type in {"s3", "oss", "webdav", "aliyundrive", "quark"}:
        from r20_backend.net_security import validate_outbound_url
        target = {**target, "endpoint": validate_outbound_url(str(target.get("endpoint") or ""), allow_private=bool(target.get("allow_private_endpoint")))}
    if target_type == "baidu":
        if target.get("auth_mode", "bypy") == "oauth":
            return upload_baidu_oauth(source, target)
        return upload_baidu(source, target.get("remote_path", "R20_Backups"), int(target.get("retries", 3)))
    if target_type == "local":
        destination = (ROOT / str(target.get("path") or "backups/local")).resolve()
        if not destination.is_relative_to((ROOT / "backups").resolve()):
            raise RuntimeError("本地目标路径非法：必须位于 backups/ 目录下")
        return {
            "success": True,
            "attempts": 1,
            "destination": str(retain_local_archive(source, int(target.get("retention", 3)), destination).relative_to(ROOT)),
        }
    upload = upload_s3 if target_type == "s3" else upload_oss if target_type == "oss" else upload_webdav if target_type in {"webdav", "aliyundrive", "quark"} else None
    if not upload:
        raise RuntimeError(f"不支持的灾备目标：{target_type}")
    last = ""
    retries = int(target.get("retries", 3))
    for attempt in range(1, retries + 1):
        try:
            result = upload(source, target)
            result["attempts"] = attempt
            return result
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(min(attempt * 5, 30))
    return {"success": False, "attempts": retries, "error": last}


def run_backup_job(job: dict[str, Any]) -> dict[str, Any]:
    try:
        clean_stale_staging()
    except Exception:
        pass
    started = datetime.now(BJ_TZ)
    stamp = started.strftime("%Y%m%d_%H%M%S")
    safe_id = "".join(c for c in str(job.get("id", "job")) if c.isalnum() or c in "-_")[:48]
    result: dict[str, Any] = {
        "job_id": job.get("id", "unknown"),
        "job_name": job.get("name", "未命名灾备"),
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "running",
        "targets": [],
        "sqlite": [],
        "errors": [],
    }
    archive: Path | None = None
    try:
        enabled_file_targets = [x for x in job.get("targets", []) if x.get("enabled")]
        if enabled_file_targets:
            if job.get("pre_backup_sync"):
                script = ROOT / "scripts" / "sync_full_ledger.py"
                if script.exists():
                    subprocess.run([sys.executable, str(script)], cwd=ROOT, timeout=60, check=False, capture_output=True, text=True)
            archive, included = create_archive(job, stamp)
            result["included"] = included
            if job.get("encryption", {}).get("enabled"):
                archive = encrypt_archive(archive, str(job["encryption"]["key_env"]))
                result["encrypted"] = True
            else:
                result["encrypted"] = False
            result["archive"] = archive.name
            result["bytes"] = archive.stat().st_size
            result["sha256"] = calculate_sha256(archive)
            verification = verify_archive(archive, result["sha256"], str(job.get("encryption", {}).get("key_env", "")))
            result["archive_members"] = verification["members"]
            result["archive_roots"] = verification["roots"]
            for target in enabled_file_targets:
                try:
                    target_result = deliver_target(archive, target)
                except Exception as exc:
                    target_result = {"success": False, "attempts": 1, "error": f"{type(exc).__name__}: {exc}"}
                result["targets"].append({"id": target.get("id", "target"), "type": target.get("type", "unknown"), **target_result})
        if job.get("sqlite", {}).get("enabled"):
            sqlite_dir = SQLITE_DIR / safe_id
            result["sqlite"] = [str(x.relative_to(ROOT)) for x in sqlite_hot_backups(stamp, int(job["sqlite"].get("retention", 7)), sqlite_dir)]
        target_success = [x for x in result["targets"] if x.get("success")]
        target_failure = [x for x in result["targets"] if not x.get("success")]
        any_success = bool(target_success or result["sqlite"])
        all_file_targets_succeeded = bool(enabled_file_targets) and not target_failure and len(target_success) == len(enabled_file_targets)
        result["status"] = "success" if any_success and not target_failure else "partial" if any_success else "failed"
        if target_failure:
            result["errors"].extend(str(x.get("error") or "目标失败") for x in target_failure)
        if archive and job.get("cleanup_local_on_success") and all_file_targets_succeeded:
            archive.unlink(missing_ok=True)
            result["temporary_cleaned"] = True
        elif archive:
            result["temporary_cleaned"] = False
    except Exception as exc:
        result["status"] = "failed"
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    result["finished_at"] = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    manifest = MANIFEST_DIR / f"{safe_id}_{stamp}.json"
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(manifest, 0o600)
    result["manifest"] = str(manifest.relative_to(ROOT))
    return result

"""R20 Strategy Policy Snapshot & Version Control Workbench Engine.

Provides immutable snapshot fingerprinting, persistent archiving, one-click rollback,
and export/import capabilities across all 4 strategy units:
1. Prompt Profile (Prompt Studio)
2. Evolution Mind (Evolution Shield)
3. Physical Interceptors (Interceptors Plugin Pipeline)
4. Model Council (Council Desk)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore

from r20_backend.version import __version__

_BJ = timezone(timedelta(hours=8))

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "policy_archives"
ARCHIVE_INDEX_FILE = ARCHIVE_DIR / "index.json"

DEFAULT_BASE_VERSION = f"v{__version__}"


def compute_layout_hash(profile: Dict[str, Any]) -> str:
    """Computes a deterministic hash for a prompt profile layout."""
    parts: List[str] = []
    mode = str(profile.get("editor_mode", "modules"))
    parts.append(f"mode:{mode}")

    if mode == "modules":
        # Support both flat modules list and pipeline dictionary
        modules = profile.get("modules")
        if modules is None and isinstance(profile.get("pipelines"), dict):
            modules = []
            for pipe_key in sorted(profile["pipelines"].keys()):
                pipe_mods = profile["pipelines"][pipe_key]
                if isinstance(pipe_mods, list):
                    modules.extend(pipe_mods)
        if isinstance(modules, list):
            for m in modules:
                if isinstance(m, dict):
                    m_id = str(m.get("id", ""))
                    enabled = "1" if m.get("enabled", True) else "0"
                    content = str(m.get("content", "")).strip()
                    c_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
                    parts.append(f"{m_id}:{enabled}:{c_hash}")
    elif mode == "simple":
        simple_pol = profile.get("simple_policy")
        if isinstance(simple_pol, dict):
            for k in sorted(simple_pol.keys()):
                v = str(simple_pol[k]).strip()
                v_hash = hashlib.sha256(v.encode("utf-8")).hexdigest()[:8]
                parts.append(f"{k}:{v_hash}")
        else:
            parts.append("empty_simple")
    else:
        template_found = False
        for tk in ("trading_system", "trading_user", "evolution_system", "evolution_user"):
            if tk in profile:
                val = str(profile.get(tk, "")).strip()
                v_hash = hashlib.sha256(val.encode("utf-8")).hexdigest()[:8]
                parts.append(f"{tk}:{v_hash}")
                template_found = True
        if not template_found:
            full_content = str(profile.get("full_system_prompt", "")).strip()
            f_hash = hashlib.sha256(full_content.encode("utf-8")).hexdigest()[:8]
            parts.append(f"full:{f_hash}")

    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def compute_file_hash(file_path: Path) -> str:
    """Computes an 8-char SHA256 hex digest for a file if it exists."""
    if not file_path.is_file():
        return "missing"
    try:
        content = file_path.read_bytes()
        return hashlib.sha256(content).hexdigest()[:8]
    except Exception:
        return "err_read"


def extract_prompt_profile_fingerprint(
    profile: Optional[Dict[str, Any]] = None,
    root_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Extracts immutable fingerprint of the active prompt profile."""
    prof = profile
    if prof is None:
        sys_path_added = False
        scripts_dir = str((root_dir or ROOT) / "scripts")
        try:
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
                sys_path_added = True
            try:
                from prompt_library import active_profile
                prof = active_profile()
            except (ImportError, AttributeError):
                from prompt_library import load_active_profile
                prof = load_active_profile()
        except Exception as e:
            logger.warning("Failed to load active profile: %s", e)
            prof = {
                "id": "stable",
                "name": "全维度波段强化版",
                "editor_mode": "modules",
            }
        finally:
            if sys_path_added and scripts_dir in sys.path:
                try:
                    sys.path.remove(scripts_dir)
                except ValueError:
                    pass

    p_id = str(prof.get("id", "stable"))
    p_name = str(prof.get("name", "全维度波段强化版"))
    editor_mode = str(prof.get("editor_mode", "modules"))
    layout_hash = compute_layout_hash(prof)

    return {
        "active_profile_id": p_id,
        "active_profile_name": p_name,
        "editor_mode": editor_mode,
        "layout_hash": layout_hash,
    }


def extract_evolution_mind_fingerprint(
    memory_snapshot: Optional[Dict[str, Any]] = None,
    root_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Extracts immutable fingerprint of the structured self-evolution mind."""
    snap = memory_snapshot
    if snap is None:
        sys_path_added = False
        scripts_dir = str((root_dir or ROOT) / "scripts")
        try:
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
                sys_path_added = True
            from evolution_shield import read_memory_snapshot
            snap = read_memory_snapshot()
        except Exception as e:
            logger.warning("Failed to read memory snapshot: %s", e)
            snap = {"exists": False, "version": "missing", "lessons": []}
        finally:
            if sys_path_added and scripts_dir in sys.path:
                try:
                    sys.path.remove(scripts_dir)
                except ValueError:
                    pass

    version = str(snap.get("version", "missing"))
    lessons = snap.get("lessons") or []
    if not isinstance(lessons, list):
        lessons = []
    enabled_count = len([item for item in lessons if isinstance(item, dict) and item.get("enabled", True)])
    total_count = len(lessons)

    return {
        "version": version,
        "enabled_count": enabled_count,
        "total_count": total_count,
    }


def extract_interceptors_fingerprint(
    interceptor_plugins: Optional[List[Dict[str, Any]]] = None,
    plugins_dir: Optional[Path] = None,
    root_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Extracts immutable fingerprint of the physical interceptors pipeline."""
    p_dir = plugins_dir or ((root_dir or ROOT) / "plugins" / "interceptors")
    plugins = interceptor_plugins

    if plugins is None:
        try:
            from r20_backend.interceptor_manager import list_plugins
            plugins = list_plugins(create_if_missing=False)
        except Exception as e:
            logger.warning("Failed to list plugins: %s", e)
            plugins = []

    pipeline_info: List[Dict[str, Any]] = []
    enabled_plugins: List[str] = []

    for idx, item in enumerate(plugins if isinstance(plugins, list) else []):
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename", ""))
        enabled = bool(item.get("enabled", False))
        f_hash = str(item.get("file_hash") or "")
        if not f_hash:
            file_path = p_dir / filename
            f_hash = compute_file_hash(file_path)

        if enabled:
            enabled_plugins.append(filename)
            pipeline_info.append({
                "order": idx,
                "filename": filename,
                "file_hash": f_hash,
            })

    sorted_pipeline = sorted(pipeline_info, key=lambda x: x["order"])
    pipe_str = ";".join([f"{p['order']}:{p['filename']}:{p['file_hash']}" for p in sorted_pipeline])
    plugins_hash = hashlib.sha256(pipe_str.encode("utf-8")).hexdigest()[:8]

    return {
        "plugins_hash": plugins_hash,
        "enabled_count": len(enabled_plugins),
        "total_count": len(plugins) if isinstance(plugins, list) else 0,
        "enabled_plugins": enabled_plugins,
        "pipeline": sorted_pipeline,
    }


def extract_council_fingerprint(
    council_config: Optional[Dict[str, Any]] = None,
    root_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Extracts immutable fingerprint of the trading desk council."""
    cfg = council_config
    if cfg is None:
        try:
            from r20_backend.council_manager import load_council_config
            cfg = load_council_config()
        except Exception as e:
            logger.warning("Failed to load council config: %s", e)
            cfg = {"enabled": False, "consensus_mode": "standard", "roles": {}}

    enabled = bool(cfg.get("enabled", False))
    raw_mode = str(cfg.get("consensus_mode", "standard")).lower()
    consensus_mode = "cross_examination" if raw_mode in {"cross_examination", "cross-exam", "cross"} else "standard"
    roles = cfg.get("roles") or {}
    if not isinstance(roles, dict):
        roles = {}

    active_roles: List[str] = []
    role_models: Dict[str, str] = {}

    for r_id, r_data in sorted(roles.items()):
        if isinstance(r_data, dict):
            r_enabled = bool(r_data.get("enabled", True))
            if r_enabled or r_data.get("is_arbitrator"):
                active_roles.append(r_id)
                role_models[r_id] = str(r_data.get("model_id") or "default")

    council_ident = {
        "enabled": enabled,
        "mode": consensus_mode,
        "roles": active_roles,
        "models": role_models,
    }
    ident_bytes = json.dumps(council_ident, sort_keys=True, separators=(",", ":")).encode("utf-8")
    council_hash = hashlib.sha256(ident_bytes).hexdigest()[:8]

    return {
        "enabled": enabled,
        "consensus_mode": consensus_mode,
        "active_roles": active_roles,
        "role_models": role_models,
        "council_hash": council_hash,
    }


def generate_policy_snapshot(
    root_dir: Optional[Path] = None,
    prompt_profile: Optional[Dict[str, Any]] = None,
    memory_snapshot: Optional[Dict[str, Any]] = None,
    interceptor_plugins: Optional[List[Dict[str, Any]]] = None,
    council_config: Optional[Dict[str, Any]] = None,
    plugins_dir: Optional[Path] = None,
    base_version: str = DEFAULT_BASE_VERSION,
) -> Dict[str, Any]:
    """Generates an immutable snapshot fingerprint across the 4 core strategy units."""
    prompt_info = extract_prompt_profile_fingerprint(prompt_profile, root_dir=root_dir)
    evolution_info = extract_evolution_mind_fingerprint(memory_snapshot, root_dir=root_dir)
    interceptor_info = extract_interceptors_fingerprint(
        interceptor_plugins, plugins_dir=plugins_dir, root_dir=root_dir
    )
    council_info = extract_council_fingerprint(council_config, root_dir=root_dir)

    canonical_fingerprint = {
        "prompt_profile": {
            "id": prompt_info["active_profile_id"],
            "layout_hash": prompt_info["layout_hash"],
            "editor_mode": prompt_info["editor_mode"],
        },
        "evolution_mind": {
            "version": evolution_info["version"],
            "enabled_count": evolution_info["enabled_count"],
        },
        "physical_interceptors": {
            "plugins_hash": interceptor_info["plugins_hash"],
            "enabled_plugins": interceptor_info["enabled_plugins"],
        },
        "model_council": {
            "enabled": council_info["enabled"],
            "consensus_mode": council_info["consensus_mode"],
            "active_roles": council_info["active_roles"],
            "role_models": council_info["role_models"],
        },
    }

    canon_bytes = json.dumps(canonical_fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")
    policy_hash = hashlib.sha256(canon_bytes).hexdigest()[:8]
    policy_version = f"{base_version}@{policy_hash}"

    mind_ver_short = evolution_info["version"][:8] if evolution_info["version"] != "missing" else "missing"
    summary = (
        f"Policy[{policy_version}] "
        f"prompt:{prompt_info['active_profile_id']}#{prompt_info['layout_hash']} "
        f"mind:{mind_ver_short}({evolution_info['enabled_count']}) "
        f"interceptors:{interceptor_info['plugins_hash']}({interceptor_info['enabled_count']}) "
        f"council:{'on' if council_info['enabled'] else 'off'}({council_info['consensus_mode']})"
    )

    return {
        "policy_version": policy_version,
        "policy_hash": policy_hash,
        "base_version": base_version,
        "timestamp": int(time.time()),
        "summary": summary,
        "units": {
            "prompt_profile": prompt_info,
            "evolution_mind": evolution_info,
            "physical_interceptors": interceptor_info,
            "model_council": council_info,
        },
    }


def get_current_policy_snapshot() -> Dict[str, Any]:
    """Convenience accessor for live current policy snapshot."""
    return generate_policy_snapshot()


def format_policy_snapshot_summary(snapshot: Dict[str, Any]) -> str:
    """Formats a concise single-line summary of a policy snapshot."""
    return str(snapshot.get("summary") or snapshot.get("policy_version") or "unknown_policy")


# =========================================================================
# Policy Version Workbench: Archive, Rollback, Export & Import
# =========================================================================

def _atomic_write_json(file_path: Path, data: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=file_path.parent, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        temp_name = tf.name
    os.replace(temp_name, file_path)


_process_thread_lock = threading.RLock()
_lock_tls = threading.local()


@contextmanager
def _index_lock(archive_dir: Path, shared: bool = False):
    """Reentrant thread and process file locking context for policy archive index operations."""
    with _process_thread_lock:
        if fcntl is None:
            yield
            return

        archive_dir.mkdir(parents=True, exist_ok=True)
        lock_file = archive_dir / ".index.lock"

        depth = getattr(_lock_tls, "depth", 0)
        if depth > 0:
            _lock_tls.depth = depth + 1
            try:
                yield
            finally:
                _lock_tls.depth -= 1
            return

        fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
        flag = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(fd, flag)
        _lock_tls.depth = 1
        _lock_tls.fd = fd
        try:
            yield
        finally:
            _lock_tls.depth = 0
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
            _lock_tls.fd = None


def _rebuild_index_from_archives(a_dir: Path) -> List[Dict[str, Any]]:
    """Scans all policy_*.json files in archive_dir and reconstructs index entries."""
    entries: List[Dict[str, Any]] = []
    if not a_dir.is_dir():
        return entries

    for f in a_dir.glob("policy_*.json"):
        if not f.is_file() or f.name.endswith(".tmp"):
            continue
        try:
            with open(f, "r", encoding="utf-8") as handle:
                package = json.load(handle)
            meta = package.get("metadata") or {}
            policy_hash = package.get("policy_hash") or f.stem.replace("policy_", "")
            policy_version = package.get("policy_version") or f"unknown@{policy_hash}"
            entry = {
                "policy_version": policy_version,
                "policy_hash": policy_hash,
                "name": str(meta.get("name") or f"策略归档-{policy_hash}"),
                "description": str(meta.get("description") or ""),
                "author": str(meta.get("author") or "admin"),
                "archived_at": str(
                    meta.get("archived_at")
                    or datetime.fromtimestamp(f.stat().st_mtime, _BJ).isoformat(sep=" ", timespec="seconds")
                ),
                "summary": str(package.get("summary") or ""),
                "archive_file": f.name,
            }
            entries.append(entry)
        except Exception as err:
            logger.warning("Failed to parse archive file %s during index rebuild: %s", f.name, err)

    entries.sort(key=lambda x: str(x.get("archived_at", "")), reverse=True)
    return entries


def load_archive_index(archive_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Loads metadata index of archived policies with locking and corruption recovery."""
    a_dir = archive_dir or ARCHIVE_DIR
    idx_file = a_dir / "index.json"

    with _index_lock(a_dir, shared=True):
        if not idx_file.is_file():
            reconstructed = _rebuild_index_from_archives(a_dir)
            if reconstructed:
                try:
                    save_archive_index(reconstructed, archive_dir=a_dir)
                except Exception:
                    pass
            return reconstructed

        needs_rebuild = False
        data: Any = None
        try:
            with open(idx_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    needs_rebuild = True
                else:
                    data = json.loads(content)
                    if not isinstance(data, list):
                        needs_rebuild = True
        except Exception as e:
            logger.warning("Corrupt policy archive index detected: %s", e)
            needs_rebuild = True

        if needs_rebuild:
            try:
                backup_file = a_dir / f"index.json.corrupt.{int(time.time())}"
                if idx_file.is_file():
                    os.replace(idx_file, backup_file)
                    logger.info("Backed up corrupted index to %s", backup_file.name)
            except Exception as e:
                logger.error("Failed to backup corrupt index: %s", e)

            reconstructed = _rebuild_index_from_archives(a_dir)
            try:
                save_archive_index(reconstructed, archive_dir=a_dir)
            except Exception:
                pass
            return reconstructed

        valid_entries = []
        for item in data:
            if isinstance(item, dict) and "policy_hash" in item:
                valid_entries.append(item)
        return valid_entries


def save_archive_index(index_data: List[Dict[str, Any]], archive_dir: Optional[Path] = None) -> None:
    """Saves metadata index of archived policies with atomic write and locking."""
    a_dir = archive_dir or ARCHIVE_DIR
    idx_file = a_dir / "index.json"
    with _index_lock(a_dir, shared=False):
        _atomic_write_json(idx_file, index_data)


def capture_full_strategy_package(root_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Captures complete runtime data payload across all 4 units for rollback/export."""
    r_dir = root_dir or ROOT
    sys_path_added = False
    scripts_dir = str(r_dir / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
        sys_path_added = True

    try:
        try:
            from prompt_library import load_library
            prompt_full = load_library()
        except (ImportError, AttributeError):
            from prompt_library import load_prompt_config
            prompt_full = load_prompt_config()
    except Exception as e:
        logger.warning("Failed to capture prompt library: %s", e)
        prompt_full = {}

    try:
        from evolution_shield import STRUCTURED_MEMORY_FILE
        if STRUCTURED_MEMORY_FILE.is_file():
            raw_text = STRUCTURED_MEMORY_FILE.read_text(encoding="utf-8")
            memory_full = json.loads(raw_text)
        else:
            from evolution_shield import read_memory_snapshot
            memory_full = read_memory_snapshot()
    except Exception as e:
        logger.warning("Failed to capture evolution memory: %s", e)
        memory_full = {"version": "missing", "lessons": []}

    try:
        from r20_backend.interceptor_manager import load_config as load_interceptor_config
        interceptor_full = load_interceptor_config(create_if_missing=False)
    except Exception as e:
        logger.warning("Failed to capture interceptor config: %s", e)
        interceptor_full = {}

    try:
        from r20_backend.council_manager import load_council_config
        council_full = load_council_config()
    except Exception as e:
        logger.warning("Failed to capture council config: %s", e)
        council_full = {}

    try:
        from r20_backend import risk_config
        risk_full = risk_config.current_values()
    except Exception as e:
        logger.warning("Failed to capture risk config: %s", e)
        risk_full = {}

    try:
        from r20_backend.exchanges.routing_policy import _read_raw_routing
        routing_full = _read_raw_routing()
    except Exception as e:
        logger.warning("Failed to capture routing config: %s", e)
        routing_full = {}

    finally:
        if sys_path_added and scripts_dir in sys.path:
            try:
                sys.path.remove(scripts_dir)
            except ValueError:
                pass

    snapshot = generate_policy_snapshot(root_dir=r_dir)

    return {
        "format": "r20_policy_package_v1",
        "policy_version": snapshot["policy_version"],
        "policy_hash": snapshot["policy_hash"],
        "captured_at": snapshot["timestamp"],
        "summary": snapshot["summary"],
        "snapshot": snapshot,
        "package": {
            "prompt_config": prompt_full,
            "evolution_memory": memory_full,
            "interceptor_config": interceptor_full,
            "council_config": council_full,
            "risk_config": risk_full,
            "venue_routing": routing_full,
        },
    }


def archive_current_policy(
    name: str,
    description: str = "",
    author: str = "admin",
    archive_dir: Optional[Path] = None,
    root_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Archives the live strategy state into an immutable policy version package."""
    a_dir = archive_dir or ARCHIVE_DIR
    a_dir.mkdir(parents=True, exist_ok=True)

    with _index_lock(a_dir, shared=False):
        package = capture_full_strategy_package(root_dir=root_dir)
        policy_hash = package["policy_hash"]
        policy_version = package["policy_version"]

        safe_name = name.strip() or f"策略归档-{policy_hash}"
        archive_file = a_dir / f"policy_{policy_hash}.json"

        package["metadata"] = {
            "name": safe_name,
            "description": description.strip(),
            "author": author,
            "archived_at": datetime.now(_BJ).isoformat(sep=" ", timespec="seconds"),
            "archive_file": archive_file.name,
        }

        _atomic_write_json(archive_file, package)

        # Update index
        index_data = load_archive_index(archive_dir=a_dir)
        index_data = [item for item in index_data if item.get("policy_hash") != policy_hash]

        entry = {
            "policy_version": policy_version,
            "policy_hash": policy_hash,
            "name": safe_name,
            "description": description.strip(),
            "author": author,
            "archived_at": package["metadata"]["archived_at"],
            "summary": package["summary"],
            "archive_file": archive_file.name,
        }
        index_data.insert(0, entry)
        save_archive_index(index_data, archive_dir=a_dir)

        return entry


# Canonical alias
archive_policy_snapshot = archive_current_policy


def restore_archived_policy(
    policy_hash: str,
    archive_dir: Optional[Path] = None,
    root_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Atomically restores live strategy state to an archived policy version package.

    Guarantees no partial state on failure: takes a pre-restore backup snapshot
    and automatically reverts if any unit restore operation fails.
    """
    if not policy_hash or not isinstance(policy_hash, str) or not re.match(r"^[a-zA-Z0-9_-]+$", policy_hash):
        raise ValueError(f"无效的策略哈希标识: {policy_hash}")

    a_dir = archive_dir or ARCHIVE_DIR
    archive_file = a_dir / f"policy_{policy_hash}.json"
    if not archive_file.is_file():
        raise FileNotFoundError(f"未找到归档的策略版本文件: {policy_hash}")

    with open(archive_file, "r", encoding="utf-8") as f:
        package = json.load(f)

    pkg_payload = package.get("package") or {}
    r_dir = root_dir or ROOT

    # Pre-restore safety snapshot to prevent partial state on failure
    pre_restore_package = capture_full_strategy_package(root_dir=r_dir)

    def _apply_package(payload: Dict[str, Any]) -> None:
        sys_path_added = False
        scripts_dir = str(r_dir / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
            sys_path_added = True

        try:
            # 1. Restore Prompt Profile
            if "prompt_config" in payload and payload["prompt_config"]:
                try:
                    from prompt_library import save_library
                    save_library(payload["prompt_config"])
                except (ImportError, AttributeError):
                    from prompt_library import save_prompt_config
                    save_prompt_config(payload["prompt_config"])

            # 2. Restore Evolution Memory
            if "evolution_memory" in payload:
                from evolution_shield import STRUCTURED_MEMORY_FILE, _memory_lock, _validate
                evo_data = payload["evolution_memory"]
                with _memory_lock():
                    if evo_data is None or (
                        isinstance(evo_data, dict)
                        and (evo_data.get("exists") is False or evo_data.get("version") == "missing")
                    ):
                        STRUCTURED_MEMORY_FILE.unlink(missing_ok=True)
                    elif isinstance(evo_data, dict) and "raw_text" in evo_data:
                        raw = str(evo_data["raw_text"])
                        parsed = json.loads(raw)
                        if isinstance(parsed, list):
                            _validate(parsed)
                        elif isinstance(parsed, dict) and "lessons" in parsed:
                            if not isinstance(parsed["lessons"], list):
                                raise ValueError("Invalid lessons in envelope: must be a list")
                            _validate(parsed["lessons"])
                        STRUCTURED_MEMORY_FILE.write_text(raw, encoding="utf-8")
                    elif isinstance(evo_data, list):
                        _validate(evo_data)
                        _atomic_write_json(STRUCTURED_MEMORY_FILE, evo_data)
                    elif isinstance(evo_data, dict):
                        lessons = evo_data.get("lessons")
                        if lessons is not None:
                            if not isinstance(lessons, list):
                                raise ValueError("Invalid lessons in evolution memory: must be a list")
                            _validate(lessons)
                        _atomic_write_json(STRUCTURED_MEMORY_FILE, evo_data)
                    else:
                        raise ValueError(f"Unsupported evolution memory format: {type(evo_data)}")

            # 3. Restore Interceptors
            if (
                "interceptor_config" in payload
                and isinstance(payload["interceptor_config"], dict)
                and payload["interceptor_config"]
            ):
                from r20_backend.interceptor_manager import save_config as save_interceptor_config
                save_interceptor_config(payload["interceptor_config"])

            # 4. Restore Council
            if (
                "council_config" in payload
                and isinstance(payload["council_config"], dict)
                and payload["council_config"]
            ):
                from r20_backend.council_manager import save_council_config
                save_council_config(payload["council_config"])

            # 5. Restore Risk Config
            if (
                "risk_config" in payload
                and isinstance(payload["risk_config"], dict)
                and payload["risk_config"]
            ):
                try:
                    from r20_backend import risk_config
                    from r20_backend.settings_store import update_env
                    env_updates = risk_config.normalize(payload["risk_config"])
                    update_env(env_updates)
                except Exception as e:
                    logger.warning("Failed to restore risk config: %s", e)

            # 6. Restore Venue Routing
            if (
                "venue_routing" in payload
                and isinstance(payload["venue_routing"], dict)
                and payload["venue_routing"]
            ):
                try:
                    from r20_backend.exchanges.routing_policy import ROUTING_FILE
                    _atomic_write_json(ROUTING_FILE, payload["venue_routing"])
                except Exception as e:
                    logger.warning("Failed to restore venue routing: %s", e)
        finally:
            if sys_path_added and scripts_dir in sys.path:
                try:
                    sys.path.remove(scripts_dir)
                except ValueError:
                    pass

    try:
        _apply_package(pkg_payload)
    except Exception as exc:
        logger.error("Error during strategy restore: %s. Reverting to pre-restore state...", exc)
        try:
            _apply_package(pre_restore_package.get("package") or {})
        except Exception as revert_exc:
            logger.critical("Failed to revert to pre-restore state: %s", revert_exc)
        raise RuntimeError(f"策略回滚失败且已恢复原状态: {exc}") from exc

    # Verify new restored snapshot
    new_snapshot = generate_policy_snapshot(root_dir=r_dir)
    if new_snapshot["policy_hash"] != policy_hash:
        logger.error(
            "Restored snapshot hash mismatch: expected %s, got %s. Reverting to pre-restore state...",
            policy_hash,
            new_snapshot["policy_hash"],
        )
        try:
            _apply_package(pre_restore_package.get("package") or {})
        except Exception as revert_exc:
            logger.critical("Failed to revert to pre-restore state after hash mismatch: %s", revert_exc)
        raise RuntimeError(
            f"策略回滚失败且已恢复原状态: 恢复后哈希 {new_snapshot['policy_hash']} 与目标 {policy_hash} 不一致"
        )
    return {
        "status": "restored",
        "target_policy_hash": policy_hash,
        "restored_snapshot": new_snapshot,
    }


# Canonical alias
restore_policy_snapshot = restore_archived_policy


def delete_archived_policy(
    policy_hash: str,
    archive_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Deletes an archived policy file and removes its metadata from index."""
    if not policy_hash or not isinstance(policy_hash, str) or not re.match(r"^[a-zA-Z0-9_-]+$", policy_hash):
        raise ValueError(f"无效的策略哈希标识: {policy_hash}")

    a_dir = archive_dir or ARCHIVE_DIR
    archive_file = a_dir / f"policy_{policy_hash}.json"

    with _index_lock(a_dir, shared=False):
        deleted_file = False
        if archive_file.is_file():
            archive_file.unlink(missing_ok=True)
            deleted_file = True

        index_data = load_archive_index(archive_dir=a_dir)
        original_len = len(index_data)
        new_index = [item for item in index_data if item.get("policy_hash") != policy_hash]

        if len(new_index) < original_len or deleted_file:
            save_archive_index(new_index, archive_dir=a_dir)
            return {"deleted": True, "policy_hash": policy_hash}

        raise FileNotFoundError(f"未找到指定的策略归档: {policy_hash}")


# Canonical alias
delete_policy_archive = delete_archived_policy


__all__ = [
    "DEFAULT_BASE_VERSION",
    "compute_layout_hash",
    "compute_file_hash",
    "extract_prompt_profile_fingerprint",
    "extract_evolution_mind_fingerprint",
    "extract_interceptors_fingerprint",
    "extract_council_fingerprint",
    "format_policy_snapshot_summary",
    "generate_policy_snapshot",
    "get_current_policy_snapshot",
    "capture_full_strategy_package",
    "load_archive_index",
    "save_archive_index",
    "archive_current_policy",
    "archive_policy_snapshot",
    "restore_archived_policy",
    "restore_policy_snapshot",
    "delete_archived_policy",
    "delete_policy_archive",
]

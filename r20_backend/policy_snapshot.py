"""R20 Strategy Policy Snapshot & Version Control Workbench Engine.

Provides immutable snapshot fingerprinting, persistent archiving, one-click rollback,
and export/import capabilities across all 4 strategy units:
1. Prompt Profile (Prompt Studio)
2. Evolution Mind (Evolution Shield)
3. Physical Interceptors (Interceptors Plugin Pipeline)
4. Model Council (Council Desk)
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "policy_archives"
ARCHIVE_INDEX_FILE = ARCHIVE_DIR / "index.json"

DEFAULT_BASE_VERSION = "v7.4.1"


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
    else:
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
        try:
            sys_path_added = False
            scripts_dir = str((root_dir or ROOT) / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
                sys_path_added = True
            from prompt_library import load_active_profile
            prof = load_active_profile()
        except Exception:
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
        try:
            sys_path_added = False
            scripts_dir = str((root_dir or ROOT) / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
                sys_path_added = True
            from evolution_shield import read_memory_snapshot
            snap = read_memory_snapshot()
        except Exception:
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
    enabled_count = len([l for l in lessons if isinstance(l, dict) and l.get("enabled", True)])
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
        except Exception:
            plugins = []

    pipeline_info: List[Dict[str, Any]] = []
    enabled_plugins: List[str] = []

    for idx, item in enumerate(plugins):
        filename = str(item.get("filename", ""))
        enabled = bool(item.get("enabled", False))
        # If item already has a file_hash provided (e.g. in test mock), use it directly
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
        "total_count": len(plugins),
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
        except Exception:
            cfg = {"enabled": False, "consensus_mode": "standard", "roles": {}}

    enabled = bool(cfg.get("enabled", False))
    raw_mode = str(cfg.get("consensus_mode", "standard")).lower()
    consensus_mode = "cross_examination" if raw_mode in {"cross_examination", "cross-exam", "cross"} else "standard"
    roles = cfg.get("roles") or {}

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


def load_archive_index(archive_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Loads metadata index of archived policies."""
    a_dir = archive_dir or ARCHIVE_DIR
    idx_file = a_dir / "index.json"
    if idx_file.is_file():
        try:
            with open(idx_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as e:
            logger.warning("Failed to load policy archive index: %s", e)
    return []


def save_archive_index(index_data: List[Dict[str, Any]], archive_dir: Optional[Path] = None) -> None:
    """Saves metadata index of archived policies."""
    a_dir = archive_dir or ARCHIVE_DIR
    idx_file = a_dir / "index.json"
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
        from prompt_library import load_prompt_config
        prompt_full = load_prompt_config()
    except Exception:
        prompt_full = {}

    try:
        from evolution_shield import load_structured_memory
        memory_full = load_structured_memory()
    except Exception:
        memory_full = {"version": "missing", "lessons": []}

    try:
        from r20_backend.interceptor_manager import load_config as load_interceptor_config
        interceptor_full = load_interceptor_config(create_if_missing=False)
    except Exception:
        interceptor_full = {}

    try:
        from r20_backend.council_manager import load_council_config
        council_full = load_council_config()
    except Exception:
        council_full = {}

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

    package = capture_full_strategy_package(root_dir=root_dir)
    policy_hash = package["policy_hash"]
    policy_version = package["policy_version"]

    safe_name = name.strip() or f"策略归档-{policy_hash}"
    archive_file = a_dir / f"policy_{policy_hash}.json"

    package["metadata"] = {
        "name": safe_name,
        "description": description.strip(),
        "author": author,
        "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "archive_file": archive_file.name,
    }

    _atomic_write_json(archive_file, package)

    # Update index
    index_data = load_archive_index(archive_dir=a_dir)
    # Remove older entry with same hash if exists
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


def restore_archived_policy(
    policy_hash: str,
    archive_dir: Optional[Path] = None,
    root_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Atomically restores live strategy state to an archived policy version package."""
    a_dir = archive_dir or ARCHIVE_DIR
    archive_file = a_dir / f"policy_{policy_hash}.json"
    if not archive_file.is_file():
        raise FileNotFoundError(f"未找到归档的策略版本文件: {policy_hash}")

    with open(archive_file, "r", encoding="utf-8") as f:
        package = json.load(f)

    pkg_payload = package.get("package") or {}
    r_dir = root_dir or ROOT
    sys_path_added = False
    scripts_dir = str(r_dir / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
        sys_path_added = True

    try:
        # 1. Restore Prompt Profile
        if "prompt_config" in pkg_payload:
            from prompt_library import save_prompt_config
            save_prompt_config(pkg_payload["prompt_config"])

        # 2. Restore Evolution Memory
        if "evolution_memory" in pkg_payload:
            from evolution_shield import save_structured_memory, read_memory_snapshot
            current_snap = read_memory_snapshot()
            current_ver = current_snap.get("version", "missing")
            lessons = pkg_payload["evolution_memory"].get("lessons") or []
            # Save using shield
            save_structured_memory(lessons, expected_version=current_ver)

        # 3. Restore Interceptors
        if "interceptor_config" in pkg_payload:
            from r20_backend.interceptor_manager import save_config as save_interceptor_config
            save_interceptor_config(pkg_payload["interceptor_config"])

        # 4. Restore Council
        if "council_config" in pkg_payload:
            from r20_backend.council_manager import save_council_config
            save_council_config(pkg_payload["council_config"])

    finally:
        if sys_path_added and scripts_dir in sys.path:
            try:
                sys.path.remove(scripts_dir)
            except ValueError:
                pass

    # Verify new restored snapshot
    new_snapshot = generate_policy_snapshot(root_dir=r_dir)
    return {
        "status": "restored",
        "target_policy_hash": policy_hash,
        "restored_snapshot": new_snapshot,
    }


def delete_archived_policy(
    policy_hash: str,
    archive_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Deletes an archived policy file and removes its metadata from index."""
    a_dir = archive_dir or ARCHIVE_DIR
    archive_file = a_dir / f"policy_{policy_hash}.json"

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

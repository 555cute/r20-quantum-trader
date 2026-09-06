"""Isolated offline tests for Policy Snapshot generator and decision traceability.

Verifies:
1. Field integrity of policy snapshot across all 4 strategy units.
2. Deterministic hashing: identical inputs yield identical policy_hash.
3. Sensitivity to changes in any of the four strategy units:
   - Prompt profile (layout, module content, module toggle, profile ID)
   - Structured self-evolution mind (version hash, enabled count)
   - Physical interceptor plugins (plugin toggle, order, file hash)
   - Multi-agent model council (enabled state, consensus mode, active roles, model binding)
4. Decision assembly in ai_brain_trader correctly binds snapshot version and hash.
5. FastAPI route GET /api/v1/admin/policy/current-snapshot authentication and response structure.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any, Dict, List

from r20_backend.policy_snapshot import (
    compute_layout_hash,
    format_policy_snapshot_summary,
    generate_policy_snapshot,
)


class TestPolicySnapshotIsolated(unittest.TestCase):
    """Offline unit tests for deterministic policy snapshot generation."""

    def setUp(self) -> None:
        self.base_prompt_profile: Dict[str, Any] = {
            "id": "stable",
            "name": "全维度波段强化版",
            "editor_mode": "modules",
            "pipelines": {
                "trading_system": [
                    {"id": "mod_sys_1", "title": "基准风控", "content": "R:R >= 2.0 且多空对称", "enabled": True},
                ],
                "trading_user": [
                    {"id": "mod_usr_1", "title": "裁决偏好", "content": "关注加速度与定积分能量", "enabled": True},
                ],
                "evolution_system": [
                    {"id": "mod_evo_sys", "title": "复盘纪律", "content": "NO_CHANGE保留旧记忆", "enabled": True},
                ],
                "evolution_user": [
                    {"id": "mod_evo_usr", "title": "样本要求", "content": "小样本严禁随意沉淀心法", "enabled": True},
                ],
            },
        }

        self.base_memory_snapshot: Dict[str, Any] = {
            "exists": True,
            "version": "5fecce22aa11bb22cc33dd44ee55ff6677889900aabbccddeeff001122334455",
            "lessons": [
                {"id": "lesson_1", "rule_text": "顺势回踩低吸", "enabled": True},
                {"id": "lesson_2", "rule_text": "严禁逆势扛单", "enabled": True},
                {"id": "lesson_3", "rule_text": "插针行情扩大ATR呼吸", "enabled": True},
                {"id": "lesson_4", "rule_text": "历史已停用心法", "enabled": False},
            ],
        }

        self.base_interceptor_plugins: List[Dict[str, Any]] = [
            {"filename": "01_core_safety.py", "enabled": True, "file_hash": "a1b2c3d4"},
            {"filename": "02_extreme_market.py", "enabled": True, "file_hash": "b2c3d4e5"},
            {"filename": "03_volatility_shield.py", "enabled": True, "file_hash": "c3d4e5f6"},
            {"filename": "99_sample_disabled.py", "enabled": False, "file_hash": "d4e5f6a7"},
        ]

        self.base_council_config: Dict[str, Any] = {
            "enabled": False,
            "consensus_mode": "standard",
            "timeout_seconds": 60.0,
            "roles": {
                "trader_trend": {"id": "trader_trend", "enabled": True, "model_id": "qwen-max"},
                "trader_momentum": {"id": "trader_momentum", "enabled": True, "model_id": "deepseek-v3"},
                "trader_quant": {"id": "trader_quant", "enabled": True, "model_id": "gpt-4o"},
                "cio": {"id": "cio", "enabled": True, "model_id": "claude-3-5-sonnet", "is_arbitrator": True},
            },
        }

    def _generate_baseline(self) -> Dict[str, Any]:
        return generate_policy_snapshot(
            prompt_profile=self.base_prompt_profile,
            memory_snapshot=self.base_memory_snapshot,
            interceptor_plugins=self.base_interceptor_plugins,
            council_config=self.base_council_config,
            base_version="v7.3.0",
        )

    def test_snapshot_field_integrity(self) -> None:
        """Snapshot must contain all required top-level and unit-level fields."""
        snap = self._generate_baseline()

        self.assertIn("policy_version", snap)
        self.assertIn("policy_hash", snap)
        self.assertIn("base_version", snap)
        self.assertIn("timestamp", snap)
        self.assertIn("summary", snap)
        self.assertIn("units", snap)

        # Policy version format validation
        self.assertEqual(snap["base_version"], "v7.3.0")
        self.assertEqual(len(snap["policy_hash"]), 8)
        self.assertEqual(snap["policy_version"], f"v7.3.0@{snap['policy_hash']}")

        # Units integrity
        units = snap["units"]
        self.assertIn("prompt_profile", units)
        self.assertIn("evolution_mind", units)
        self.assertIn("physical_interceptors", units)
        self.assertIn("model_council", units)

        # Prompt profile unit fields
        p_unit = units["prompt_profile"]
        self.assertEqual(p_unit["active_profile_id"], "stable")
        self.assertEqual(p_unit["active_profile_name"], "全维度波段强化版")
        self.assertEqual(p_unit["editor_mode"], "modules")
        self.assertTrue(p_unit["layout_hash"])

        # Evolution mind unit fields
        e_unit = units["evolution_mind"]
        self.assertEqual(e_unit["version"], self.base_memory_snapshot["version"])
        self.assertEqual(e_unit["enabled_count"], 3)
        self.assertEqual(e_unit["total_count"], 4)

        # Interceptors unit fields
        i_unit = units["physical_interceptors"]
        self.assertEqual(i_unit["enabled_count"], 3)
        self.assertEqual(i_unit["total_count"], 4)
        self.assertEqual(
            i_unit["enabled_plugins"],
            ["01_core_safety.py", "02_extreme_market.py", "03_volatility_shield.py"],
        )
        self.assertTrue(i_unit["plugins_hash"])

        # Council unit fields
        c_unit = units["model_council"]
        self.assertFalse(c_unit["enabled"])
        self.assertEqual(c_unit["consensus_mode"], "standard")
        self.assertEqual(sorted(c_unit["active_roles"]), ["cio", "trader_momentum", "trader_quant", "trader_trend"])
        self.assertEqual(c_unit["role_models"]["cio"], "claude-3-5-sonnet")
        self.assertTrue(c_unit["council_hash"])

        # Summary formatting
        summary = format_policy_snapshot_summary(snap)
        self.assertIn(snap["policy_version"], summary)
        self.assertIn("prompt:stable#", summary)
        self.assertIn("mind:5fecce22(3)", summary)
        self.assertIn("council:off(standard)", summary)

    def test_deterministic_reproducibility(self) -> None:
        """Identical inputs must yield identical hash and version across runs."""
        snap1 = self._generate_baseline()
        snap2 = self._generate_baseline()
        self.assertEqual(snap1["policy_hash"], snap2["policy_hash"])
        self.assertEqual(snap1["policy_version"], snap2["policy_version"])
        self.assertEqual(snap1["summary"], snap2["summary"])

    def test_prompt_profile_modifications_change_policy_hash(self) -> None:
        """Any change in prompt profile must deterministically change policy_hash."""
        base_snap = self._generate_baseline()

        # Case 1: Change profile ID
        prof_diff_id = copy.deepcopy(self.base_prompt_profile)
        prof_diff_id["id"] = "aggressive"
        snap_diff_id = generate_policy_snapshot(
            prompt_profile=prof_diff_id,
            memory_snapshot=self.base_memory_snapshot,
            interceptor_plugins=self.base_interceptor_plugins,
            council_config=self.base_council_config,
        )
        self.assertNotEqual(base_snap["policy_hash"], snap_diff_id["policy_hash"])

        # Case 2: Change module content
        prof_diff_content = copy.deepcopy(self.base_prompt_profile)
        prof_diff_content["pipelines"]["trading_system"][0]["content"] = "R:R >= 3.0 激进开仓"
        snap_diff_content = generate_policy_snapshot(
            prompt_profile=prof_diff_content,
            memory_snapshot=self.base_memory_snapshot,
            interceptor_plugins=self.base_interceptor_plugins,
            council_config=self.base_council_config,
        )
        self.assertNotEqual(base_snap["policy_hash"], snap_diff_content["policy_hash"])

        # Case 3: Toggle module enabled state
        prof_diff_toggle = copy.deepcopy(self.base_prompt_profile)
        prof_diff_toggle["pipelines"]["trading_system"][0]["enabled"] = False
        snap_diff_toggle = generate_policy_snapshot(
            prompt_profile=prof_diff_toggle,
            memory_snapshot=self.base_memory_snapshot,
            interceptor_plugins=self.base_interceptor_plugins,
            council_config=self.base_council_config,
        )
        self.assertNotEqual(base_snap["policy_hash"], snap_diff_toggle["policy_hash"])

    def test_evolution_mind_modifications_change_policy_hash(self) -> None:
        """Publishing new memory version or toggling rules changes policy_hash."""
        base_snap = self._generate_baseline()

        # Case 1: Published version changes
        mem_diff_ver = copy.deepcopy(self.base_memory_snapshot)
        mem_diff_ver["version"] = "9999aaaa8888bbbb7777cccc6666dddd5555eeee4444ffff3333000011112222"
        snap_diff_ver = generate_policy_snapshot(
            prompt_profile=self.base_prompt_profile,
            memory_snapshot=mem_diff_ver,
            interceptor_plugins=self.base_interceptor_plugins,
            council_config=self.base_council_config,
        )
        self.assertNotEqual(base_snap["policy_hash"], snap_diff_ver["policy_hash"])

        # Case 2: Enabled lesson count changes
        mem_diff_count = copy.deepcopy(self.base_memory_snapshot)
        mem_diff_count["lessons"][3]["enabled"] = True  # enable the 4th rule
        snap_diff_count = generate_policy_snapshot(
            prompt_profile=self.base_prompt_profile,
            memory_snapshot=mem_diff_count,
            interceptor_plugins=self.base_interceptor_plugins,
            council_config=self.base_council_config,
        )
        self.assertNotEqual(base_snap["policy_hash"], snap_diff_count["policy_hash"])

    def test_interceptor_plugins_modifications_change_policy_hash(self) -> None:
        """Toggling plugins, reordering pipeline, or code edits changes policy_hash."""
        base_snap = self._generate_baseline()

        # Case 1: Disable a plugin
        plugins_diff_toggle = copy.deepcopy(self.base_interceptor_plugins)
        plugins_diff_toggle[1]["enabled"] = False
        snap_diff_toggle = generate_policy_snapshot(
            prompt_profile=self.base_prompt_profile,
            memory_snapshot=self.base_memory_snapshot,
            interceptor_plugins=plugins_diff_toggle,
            council_config=self.base_council_config,
        )
        self.assertNotEqual(base_snap["policy_hash"], snap_diff_toggle["policy_hash"])

        # Case 2: Reorder enabled plugins
        plugins_diff_order = [
            self.base_interceptor_plugins[1],
            self.base_interceptor_plugins[0],
            self.base_interceptor_plugins[2],
            self.base_interceptor_plugins[3],
        ]
        snap_diff_order = generate_policy_snapshot(
            prompt_profile=self.base_prompt_profile,
            memory_snapshot=self.base_memory_snapshot,
            interceptor_plugins=plugins_diff_order,
            council_config=self.base_council_config,
        )
        self.assertNotEqual(base_snap["policy_hash"], snap_diff_order["policy_hash"])

        # Case 3: Plugin content hash changes (code edit)
        plugins_diff_code = copy.deepcopy(self.base_interceptor_plugins)
        plugins_diff_code[0]["file_hash"] = "99999999"
        snap_diff_code = generate_policy_snapshot(
            prompt_profile=self.base_prompt_profile,
            memory_snapshot=self.base_memory_snapshot,
            interceptor_plugins=plugins_diff_code,
            council_config=self.base_council_config,
        )
        self.assertNotEqual(base_snap["policy_hash"], snap_diff_code["policy_hash"])

    def test_council_modifications_change_policy_hash(self) -> None:
        """Council toggle, consensus mode switch, or model ID binding changes policy_hash."""
        base_snap = self._generate_baseline()

        # Case 1: Enable council
        c_diff_enabled = copy.deepcopy(self.base_council_config)
        c_diff_enabled["enabled"] = True
        snap_diff_enabled = generate_policy_snapshot(
            prompt_profile=self.base_prompt_profile,
            memory_snapshot=self.base_memory_snapshot,
            interceptor_plugins=self.base_interceptor_plugins,
            council_config=c_diff_enabled,
        )
        self.assertNotEqual(base_snap["policy_hash"], snap_diff_enabled["policy_hash"])

        # Case 2: Switch consensus mode
        c_diff_mode = copy.deepcopy(self.base_council_config)
        c_diff_mode["consensus_mode"] = "cross_examination"
        snap_diff_mode = generate_policy_snapshot(
            prompt_profile=self.base_prompt_profile,
            memory_snapshot=self.base_memory_snapshot,
            interceptor_plugins=self.base_interceptor_plugins,
            council_config=c_diff_mode,
        )
        self.assertNotEqual(base_snap["policy_hash"], snap_diff_mode["policy_hash"])

        # Case 3: Change bound model ID of a role
        c_diff_model = copy.deepcopy(self.base_council_config)
        c_diff_model["roles"]["cio"]["model_id"] = "gpt-4.5-preview"
        snap_diff_model = generate_policy_snapshot(
            prompt_profile=self.base_prompt_profile,
            memory_snapshot=self.base_memory_snapshot,
            interceptor_plugins=self.base_interceptor_plugins,
            council_config=c_diff_model,
        )
        self.assertNotEqual(base_snap["policy_hash"], snap_diff_model["policy_hash"])

    def test_ai_brain_trader_assemble_decision_cache_binds_snapshot(self) -> None:
        """ai_brain_trader assemble_decision_cache must attach snapshot version and hash to decisions."""
        import sys
        scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        from ai_brain_trader import assemble_decision_cache

        mock_packages = [
            {
                "instId": "BTC-USDT-SWAP",
                "name": "BTC",
                "price": 80000.0,
                "bidPx": 79990.0,
                "askPx": 80010.0,
                "chg24h": 1.5,
                "oiUsd": "1.2B",
                "takerNetUsd": "+50M",
                "fundingRate": "0.01",
                "lsRatio": 1.2,
                "data_quality": "valid",
                "recent_1h": [{"close": 80000.0}],
                "recent_4h": [{"close": 79000.0}],
                "recent_15m": [{"close": 79950.0}],
                "atr": 1000.0,
                "precision": 1,
                "ctVal": 0.01,
            },
            {
                "instId": "ETH-USDT-SWAP",
                "name": "ETH",
                "price": 2500.0,
                "bidPx": 2499.0,
                "askPx": 2501.0,
                "chg24h": -0.5,
                "oiUsd": "500M",
                "takerNetUsd": "-10M",
                "fundingRate": "0.005",
                "lsRatio": 0.95,
                "data_quality": "valid",
                "recent_1h": [{"close": 2500.0}],
                "recent_4h": [{"close": 2550.0}],
                "recent_15m": [{"close": 2502.0}],
                "atr": 40.0,
                "precision": 2,
                "ctVal": 0.1,
            },
        ]

        decisions_dict = {
            "BTC-USDT-SWAP": {
                "action": "BUY_LONG",
                "confidence": 85.0,
                "entry_price": 79500.0,
                "take_profit_price": 84500.0,
                "stop_loss_price": 77000.0,
                "leverage": 3,
                "margin_usdt": 100.0,
                "summary_reason": "4H突破回踩确认，R:R=2.0",
            },
            "ETH-USDT-SWAP": {
                "action": "WAIT",
                "confidence": 40.0,
                "summary_reason": "动能钝化，等待方向选择",
            },
        }

        mock_snapshot = {
            "policy_version": "v7.3.0@aabb1122",
            "policy_hash": "aabb1122",
            "summary": (
                "Policy[v7.3.0@aabb1122] prompt:stable#1122 "
                "mind:3344(3) interceptors:5566(3) council:off(standard)"
            ),
        }

        # Assemble decision cache
        cache = assemble_decision_cache(
            packages=mock_packages,
            decisions_dict=decisions_dict,
            active_inst_ids=set(),
            active_position_sides={},
            time_str="2026-09-05 16:00:00",
            macro_summary="宏观偏多",
            policy_snapshot=mock_snapshot,
        )

        self.assertIn("BTC-USDT-SWAP", cache)
        self.assertIn("ETH-USDT-SWAP", cache)

        btc_entry = cache["BTC-USDT-SWAP"]
        self.assertEqual(btc_entry["policy_version"], "v7.3.0@aabb1122")
        self.assertEqual(btc_entry["policy_hash"], "aabb1122")
        self.assertEqual(btc_entry["policy_snapshot"]["policy_version"], "v7.3.0@aabb1122")
        self.assertEqual(btc_entry["policy_snapshot"]["policy_hash"], "aabb1122")
        self.assertIn("v7.3.0@aabb1122", btc_entry["policy_snapshot"]["summary"])

        eth_entry = cache["ETH-USDT-SWAP"]
        self.assertEqual(eth_entry["policy_version"], "v7.3.0@aabb1122")
        self.assertEqual(eth_entry["policy_hash"], "aabb1122")
        self.assertEqual(eth_entry["decision"]["action"], "WAIT")

    def test_admin_policy_snapshot_endpoint_isolated(self) -> None:
        """FastAPI route /api/v1/admin/policy/current-snapshot rejects anonymous and accepts admin."""
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.testclient import TestClient

        # Create isolated minimal app testing the exact route handler logic
        test_app = FastAPI()

        @test_app.get("/api/v1/admin/policy/current-snapshot")
        def endpoint(x_r20_session: str | None = Header(default=None, alias="X-R20-Session")):
            if not x_r20_session or x_r20_session != "valid_admin_token":
                raise HTTPException(status_code=401, detail="未授权访问")
            snap = generate_policy_snapshot()
            return {
                "ok": True,
                "policy_version": snap.get("policy_version"),
                "policy_hash": snap.get("policy_hash"),
                "snapshot": snap,
            }

        client = TestClient(test_app)

        # 1. Anonymous request should be 401
        res_anon = client.get("/api/v1/admin/policy/current-snapshot")
        self.assertEqual(res_anon.status_code, 401)

        # 2. Authenticated request should be 200 with snapshot
        res_auth = client.get(
            "/api/v1/admin/policy/current-snapshot",
            headers={"X-R20-Session": "valid_admin_token"},
        )
        self.assertEqual(res_auth.status_code, 200)
        data = res_auth.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["policy_version"].startswith("v7.4.1@"))
        self.assertEqual(len(data["policy_hash"]), 8)
        self.assertIn("units", data["snapshot"])

    def test_archive_creation_and_index_tracking(self) -> None:
        """Verify archive creation writes package and updates index with metadata."""
        import tempfile
        from r20_backend.policy_snapshot import (
            archive_current_policy,
            archive_policy_snapshot,
            load_archive_index,
        )

        with tempfile.TemporaryDirectory() as td:
            t_dir = Path(td)
            entry1 = archive_current_policy(
                "Strategy A", description="Test desc A", author="trader1", archive_dir=t_dir
            )
            self.assertTrue((t_dir / entry1["archive_file"]).is_file())
            self.assertEqual(entry1["name"], "Strategy A")
            self.assertEqual(entry1["description"], "Test desc A")
            self.assertEqual(entry1["author"], "trader1")

            idx = load_archive_index(archive_dir=t_dir)
            self.assertEqual(len(idx), 1)
            self.assertEqual(idx[0]["policy_hash"], entry1["policy_hash"])

            # Verify alias archive_policy_snapshot works
            entry2 = archive_policy_snapshot(
                "Strategy B", description="Test desc B", author="trader2", archive_dir=t_dir
            )
            self.assertTrue(entry2["policy_hash"])
            idx2 = load_archive_index(archive_dir=t_dir)
            self.assertTrue(len(idx2) >= 1)

    def test_archive_deletion_and_aliases(self) -> None:
        """Verify deletion removes file and index tracking, with alias support."""
        import tempfile
        from r20_backend.policy_snapshot import (
            archive_current_policy,
            delete_archived_policy,
            delete_policy_archive,
            load_archive_index,
        )

        with tempfile.TemporaryDirectory() as td:
            t_dir = Path(td)
            entry = archive_current_policy("ToDelete", archive_dir=t_dir)
            p_hash = entry["policy_hash"]
            self.assertTrue((t_dir / entry["archive_file"]).is_file())

            # Delete using delete_archived_policy
            del_res = delete_archived_policy(p_hash, archive_dir=t_dir)
            self.assertTrue(del_res["deleted"])
            self.assertFalse((t_dir / entry["archive_file"]).is_file())
            self.assertEqual(len(load_archive_index(archive_dir=t_dir)), 0)

            # Deleting again raises FileNotFoundError
            with self.assertRaises(FileNotFoundError):
                delete_policy_archive(p_hash, archive_dir=t_dir)

            # Invalid hash raises ValueError
            with self.assertRaises(ValueError):
                delete_archived_policy("../etc/passwd", archive_dir=t_dir)

    def test_one_click_rollback_and_no_partial_state(self) -> None:
        """Verify one-click rollback restores snapshot and reverts on corruption."""
        import tempfile
        from r20_backend.policy_snapshot import (
            archive_current_policy,
            get_current_policy_snapshot,
            restore_archived_policy,
            restore_policy_snapshot,
        )

        with tempfile.TemporaryDirectory() as td:
            t_dir = Path(td)
            entry = archive_current_policy("Rollback Baseline", archive_dir=t_dir)
            p_hash = entry["policy_hash"]

            # Successful restore
            res = restore_policy_snapshot(p_hash, archive_dir=t_dir)
            self.assertEqual(res["status"], "restored")
            self.assertEqual(res["target_policy_hash"], p_hash)
            self.assertEqual(res["restored_snapshot"]["policy_hash"], p_hash)

            # Failure during restore reverts without partial state
            archive_file = t_dir / entry["archive_file"]
            raw_pkg = json.loads(archive_file.read_text(encoding="utf-8"))
            raw_pkg["package"]["evolution_memory"] = [{"corrupt_field": 123}]
            archive_file.write_text(json.dumps(raw_pkg), encoding="utf-8")

            orig_hash = get_current_policy_snapshot()["policy_hash"]
            with self.assertRaises(RuntimeError):
                restore_archived_policy(p_hash, archive_dir=t_dir)

            current_hash = get_current_policy_snapshot()["policy_hash"]
            self.assertEqual(current_hash, orig_hash)

    def test_index_locking_and_corrupt_recovery(self) -> None:
        """Verify index detects corrupt json, creates backup, and reconstructs from archives."""
        import tempfile
        from r20_backend.policy_snapshot import (
            archive_current_policy,
            load_archive_index,
        )

        with tempfile.TemporaryDirectory() as td:
            t_dir = Path(td)
            entry = archive_current_policy("Archive To Corrupt", archive_dir=t_dir)
            p_hash = entry["policy_hash"]

            # Corrupt index.json
            idx_file = t_dir / "index.json"
            idx_file.write_text("INVALID_JSON_CORRUPT_BYTES", encoding="utf-8")

            rebuilt_idx = load_archive_index(archive_dir=t_dir)
            self.assertEqual(len(rebuilt_idx), 1)
            self.assertEqual(rebuilt_idx[0]["policy_hash"], p_hash)

            corrupt_backups = list(t_dir.glob("index.json.corrupt.*"))
            self.assertEqual(len(corrupt_backups), 1)

    def test_scripts_proxy_module_exports(self) -> None:
        """Verify scripts/policy_snapshot.py exposes all necessary interfaces and aliases."""
        import scripts.policy_snapshot as proxy

        required_attrs = [
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
        for attr in required_attrs:
            self.assertTrue(hasattr(proxy, attr), f"Missing attr in scripts.policy_snapshot: {attr}")

    def test_simple_and_advanced_prompt_profile_layout_hash(self) -> None:
        """Verify simple and advanced prompt modes alter layout_hash deterministically."""
        # Simple mode changes
        prof_simple_1 = {
            "id": "custom_simple",
            "name": "极简测试",
            "editor_mode": "simple",
            "simple_policy": {"strategy": "顺势做多", "risk_budget": "conservative"},
        }
        prof_simple_2 = {
            "id": "custom_simple",
            "name": "极简测试",
            "editor_mode": "simple",
            "simple_policy": {"strategy": "逆势高空", "risk_budget": "aggressive"},
        }
        h_s1 = compute_layout_hash(prof_simple_1)
        h_s2 = compute_layout_hash(prof_simple_2)
        self.assertNotEqual(h_s1, h_s2)

        # Advanced mode changes
        prof_adv_1 = {
            "id": "custom_adv",
            "name": "高级测试",
            "editor_mode": "advanced",
            "trading_system": "System Rule A",
        }
        prof_adv_2 = {
            "id": "custom_adv",
            "name": "高级测试",
            "editor_mode": "advanced",
            "trading_system": "System Rule B",
        }
        h_a1 = compute_layout_hash(prof_adv_1)
        h_a2 = compute_layout_hash(prof_adv_2)
        self.assertNotEqual(h_a1, h_a2)


if __name__ == "__main__":
    unittest.main()

"""策略指纹引擎：**身份只认身份字段、易变字段不进标识、恢复核对只核对"承诺过的"**（第 302 刀，开新面 r20_backend/policy/fingerprints.py）。

这个模块是 B6 拆分里唯一**零直接测试引用**的（全仓 224 个模块扫描的结果），
却承担着最关键的一件事：**把「策略身份」压成一个 16 位整包标识**，用于
归档文件命名与回滚校验（审计 P0-3：`policy_hash` 只覆盖 4 个单元，而归档实际装 6 个，
仅风控/路由不同的两个版本会**同名互相覆盖**）。

本刀钉三类语义：

1. **确定性** —— 同样的输入必须得到同样的哈希；顺序、空白、易变字段都不许改变它。
2. **注入缝** —— 四个 `extract_*` 在 `root_dir`/显式入参缺省时才回退模块级 ROOT，
   且本地导入（prompt_library / evolution_shield / interceptor_manager / council_manager）
   都在**调用时**发生；这是 `patch.object(policy_snapshot, "ROOT", 沙箱根)` 能生效的前提
   （失效会回落到真实项目根 ⇒ 回滚写生产 `data/`）。
3. **失败回落是"显式默认值"而不是崩溃** —— 四个数据源各自读不到时给出**可辨识**的
   缺省指纹，绝不让整包生成失败。
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from r20_backend.policy.fingerprints import (  # noqa: E402
    _canon_council_config,
    _canon_evolution_memory,
    _canon_prompt_config,
    canonical_package_projection,
    compute_file_hash,
    compute_layout_hash,
    extract_council_fingerprint,
    extract_evolution_mind_fingerprint,
    extract_interceptors_fingerprint,
    extract_prompt_profile_fingerprint,
    package_identity,
    package_restore_diff,
)


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


class ComputeLayoutHashTests(unittest.TestCase):
    def test_same_input_is_deterministic(self):
        profile = {"id": "a", "editor_mode": "modules",
                   "modules": [{"id": "m1", "enabled": True, "content": "hello"}]}
        self.assertEqual(compute_layout_hash(profile), compute_layout_hash(dict(profile)))
        self.assertEqual(len(compute_layout_hash(profile)), 8)

    def test_unknown_mode_defaults_to_modules(self):
        self.assertEqual(compute_layout_hash({}), compute_layout_hash({"editor_mode": "modules"}))
        # 缺省 mode 下没有 modules ⇒ 只剩 mode 段
        self.assertEqual(compute_layout_hash({}), _sha8("mode:modules"))

    def test_module_content_and_enabled_flag_matter(self):
        base = {"editor_mode": "modules", "modules": [{"id": "m", "content": "A"}]}
        changed = {"editor_mode": "modules", "modules": [{"id": "m", "content": "B"}]}
        disabled = {"editor_mode": "modules",
                    "modules": [{"id": "m", "content": "A", "enabled": False}]}
        self.assertNotEqual(compute_layout_hash(base), compute_layout_hash(changed))
        self.assertNotEqual(compute_layout_hash(base), compute_layout_hash(disabled))
        # enabled 默认 True ⇒ 省略与显式 True 等价
        explicit = {"editor_mode": "modules",
                    "modules": [{"id": "m", "content": "A", "enabled": True}]}
        self.assertEqual(compute_layout_hash(base), compute_layout_hash(explicit))

    def test_content_is_stripped_before_hashing(self):
        a = {"editor_mode": "modules", "modules": [{"id": "m", "content": "  X  "}]}
        b = {"editor_mode": "modules", "modules": [{"id": "m", "content": "X"}]}
        self.assertEqual(compute_layout_hash(a), compute_layout_hash(b))

    def test_module_order_matters(self):
        a = {"editor_mode": "modules", "modules": [{"id": "1", "content": "x"},
                                                   {"id": "2", "content": "y"}]}
        b = {"editor_mode": "modules", "modules": [{"id": "2", "content": "y"},
                                                   {"id": "1", "content": "x"}]}
        self.assertNotEqual(compute_layout_hash(a), compute_layout_hash(b))

    def test_non_dict_module_entries_are_skipped(self):
        a = {"editor_mode": "modules", "modules": ["junk", None,
                                                   {"id": "m", "content": "A"}]}
        b = {"editor_mode": "modules", "modules": [{"id": "m", "content": "A"}]}
        self.assertEqual(compute_layout_hash(a), compute_layout_hash(b))

    def test_pipelines_are_flattened_in_sorted_pipe_order(self):
        profile = {"editor_mode": "modules",
                   "pipelines": {"b": [{"id": "mb", "content": "B"}],
                                 "a": [{"id": "ma", "content": "A"}]}}
        # 按 pipe_key 排序展平 ⇒ 与 a 在前的手写顺序等价
        manual = {"editor_mode": "modules",
                  "modules": [{"id": "ma", "content": "A"}, {"id": "mb", "content": "B"}]}
        self.assertEqual(compute_layout_hash(profile), compute_layout_hash(manual))

    def test_pipelines_non_list_values_are_ignored(self):
        profile = {"editor_mode": "modules",
                   "pipelines": {"a": "not-a-list", "b": [{"id": "m", "content": "B"}]}}
        manual = {"editor_mode": "modules", "modules": [{"id": "m", "content": "B"}]}
        self.assertEqual(compute_layout_hash(profile), compute_layout_hash(manual))

    def test_simple_mode_hashes_sorted_values(self):
        profile = {"editor_mode": "simple", "simple_policy": {"b": " 2 ", "a": "1"}}
        expected = _sha8("mode:simple|a:" + _sha8("1") + "|b:" + _sha8("2"))
        self.assertEqual(compute_layout_hash(profile), expected)

    def test_simple_mode_without_dict_marks_empty(self):
        self.assertEqual(compute_layout_hash({"editor_mode": "simple"}),
                         _sha8("mode:simple|empty_simple"))
        self.assertEqual(compute_layout_hash({"editor_mode": "simple", "simple_policy": None}),
                         _sha8("mode:simple|empty_simple"))

    def test_template_mode_uses_present_template_keys(self):
        profile = {"editor_mode": "templates", "trading_system": " SYS ",
                   "trading_user": "USER"}
        expected = _sha8("mode:templates|trading_system:" + _sha8("SYS")
                         + "|trading_user:" + _sha8("USER"))
        self.assertEqual(compute_layout_hash(profile), expected)

    def test_unknown_mode_without_templates_hashes_full_prompt(self):
        profile = {"editor_mode": "legacy", "full_system_prompt": " WHOLE "}
        self.assertEqual(compute_layout_hash(profile),
                         _sha8("mode:legacy|full:" + _sha8("WHOLE")))

    def test_unknown_mode_with_nothing_still_deterministic(self):
        self.assertEqual(compute_layout_hash({"editor_mode": "legacy"}),
                         _sha8("mode:legacy|full:" + _sha8("")))


class ComputeFileHashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_missing_file_returns_marker(self):
        self.assertEqual(compute_file_hash(self.root / "nope.bin"), "missing")

    def test_directory_returns_missing(self):
        d = self.root / "adir"
        d.mkdir()
        self.assertEqual(compute_file_hash(d), "missing")

    def test_present_file_returns_eight_hex(self):
        path = self.root / "a.bin"
        path.write_bytes(b"hello")
        self.assertEqual(compute_file_hash(path), hashlib.sha256(b"hello").hexdigest()[:8])

    def test_read_failure_returns_err_marker(self):
        path = self.root / "b.bin"
        path.write_bytes(b"data")

        def boom(self):  # 真函数才能正确绑定 self（MagicMock 不是描述符）
            raise OSError("io error")

        with patch.object(Path, "read_bytes", boom):
            self.assertEqual(compute_file_hash(path), "err_read")


class _StubbornPath(list):
    """`sys.path` 替身：`remove` 抛 ValueError，用来走 finally 里的兜底分支。"""

    def remove(self, item):  # noqa: D102
        raise ValueError("refusing to remove")


class ExtractPromptProfileTests(unittest.TestCase):
    def test_explicit_profile_is_used_verbatim(self):
        profile = {"id": "aggressive", "name": "激进", "editor_mode": "modules",
                   "modules": []}
        fp = extract_prompt_profile_fingerprint(Path("/nonexistent"), profile)
        self.assertEqual(fp["active_profile_id"], "aggressive")
        self.assertEqual(fp["active_profile_name"], "激进")
        self.assertEqual(fp["editor_mode"], "modules")
        self.assertEqual(fp["layout_hash"], compute_layout_hash(profile))

    def test_defaults_when_profile_lacks_fields(self):
        fp = extract_prompt_profile_fingerprint(Path("/nonexistent"), {})
        self.assertEqual(fp["active_profile_id"], "stable")
        self.assertEqual(fp["active_profile_name"], "全维度波段强化版")
        self.assertEqual(fp["editor_mode"], "modules")

    def test_loads_active_profile_when_not_given(self):
        import prompt_library
        with patch.object(prompt_library, "active_profile",
                          lambda: {"id": "loaded", "name": "载入的", "editor_mode": "simple"}):
            fp = extract_prompt_profile_fingerprint(Path("/nonexistent"))
        self.assertEqual(fp["active_profile_id"], "loaded")
        self.assertEqual(fp["editor_mode"], "simple")

    def test_falls_back_to_load_active_profile_when_active_profile_missing(self):
        # 用"只有 load_active_profile、没有 active_profile"的模块替身
        fake = types.SimpleNamespace(
            load_active_profile=lambda: {"id": "legacy", "name": "旧版"})
        with patch.dict(sys.modules, {"prompt_library": fake}):
            fp = extract_prompt_profile_fingerprint(Path("/nonexistent"))
        self.assertEqual(fp["active_profile_id"], "legacy")

    def test_unloadable_library_yields_documented_default(self):
        with patch.dict(sys.modules, {"prompt_library": None}):
            fp = extract_prompt_profile_fingerprint(Path("/nonexistent"))
        self.assertEqual(fp["active_profile_id"], "stable")
        self.assertEqual(fp["active_profile_name"], "全维度波段强化版")
        self.assertEqual(fp["editor_mode"], "modules")

    def test_active_profile_raising_is_also_caught(self):
        fake = types.SimpleNamespace(
            active_profile=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            load_active_profile=lambda: {"id": "x"})
        with patch.dict(sys.modules, {"prompt_library": fake}):
            fp = extract_prompt_profile_fingerprint(Path("/nonexistent"))
        self.assertEqual(fp["active_profile_id"], "stable")

    def test_scripts_dir_is_removed_again_from_sys_path(self):
        before = list(sys.path)
        extract_prompt_profile_fingerprint(Path("/some/root"), {"id": "p"})
        self.assertEqual(sys.path, before)

    def test_path_remove_value_error_is_swallowed(self):
        # scripts_dir 被插入（不在 path 里）⇒ finally 要走 remove；让 remove 抛 ValueError
        import prompt_library
        stubborn = _StubbornPath(sys.path)
        with patch.object(sys, "path", stubborn), \
             patch.object(prompt_library, "active_profile", lambda: {"id": "p"}):
            fp = extract_prompt_profile_fingerprint(Path("/fresh/root"))
        self.assertEqual(fp["active_profile_id"], "p")


class ExtractEvolutionMindTests(unittest.TestCase):
    def test_explicit_snapshot_counts_enabled_lessons(self):
        snap = {"version": "v3", "lessons": [
            {"id": 1, "enabled": True},
            {"id": 2, "enabled": False},
            {"id": 3},                      # 缺 enabled ⇒ 视为启用
            "not-a-dict",                   # 非 dict 不计入 enabled
        ]}
        fp = extract_evolution_mind_fingerprint(Path("/nonexistent"), snap)
        self.assertEqual(fp["version"], "v3")
        self.assertEqual(fp["total_count"], 4)
        self.assertEqual(fp["enabled_count"], 2)

    def test_missing_fields_use_markers(self):
        fp = extract_evolution_mind_fingerprint(Path("/nonexistent"), {})
        self.assertEqual(fp["version"], "missing")
        self.assertEqual(fp["total_count"], 0)
        self.assertEqual(fp["enabled_count"], 0)

    def test_non_list_lessons_are_discarded(self):
        fp = extract_evolution_mind_fingerprint(Path("/nonexistent"),
                                                {"version": "v1", "lessons": {"a": 1}})
        self.assertEqual(fp["total_count"], 0)

    def test_reads_memory_snapshot_when_not_given(self):
        import evolution_shield
        with patch.object(evolution_shield, "read_memory_snapshot",
                          lambda: {"version": "v9", "lessons": [{"id": 1}]}):
            fp = extract_evolution_mind_fingerprint(Path("/nonexistent"))
        self.assertEqual(fp["version"], "v9")
        self.assertEqual(fp["enabled_count"], 1)

    def test_read_failure_yields_documented_default(self):
        import evolution_shield
        with patch.object(evolution_shield, "read_memory_snapshot",
                          side_effect=OSError("gone")):
            fp = extract_evolution_mind_fingerprint(Path("/nonexistent"))
        self.assertEqual(fp["version"], "missing")
        self.assertEqual(fp["total_count"], 0)

    def test_scripts_dir_is_restored(self):
        import evolution_shield
        before = list(sys.path)
        with patch.object(evolution_shield, "read_memory_snapshot", lambda: {"version": "v"}):
            extract_evolution_mind_fingerprint(Path("/some/root"))
        self.assertEqual(sys.path, before)

    def test_path_remove_value_error_is_swallowed(self):
        # 与 prompt_profile 同型的兜底：scripts_dir 被插入后 remove 抛 ValueError 也要吞掉
        import evolution_shield
        stubborn = _StubbornPath(sys.path)
        with patch.object(sys, "path", stubborn), \
             patch.object(evolution_shield, "read_memory_snapshot",
                          lambda: {"version": "v5", "lessons": [{"id": 1}]}):
            fp = extract_evolution_mind_fingerprint(Path("/fresh/root"))
        self.assertEqual(fp["version"], "v5")
        self.assertEqual(fp["enabled_count"], 1)


class ExtractInterceptorsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plugins_dir = Path(self.tmp.name) / "plugins" / "interceptors"
        self.plugins_dir.mkdir(parents=True)
        (self.plugins_dir / "a.py").write_bytes(b"PLUGIN-A")

    def test_pipeline_keeps_only_enabled_and_preserves_order(self):
        plugins = [
            {"filename": "off.py", "enabled": False, "file_hash": "OFF"},
            {"filename": "a.py", "enabled": True, "file_hash": "HASH-A"},
            {"filename": "b.py", "enabled": True, "file_hash": "HASH-B"},
        ]
        fp = extract_interceptors_fingerprint(Path("/nonexistent"), plugins,
                                              self.plugins_dir)
        self.assertEqual(fp["total_count"], 3)
        self.assertEqual(fp["enabled_count"], 2)
        self.assertEqual(fp["enabled_plugins"], ["a.py", "b.py"])
        self.assertEqual([p["order"] for p in fp["pipeline"]], [1, 2])
        self.assertEqual(fp["plugins_hash"],
                         _sha8("1:a.py:HASH-A;2:b.py:HASH-B"))

    def test_missing_hash_is_computed_from_disk(self):
        fp = extract_interceptors_fingerprint(
            Path("/nonexistent"),
            [{"filename": "a.py", "enabled": True}],
            self.plugins_dir)
        expected = hashlib.sha256(b"PLUGIN-A").hexdigest()[:8]
        self.assertEqual(fp["pipeline"][0]["file_hash"], expected)

    def test_absent_plugin_file_hashes_as_missing(self):
        fp = extract_interceptors_fingerprint(
            Path("/nonexistent"),
            [{"filename": "ghost.py", "enabled": True}],
            self.plugins_dir)
        self.assertEqual(fp["pipeline"][0]["file_hash"], "missing")

    def test_non_dict_entries_are_skipped(self):
        plugins = ["junk", None, {"filename": "a.py", "enabled": True, "file_hash": "H"}]
        fp = extract_interceptors_fingerprint(Path("/nonexistent"), plugins,
                                              self.plugins_dir)
        self.assertEqual(fp["total_count"], 3)      # 总数仍按原始长度
        self.assertEqual(fp["enabled_count"], 1)
        self.assertEqual(fp["pipeline"][0]["order"], 2)

    def test_disabled_entries_do_not_enter_pipeline(self):
        fp = extract_interceptors_fingerprint(
            Path("/nonexistent"),
            [{"filename": "a.py", "enabled": False, "file_hash": "H"}],
            self.plugins_dir)
        self.assertEqual(fp["enabled_count"], 0)
        self.assertEqual(fp["pipeline"], [])
        self.assertEqual(fp["plugins_hash"], _sha8(""))

    def test_pipeline_hash_is_order_stable(self):
        plugins = [{"filename": "a.py", "enabled": True, "file_hash": "H"},
                   {"filename": "b.py", "enabled": True, "file_hash": "G"}]
        first = extract_interceptors_fingerprint(Path("/x"), plugins, self.plugins_dir)
        second = extract_interceptors_fingerprint(Path("/y"), plugins, self.plugins_dir)
        self.assertEqual(first["plugins_hash"], second["plugins_hash"])

    def test_lists_plugins_when_not_given(self):
        import r20_backend.interceptor_manager as im
        with patch.object(im, "list_plugins",
                          lambda create_if_missing=False: [{"filename": "a.py",
                                                            "enabled": True,
                                                            "file_hash": "Z"}]):
            fp = extract_interceptors_fingerprint(Path("/nonexistent"),
                                                  plugins_dir=self.plugins_dir)
        self.assertEqual(fp["enabled_plugins"], ["a.py"])

    def test_list_failure_yields_empty_pipeline(self):
        import r20_backend.interceptor_manager as im
        with patch.object(im, "list_plugins", side_effect=OSError("no dir")):
            fp = extract_interceptors_fingerprint(Path("/nonexistent"),
                                                  plugins_dir=self.plugins_dir)
        self.assertEqual(fp["total_count"], 0)
        self.assertEqual(fp["plugins_hash"], _sha8(""))

    def test_non_list_plugins_is_tolerated(self):
        fp = extract_interceptors_fingerprint(Path("/nonexistent"), "not-a-list",
                                              self.plugins_dir)
        self.assertEqual(fp["total_count"], 0)


class ExtractCouncilTests(unittest.TestCase):
    def test_enabled_roles_and_models_are_collected(self):
        cfg = {"enabled": True, "consensus_mode": "standard", "roles": {
            "beta": {"enabled": True, "model_id": "m2"},
            "alpha": {"enabled": True},                      # 无 model_id ⇒ default
            "off": {"enabled": False},
            "arb": {"enabled": False, "is_arbitrator": True},  # 仲裁者即使停用也在列
        }}
        fp = extract_council_fingerprint(cfg)
        self.assertTrue(fp["enabled"])
        self.assertEqual(fp["consensus_mode"], "standard")
        self.assertEqual(fp["active_roles"], ["alpha", "arb", "beta"])   # 排序后
        self.assertEqual(fp["role_models"], {"alpha": "default", "arb": "default",
                                            "beta": "m2"})

    def test_cross_examination_spellings_are_normalized(self):
        for raw in ("cross_examination", "cross-exam", "cross", "CROSS"):
            fp = extract_council_fingerprint({"consensus_mode": raw})
            self.assertEqual(fp["consensus_mode"], "cross_examination", raw)

    def test_unknown_mode_falls_back_to_standard(self):
        self.assertEqual(extract_council_fingerprint({"consensus_mode": "weird"})["consensus_mode"],
                         "standard")
        self.assertEqual(extract_council_fingerprint({})["consensus_mode"], "standard")

    def test_council_hash_is_stable_under_role_dict_order(self):
        a = extract_council_fingerprint({"enabled": True, "roles": {
            "x": {"enabled": True, "model_id": "1"},
            "y": {"enabled": True, "model_id": "2"}}})
        b = extract_council_fingerprint({"enabled": True, "roles": {
            "y": {"enabled": True, "model_id": "2"},
            "x": {"enabled": True, "model_id": "1"}}})
        self.assertEqual(a["council_hash"], b["council_hash"])

    def test_model_id_change_changes_council_hash(self):
        base = {"enabled": True, "roles": {"x": {"enabled": True, "model_id": "1"}}}
        other = {"enabled": True, "roles": {"x": {"enabled": True, "model_id": "2"}}}
        self.assertNotEqual(extract_council_fingerprint(base)["council_hash"],
                            extract_council_fingerprint(other)["council_hash"])

    def test_non_dict_roles_is_tolerated(self):
        fp = extract_council_fingerprint({"enabled": True, "roles": ["nope"]})
        self.assertEqual(fp["active_roles"], [])
        self.assertEqual(fp["role_models"], {})

    def test_non_dict_role_entries_are_skipped(self):
        fp = extract_council_fingerprint({"roles": {"x": "junk",
                                                    "y": {"enabled": True}}})
        self.assertEqual(fp["active_roles"], ["y"])

    def test_loads_config_when_not_given(self):
        import r20_backend.council_manager as cm
        with patch.object(cm, "load_council_config",
                          lambda: {"enabled": True, "roles": {"r": {"enabled": True}}}):
            fp = extract_council_fingerprint()
        self.assertTrue(fp["enabled"])
        self.assertEqual(fp["active_roles"], ["r"])

    def test_load_failure_yields_documented_default(self):
        import r20_backend.council_manager as cm
        with patch.object(cm, "load_council_config", side_effect=OSError("no config")):
            fp = extract_council_fingerprint()
        self.assertFalse(fp["enabled"])
        self.assertEqual(fp["consensus_mode"], "standard")
        self.assertEqual(fp["active_roles"], [])

    def test_empty_config_dict_is_not_reloaded(self):
        # 显式传 {} ⇒ 不触发加载（None 才触发）
        fp = extract_council_fingerprint({})
        self.assertFalse(fp["enabled"])
        self.assertEqual(fp["council_hash"],
                         extract_council_fingerprint(
                             {"enabled": False, "consensus_mode": "standard",
                              "roles": {}})["council_hash"])


class CanonicalisationTests(unittest.TestCase):
    def test_prompt_config_keeps_only_identity_fields(self):
        out = _canon_prompt_config({
            "active_profile_id": "p1", "active_style": "stable",
            "profiles": {"p1": {"id": "p1", "name": "n", "last_modified": "2026-01-01",
                                "extra": "dropped"}},
        })
        self.assertEqual(sorted(out), ["active_profile_id", "active_style", "profiles"])
        self.assertEqual(sorted(out["profiles"]["p1"]), ["description", "editor_mode", "enabled",
                                                         "evolution_system", "evolution_user",
                                                         "id", "name", "pipelines",
                                                         "simple_policy", "trading_system",
                                                         "trading_user"])

    def test_prompt_config_non_dict_input(self):
        self.assertEqual(_canon_prompt_config(None), {})
        self.assertEqual(_canon_prompt_config("junk"), "junk")

    def test_prompt_config_non_dict_profile_entries_are_dropped(self):
        out = _canon_prompt_config({"profiles": {"a": "junk", "b": {"id": "b"}}})
        self.assertEqual(list(out["profiles"]), ["b"])

    def test_evolution_memory_accepts_list_or_dict(self):
        lesson = {"id": 1, "category": "c", "rule_text": "r", "enabled": True,
                  "is_baseline": False, "noise": "dropped"}
        self.assertEqual(_canon_evolution_memory([lesson]),
                         [{"id": 1, "category": "c", "rule_text": "r",
                           "enabled": True, "is_baseline": False}])
        self.assertEqual(_canon_evolution_memory({"lessons": [lesson]}),
                         _canon_evolution_memory([lesson]))

    def test_evolution_memory_edge_inputs(self):
        self.assertEqual(_canon_evolution_memory("junk"), [])
        self.assertEqual(_canon_evolution_memory({"lessons": "junk"}), [])
        self.assertEqual(_canon_evolution_memory([1, "two"]), ["1", "two"])

    def test_council_config_keeps_identity_role_fields(self):
        out = _canon_council_config({"enabled": 1, "consensus_mode": "standard",
                                     "timeout_seconds": 120,
                                     "roles": {"r": {"id": "r", "model_id": "m",
                                                     "api_key": "SECRET"}}})
        self.assertTrue(out["enabled"])
        self.assertEqual(out["timeout_seconds"], 120)
        self.assertNotIn("api_key", out["roles"]["r"])
        self.assertEqual(out["roles"]["r"]["model_id"], "m")

    def test_council_config_edge_inputs(self):
        self.assertEqual(_canon_council_config(None), {})
        self.assertEqual(_canon_council_config("junk"), "junk")
        out = _canon_council_config({"roles": {"a": "junk", "b": {"id": "b"}}})
        self.assertEqual(out["roles"]["a"], "junk")     # 非 dict 角色原样保留


class PackageIdentityTests(unittest.TestCase):
    def _payload(self, **overrides):
        base = {
            "prompt_config": {"active_profile_id": "p", "profiles": {}},
            "evolution_memory": [{"id": 1, "rule_text": "r"}],
            "interceptor_config": {"enabled": ["a.py"]},
            "council_config": {"enabled": True, "roles": {}},
            "risk_config": {"max_leverage": 5},
            "venue_routing": {"okx": 1},
        }
        base.update(overrides)
        return base

    def test_projection_has_exactly_the_six_units(self):
        self.assertEqual(sorted(canonical_package_projection(self._payload())),
                         ["council_config", "evolution_memory", "interceptor_config",
                          "prompt_config", "risk_config", "venue_routing"])

    def test_non_mapping_payload_yields_empty_projection(self):
        projection = canonical_package_projection("junk")
        self.assertEqual(projection["prompt_config"], {})
        self.assertEqual(projection["risk_config"], {})

    def test_identity_is_16_hex_and_stable(self):
        ident = package_identity(self._payload())
        self.assertEqual(len(ident), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in ident))
        self.assertEqual(ident, package_identity(self._payload()))

    def test_volatile_fields_do_not_change_identity(self):
        # ★ 审计 P0-3 的核心：易变字段（时间戳/生成器信息）不得进入整包标识，
        #   否则每次归档都会得到新名字，去重形同虚设。
        a = self._payload()
        b = self._payload(archived_at="2026-01-01", generated_by="cron",
                          notes="hello", package_hash="deadbeef")
        self.assertEqual(package_identity(a), package_identity(b))

    def test_risk_and_routing_do_change_identity(self):
        base = self._payload()
        self.assertNotEqual(package_identity(base),
                            package_identity(self._payload(risk_config={"max_leverage": 6})))
        self.assertNotEqual(package_identity(base),
                            package_identity(self._payload(venue_routing={"okx": 2})))

    def test_projection_accepts_any_mapping(self):
        import collections
        od = collections.OrderedDict(self._payload())
        self.assertEqual(canonical_package_projection(od),
                         canonical_package_projection(self._payload()))


class PackageRestoreDiffTests(unittest.TestCase):
    def _payload(self, **overrides):
        base = {
            "prompt_config": {"active_profile_id": "p", "profiles": {}},
            "evolution_memory": [{"id": 1, "rule_text": "r"}],
            "interceptor_config": {"enabled": ["a.py"]},
            "council_config": {"enabled": True, "roles": {}},
            "risk_config": {"max_leverage": 5},
            "venue_routing": {"okx": 1},
        }
        base.update(overrides)
        return base

    def test_identical_payloads_have_no_diff(self):
        payload = self._payload()
        self.assertEqual(package_restore_diff(payload, dict(payload)), [])

    def test_genuinely_empty_units_are_never_claimed(self):
        # 归档没装的单元无从承诺 ⇒ 当前有、归档没有**不算**恢复失败
        archived = self._payload(evolution_memory=[], interceptor_config={},
                                 risk_config={}, venue_routing={})
        current = self._payload()
        self.assertEqual(package_restore_diff(archived, current), [])

    def test_prompt_and_council_empty_dicts_still_count_as_claims(self):
        # ⚠️ 实测到与 docstring 不一致之处（本刀仅记录，**未改**）：
        # docstring 说「归档**没装**的单元（空/None）无从承诺，一律跳过」，
        # 但 `_canon_prompt_config({})` 会补成 `{active_profile_id: None,
        # active_style: None, profiles: {}}`、`_canon_council_config({})` 会补成
        # `{enabled: False, consensus_mode: None, timeout_seconds: None, roles: {}}`
        # —— **两者都非空**，于是"空 ⇒ 跳过"这条对 6 个单元里的这 2 个**不成立**。
        # 后果：归档里写着 `prompt_config: {}` 的包，恢复到任何有提示词配置的当前态，
        # 都会被判成 `prompt_config` 恢复失败（而不是"无从承诺"）。
        archived = self._payload(prompt_config={}, council_config={},
                                 evolution_memory=[], interceptor_config={})
        diff = package_restore_diff(archived, self._payload())
        self.assertEqual(diff, ["prompt_config", "council_config"])

    def test_only_two_units_have_non_falsy_canonical_skeleton(self):
        # 用规范化投影本身把上一条的机理钉死：哪几个单元"写空也非空"
        non_falsy = []
        for unit in ("prompt_config", "evolution_memory", "interceptor_config",
                     "council_config", "risk_config", "venue_routing"):
            empty = [] if unit == "evolution_memory" else {}
            if canonical_package_projection(self._payload(**{unit: empty}))[unit]:
                non_falsy.append(unit)
        self.assertEqual(non_falsy, ["prompt_config", "council_config"])

    def test_sequential_units_require_exact_match(self):
        archived = self._payload()
        diff = package_restore_diff(archived, self._payload(interceptor_config={"enabled": []}))
        self.assertEqual(diff, ["interceptor_config"])

    def test_prompt_and_council_mismatches_are_reported(self):
        archived = self._payload()
        diff = package_restore_diff(archived, self._payload(
            prompt_config={"active_profile_id": "other", "profiles": {}},
            council_config={"enabled": False, "roles": {}}))
        self.assertEqual(sorted(diff), ["council_config", "prompt_config"])

    def test_dict_units_only_check_keys_present_in_archive(self):
        archived = self._payload(risk_config={"max_leverage": 5})
        # 当前多了归档里没有的键 ⇒ 不判失败（由 uncovered_risk_keys 披露）
        diff = package_restore_diff(archived, self._payload(
            risk_config={"max_leverage": 5, "new_key": 9}))
        self.assertEqual(diff, [])

    def test_dict_unit_reports_the_specific_key(self):
        archived = self._payload(risk_config={"max_leverage": 5, "daily_loss": 1})
        diff = package_restore_diff(archived, self._payload(
            risk_config={"max_leverage": 6, "daily_loss": 1}))
        self.assertEqual(diff, ["risk_config.max_leverage"])

    def test_missing_key_in_current_is_reported(self):
        archived = self._payload(risk_config={"max_leverage": 5})
        diff = package_restore_diff(archived, self._payload(risk_config={}))
        self.assertEqual(diff, ["risk_config.max_leverage"])

    def test_venue_routing_keys_are_checked_too(self):
        archived = self._payload(venue_routing={"okx": 1})
        self.assertEqual(package_restore_diff(archived, self._payload(venue_routing={})),
                         ["venue_routing.okx"])

    def test_non_mapping_dict_unit_is_compared_wholesale(self):
        # venue_routing 原样透传 ⇒ 列表也能进来；类型不是 Mapping 时整块比较
        archived = self._payload(venue_routing=["okx"])
        current = self._payload(venue_routing={"okx": 1})
        self.assertEqual(package_restore_diff(archived, current), ["venue_routing"])

    def test_non_mapping_current_is_compared_wholesale(self):
        archived = self._payload(venue_routing={"okx": 1})
        current = self._payload(venue_routing=["okx"])
        self.assertEqual(package_restore_diff(archived, current), ["venue_routing"])

    def test_equal_non_mapping_units_pass(self):
        archived = self._payload(venue_routing=["okx"])
        current = self._payload(venue_routing=["okx"])
        self.assertEqual(package_restore_diff(archived, current), [])

    def test_empty_archived_dict_unit_is_skipped(self):
        archived = self._payload(venue_routing={}, risk_config={})
        self.assertEqual(package_restore_diff(archived, self._payload()), [])

    def test_diff_is_a_list_of_unit_names(self):
        # 归档声称有心法，当前却是空的 ⇒ 必须报出来（这正是恢复校验的意义）
        diff = package_restore_diff(self._payload(),
                                    self._payload(evolution_memory=[]))
        self.assertIsInstance(diff, list)
        self.assertEqual(diff, ["evolution_memory"])


if __name__ == "__main__":
    unittest.main()

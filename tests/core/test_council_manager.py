"""投委会配置门面：**可解析就绝不覆盖、损坏先留备份、写闸夹超时**（第二百八十刀，开新面 council_manager.py）。

先打印整个文件（422 行）再动笔。它是 Council Pro 的**配置与编排门面**：
真实逻辑（读/写/迁移/导出/导入）+ 一批"调用期解析门面全局"的薄壳。

| 语义 | 口径 |
|---|---|
| ★ **可解析就绝不覆盖** | 审计 P1-4a 的核心：文件能被解析且含 `roles` ⇒ **原样返回**；结构有问题只打 `config_warning`，用户自写提示词一个字都不许蒸发 |
| ★ **损坏先留痕再重建** | 不可解析/缺 `roles` ⇒ 先把原字节备份成 `council_config_corrupt_<stamp>.json`，再重建工厂默认；**备份失败也要继续重建**并打日志 |
| ★ **超时预算单一事实源** | 审计 P2-13：`clamp_council_timeout` 是唯一事实源（`[30, 420]`）；`save_council_config` 任何写入口都夹取 |
| ★ **读写共用同一校验** | 审计 P1-4a：读写两侧都过 `validate_council_roles`，杜绝"写得进、读不回"的白名单漂移 |
| ★ **模型绑定不许静默回落** | 审计 P1-4b：`enforce_models=True` ⇒ 未登记绑定直接拒绝；整包导入（`False`）⇒ **清空**该绑定（=跟随主脑）并如实回报，绝不静默保留一个查不到的 id |
| ★ **RMW 全程持锁** | `_locked_council` 装饰器把 load/save/import/套用预设整段包进可重入 `file_lock`（嵌套 save 不会自锁）|
| ★ **stdout 保护** | `save` 读旧配置前显式限定 `str/Path`：Mock 既像路径又会被 `open()` 当**文件描述符**解释 ⇒ 会关掉 fd 1 让套件退出码变 120。用例注入 Mock 后断言 stdout 仍可用 |
| ★ **导入包自描述** | `export` 产出 `{format,version,exported_at,config}`；`import` 同时接受标准包与裸 `{roles:...}`，并做字段清洗（截断/夹取/枚举归一）|

## 🐞 本刀实测到一处**单一事实源被绕过**（未擅自改生产代码）

`import_council_config` 里超时预算是**硬编码的 `[10.0, 300.0]`**，而不是审计 P2-13 指定的
`clamp_council_timeout`（`[30, 420]`）：实测 `timeout_seconds=400`（策略允许的合法值）
导入后被**悄悄压到 300**，`5000` 也压到 300 而非 420。
即"唯一事实源"在导入这条路上并未生效。用例按实际行为钉住并标注。

另登记一处：`_atomic_write_json` **没有失败清理**（其余 6 个原子写辅助都有 `finally: unlink`），
`os.replace` 抛错时会留下一个 `tmp*` 临时文件。用例按实际行为钉住。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from r20_backend import council_manager as CM
from r20_backend import file_locks
from r20_backend.council import debate, policy, presets, roster


def _roles(**overrides):
    roles = {rid: dict(tpl) for rid, tpl in policy.DEFAULT_PRESET_TEMPLATES.items()}
    for rid, patch in overrides.items():
        roles.setdefault(rid, {}).update(patch)
    return roles


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-council-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.file = self.tmp / "council_config.json"
        self._start(mock.patch.object(CM, "COUNCIL_CONFIG_FILE", self.file))
        self._start(mock.patch.object(CM, "DATA_DIR", self.tmp))
        self.lock = self._start(mock.patch.object(CM, "file_lock"))
        # 角色结构校验在多数用例里无所谓，默认放行
        self.validate = self._start(mock.patch.object(CM, "validate_council_roles",
                                                      return_value=""))
        self._start(mock.patch.object(CM, "validate_seat_model_bindings",
                                      return_value=[]))
        self._start(mock.patch.object(CM, "clamp_council_timeout",
                                      side_effect=lambda v: min(420.0, max(30.0, float(v)))))

    def _write(self, payload, raw=None):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(raw if raw is not None else json.dumps(payload),
                             encoding="utf-8")


class ReexportTests(unittest.TestCase):
    """门面再导出而非搬空：审计/子进程靠 `from r20_backend.council_manager import X` 取用。"""

    def test_constants_are_the_policy_objects(self):
        self.assertEqual(CM.DEFAULT_CONSENSUS_MODE, policy.DEFAULT_CONSENSUS_MODE)
        self.assertEqual(CM.DEFAULT_COUNCIL_TIMEOUT, policy.DEFAULT_COUNCIL_TIMEOUT)
        self.assertEqual(CM.MIN_COUNCIL_TIMEOUT, policy.MIN_COUNCIL_TIMEOUT)
        self.assertEqual(CM.MAX_COUNCIL_TIMEOUT, policy.MAX_COUNCIL_TIMEOUT)
        self.assertEqual(CM.VALID_CONSENSUS_MODES, policy.VALID_CONSENSUS_MODES)
        self.assertEqual(CM.ALL_AVAILABLE_PRESETS, policy.ALL_AVAILABLE_PRESETS)
        self.assertEqual(CM.COUNCIL_PRESET_SUITES, policy.COUNCIL_PRESET_SUITES)
        self.assertEqual(CM.DEFAULT_PRESET_TEMPLATES, policy.DEFAULT_PRESET_TEMPLATES)

    def test_roster_helpers_are_the_same_objects(self):
        self.assertIs(CM.clamp_council_timeout, roster.clamp_council_timeout)
        self.assertIs(CM.resolve_seat_model, roster.resolve_seat_model)
        self.assertIs(CM.seat_model_health, roster.seat_model_health)
        self.assertIs(CM.validate_council_roles, roster.validate_council_roles)
        self.assertIs(CM.validate_seat_model_bindings,
                      roster.validate_seat_model_bindings)

    def test_preset_helpers_are_the_same_objects(self):
        self.assertIs(CM.get_available_presets, presets.get_available_presets)
        self.assertIs(CM.get_preset_suites, presets.get_preset_suites)
        self.assertIs(CM._render_seat_prompt, debate._render_seat_prompt)

    def test_export_format_constants(self):
        self.assertEqual(CM.COUNCIL_EXPORT_FORMAT, "r20-council-config")
        self.assertEqual(CM.COUNCIL_EXPORT_VERSION, 1)
        self.assertEqual(CM._VALID_REASONING_EFFORTS,
                         {"none", "minimal", "low", "medium", "high"})

    def test_legacy_hash_map_covers_the_four_seats(self):
        self.assertEqual(set(CM._LEGACY_PRESET_PROMPT_HASHES),
                         {"trader_trend", "trader_momentum", "trader_quant", "cio"})
        for role_id, hashes in CM._LEGACY_PRESET_PROMPT_HASHES.items():
            with self.subTest(role_id=role_id):
                self.assertTrue(hashes)


class LockDecoratorTests(_Base):
    def test_wrapper_takes_the_config_lock(self):
        @CM._locked_council
        def _fn(value):
            return value * 2

        self.assertEqual(_fn(21), 42)
        self.lock.assert_called_once_with(CM.COUNCIL_CONFIG_FILE)

    def test_wrapper_preserves_the_function_metadata(self):
        @CM._locked_council
        def _named():
            """docstring"""

        self.assertEqual(_named.__name__, "_named")
        self.assertEqual(_named.__doc__, "docstring")

    def test_shipped_entry_points_are_locked(self):
        for fn in (CM.load_council_config, CM.save_council_config,
                   CM.import_council_config, CM.apply_preset_suite,
                   CM.reset_role_template):
            with self.subTest(fn=fn.__name__):
                self.assertTrue(hasattr(fn, "__wrapped__"), "应被 _locked_council 包住")


class AtomicWriteTests(_Base):
    def test_writes_readable_utf8_json(self):
        from r20_backend import council_manager as _cm
        target = self.tmp / "nested" / "out.json"
        with mock.patch.object(_cm, "COUNCIL_CONFIG_FILE", target):
            _cm._atomic_write_json(target, {"中文": 1, "b": 2})
        text = target.read_text(encoding="utf-8")
        self.assertIn("中文", text)
        self.assertEqual(json.loads(text), {"中文": 1, "b": 2})
        self.assertIn("\n  ", text, "indent=2")

    def test_creates_parent_directories(self):
        target = self.tmp / "deep" / "deeper" / "out.json"
        CM._atomic_write_json(target, {})
        self.assertTrue(target.exists())

    def test_fsync_is_called_before_replace(self):
        fsync = self._start(mock.patch.object(CM.os, "fsync"))
        CM._atomic_write_json(self.tmp / "x.json", {})
        fsync.assert_called_once()

    def test_normal_path_leaves_no_temp_file(self):
        CM._atomic_write_json(self.tmp / "x.json", {})
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["x.json"])

    def test_failure_leaves_a_temp_file_behind(self):
        """⚠️ 实测观察：本辅助**没有** `finally: unlink`（其余 6 个原子写辅助都有），
        `os.replace` 失败时会残留一个 `tmp*` 临时文件。按实际行为钉住，未擅自改。"""
        with mock.patch.object(CM.os, "replace", side_effect=OSError("磁盘满了")):
            with self.assertRaises(OSError):
                CM._atomic_write_json(self.tmp / "x.json", {})
        leftovers = list(self.tmp.glob("tmp*"))
        self.assertEqual(len(leftovers), 1, "确实残留了一个临时文件")


class LoadConfigTests(_Base):
    def test_missing_file_rebuilds_and_returns_the_factory_default(self):
        config = CM.load_council_config()
        self.assertIs(config["enabled"], False)
        self.assertEqual(config["consensus_mode"], policy.DEFAULT_CONSENSUS_MODE)
        self.assertEqual(config["timeout_seconds"], policy.DEFAULT_COUNCIL_TIMEOUT)
        self.assertEqual(set(config["roles"]), set(policy.DEFAULT_PRESET_TEMPLATES))
        self.assertIn("updated_at", config)
        self.assertEqual(json.loads(self.file.read_text())["roles"].keys(),
                         config["roles"].keys(), "默认档必须落盘")

    def test_factory_default_roles_are_deep_copies(self):
        config = CM.load_council_config()
        config["roles"]["cio"]["prompt"] = "MUTATED"
        self.assertNotEqual(policy.DEFAULT_PRESET_TEMPLATES["cio"]["prompt"], "MUTATED")

    def test_parseable_file_is_returned_verbatim(self):
        self._write({"roles": {"cio": {"prompt": "我的提示词"}}, "enabled": True,
                     "consensus_mode": "cross_examination", "timeout_seconds": 120})
        config = CM.load_council_config()
        self.assertIs(config["enabled"], True)
        self.assertEqual(config["consensus_mode"], "cross_examination")
        self.assertEqual(config["timeout_seconds"], 120)
        self.assertEqual(config["roles"]["cio"]["prompt"], "我的提示词")
        self.assertNotIn("config_warning", config)

    def test_invalid_consensus_mode_is_normalised_in_memory(self):
        self._write({"roles": {"cio": {"prompt": "x"}}, "consensus_mode": "WEIRD"})
        self.assertEqual(CM.load_council_config()["consensus_mode"],
                         policy.DEFAULT_CONSENSUS_MODE)

    def test_structural_problem_warns_but_preserves_the_file(self):
        self.validate.return_value = "缺少仲裁官席位"
        self._write({"roles": {"cio": {"prompt": "我的提示词"}}})
        config = CM.load_council_config()
        self.assertIn("config_warning", config)
        self.assertIn("缺少仲裁官席位", config["config_warning"])
        self.assertIn("已保留原文件", config["config_warning"])
        self.assertEqual(json.loads(self.file.read_text())["roles"]["cio"]["prompt"],
                         "我的提示词", "结构异常不得引发覆盖")

    def test_corrupt_json_is_backed_up_then_rebuilt(self):
        self._write(None, raw="{不是 json")
        with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()) as out:
            config = CM.load_council_config()
        backups = list(self.tmp.glob("council_config_corrupt_*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{不是 json",
                         "备份必须是原始字节，便于人工恢复")
        self.assertIn("已备份为", out.getvalue())
        self.assertIs(config["enabled"], False)

    def test_valid_json_without_roles_is_treated_as_corrupt(self):
        self._write({"enabled": True})
        with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()) as out:
            CM.load_council_config()
        self.assertIn("缺少 roles 字段", out.getvalue())
        self.assertEqual(len(list(self.tmp.glob("council_config_corrupt_*.json"))), 1)

    def test_non_dict_json_is_treated_as_corrupt(self):
        self._write([1, 2, 3])
        with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()):
            CM.load_council_config()
        self.assertEqual(len(list(self.tmp.glob("council_config_corrupt_*.json"))), 1)

    def test_backup_failure_is_reported_and_the_rebuild_still_happens(self):
        self._write(None, raw="{坏")
        with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()) as out:
            with mock.patch.object(Path, "write_bytes", side_effect=OSError("只读")):
                config = CM.load_council_config()
        self.assertIn("备份失败", out.getvalue())
        self.assertIs(config["enabled"], False)

    def test_directory_in_place_of_the_file_crashes(self):
        """⚠️ 实测缺陷：`is_file()` 为假 ⇒ 直接走"重建默认"分支，而 `os.replace`
        无法把文件替换到一个**目录**上 ⇒ 抛 `IsADirectoryError`。
        注意这条路径**既没备份也没打日志**（损坏文件那条路才有），
        即"配置路径被目录占住"这种误配置会让读配置直接崩。按实际行为钉住。"""
        self.file.mkdir(parents=True, exist_ok=True)
        with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()):
            with self.assertRaises(IsADirectoryError):
                CM.load_council_config()
        self.assertEqual(list(self.tmp.glob("council_config_corrupt_*.json")), [],
                         "这条路径不产生备份（与「损坏文件」路径不同）")


class MigrateTests(_Base):
    def _legacy_prompt(self, role_id):
        """反推出一个能命中 legacy 哈希表的提示词（直接构造 sha256 前16位）。"""
        import hashlib
        for candidate in ("legacy-prompt-" + role_id, "old-" + role_id):
            digest = hashlib.sha256(candidate.encode()).hexdigest()[:16]
            if digest in CM._LEGACY_PRESET_PROMPT_HASHES[role_id]:
                return candidate
        return None

    def test_role_without_a_legacy_hash_is_skipped(self):
        config = {"roles": {"unknown_role": {"prompt": "x"}}}
        self.assertIs(CM._migrate_untouched_preset_prompts(config), False)

    def test_non_dict_role_is_skipped(self):
        config = {"roles": {"cio": "not-a-dict"}}
        self.assertIs(CM._migrate_untouched_preset_prompts(config), False)

    def test_customised_prompt_is_never_touched(self):
        config = {"roles": {"cio": {"prompt": "我自己写的提示词"}}}
        self.assertIs(CM._migrate_untouched_preset_prompts(config), False)
        self.assertEqual(config["roles"]["cio"]["prompt"], "我自己写的提示词")

    def test_a_matching_legacy_hash_is_replaced_by_the_factory_template(self):
        import hashlib
        target = None
        for role_id, hashes in CM._LEGACY_PRESET_PROMPT_HASHES.items():
            if not isinstance(hashes, set):
                continue
            # 造一个 hash 落在该集合里的提示词成本高；直接改写比对用的映射
            target = role_id
            break
        fake_prompt = "some legacy text"
        digest = hashlib.sha256(fake_prompt.encode()).hexdigest()[:16]
        with mock.patch.dict(CM._LEGACY_PRESET_PROMPT_HASHES,
                             {target: {digest}}, clear=True):
            config = {"roles": {target: {"prompt": fake_prompt}}}
            self.assertIs(CM._migrate_untouched_preset_prompts(config), True)
        self.assertEqual(config["roles"][target]["prompt"],
                         policy.DEFAULT_PRESET_TEMPLATES[target]["prompt"])
        self.assertEqual(config["roles"][target]["description"],
                         policy.DEFAULT_PRESET_TEMPLATES[target]["description"])

    def test_string_form_hash_is_supported(self):
        import hashlib
        fake_prompt = "single hash form"
        digest = hashlib.sha256(fake_prompt.encode()).hexdigest()[:16]
        with mock.patch.dict(CM._LEGACY_PRESET_PROMPT_HASHES,
                             {"cio": digest}, clear=True):
            config = {"roles": {"cio": {"prompt": fake_prompt}}}
            self.assertIs(CM._migrate_untouched_preset_prompts(config), True)

    def test_missing_template_for_a_listed_role_is_skipped(self):
        import hashlib
        fake_prompt = "ghost role"
        digest = hashlib.sha256(fake_prompt.encode()).hexdigest()[:16]
        with mock.patch.dict(CM._LEGACY_PRESET_PROMPT_HASHES,
                             {"ghost": {digest}}, clear=True):
            config = {"roles": {"ghost": {"prompt": fake_prompt}}}
            self.assertIs(CM._migrate_untouched_preset_prompts(config), False)

    def test_migration_through_load_updates_the_timestamp_and_the_file(self):
        import hashlib
        fake_prompt = "legacy-cmo"
        digest = hashlib.sha256(fake_prompt.encode()).hexdigest()[:16]
        self._write({"roles": {"cio": {"prompt": fake_prompt}},
                     "updated_at": "2000-01-01 00:00:00"})
        with mock.patch.dict(CM._LEGACY_PRESET_PROMPT_HASHES,
                             {"cio": {digest}}, clear=True):
            config = CM.load_council_config()
        self.assertNotEqual(config["updated_at"], "2000-01-01 00:00:00")
        self.assertEqual(json.loads(self.file.read_text())["roles"]["cio"]["prompt"],
                         policy.DEFAULT_PRESET_TEMPLATES["cio"]["prompt"])


class SaveConfigTests(_Base):
    def test_non_dict_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            CM.save_council_config([1, 2])
        self.assertIn("must be a dict", str(ctx.exception))

    def test_roles_must_be_a_non_empty_dict(self):
        for roles in (None, {}, [], "x"):
            with self.subTest(roles=roles):
                with self.assertRaises(ValueError) as ctx:
                    CM.save_council_config({"roles": roles})
                self.assertIn("至少需要包含角色配置", str(ctx.exception))

    def test_missing_arbitrator_gets_a_dedicated_message(self):
        self.validate.return_value = "缺少首席终审仲裁官席位"
        with self.assertRaises(ValueError) as ctx:
            CM.save_council_config({"roles": _roles()})
        self.assertIn("必须保留至少一位首席终审仲裁官", str(ctx.exception))

    def test_other_structural_problems_are_reported_generically(self):
        self.validate.return_value = "权重之和不为 1"
        with self.assertRaises(ValueError) as ctx:
            CM.save_council_config({"roles": _roles()})
        self.assertIn("委员会配置不合法：权重之和不为 1", str(ctx.exception))

    def test_non_dict_role_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            CM.save_council_config({"roles": {"cio": "nope"}})
        self.assertIn("角色 cio 配置必须为字典", str(ctx.exception))

    def test_role_defaults_are_filled_in(self):
        config = CM.save_council_config({"roles": {"cio": {"prompt": "x"}}})
        role = config["roles"]["cio"]
        self.assertEqual(role["id"], "cio")
        self.assertIs(role["enabled"], True)
        self.assertEqual(role["weight"], 0.3)
        self.assertEqual(role["reasoning_effort"], "medium")
        self.assertEqual(role["temperature"], 0.2)

    def test_existing_role_fields_are_not_overwritten(self):
        config = CM.save_council_config({"roles": {"cio": {
            "prompt": "x", "enabled": False, "weight": 0.9,
            "reasoning_effort": "high", "temperature": 0.7}}})
        role = config["roles"]["cio"]
        self.assertIs(role["enabled"], False)
        self.assertEqual(role["weight"], 0.9)
        self.assertEqual(role["reasoning_effort"], "high")
        self.assertEqual(role["temperature"], 0.7)

    def test_consensus_mode_is_normalised(self):
        config = CM.save_council_config({"roles": _roles(), "consensus_mode": "  STANDARD "})
        self.assertEqual(config["consensus_mode"], "standard")
        config = CM.save_council_config({"roles": _roles(), "consensus_mode": "???"})
        self.assertEqual(config["consensus_mode"], policy.DEFAULT_CONSENSUS_MODE)

    def test_timeout_goes_through_the_single_source_of_truth(self):
        clamp = self._start(mock.patch.object(CM, "clamp_council_timeout",
                                              side_effect=lambda v: min(420.0, max(30.0, float(v)))))
        self.assertEqual(CM.save_council_config({"roles": _roles(),
                                                 "timeout_seconds": 5000})["timeout_seconds"],
                         420.0)
        self.assertEqual(CM.save_council_config({"roles": _roles(),
                                                 "timeout_seconds": 5})["timeout_seconds"],
                         30.0)
        clamp.assert_called()

    def test_timestamp_is_written_and_persisted(self):
        config = CM.save_council_config({"roles": _roles()})
        self.assertRegex(config["updated_at"],
                         r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+08:00$")
        self.assertEqual(json.loads(self.file.read_text())["updated_at"],
                         config["updated_at"])

    def test_model_problems_are_fatal_when_enforced(self):
        self._start(mock.patch.object(CM, "validate_seat_model_bindings",
                                      return_value=["席位 cio 绑定的模型未登记"]))
        with self.assertRaises(ValueError) as ctx:
            CM.save_council_config({"roles": _roles()})
        self.assertIn("席位 cio 绑定的模型未登记", str(ctx.exception))

    def test_model_problems_are_cleared_when_not_enforced(self):
        self._start(mock.patch.object(CM, "validate_seat_model_bindings",
                                      return_value=["席位 cio 绑定的模型未登记"]))
        config = CM.save_council_config(
            {"roles": {"cio": {"prompt": "x", "model_id": "ghost-model"},
                       "other": {"prompt": "y", "model_id": "fine"}}},
            enforce_models=False)
        self.assertEqual(config["roles"]["cio"]["model_id"], "")
        self.assertEqual(config["roles"]["other"]["model_id"], "fine")
        self.assertEqual(config["cleared_model_bindings"],
                         [{"role_id": "cio", "model_id": "ghost-model"}])

    def test_no_problems_means_no_cleared_key(self):
        config = CM.save_council_config({"roles": _roles()}, enforce_models=False)
        self.assertNotIn("cleared_model_bindings", config)

    def test_non_dict_role_is_skipped_in_the_clearing_pass(self):
        self._start(mock.patch.object(CM, "validate_seat_model_bindings",
                                      return_value=["席位 cio 绑定的模型未登记"]))
        with self.assertRaises(ValueError):
            CM.save_council_config({"roles": {"cio": "not-a-dict"}})

    def test_mock_config_path_does_not_close_stdout(self):
        """审计 P1-4b 的 fd-1 事故：Mock 会被 `open()` 当文件描述符解释并关掉 fd 1。"""
        self._start(mock.patch.object(CM, "_atomic_write_json"))
        with mock.patch.object(CM, "COUNCIL_CONFIG_FILE", mock.MagicMock()):
            config = CM.save_council_config({"roles": _roles()})
        self.assertEqual(config["roles"]["cio"]["id"], "cio")
        os.fstat(1)                                   # 关掉的话这里会 OSError
        sys.stdout.write("")                          # 仍然可写

    def test_previous_roles_read_failure_is_swallowed(self):
        """旧配置是坏 JSON ⇒ 读 previous 时 json.load 抛 JSONDecodeError，必须吞掉当空。"""
        self._write(None, raw="{坏掉的 json")
        seen = {}
        self._start(mock.patch.object(
            CM, "validate_seat_model_bindings",
            side_effect=lambda roles, previous=None: seen.update(previous=previous) or []))
        CM.save_council_config({"roles": _roles()})
        self.assertEqual(seen["previous"], {})

    def test_non_dict_role_is_skipped_by_the_clearing_pass(self):
        """清除绑定那一趟先跑，遇到非字典角色必须 `continue`（随后才由结构校验拒绝）。"""
        self._start(mock.patch.object(CM, "validate_seat_model_bindings",
                                      return_value=["席位 other 绑定的模型未登记"]))
        with self.assertRaises(ValueError) as ctx:
            CM.save_council_config({"roles": {"cio": "not-a-dict",
                                              "other": {"prompt": "p",
                                                        "model_id": "ghost"}}},
                                   enforce_models=False)
        self.assertIn("角色 cio 配置必须为字典", str(ctx.exception))

    def test_previous_roles_are_read_from_a_real_file(self):
        self._write({"roles": {"legacy": {"model_id": "old"}}})
        seen = {}
        self._start(mock.patch.object(
            CM, "validate_seat_model_bindings",
            side_effect=lambda roles, previous=None: seen.update(previous=previous) or []))
        CM.save_council_config({"roles": _roles()})
        self.assertEqual(seen["previous"], {"legacy": {"model_id": "old"}})


class ExportImportTests(_Base):
    def test_export_shape(self):
        self._write({"roles": {"cio": {"prompt": "p"}}, "enabled": True,
                     "consensus_mode": "standard", "timeout_seconds": 240})
        package = CM.export_council_config()
        self.assertEqual(package["format"], CM.COUNCIL_EXPORT_FORMAT)
        self.assertEqual(package["version"], CM.COUNCIL_EXPORT_VERSION)
        self.assertRegex(package["exported_at"],
                         r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+08:00$")
        self.assertEqual(sorted(package["config"]),
                         ["consensus_mode", "enabled", "roles", "timeout_seconds"])
        self.assertIs(package["config"]["enabled"], True)

    def test_export_defaults_when_keys_are_absent(self):
        self._write({"roles": {"cio": {"prompt": "p"}}})
        package = CM.export_council_config()
        self.assertIs(package["config"]["enabled"], False)
        self.assertEqual(package["config"]["timeout_seconds"],
                         policy.DEFAULT_COUNCIL_TIMEOUT)

    def test_import_requires_a_dict(self):
        with self.assertRaises(ValueError) as ctx:
            CM.import_council_config([1])
        self.assertIn("必须是 JSON 对象", str(ctx.exception))

    def test_import_requires_roles(self):
        for payload in ({}, {"roles": {}}, {"roles": "x"}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError) as ctx:
                    CM.import_council_config(payload)
                self.assertIn("缺少有效的 roles", str(ctx.exception))

    def test_import_accepts_the_standard_package(self):
        package = {"format": CM.COUNCIL_EXPORT_FORMAT, "version": 1,
                   "config": {"roles": {"cio": {"prompt": "来自包"}}, "enabled": True}}
        out = CM.import_council_config(package)
        self.assertEqual(out["roles"], ["cio"])
        self.assertIs(CM.load_council_config()["enabled"], True)

    def test_import_accepts_a_bare_roles_object(self):
        out = CM.import_council_config({"roles": {"cio": {"prompt": "裸对象"}}})
        self.assertEqual(out["roles"], ["cio"])

    def test_import_rejects_non_dict_roles_entries(self):
        with self.assertRaises(ValueError) as ctx:
            CM.import_council_config({"roles": {"cio": "nope"}})
        self.assertIn("角色 cio 配置必须为字典", str(ctx.exception))

    def test_import_requires_a_prompt(self):
        for prompt in ("", "   "):
            with self.subTest(prompt=repr(prompt)):
                with self.assertRaises(ValueError) as ctx:
                    CM.import_council_config({"roles": {"cio": {"prompt": prompt}}})
                self.assertIn("缺少提示词", str(ctx.exception))

    def test_import_of_a_null_prompt_stores_the_string_none(self):
        """⚠️ 实测缺陷：`str(role.get("prompt",""))` 会把 `null` 转成**字符串 "None"**，
        于是"没写提示词"被当成一条合法提示词导入（而不是被拒）。按实际行为钉住。"""
        out = CM.import_council_config({"roles": {"cio": {"prompt": None}}})
        self.assertEqual(out["roles"], ["cio"])
        self.assertEqual(CM.load_council_config()["roles"]["cio"]["prompt"], "None")

    def test_import_rejects_over_long_prompts(self):
        with self.assertRaises(ValueError) as ctx:
            CM.import_council_config({"roles": {"cio": {"prompt": "x" * 20001}}})
        self.assertIn("提示词过长", str(ctx.exception))

    def test_import_clamps_weight_and_temperature(self):
        out = CM.import_council_config({"roles": {
            "a": {"prompt": "p", "weight": 9, "temperature": 9},
            "b": {"prompt": "p", "weight": 0.001, "temperature": -3},
            "c": {"prompt": "p", "weight": "bad", "temperature": "bad"}}})
        cfg = CM.load_council_config()
        self.assertEqual(out["roles"], ["a", "b", "c"])
        self.assertEqual(cfg["roles"]["a"]["weight"], 1.0)
        self.assertEqual(cfg["roles"]["a"]["temperature"], 1.0)
        self.assertEqual(cfg["roles"]["b"]["weight"], 0.05)
        self.assertEqual(cfg["roles"]["b"]["temperature"], 0.0)
        self.assertEqual(cfg["roles"]["c"]["weight"], 0.3)
        self.assertEqual(cfg["roles"]["c"]["temperature"], 0.2)

    def test_import_normalises_reasoning_effort(self):
        CM.import_council_config({"roles": {
            "a": {"prompt": "p", "reasoning_effort": "HIGH"},
            "b": {"prompt": "p", "reasoning_effort": "???"}}})
        cfg = CM.load_council_config()
        self.assertEqual(cfg["roles"]["a"]["reasoning_effort"], "high")
        self.assertEqual(cfg["roles"]["b"]["reasoning_effort"], "medium")

    def test_import_truncates_long_strings(self):
        CM.import_council_config({"roles": {"r": {
            "prompt": "p", "name": "n" * 100, "role_title": "t" * 100,
            "description": "d" * 300, "model_id": "m" * 100}}})
        role = CM.load_council_config()["roles"]["r"]
        self.assertEqual(len(role["name"]), 60)
        self.assertEqual(len(role["role_title"]), 80)
        self.assertEqual(len(role["description"]), 200)
        self.assertEqual(len(role["model_id"]), 80)

    def test_import_truncates_and_defaults_the_role_id(self):
        out = CM.import_council_config({"roles": {"  ": {"prompt": "p"}}})
        self.assertEqual(out["roles"], ["role"], "空 id 回落到 role")

    def test_import_infers_the_arbitrator(self):
        CM.import_council_config({"roles": {
            "cio": {"prompt": "p"}, "ARBITRATOR": {"prompt": "p"},
            "trader": {"prompt": "p"}, "boss": {"prompt": "p", "is_arbitrator": True}}})
        roles = CM.load_council_config()["roles"]
        self.assertIs(roles["cio"]["is_arbitrator"], True)
        self.assertIs(roles["ARBITRATOR"]["is_arbitrator"], True,
                      "判定用小写化，但入库的 id 保留原始大小写")
        self.assertIs(roles["trader"]["is_arbitrator"], False)
        self.assertIs(roles["boss"]["is_arbitrator"], True)

    def test_import_backs_up_the_previous_configuration(self):
        self._write({"roles": {"cio": {"prompt": "旧配置"}}})
        out = CM.import_council_config({"roles": {"cio": {"prompt": "新配置"}}})
        self.assertTrue(out["backup_file"].startswith("council_config_backup_"))
        backup = self.tmp / out["backup_file"]
        self.assertEqual(json.loads(backup.read_text())["roles"]["cio"]["prompt"], "旧配置")

    def test_import_returns_the_saved_shape(self):
        out = CM.import_council_config({"roles": {"cio": {"prompt": "p"}},
                                        "consensus_mode": "cross_examination",
                                        "timeout_seconds": 120})
        self.assertEqual(out["consensus_mode"], "cross_examination")
        self.assertEqual(out["timeout_seconds"], 120.0)
        self.assertEqual(out["cleared_model_bindings"], [])

    def test_import_clears_unregistered_model_bindings(self):
        self._start(mock.patch.object(CM, "validate_seat_model_bindings",
                                      return_value=["席位 cio 绑定的模型未登记"]))
        out = CM.import_council_config({"roles": {"cio": {"prompt": "p",
                                                          "model_id": "ghost"}}})
        self.assertEqual(out["cleared_model_bindings"],
                         [{"role_id": "cio", "model_id": "ghost"}])
        self.assertEqual(CM.load_council_config()["roles"]["cio"]["model_id"], "")

    def test_import_timeout_window_narrows_the_single_source_of_truth(self):
        """🐞 实测缺陷：审计 P2-13 声明 `clamp_council_timeout`(`[30,420]`) 是唯一事实源，
        但 import 里硬编码的是 `[10,300]` ⇒ 合法的 400 秒预算被悄悄压到 300。"""
        for given, expected in ((5, 30.0), (100, 100.0), (400, 300.0), (5000, 300.0)):
            with self.subTest(given=given):
                out = CM.import_council_config(
                    {"roles": {"cio": {"prompt": "p"}}, "timeout_seconds": given})
                self.assertEqual(out["timeout_seconds"], expected)
        self.assertEqual(CM.clamp_council_timeout.__wrapped__ if hasattr(
            CM.clamp_council_timeout, "__wrapped__") else 400.0, 400.0,
            "对照：策略侧允许 400（MAX=420）")

    def test_import_non_numeric_timeout_falls_back_to_the_default(self):
        out = CM.import_council_config({"roles": {"cio": {"prompt": "p"}},
                                        "timeout_seconds": "很久"})
        self.assertEqual(out["timeout_seconds"], policy.DEFAULT_COUNCIL_TIMEOUT)


class BackupTests(_Base):
    def test_no_file_means_no_backup(self):
        self.assertEqual(CM._backup_council_config(), "")

    def test_backup_copies_the_bytes_and_returns_the_name(self):
        self._write({"roles": {"cio": {"prompt": "p"}}})
        name = CM._backup_council_config()
        self.assertTrue(name.startswith("council_config_backup_"))
        self.assertEqual((self.tmp / name).read_bytes(), self.file.read_bytes())

    def test_unlink_failure_while_pruning_is_swallowed(self):
        """裁剪旧备份时 unlink 失败不能把备份流程带崩（只吞掉继续）。"""
        self._write({"roles": {}})
        for index in range(12):
            (self.tmp / f"council_config_backup_20260101_0000{index:02d}.json").write_text(
                "{}", encoding="utf-8")
        real_unlink = Path.unlink

        def _unlink(self_path, *args, **kwargs):
            if self_path.name.startswith("council_config_backup_"):
                raise OSError("只读文件系统")
            return real_unlink(self_path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", _unlink):
            name = CM._backup_council_config()
        self.assertTrue(name.startswith("council_config_backup_"))
        self.assertTrue((self.tmp / name).exists(), "裁剪失败不影响新备份落盘")

    def test_only_the_ten_newest_backups_survive(self):
        self._write({"roles": {}})
        for index in range(12):
            (self.tmp / f"council_config_backup_20260101_0000{index:02d}.json").write_text(
                "{}", encoding="utf-8")
        CM._backup_council_config()
        remaining = sorted(p.name for p in self.tmp.glob("council_config_backup_*.json"))
        self.assertEqual(len(remaining), 10)


class ThinShellTests(_Base):
    """B5 薄壳的验收条件：core 必须收到**调用时**的门面全局（patch 才生效）。"""

    def test_apply_preset_suite_passes_the_facade_callables(self):
        core = self._start(mock.patch.object(CM, "_core_apply_preset_suite",
                                             return_value={"ok": 1}))
        self.assertEqual(CM.apply_preset_suite("suite-a"), {"ok": 1})
        self.assertEqual(core.call_args[0],
                         (CM.load_council_config, CM.save_council_config, "suite-a"))

    def test_reset_role_template_passes_the_facade_callables(self):
        core = self._start(mock.patch.object(CM, "_core_reset_role_template",
                                             return_value={"ok": 2}))
        self.assertEqual(CM.reset_role_template("cio"), {"ok": 2})
        self.assertEqual(core.call_args[0],
                         (CM.load_council_config, CM.save_council_config, "cio"))

    def test_call_single_trader_passes_resolve_seat_model(self):
        core = self._start(mock.patch.object(CM, "_core__call_single_trader",
                                             return_value={"proposal": "x"}))
        out = CM._call_single_trader("cio", {"id": "cio"}, "prompt", "rules",
                                    timeout=20.0, runtime_context={"a": 1})
        self.assertEqual(out, {"proposal": "x"})
        self.assertEqual(core.call_args[0][:2], (CM.resolve_seat_model, "cio"))
        self.assertEqual(core.call_args[0][2:], ({"id": "cio"}, "prompt", "rules",
                                                 20.0, {"a": 1}))

    def test_call_single_trader_critique_passes_resolve_seat_model(self):
        core = self._start(mock.patch.object(CM, "_core__call_single_trader_critique",
                                             return_value={"critique": "y"}))
        out = CM._call_single_trader_critique("cio", {"id": "cio"}, "mine", "peers",
                                              "rules", timeout=15.0)
        self.assertEqual(out, {"critique": "y"})
        self.assertEqual(core.call_args[0][:2], (CM.resolve_seat_model, "cio"))
        self.assertEqual(core.call_args[0][2:], ({"id": "cio"}, "mine", "peers",
                                                 "rules", 15.0, None))

    def test_execute_council_debate_passes_every_facade_seam(self):
        core = self._start(mock.patch.object(CM, "_core_execute_council_debate",
                                             return_value=({"d": 1}, {"m": 2})))
        out = CM.execute_council_debate("market", "system", timeout=240.0,
                                       runtime_context={"r": 1})
        self.assertEqual(out, ({"d": 1}, {"m": 2}))
        self.assertEqual(core.call_args[0], (
            CM.load_council_config, CM.resolve_seat_model, CM._call_single_trader,
            CM._call_single_trader_critique, "market", "system", 240.0, {"r": 1}))

    def test_defaults_are_applied_by_the_debate_shells(self):
        core = self._start(mock.patch.object(CM, "_core__call_single_trader"))
        CM._call_single_trader("cio", {}, "p", "r")
        self.assertEqual(core.call_args[0][-2:], (20.0, None))

        critique = self._start(mock.patch.object(CM, "_core__call_single_trader_critique"))
        CM._call_single_trader_critique("cio", {}, "m", "peers", "r")
        self.assertEqual(critique.call_args[0][-2:], (15.0, None))

        debate_core = self._start(mock.patch.object(CM, "_core_execute_council_debate"))
        CM.execute_council_debate("m", "s")
        self.assertEqual(debate_core.call_args[0][-2:], (240.0, None))


if __name__ == "__main__":
    unittest.main()

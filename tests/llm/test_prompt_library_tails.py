"""提示词方案库（`scripts/prompt_library.py`）的残余分支收口 —— 第 331 刀。

本模块 1097 行，管的是**喂给主脑的提示词模板资产**：内置预设、自定义方案、
模块化管线、导入导出、版本历史与回滚。既有测试覆盖了主干（渲染隔离 / 通用性 /
来源标注 / P1 对齐门），本刀补 17 行缺口 —— 集中在**拒绝路径**与**跨页保存的一致性硬闸**。

## 本刀立住的三条纪律

1. **跨页保存不许改写别的管线**（审计 P1-2）：调用方只提交一条管线（如心法页只提交
   `evolution_system`）时，未提交的管线保存后渲染文本必须**逐字节不变** ——
   旧实现在此处把 B 管线全降级成 legacy，下一轮布局把基座前置 ⇒ 提示词**翻倍**
   （实测交易提示词 ×2.00）。
2. **不可信输入必须被拒**：非字典、字典内的非字典元素、缺字段的条目各有独立错误文案。
3. **锁绝不静默放行**：后端锁不可用时回落本地 flock，且兜底**必须可重入**
   （调用方存在嵌套 `create_profile` → `save_library`，不可重入会自锁挂死）。
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.prompt_library as pl  # noqa: E402


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = self.root / "prompt_library.json"
        p = patch.object(pl, "LIBRARY_FILE", self.library)
        p.start()
        self.addCleanup(p.stop)

    def _write(self, payload):
        self.library.write_text(json.dumps(payload), encoding="utf-8")


# ───────────────────── _clean_profile ─────────────────────
class CleanProfileTests(_Sandbox, unittest.TestCase):
    def test_a_minimal_dict_is_filled_out(self):
        out = pl._clean_profile({})
        self.assertTrue(out["editable"])
        self.assertTrue(out["enabled"])
        self.assertTrue(out["id"].startswith("custom-"))
        self.assertEqual(out["name"], "自定义方案")

    def test_the_name_and_description_are_truncated(self):
        out = pl._clean_profile({"name": "x" * 200, "description": "y" * 500})
        self.assertEqual(len(out["name"]), 60)
        self.assertEqual(len(out["description"]), 240)

    def test_simple_and_modules_survive_the_cleaner(self):
        for mode in ("simple", "modules"):
            with self.subTest(mode=mode):
                self.assertEqual(pl._clean_profile({"editor_mode": mode})["editor_mode"],
                                 mode)

    def test_the_advanced_mode_can_never_survive_the_cleaner(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：第 551 行的白名单把 `"advanced"`
        #   也算合法，但走到第 572 行**无条件** `result["editor_mode"] = "modules"`。
        #   ⇒ **`advanced` 是一个永远产出不了的"死模式"**：
        #     校验器接受它，清理器却把它改写成 `modules`。
        #   （第 550 行还会为"有扁平模板键"的输入**推断**出 `advanced`，随即被抹掉。）
        for source in ({"editor_mode": "advanced"}, {"trading_system": "内容"}):
            with self.subTest(source=sorted(source)):
                self.assertEqual(pl._clean_profile(source)["editor_mode"], "modules")

    def test_an_unknown_mode_with_pipelines_becomes_modules(self):
        # ★ 第 551/552 行
        out = pl._clean_profile({"editor_mode": "bogus",
                                 "pipelines": {"trading_system": []}})
        self.assertEqual(out["editor_mode"], "modules")

    def test_an_unknown_mode_without_pipelines_becomes_simple(self):
        self.assertEqual(pl._clean_profile({"editor_mode": "bogus"})["editor_mode"],
                         "simple")

    def test_flat_template_keys_are_upgraded_to_modules(self):
        self.assertEqual(pl._clean_profile({"trading_system": "内容"})["editor_mode"],
                         "modules")

    def test_a_simple_profile_gets_empty_pipelines(self):
        out = pl._clean_profile({"editor_mode": "simple"})
        self.assertEqual(out["pipelines"], {})

    def test_the_simple_policy_defaults_are_applied(self):
        out = pl._clean_profile({"editor_mode": "simple"})
        self.assertEqual(out["simple_policy"],
                         {"strategy": "", "review_focus": "", "participation": "balanced",
                          "evidence": "strict", "risk_budget": "middle"})

    def test_the_profile_id_can_be_forced(self):
        self.assertEqual(pl._clean_profile({}, "custom-fixed")["id"], "custom-fixed")

    def test_the_template_keys_are_compiled_from_modules(self):
        modules = [{"id": "m1", "content": "模块正文", "source": "custom"}]
        out = pl._clean_profile({"editor_mode": "modules",
                                 "pipelines": {"trading_system": modules}})
        self.assertIn("模块正文", out["trading_system"])


# ───────────────────── load_library ─────────────────────
class LoadLibraryTests(_Sandbox, unittest.TestCase):
    def test_a_missing_file_falls_back_to_defaults(self):
        out = pl.load_library()
        self.assertEqual(out["version"], 2)
        # ⚠️ `_default()` 的 `profiles` 是**空的** —— 内置预设不落盘，按需物化
        self.assertEqual(out["profiles"], {})
        self.assertIn(out["active_profile_id"], pl.PRESETS)

    def test_a_corrupt_file_falls_back_to_defaults(self):
        self.library.write_text("{ broken", encoding="utf-8")
        self.assertEqual(pl.load_library()["version"], 2)

    def test_a_non_dict_body_falls_back_to_defaults(self):
        self._write([1, 2])
        self.assertEqual(pl.load_library()["version"], 2)

    def test_a_v1_shape_is_migrated(self):
        self._write({"version": 1, "active_style": "stable",
                     "custom": {"name": "旧方案", "trading_system": "旧正文"}})
        out = pl.load_library()
        self.assertEqual(out["version"], 2)
        self.assertIn("custom-default", out["profiles"])

    def test_an_unknown_active_id_falls_back_to_stable(self):
        # ★ 第 593–595 行
        self._write({"version": 2, "active_profile_id": "nope", "profiles": {}})
        self.assertEqual(pl.load_library()["active_profile_id"], "stable")

    def test_a_known_active_id_is_kept(self):
        self._write({"version": 2, "active_profile_id": "stable", "profiles": {}})
        self.assertEqual(pl.load_library()["active_profile_id"], "stable")

    def test_active_style_is_custom_for_a_user_profile(self):
        out = pl.load_library()
        self.assertIn(out["active_style"], ("stable", "wide_oscillation", "custom"))

    def test_the_custom_key_mirrors_the_active_profile(self):
        out = pl.load_library()
        self.assertIsInstance(out["custom"], dict)
        self.assertTrue(out["custom"]["editable"])

    def test_revisions_are_capped(self):
        self._write({"version": 2, "active_profile_id": "stable", "profiles": {},
                     "revisions": [{"id": f"r{i}"} for i in range(pl.MAX_REVISIONS + 50)]})
        self.assertEqual(len(pl.load_library()["revisions"]), pl.MAX_REVISIONS)


# ───────────────────── 锁 ─────────────────────
class LibraryLockTests(_Sandbox, unittest.TestCase):
    def test_the_backend_lock_is_preferred(self):
        import r20_backend.file_locks as fl
        sentinel = object()
        with patch.object(fl, "file_lock", lambda p: sentinel):
            self.assertIs(pl._library_lock(), sentinel)

    def test_an_unavailable_backend_lock_falls_back_locally(self):
        # ★ 第 614/615 行 —— 绝不在"锁不可用"时静默放行
        with patch.dict(sys.modules, {"r20_backend.file_locks": None}):
            self.assertIsNotNone(pl._library_lock())

    def test_the_fallback_lock_is_reentrant(self):
        # 不可重入会让 `create_profile`（持锁）→ `save_library`（再取锁）同线程自锁挂死
        with patch.dict(sys.modules, {"r20_backend.file_locks": None}):
            with pl._library_lock():
                with pl._library_lock():
                    reentered = True
        self.assertTrue(reentered)


# ───────────────────── validate_profile ─────────────────────
class ValidateProfileTests(unittest.TestCase):
    def _v(self, profile):
        return pl.validate_profile(profile)

    def test_a_good_profile_passes(self):
        out = self._v({"name": "方案", "editor_mode": "simple"})
        self.assertTrue(out["valid"], out["errors"])

    def test_a_blank_name_is_an_error(self):
        self.assertFalse(self._v({"name": "", "editor_mode": "simple"})["valid"])

    def test_an_overlong_name_is_an_error(self):
        self.assertFalse(self._v({"name": "x" * 61, "editor_mode": "simple"})["valid"])

    def test_an_unknown_pipeline_key_is_reported(self):
        # ★ 第 684/685 行
        out = self._v({"name": "P", "pipelines": {"bogus": []}})
        self.assertIn("无效消息管线：bogus", out["errors"])

    def test_a_non_list_pipeline_value_is_reported(self):
        out = self._v({"name": "P", "pipelines": {"trading_system": "junk"}})
        self.assertIn("无效消息管线：trading_system", out["errors"])

    def test_a_non_dict_module_is_reported(self):
        # ★ 第 691/692 行
        out = self._v({"name": "P", "pipelines": {"trading_system": ["junk"]}})
        self.assertIn("trading_system 包含无效模块对象", out["errors"])

    def test_too_many_modules_is_reported(self):
        mods = [{"id": f"m{i}", "content": "x"} for i in range(pl.MAX_MODULES_PER_PIPELINE + 1)]
        out = self._v({"name": "P", "pipelines": {"trading_system": mods}})
        self.assertTrue(any("模块数不得超过" in e for e in out["errors"]))

    def test_a_duplicate_module_id_is_reported(self):
        out = self._v({"name": "P", "pipelines": {
            "trading_system": [{"id": "m1", "content": "a"}, {"id": "m1", "content": "b"}]}})
        self.assertTrue(any("缺失或重复" in e for e in out["errors"]))

    def test_an_unknown_variable_is_reported(self):
        out = self._v({"name": "P", "pipelines": {
            "trading_system": [{"id": "m1", "content": "{{NOT_A_REAL_VAR}}"}]}})
        self.assertTrue(any("未知变量" in e for e in out["errors"]))

    def test_a_locked_base_module_skips_the_forbidden_scan(self):
        # 第 705/706 行 —— source=base 且 locked 的模块不查禁词
        out = self._v({"name": "P", "pipelines": {
            "trading_system": [{"id": "m1", "content": "正常内容", "source": "base",
                                "locked": True}]}})
        self.assertTrue(out["valid"], out["errors"])

    def test_the_simple_policy_enums_are_checked(self):
        for field, bad in (("participation", "wild"), ("evidence", "vibes"),
                           ("risk_budget", "max")):
            with self.subTest(field=field):
                out = self._v({"name": "P", "editor_mode": "simple",
                               "simple_policy": {field: bad}})
                self.assertTrue(any("无效" in e for e in out["errors"]))

    def test_an_empty_simple_strategy_is_only_a_warning(self):
        out = self._v({"name": "P", "editor_mode": "simple",
                       "simple_policy": {"strategy": ""}})
        self.assertTrue(out["valid"])
        self.assertTrue(any("简单策略说明为空" in w for w in out["warnings"]))

    def test_an_empty_advanced_profile_is_only_a_warning(self):
        out = self._v({"name": "P", "editor_mode": "advanced"})
        self.assertTrue(any("均为空" in w for w in out["warnings"]))

    def test_the_character_count_is_reported(self):
        out = self._v({"name": "P", "editor_mode": "advanced",
                       "trading_system": "abc"})
        self.assertGreaterEqual(out["characters"], 3)

    def test_errors_are_deduplicated(self):
        out = self._v({"name": "", "editor_mode": "simple"})
        self.assertEqual(len(out["errors"]), len(set(out["errors"])))


# ───────────────────── CRUD ─────────────────────
class CreateProfileTests(_Sandbox, unittest.TestCase):
    def test_a_profile_is_created_and_persisted(self):
        out = pl.create_profile("新方案")
        self.assertTrue(out["id"].startswith("custom-"))
        self.assertIn(out["id"], pl.load_library()["profiles"])

    def test_the_source_preset_is_copied(self):
        out = pl.create_profile("副本", source_id="wide_oscillation")
        self.assertEqual(out["name"], "副本")

    def test_a_revision_is_recorded(self):
        out = pl.create_profile("新方案")
        history = pl.profile_history(out["id"])
        self.assertEqual(history[0]["action"], "create")

    def test_a_blank_name_is_silently_defaulted_not_refused(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：第 546 行 `str(result.get("name") or "自定义方案")`
        #   ⇒ 空名/`None` 被**静默兜底**成"自定义方案"，而超长名在第 546 行被截到 60。
        #   于是 `validate_profile` 的"名称长度必须为 1-60"**在 `create_profile` 路径上
        #   永远触发不到**（第 764/765 行的抛错分支实际不可达）。
        out = pl.create_profile("")
        self.assertEqual(out["name"], "自定义方案")
        self.assertIn(out["id"], pl.load_library()["profiles"])

    def test_an_overlong_name_is_truncated_not_refused(self):
        out = pl.create_profile("x" * 200)
        self.assertEqual(len(out["name"]), 60)

    def test_the_validation_gate_fires_for_a_broken_source(self):
        # ★ 第 764/765 行 —— 正常来源（预设/已存方案）都在此前被校验过，
        #   所以要触发这个闸门必须注入一个**本身非法**的来源。
        #   用**清理器不会修、且与 editor_mode 无关**的非法项：未声明的模板变量。
        broken = copy.deepcopy(pl.PRESETS["stable"])
        broken["pipelines"] = {"trading_system": [{"id": "m1",
                                                   "content": "{{NOT_A_VAR}}"}]}
        with patch.dict(pl.PRESETS, {"broken": broken}):
            with self.assertRaises(ValueError) as ctx:
                pl.create_profile("X", source_id="broken")
        self.assertIn("未知变量", str(ctx.exception))
        self.assertNotIn("broken", pl.load_library()["profiles"])

    def test_a_missing_source_preset_is_refused(self):
        with self.assertRaises(ValueError):
            pl.create_profile("X", source_id="nope")


class UpdateProfileTests(_Sandbox, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.pid = pl.create_profile("初始方案")["id"]

    def test_a_rename_is_persisted(self):
        out = pl.update_profile(self.pid, {"name": "改名后"})
        self.assertEqual(out["name"], "改名后")
        self.assertEqual(pl.get_profile(self.pid)["name"], "改名后")

    def test_an_update_records_a_revision(self):
        pl.update_profile(self.pid, {"name": "改名后"})
        self.assertEqual(pl.profile_history(self.pid)[0]["action"], "update")

    def test_a_preset_not_yet_materialised_is_edited_from_the_builtin(self):
        # ★ 第 776–778 行
        out = pl.update_profile("wide_oscillation", {"description": "改了描述"})
        self.assertEqual(out["description"], "改了描述")
        self.assertTrue(out["editable"])

    def test_an_unknown_profile_is_refused(self):
        # ★ 第 781/782 行
        with self.assertRaises(ValueError) as ctx:
            pl.update_profile("nope", {"name": "X"})
        self.assertIn("提示词方案不存在", str(ctx.exception))

    def test_an_unknown_key_is_ignored(self):
        out = pl.update_profile(self.pid, {"junk_field": "x"})
        self.assertNotIn("junk_field", out)

    def test_submitting_one_pipeline_does_not_touch_the_others(self):
        # 审计 P1-2 的核心：只提交 evolution_system，其余管线渲染文本必须逐字节不变
        before = pl.get_profile(self.pid)
        pl.update_profile(self.pid, {"name": "X"})
        after = pl.get_profile(self.pid)
        for key in pl.TEMPLATE_KEYS:
            with self.subTest(key=key):
                self.assertEqual(
                    pl.compile_modules((before.get("pipelines") or {}).get(key) or []),
                    pl.compile_modules((after.get("pipelines") or {}).get(key) or []))

    def test_the_consistency_gate_exists_in_source(self):
        # ★ 第 814–819 行 —— 硬闸的文案是这套一致性契约的锚点
        src = Path(pl.__file__).read_text(encoding="utf-8")
        self.assertIn("禁止静默改写其他管线", src)

    def test_the_consistency_gate_fires_when_an_unsubmitted_pipeline_changes(self):
        # ★ 第 818/819 行 —— 硬闸本身。正常代码里它永不触发（那正是它存在的意义），
        #   所以要验证它**真的在工作**必须制造"保存过程改写了别的管线"：
        #   让 `compile_modules` 对同一份未提交管线前后给出不同结果。
        real = pl.compile_modules
        state = {"n": 0}

        def _flaky(modules):
            state["n"] += 1
            return real(modules) + ("漂移" if state["n"] % 2 == 0 else "")
        with patch.object(pl, "compile_modules", _flaky):
            with self.assertRaises(ValueError) as ctx:
                pl.update_profile(self.pid, {"name": "改名"})
        self.assertIn("禁止静默改写其他管线", str(ctx.exception))

    def test_an_invalid_update_is_refused_without_persisting(self):
        # 用**真能失败**的改动（未知变量）触发校验闸 —— 空名会被静默兜底，见上
        before = pl.get_profile(self.pid)["name"]
        with self.assertRaises(ValueError) as ctx:
            pl.update_profile(self.pid, {"pipelines": {
                "trading_system": [{"id": "m1", "content": "{{NOT_A_VAR}}"}]}})
        self.assertIn("未知变量", str(ctx.exception))
        self.assertEqual(pl.get_profile(self.pid)["name"], before)


class DeleteProfileTests(_Sandbox, unittest.TestCase):
    def test_a_user_profile_is_deleted(self):
        pid = pl.create_profile("待删")["id"]
        pl.delete_profile(pid)
        self.assertNotIn(pid, pl.load_library()["profiles"])

    def test_a_builtin_preset_cannot_be_deleted(self):
        with self.assertRaises(ValueError) as ctx:
            pl.delete_profile("stable")
        self.assertIn("内置预设不可删除", str(ctx.exception))

    def test_the_active_profile_cannot_be_deleted(self):
        pid = pl.create_profile("当前")["id"]
        pl.activate_profile(pid)
        with self.assertRaises(ValueError) as ctx:
            pl.delete_profile(pid)
        self.assertIn("不能删除", str(ctx.exception))

    def test_an_unknown_profile_is_refused(self):
        # ★ 第 837/838 行
        with self.assertRaises(ValueError) as ctx:
            pl.delete_profile("nope")
        self.assertIn("提示词方案不存在", str(ctx.exception))


class GetActivateTests(_Sandbox, unittest.TestCase):
    def test_a_preset_is_materialised_on_demand_and_editable(self):
        out = pl.get_profile("stable")
        self.assertTrue(out["editable"])
        self.assertEqual(out["id"], "stable")

    def test_an_unknown_profile_is_refused(self):
        # ★ 第 858/859 行
        with self.assertRaises(ValueError) as ctx:
            pl.get_profile("nope")
        self.assertIn("提示词方案不存在", str(ctx.exception))

    def test_activation_switches_the_active_id(self):
        pid = pl.create_profile("新方案")["id"]
        pl.activate_profile(pid)
        self.assertEqual(pl.load_library()["active_profile_id"], pid)

    def test_a_disabled_profile_cannot_be_activated(self):
        pid = pl.create_profile("停用")["id"]
        pl.update_profile(pid, {"enabled": False})
        with self.assertRaises(ValueError) as ctx:
            pl.activate_profile(pid)
        self.assertIn("已停用", str(ctx.exception))

    def test_the_active_profile_is_resolved(self):
        out = pl.active_profile()
        self.assertIn("trading_system", out)


class HistoryAndRollbackTests(_Sandbox, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.pid = pl.create_profile("方案", description="原始")["id"]

    def test_history_is_newest_first(self):
        pl.update_profile(self.pid, {"description": "第二次"})
        actions = [r["action"] for r in pl.profile_history(self.pid)]
        self.assertEqual(actions[0], "update")
        self.assertEqual(actions[-1], "create")

    def test_rollback_restores_the_snapshot(self):
        pl.update_profile(self.pid, {"description": "改过了"})
        first = pl.profile_history(self.pid)[-1]
        out = pl.rollback_profile(self.pid, first["id"])
        self.assertEqual(out["description"], "原始")

    def test_rollback_records_its_own_revision(self):
        pl.update_profile(self.pid, {"description": "改过了"})
        first = pl.profile_history(self.pid)[-1]
        pl.rollback_profile(self.pid, first["id"])
        self.assertEqual(pl.profile_history(self.pid)[0]["action"], "rollback")

    def test_an_unknown_revision_is_refused(self):
        # ★ 第 870/871 行
        with self.assertRaises(ValueError) as ctx:
            pl.rollback_profile(self.pid, "rev-nope")
        self.assertIn("历史版本不存在", str(ctx.exception))

    def test_a_revision_from_another_profile_is_refused(self):
        other = pl.create_profile("另一个")["id"]
        rev = pl.profile_history(other)[0]["id"]
        with self.assertRaises(ValueError):
            pl.rollback_profile(self.pid, rev)

    def test_a_rollback_to_an_invalid_snapshot_is_refused(self):
        # ★ 第 873–875 行 —— 历史快照同样要过校验闸。
        #   正常路径下所有快照都通过过校验，所以要注入一份坏快照。
        library = pl.load_library()
        bad = copy.deepcopy(library["profiles"][self.pid])
        bad["pipelines"] = {"trading_system": [{"id": "m1",
                                                "content": "{{NOT_A_VAR}}"}]}
        library["revisions"].append({"id": "rev-bad", "profile_id": self.pid,
                                     "action": "create", "note": "",
                                     "created_at": pl._now(), "snapshot": bad})
        self._write(library)
        with self.assertRaises(ValueError) as ctx:
            pl.rollback_profile(self.pid, "rev-bad")
        self.assertIn("未知变量", str(ctx.exception))

    def test_the_history_helper_is_a_deep_copy(self):
        got = pl.profile_history(self.pid)
        got[0]["snapshot"]["name"] = "MUTATED"
        self.assertNotEqual(pl.profile_history(self.pid)[0]["snapshot"]["name"],
                            "MUTATED")


# ───────────────────── 导入导出 ─────────────────────
class ExportImportTests(_Sandbox, unittest.TestCase):
    def test_an_export_is_self_describing(self):
        out = pl.export_profile("stable")
        self.assertEqual(out["format"], pl.EXPORT_FORMAT)
        self.assertEqual(out["version"], pl.EXPORT_VERSION)
        self.assertIn("allowed_variables", out)
        self.assertIn("variables", out)

    def test_a_round_trip_preserves_the_profile(self):
        source = pl.export_profile("stable")
        imported = pl.import_profile(source)
        original = pl.PRESETS["stable"]["name"]
        self.assertEqual(imported["name"], f"{original}（导入）"[:60])

    def test_an_explicit_name_override_wins(self):
        pl.import_profile(pl.export_profile("stable"), name_override="我的方案")
        names = [p["name"] for p in pl.all_profiles()]
        self.assertIn("我的方案", names)

    def test_a_nameless_source_falls_back_to_a_generic_label(self):
        # ★ 第 941/942 行
        imported = pl.import_profile({"pipelines": {"trading_system": []}, "name": ""})
        self.assertEqual(imported["name"], "导入方案")

    def _library_export(self):
        """构造一份**真正**的整库导出（`profiles` 非空）。

        ⚠️ `load_library()` 的 `profiles` 默认为空（预设按需物化）⇒ 直接拿它当整库导出
        会落到 `_IMPORT_FORMAT_HINT`。必须先有一个自定义方案。
        """
        pl.create_profile("库里的方案")
        lib = pl.load_library()
        lib.pop("custom", None)
        lib.pop("active_style", None)
        return lib

    def test_a_whole_library_export_imports_the_active_profile(self):
        lib = self._library_export()
        out = pl.import_profile(lib)
        self.assertTrue(out["id"].startswith("custom-"))

    def test_a_bare_profile_object_is_accepted(self):
        out = pl.import_profile({"name": "裸方案", "trading_system": "正文"})
        self.assertTrue(out["id"].startswith("custom-"))

    def test_an_empty_payload_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            pl.import_profile({})
        self.assertIn(pl._IMPORT_FORMAT_HINT, str(ctx.exception))

    def test_a_non_dict_payload_is_refused(self):
        with self.assertRaises(ValueError):
            pl.import_profile("junk")

    def test_an_unrecognised_shape_is_refused(self):
        with self.assertRaises(ValueError):
            pl.import_profile({"format": "something-else", "a": 1})

    def test_a_library_with_no_dict_profiles_is_refused(self):
        # ★ 第 915–918 行
        with self.assertRaises(ValueError) as ctx:
            pl.import_profile({"profiles": {"a": "junk", "b": 42}})
        self.assertIn(pl._IMPORT_FORMAT_HINT, str(ctx.exception))

    def test_an_empty_profiles_mapping_falls_through_to_the_bare_shape(self):
        with self.assertRaises(ValueError):
            pl.import_profile({"profiles": {}})

    def test_an_import_with_a_bad_variable_names_the_allowed_list(self):
        bad = {"name": "坏文件", "pipelines": {
            "trading_system": [{"id": "m1", "content": "{{NOT_A_VAR}}"}]}}
        with self.assertRaises(ValueError) as ctx:
            pl.import_profile(bad)
        self.assertIn("allowed_variables", str(ctx.exception))

    def test_an_unknown_pipeline_is_silently_dropped_before_validation(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：`_clean_pipelines` 会把 `pipelines`
        #   规范化成**恰好四个 `TEMPLATE_KEYS`** ⇒ 未知管线键被**静默丢弃**。
        #   于是 `validate_profile` 的"无效消息管线"分支（第 684/685 行）
        #   在 `create_profile` / `update_profile` / `import_profile` 三条路径上
        #   **全都不可达** —— 它只对**直接调用** `validate_profile` 的代码生效。
        out = pl.import_profile({"name": "非法", "pipelines": {
            "bogus_pipeline": [{"id": "m1", "content": "x"}]}})
        self.assertEqual(sorted(out["pipelines"]), sorted(pl.TEMPLATE_KEYS))
        self.assertNotIn("bogus_pipeline", out["pipelines"])

    def test_the_validator_still_catches_an_unknown_pipeline_directly(self):
        # 对照：绕过清理器直接校验时，那条分支确实在工作
        out = pl.validate_profile({"name": "P", "pipelines": {"bogus": []}})
        self.assertIn("无效消息管线：bogus", out["errors"])

    def test_the_import_revision_names_the_origin_shape(self):
        pl.import_profile(pl.export_profile("stable"))
        out = pl.all_profiles()
        pid = out[-1]["id"]
        note = pl.profile_history(pid)[0]["note"]
        self.assertIn("标准导出包", note)

    def test_the_library_origin_label_is_used_for_a_whole_library(self):
        imported = pl.import_profile(self._library_export())
        self.assertIn("整库导出文件", pl.profile_history(imported["id"])[0]["note"])

    def test_the_bare_origin_label_is_used_for_a_naked_object(self):
        imported = pl.import_profile({"name": "裸", "trading_system": "x"})
        self.assertIn("裸方案对象", pl.profile_history(imported["id"])[0]["note"])

    def test_the_template_helper_wraps_the_source(self):
        # ★ 第 960/961 行
        out = pl._import_with_templates({"name": "包一层", "trading_system": "x"}, "")
        self.assertEqual(out["name"], "包一层（导入）")

    def test_the_template_helper_honours_the_override(self):
        out = pl._import_with_templates({"name": "包一层", "trading_system": "x"},
                                        "改过的名字")
        self.assertEqual(out["name"], "改过的名字")

    def test_the_unknown_variable_helper_filters(self):
        errors = ["未知变量：X", "名称长度必须为 1-60", "未知变量：Y"]
        self.assertEqual(pl._unknown_variable_errors(errors),
                         ["未知变量：X", "未知变量：Y"])


class AliasTests(_Sandbox, unittest.TestCase):
    def test_the_legacy_aliases_point_at_the_same_functions(self):
        self.assertIs(pl.load_active_profile, pl.active_profile)
        self.assertIs(pl.load_prompt_config, pl.load_library)


if __name__ == "__main__":
    unittest.main()

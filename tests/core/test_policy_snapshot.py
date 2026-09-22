"""策略快照门面：**ROOT 必须调用时解析、薄壳必须传"门面自己的"包装、整包标识去重**（第二百八十六刀，开新面 policy_snapshot.py）。

先打印整个文件（295 行）再动笔。B6 拆分后本模块只剩**门面薄壳** + 一段索引重建 + 归档：
真正的指纹/规范化/整包标识在 `r20_backend/policy/*`。

| 语义 | 口径 |
|---|---|
| ★ **ROOT 一律"调用时"解析再注入核心** | 文件头长注释点名的地雷：若核心在**导入期**绑定 ROOT，`patch.object(policy_snapshot, "ROOT", 沙箱根)` 会**静默失效**并回落到真实项目根 —— 回滚会**写生产 `data/`**。本刀用"导入后再改 ROOT，断言新值被传进去"专测钉死 |
| ★ **薄壳必须传"门面自己的"包装函数** | B6 第二刀 b：`restore/delete/_resolve` 传的是 `policy_snapshot.resolve_archive_file` 等**本模块**的名字，而不是核心里的实现 ⇒ 测试 patch 门面名才生效（传核心名 = patch 打空）|
| ★ **整包标识（审计 P0-3）** | `policy_hash` 只覆盖 4 个单元，归档包实际装 6 个（另含 `risk_config`/`venue_routing`）⇒ **仅风控/路由不同的两个版本会同名互相覆盖**，回滚校验也对这两块失明。文件命名与去重改用 `package_identity` 算出的整包标识 |
| ★ **去重键兼容历史条目** | 有 `package_hash` 的按整包标识判等；**没有**的历史条目退回 `policy_hash` 判等（保持旧行为，不误删老归档）|
| ★ **重建索引时字段逐级兜底** | 归档文件可能被手改/截断：`policy_hash` 退回文件名、`policy_version` 退回 `unknown@<hash>`、`name` 退回 `策略归档-<hash>`、`archived_at` 退回文件 mtime（+08:00）|
| ★ **单个坏归档不能带崩整个列表** | 解析失败只 `logger.warning` 并跳过该条（工作台要能列出"还能读的那些"）|
| ★ **索引按归档时间倒序** | 最新在前 |
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from r20_backend import policy_snapshot as PS

_BJ = timezone(timedelta(hours=8))
_SANDBOX_ROOT = Path("/tmp/r20-facade-sandbox-root")


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a_dir = Path(self.tmp.name) / "archive"


class RootIsResolvedAtCallTimeTests(_Base):
    """★ 本模块最重要的一条：ROOT 必须是**调用时**从门面全局取。"""

    def test_prompt_profile(self):
        core = self._start(mock.patch.object(PS, "_core_extract_prompt_profile_fingerprint",
                                             return_value={"ok": 1}))
        self._start(mock.patch.object(PS, "ROOT", _SANDBOX_ROOT))
        self.assertEqual(PS.extract_prompt_profile_fingerprint({"p": 1}, Path("/x")), {"ok": 1})
        core.assert_called_once_with(_SANDBOX_ROOT, {"p": 1}, Path("/x"))

    def test_evolution_mind(self):
        core = self._start(mock.patch.object(PS, "_core_extract_evolution_mind_fingerprint",
                                             return_value={}))
        self._start(mock.patch.object(PS, "ROOT", _SANDBOX_ROOT))
        PS.extract_evolution_mind_fingerprint({"m": 1})
        core.assert_called_once_with(_SANDBOX_ROOT, {"m": 1}, None)

    def test_interceptors(self):
        core = self._start(mock.patch.object(PS, "_core_extract_interceptors_fingerprint",
                                             return_value={}))
        self._start(mock.patch.object(PS, "ROOT", _SANDBOX_ROOT))
        PS.extract_interceptors_fingerprint([{"a": 1}], Path("/p"), Path("/r"))
        core.assert_called_once_with(_SANDBOX_ROOT, [{"a": 1}], Path("/p"), Path("/r"))

    def test_generate_policy_snapshot_positional_order(self):
        core = self._start(mock.patch.object(PS, "_core_generate_policy_snapshot",
                                             return_value={}))
        self._start(mock.patch.object(PS, "ROOT", _SANDBOX_ROOT))
        PS.generate_policy_snapshot(Path("/r"), {"p": 1}, {"m": 1}, [{"i": 1}], {"c": 1},
                                    Path("/pl"), "9.9.9")
        core.assert_called_once_with(_SANDBOX_ROOT, Path("/r"), {"p": 1}, {"m": 1},
                                     [{"i": 1}], {"c": 1}, Path("/pl"), "9.9.9")

    def test_generate_policy_snapshot_defaults(self):
        core = self._start(mock.patch.object(PS, "_core_generate_policy_snapshot",
                                             return_value={}))
        self._start(mock.patch.object(PS, "ROOT", _SANDBOX_ROOT))
        PS.generate_policy_snapshot()
        core.assert_called_once_with(_SANDBOX_ROOT, None, None, None, None, None, None,
                                     PS.DEFAULT_BASE_VERSION)

    def test_current_snapshot(self):
        core = self._start(mock.patch.object(PS, "_core_get_current_policy_snapshot",
                                             return_value={}))
        self._start(mock.patch.object(PS, "ROOT", _SANDBOX_ROOT))
        PS.get_current_policy_snapshot()
        core.assert_called_once_with(_SANDBOX_ROOT)

    def test_capture_package(self):
        core = self._start(mock.patch.object(PS, "_core_capture_full_strategy_package",
                                             return_value={}))
        self._start(mock.patch.object(PS, "ROOT", _SANDBOX_ROOT))
        PS.capture_full_strategy_package(Path("/r"))
        core.assert_called_once_with(_SANDBOX_ROOT, Path("/r"))

    def test_a_root_changed_after_import_is_honoured(self):
        """先调到假根、再换一个，两次都必须用**当次**的值（不能缓存第一次的）。"""
        core = self._start(mock.patch.object(PS, "_core_get_current_policy_snapshot",
                                             return_value={}))
        with mock.patch.object(PS, "ROOT", Path("/root-a")):
            PS.get_current_policy_snapshot()
        with mock.patch.object(PS, "ROOT", Path("/root-b")):
            PS.get_current_policy_snapshot()
        self.assertEqual([c[0][0] for c in core.call_args_list],
                         [Path("/root-a"), Path("/root-b")])


class FacadePassesItsOwnWrappersTests(_Base):
    """★ B6 第二刀 b：薄壳必须把**门面自己的**包装传进核心，否则 patch 门面名会打空。"""

    def test_restore_passes_the_local_resolver_and_root(self):
        core = self._start(mock.patch.object(PS, "_core_restore_archived_policy",
                                             return_value={}))
        self._start(mock.patch.object(PS, "ROOT", _SANDBOX_ROOT))
        PS.restore_archived_policy("abcdef", self.a_dir, Path("/r"))
        core.assert_called_once_with(PS._resolve_archive_file, _SANDBOX_ROOT,
                                     "abcdef", self.a_dir, Path("/r"))

    def test_a_patched_local_resolver_is_actually_used(self):
        core = self._start(mock.patch.object(PS, "_core_restore_archived_policy",
                                             return_value={}))
        replacement = mock.Mock()
        with mock.patch.object(PS, "_resolve_archive_file", replacement):
            PS.restore_archived_policy("abcdef")
        self.assertIs(core.call_args[0][0], replacement,
                      "传核心实现的话，这里 patch 就完全打空")

    def test_delete_passes_both_local_wrappers(self):
        core = self._start(mock.patch.object(PS, "_core_delete_archived_policy",
                                             return_value={}))
        PS.delete_archived_policy("abcdef", self.a_dir)
        core.assert_called_once_with(PS.load_archive_index, PS._resolve_archive_file,
                                     "abcdef", self.a_dir)

    def test_resolve_passes_the_local_index_loader(self):
        core = self._start(mock.patch.object(PS, "_core__resolve_archive_file",
                                             return_value=Path("/f.json")))
        PS._resolve_archive_file(self.a_dir, "abcdef")
        core.assert_called_once_with(PS.load_archive_index, self.a_dir, "abcdef")

    def test_load_index_passes_the_local_rebuilder(self):
        core = self._start(mock.patch.object(PS, "_core_load_archive_index",
                                             return_value=[]))
        PS.load_archive_index(self.a_dir)
        core.assert_called_once_with(PS._rebuild_index_from_archives, self.a_dir)

    def test_a_patched_index_loader_is_actually_used(self):
        """回滚/删除链路的 patch 之所以生效，全靠这一层"传门面名"。"""
        core = self._start(mock.patch.object(PS, "_core__resolve_archive_file",
                                             return_value=Path("/f.json")))
        replacement = mock.Mock(return_value=[])
        with mock.patch.object(PS, "load_archive_index", replacement):
            PS._resolve_archive_file(self.a_dir, "x")
        self.assertIs(core.call_args[0][0], replacement)


class AliasTests(unittest.TestCase):
    def test_the_canonical_aliases_are_the_same_objects(self):
        self.assertIs(PS.archive_policy_snapshot, PS.archive_current_policy)
        self.assertIs(PS.restore_policy_snapshot, PS.restore_archived_policy)
        self.assertIs(PS.delete_policy_archive, PS.delete_archived_policy)

    def test_reexports_are_available(self):
        for name in ("compute_layout_hash", "compute_file_hash",
                     "extract_council_fingerprint", "format_policy_snapshot_summary",
                     "save_archive_index", "DEFAULT_BASE_VERSION"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(PS, name))

    def test_the_all_list_matches_the_documented_surface(self):
        for name in ("archive_current_policy", "restore_archived_policy",
                     "delete_archived_policy", "load_archive_index", "save_archive_index",
                     "capture_full_strategy_package", "generate_policy_snapshot"):
            self.assertIn(name, PS.__all__)

    def test_every_dunder_all_name_exists(self):
        for name in PS.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(PS, name))

    def test_the_beijing_offset_is_used_for_archive_timestamps(self):
        self.assertEqual(_BJ.utcoffset(None), timedelta(hours=8))
        self.assertEqual(PS._BJ.utcoffset(None), timedelta(hours=8))


class RebuildIndexTests(_Base):
    def _archive(self, name, payload, mtime=None):
        self.a_dir.mkdir(parents=True, exist_ok=True)
        path = self.a_dir / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        if mtime is not None:
            import os
            os.utime(path, (mtime, mtime))
        return path

    def test_a_missing_directory_yields_an_empty_list(self):
        self.assertEqual(PS._rebuild_index_from_archives(self.a_dir), [])

    def test_a_path_that_is_not_a_directory_yields_an_empty_list(self):
        self.a_dir.parent.mkdir(parents=True, exist_ok=True)
        self.a_dir.write_text("not a dir", encoding="utf-8")
        self.assertEqual(PS._rebuild_index_from_archives(self.a_dir), [])

    def test_a_full_metadata_entry_is_mapped(self):
        self._archive("policy_abc.json", {
            "policy_hash": "abc", "policy_version": "v1", "summary": "简述",
            "metadata": {"name": "名字", "description": "描述", "author": "alice",
                         "archived_at": "2026-09-22 16:00:00"},
        })
        entries = PS._rebuild_index_from_archives(self.a_dir)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0], {
            "policy_version": "v1", "policy_hash": "abc", "name": "名字",
            "description": "描述", "author": "alice",
            "archived_at": "2026-09-22 16:00:00", "summary": "简述",
            "archive_file": "policy_abc.json",
        })

    def test_every_field_falls_back_independently(self):
        """手改/截断过的归档：每个字段各自兜底，不能一个缺失就整条丢掉。"""
        self._archive("policy_deadbeef.json", {})
        entry = PS._rebuild_index_from_archives(self.a_dir)[0]
        self.assertEqual(entry["policy_hash"], "deadbeef", "hash 退回文件名")
        self.assertEqual(entry["policy_version"], "unknown@deadbeef")
        self.assertEqual(entry["name"], "策略归档-deadbeef")
        self.assertEqual(entry["description"], "")
        self.assertEqual(entry["author"], "admin")
        self.assertEqual(entry["summary"], "")

    def test_the_missing_timestamp_falls_back_to_file_mtime_in_beijing_time(self):
        stamp = 1790000000.0
        self._archive("policy_abc.json", {}, mtime=stamp)
        entry = PS._rebuild_index_from_archives(self.a_dir)[0]
        expected = datetime.fromtimestamp(stamp, _BJ).isoformat(sep=" ",
                                                               timespec="seconds")
        self.assertEqual(entry["archived_at"], expected)

    def test_a_metadata_timestamp_wins_over_the_file_mtime(self):
        self._archive("policy_abc.json", {"metadata": {"archived_at": "2020-01-01 00:00:00"}},
                      mtime=1790000000.0)
        self.assertEqual(PS._rebuild_index_from_archives(self.a_dir)[0]["archived_at"],
                         "2020-01-01 00:00:00")

    def test_only_policy_json_files_are_scanned(self):
        self._archive("policy_abc.json", {"policy_hash": "abc"})
        self._archive("other.json", {"policy_hash": "nope"})
        self._archive("policy_abc.tmp", {"policy_hash": "tmp"})
        (self.a_dir / "policy_dir.json").mkdir()
        names = [e["archive_file"] for e in PS._rebuild_index_from_archives(self.a_dir)]
        self.assertEqual(names, ["policy_abc.json"])

    def test_a_tmp_suffixed_file_is_skipped(self):
        self._archive("policy_ok.json", {"policy_hash": "ok"})
        self._archive("policy_x.json.tmp", {"policy_hash": "tmp"})
        self.assertEqual([e["policy_hash"] for e in PS._rebuild_index_from_archives(self.a_dir)],
                         ["ok"])

    def test_a_broken_archive_is_skipped_with_a_warning(self):
        self._archive("policy_good.json", {"policy_hash": "good"})
        (self.a_dir / "policy_bad.json").write_text("{不是 JSON", encoding="utf-8")
        with self.assertLogs("r20_backend.policy_snapshot", level="WARNING") as logs:
            entries = PS._rebuild_index_from_archives(self.a_dir)
        self.assertEqual([e["policy_hash"] for e in entries], ["good"])
        self.assertIn("policy_bad.json", "\n".join(logs.output))

    def test_a_non_dict_package_does_not_crash_the_scan(self):
        self._archive("policy_list.json", [1, 2, 3])
        with self.assertLogs("r20_backend.policy_snapshot", level="WARNING"):
            self.assertEqual(PS._rebuild_index_from_archives(self.a_dir), [])

    def test_entries_are_sorted_newest_first(self):
        for i, day in enumerate(("2026-01-01 00:00:00", "2026-09-01 00:00:00",
                                 "2026-05-01 00:00:00")):
            self._archive(f"policy_{i}.json",
                          {"policy_hash": f"h{i}",
                           "metadata": {"archived_at": day}})
        stamps = [e["archived_at"] for e in PS._rebuild_index_from_archives(self.a_dir)]
        self.assertEqual(stamps, ["2026-09-01 00:00:00", "2026-05-01 00:00:00",
                                  "2026-01-01 00:00:00"])

    def test_an_empty_directory_yields_an_empty_list(self):
        self.a_dir.mkdir(parents=True, exist_ok=True)
        self.assertEqual(PS._rebuild_index_from_archives(self.a_dir), [])


class ArchiveCurrentPolicyTests(_Base):
    def setUp(self):
        super().setUp()
        self.package = {"policy_hash": "ph-1", "policy_version": "v-1",
                        "summary": "摘要", "package": {"unit": 1}}
        self.capture = self._start(mock.patch.object(
            PS, "capture_full_strategy_package", return_value=self.package))
        self.identity = self._start(mock.patch.object(PS, "package_identity",
                                                      return_value="PKG-1"))
        self.write = self._start(mock.patch.object(PS, "_atomic_write_json"))
        self.load = self._start(mock.patch.object(PS, "load_archive_index",
                                                  return_value=[]))
        self.save = self._start(mock.patch.object(PS, "save_archive_index"))
        self.lock = self._start(mock.patch.object(PS, "_index_lock"))

    def _archive(self, name="名字", description="描述", author="alice", root_dir=None):
        return PS.archive_current_policy(name, description, author,
                                         archive_dir=self.a_dir, root_dir=root_dir)

    def test_it_captures_the_live_package(self):
        self._archive(root_dir=Path("/r"))
        self.capture.assert_called_once_with(root_dir=Path("/r"))

    def test_the_archive_file_is_named_by_the_package_identity(self):
        self._archive()
        self.write.assert_called_once_with(self.a_dir / "policy_PKG-1.json", self.package)

    def test_the_package_identity_is_computed_from_the_package_payload(self):
        self._archive()
        self.identity.assert_called_once_with({"unit": 1})

    def test_a_missing_package_payload_degrades_to_an_empty_mapping(self):
        self.package.pop("package")
        self._archive()
        self.identity.assert_called_once_with({})

    def test_an_empty_identity_falls_back_to_the_policy_hash(self):
        """审计 P0-3 的兜底：算不出整包标识时退回 policy_hash（不能生成 `policy_.json`）。"""
        self.identity.return_value = ""
        self._archive()
        self.assertEqual(self.write.call_args[0][0], self.a_dir / "policy_ph-1.json")

    def test_the_package_carries_both_hashes_and_metadata(self):
        entry = self._archive()
        self.assertEqual(self.package["package_hash"], "PKG-1")
        meta = self.package["metadata"]
        self.assertEqual(meta["name"], "名字")
        self.assertEqual(meta["description"], "描述")
        self.assertEqual(meta["author"], "alice")
        self.assertEqual(meta["archive_file"], "policy_PKG-1.json")
        self.assertEqual(meta["package_hash"], "PKG-1")
        self.assertRegex(meta["archived_at"],
                         r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+08:00$")
        self.assertEqual(entry["package_hash"], "PKG-1")

    def test_a_blank_name_falls_back_to_the_hash_based_default(self):
        entry = self._archive(name="   ")
        self.assertEqual(entry["name"], "策略归档-ph-1")

    def test_the_name_and_description_are_stripped(self):
        entry = self._archive(name="  名字  ", description="  描述  ")
        self.assertEqual(entry["name"], "名字")
        self.assertEqual(entry["description"], "描述")

    def test_the_entry_shape(self):
        entry = self._archive()
        self.assertEqual(set(entry), {"policy_version", "policy_hash", "package_hash",
                                      "name", "description", "author", "archived_at",
                                      "summary", "archive_file"})
        self.assertEqual(entry["policy_version"], "v-1")
        self.assertEqual(entry["policy_hash"], "ph-1")
        self.assertEqual(entry["summary"], "摘要")
        self.assertEqual(entry["archive_file"], "policy_PKG-1.json")

    def test_the_new_entry_goes_to_the_front(self):
        self.load.return_value = [{"policy_hash": "old", "name": "旧"}]
        self._archive()
        saved = self.save.call_args[0][0]
        self.assertEqual([e["name"] for e in saved], ["名字", "旧"])

    def test_the_index_is_saved_into_the_same_archive_dir(self):
        self._archive()
        self.save.assert_called_once()
        self.assertEqual(self.save.call_args[1]["archive_dir"], self.a_dir)

    def test_the_write_happens_under_an_exclusive_index_lock(self):
        self._archive()
        self.lock.assert_called_once_with(self.a_dir, shared=False)

    def test_the_archive_directory_is_created(self):
        self.assertFalse(self.a_dir.exists())
        self._archive()
        self.assertTrue(self.a_dir.is_dir())

    def test_it_reads_the_index_after_writing_the_package(self):
        order = []
        self.write.side_effect = lambda *a: order.append("write")
        self.load.side_effect = lambda **k: order.append("load") or []
        self._archive()
        self.assertEqual(order, ["write", "load"], "先落包再读索引，避免读到半写状态")

    def test_it_returns_the_index_entry(self):
        entry = self._archive()
        self.assertEqual(entry["name"], "名字")


class ArchiveDedupeTests(_Base):
    """审计 P0-3 的去重口径：整包标识优先，历史条目退回 policy_hash。"""

    def setUp(self):
        super().setUp()
        self.package = {"policy_hash": "ph-1", "policy_version": "v-1",
                        "summary": "s", "package": {}}
        self._start(mock.patch.object(PS, "capture_full_strategy_package",
                                      return_value=self.package))
        self._start(mock.patch.object(PS, "package_identity", return_value="PKG-1"))
        self._start(mock.patch.object(PS, "_atomic_write_json"))
        self.load = self._start(mock.patch.object(PS, "load_archive_index"))
        self.save = self._start(mock.patch.object(PS, "save_archive_index"))
        self._start(mock.patch.object(PS, "_index_lock"))

    def _kept_names(self, existing):
        self.load.return_value = list(existing)
        PS.archive_current_policy("new", archive_dir=self.a_dir)
        return [e.get("name") for e in self.save.call_args[0][0]]

    def test_the_same_package_hash_is_replaced(self):
        self.assertEqual(self._kept_names([{"package_hash": "PKG-1", "name": "旧同名"}]),
                         ["new"])

    def test_a_different_package_hash_is_kept(self):
        self.assertEqual(self._kept_names([{"package_hash": "OTHER", "name": "别的"}]),
                         ["new", "别的"])

    def test_a_legacy_entry_without_a_package_hash_matching_by_policy_hash_is_replaced(self):
        self.assertEqual(self._kept_names([{"policy_hash": "ph-1", "name": "老条目"}]), ["new"])

    def test_a_legacy_entry_with_a_different_policy_hash_is_kept(self):
        self.assertEqual(self._kept_names([{"policy_hash": "ph-0", "name": "老条目"}]),
                         ["new", "老条目"])

    def test_a_legacy_entry_takes_the_policy_hash_path_even_if_the_package_hash_differs(self):
        """历史条目只看 `policy_hash`（不受本次的整包标识影响）。"""
        self.assertEqual(self._kept_names([{"policy_hash": "ph-0", "package_hash": "",
                                            "name": "老条目"}]), ["new", "老条目"])

    def test_mixed_entries(self):
        self.assertEqual(
            self._kept_names([{"package_hash": "PKG-1", "name": "A"},
                              {"package_hash": "OTHER", "name": "B"},
                              {"policy_hash": "ph-1", "name": "C"},
                              {"policy_hash": "ph-0", "name": "D"}]),
            ["new", "B", "D"])


class ArchiveDirDefaultTests(_Base):
    def test_it_defaults_to_the_module_archive_dir(self):
        seen = {}

        def _lock(a_dir, shared=False):
            seen["dir"] = a_dir
            return mock.MagicMock()

        self._start(mock.patch.object(PS, "_index_lock", side_effect=_lock))
        self._start(mock.patch.object(PS, "capture_full_strategy_package",
                                      return_value={"policy_hash": "h", "policy_version": "v",
                                                    "summary": "", "package": {}}))
        self._start(mock.patch.object(PS, "package_identity", return_value="P"))
        self._start(mock.patch.object(PS, "_atomic_write_json"))
        self._start(mock.patch.object(PS, "load_archive_index", return_value=[]))
        self._start(mock.patch.object(PS, "save_archive_index"))
        with mock.patch.object(PS, "ARCHIVE_DIR", Path(self.tmp.name) / "def"):
            PS.archive_current_policy("n")
        self.assertEqual(seen["dir"], Path(self.tmp.name) / "def")

    def test_an_explicit_archive_dir_wins(self):
        seen = {}

        def _lock(a_dir, shared=False):
            seen["dir"] = a_dir
            return mock.MagicMock()

        self._start(mock.patch.object(PS, "_index_lock", side_effect=_lock))
        self._start(mock.patch.object(PS, "capture_full_strategy_package",
                                      return_value={"policy_hash": "h", "policy_version": "v",
                                                    "summary": "", "package": {}}))
        self._start(mock.patch.object(PS, "package_identity", return_value="P"))
        self._start(mock.patch.object(PS, "_atomic_write_json"))
        self._start(mock.patch.object(PS, "load_archive_index", return_value=[]))
        self._start(mock.patch.object(PS, "save_archive_index"))
        PS.archive_current_policy("n", archive_dir=self.a_dir)
        self.assertEqual(seen["dir"], self.a_dir)


if __name__ == "__main__":
    unittest.main()

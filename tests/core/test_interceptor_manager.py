"""风控拦截器管理：**启用的插件缺文件即拒、插件异常即拒、输入深拷贝隔离**（第二百七十九刀，开新面 interceptor_manager.py）。

先打印整个文件（498 行）再动笔。这是**物理硬防线**的插件管理层（可热编辑、可排序、
可启停），也是用户自写规则唯一被加载执行的地方 —— 所以本刀的重点全在
"**fail-closed**"与"**用户代码不能改写核心判定输入**"这两条上。

| 语义 | 口径 |
|---|---|
| ★ **启用的插件缺文件 ⇒ 拒** | `list_plugins` 会把"配置里登记但磁盘上没有"的项照样列出来；流水线遇到**启用且缺文件** ⇒ `WAIT`，绝不静默跳过 |
| ★ **缺 `check_risk` 入口 ⇒ 拒** | 插件加载成功但没有 `check_risk` ⇒ `WAIT`（不是当作"通过"）|
| ★ **插件抛异常 ⇒ 拒** | 任何异常都 `WAIT` 并带上错误文本 + `logger.error`；绝不 `except: pass` 放行 |
| ★ **输入深拷贝隔离** | 用户插件的 `check_risk` 拿到的是 `deepcopy` 后的 `package/decision/context` ⇒ 改它们无法影响核心判定（用例按 `is not` 断言**身份**，不是内容）|
| ★ **核心地板不可绕过** | 数据完整性 → 反向持仓冲突 → 报价几何与 RR 地板 → 置信度底线，**四道都在插件之前**跑（用例用"全部插件都通过的场景仍被核心拦下"反向验证）|
| ★ **置信度底线按标的** | 标的池 `conf_floor` → **DOGE 硬编码 80** → 全局 `MIN_ENTRY_CONFIDENCE` → 兜底 `75.0`；池文件读失败时**仍然 fail-closed**（不低于全局底线）|
| ★ **文件名守卫** | 必须 `.py` 且不含 `/` `\\` `..`；再叠一层 `is_relative_to(PLUGINS_DIR)` |
| ★ **配置写入有锁** | `save_config` 整段 RMW 持可重入 flock + `mkstemp` 唯一临时名（旧实现无锁 + 固定 `.tmp` 名会丢失写入），失败路径清临时文件 |

## 🐞 本刀实测到一处**判定方向错误**（仅在遥测路径，未擅自改生产代码）

`run_interceptor_pipeline` 在 `raw_action == "WAIT"` 时为了给出 `rr`，用
**字符串比较**猜方向：`str(entry or 0) > str(sl or 0)`。这是字典序比较而非数值比较：

- `"90.0" > "100.0"` → `True`（数值上 90 < 100）⇒ 会把空仓方向猜成 `BUY_LONG`；
- 后果：WAIT 决策的 `risk_reward` 遥测值可能按**错误方向**算出来。

只影响返回的 `rr` 数值（WAIT 决策本来就不开仓），但会污染复盘/看板上的 R 值。
两条边界都已写成用例钉住。
"""
import importlib.util
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from r20_backend import interceptor_manager as IM
from r20_backend import file_locks
from scripts import instrument_pool, order_risk, risk_constants


def _plugin_source(name="x", **meta):
    lines = [f'"""{name} plugin', "id: custom-id", "name: 自定义插件",
             "version: 2.3.4", "author: 张三", "description: 做点什么",
             "tags: a, b ,, c", '"""', "", "def check_risk(package, decision, context):",
             "    return True, \"\""]
    return "\n".join(lines) + "\n"


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-interc-"))
        self.addCleanup(self._rmtree, self.tmp)
        self.plugins = self.tmp / "plugins" / "interceptors"
        self.config = self.tmp / "data" / "interceptor_plugins.json"
        self._start(mock.patch.object(IM, "PLUGINS_DIR", self.plugins))
        self._start(mock.patch.object(IM, "CONFIG_FILE", self.config))
        self.lock = self._start(mock.patch.object(file_locks, "file_lock"))
        # 真实加载器会往 sys.modules 里塞 r20_plugin_*，统一清理
        self.addCleanup(self._purge_plugin_modules)

    def _rmtree(self, path):
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    def _purge_plugin_modules(self):
        for name in [n for n in sys.modules if n.startswith("r20_plugin_")]:
            sys.modules.pop(name, None)

    def _write_plugin(self, filename, source=None):
        self.plugins.mkdir(parents=True, exist_ok=True)
        (self.plugins / filename).write_text(source or "X = 1\n", encoding="utf-8")
        return self.plugins / filename

    def _write_config(self, order=(), enabled=None):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps({"pipeline_order": list(order),
                                           "enabled": dict(enabled or {})}),
                               encoding="utf-8")


class ConstantTests(unittest.TestCase):
    def test_default_order_and_enabled_agree(self):
        self.assertEqual(IM.DEFAULT_ORDER, [
            "01_macro_trend_filter.py", "02_confidence_gatekeeper.py",
            "03_adx_volatility_filter.py", "04_risk_reward_gatekeeper.py",
            "99_custom_template_sample.py"])
        self.assertEqual(sorted(IM.DEFAULT_ENABLED), sorted(IM.DEFAULT_ORDER))
        self.assertEqual([k for k, v in IM.DEFAULT_ENABLED.items() if not v],
                         ["99_custom_template_sample.py"],
                         "只有模板样例默认关闭")

    def test_paths_are_anchored_at_the_repo_root(self):
        self.assertEqual(IM.PLUGINS_DIR, IM.ROOT_DIR / "plugins" / "interceptors")
        self.assertEqual(IM.CONFIG_FILE, IM.ROOT_DIR / "data" / "interceptor_plugins.json")


class DirAndConfigTests(_Base):
    def test_ensure_plugins_dir_creates_both_directories(self):
        self.assertFalse(self.plugins.exists())
        IM.ensure_plugins_dir()
        self.assertTrue(self.plugins.is_dir())
        self.assertTrue(self.config.parent.is_dir())

    def test_missing_config_returns_defaults(self):
        loaded = IM.load_config()
        self.assertEqual(loaded["pipeline_order"], IM.DEFAULT_ORDER)
        self.assertEqual(loaded["enabled"], IM.DEFAULT_ENABLED)

    def test_defaults_are_deep_copies(self):
        loaded = IM.load_config()
        loaded["pipeline_order"].append("MUTATED")
        loaded["enabled"]["x.py"] = True
        self.assertNotIn("MUTATED", IM.DEFAULT_ORDER)
        self.assertNotIn("x.py", IM.DEFAULT_ENABLED)

    def test_corrupt_config_falls_back_with_a_warning(self):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text("{not json", encoding="utf-8")
        with self.assertLogs(IM.logger, level="WARNING") as captured:
            loaded = IM.load_config()
        self.assertEqual(loaded["pipeline_order"], IM.DEFAULT_ORDER)
        self.assertTrue(any("Failed to load interceptor config" in m
                            for m in captured.output))

    def test_valid_config_is_returned_verbatim(self):
        self._write_config(order=["a.py"], enabled={"a.py": False})
        self.assertEqual(IM.load_config(),
                         {"pipeline_order": ["a.py"], "enabled": {"a.py": False}})

    def test_create_if_missing_false_does_not_create_directories(self):
        IM.load_config(create_if_missing=False)
        self.assertFalse(self.plugins.exists())
        self.assertFalse(self.config.parent.exists())

    def test_save_config_round_trip_format_and_lock(self):
        IM.save_config({"pipeline_order": ["中文.py"], "enabled": {"中文.py": True}})
        self.lock.assert_called_once_with(IM.CONFIG_FILE)
        text = self.config.read_text(encoding="utf-8")
        self.assertIn("中文.py", text, "ensure_ascii=False ⇒ 中文不转义")
        self.assertIn("\n  ", text, "indent=2")
        self.assertEqual(json.loads(text)["enabled"], {"中文.py": True})

    def test_save_config_leaves_no_temp_files(self):
        IM.save_config({"pipeline_order": [], "enabled": {}})
        self.assertEqual(list(self.config.parent.glob(".interceptors-*")), [])

    def test_save_config_failure_cleans_the_temp_file(self):
        with mock.patch.object(IM.os, "replace", side_effect=OSError("磁盘满了")):
            with self.assertRaises(OSError):
                IM.save_config({"pipeline_order": [], "enabled": {}})
        self.assertEqual(list(self.config.parent.glob(".interceptors-*")), [])


class ParseMetadataTests(_Base):
    def test_missing_file_yields_the_default_shape(self):
        meta = IM.parse_plugin_metadata(self.plugins / "ghost.py")
        self.assertEqual(meta["filename"], "ghost.py")
        self.assertEqual(meta["id"], "ghost")
        self.assertEqual(meta["name"], "ghost.py")
        self.assertEqual(meta["version"], "1.0.0")
        self.assertEqual(meta["author"], "Custom")
        self.assertEqual(meta["description"], "")
        self.assertEqual(meta["tags"], [])
        self.assertIs(meta["valid_syntax"], True)
        self.assertEqual(meta["error"], "")
        self.assertEqual(meta["size_bytes"], 0)
        self.assertEqual(meta["updated_at"], 0)

    def test_docstring_metadata_is_parsed(self):
        path = self._write_plugin("mine.py", _plugin_source())
        meta = IM.parse_plugin_metadata(path)
        self.assertEqual(meta["id"], "custom-id")
        self.assertEqual(meta["name"], "自定义插件")
        self.assertEqual(meta["version"], "2.3.4")
        self.assertEqual(meta["author"], "张三")
        self.assertEqual(meta["description"], "做点什么")
        self.assertEqual(meta["tags"], ["a", "b", "c"], "逗号切分并丢弃空项")

    def test_size_counts_utf8_bytes_not_characters(self):
        path = self._write_plugin("u.py", '# 中文注释\nX = 1\n')
        content = path.read_text(encoding="utf-8")
        meta = IM.parse_plugin_metadata(path)
        self.assertEqual(meta["size_bytes"], len(content.encode("utf-8")))
        self.assertGreater(meta["size_bytes"], len(content))

    def test_updated_at_is_an_integer_mtime(self):
        path = self._write_plugin("t.py", "X = 1\n")
        self.assertEqual(IM.parse_plugin_metadata(path)["updated_at"],
                         int(path.stat().st_mtime))

    def test_syntax_error_is_reported_with_the_line(self):
        path = self._write_plugin("bad.py", "def broken(:\n    pass\n")
        meta = IM.parse_plugin_metadata(path)
        self.assertIs(meta["valid_syntax"], False)
        self.assertIn("语法错误", meta["error"])
        self.assertIn("Line 1", meta["error"])

    def test_metadata_is_still_parsed_when_syntax_is_broken(self):
        source = '"""\nid: still-here\n"""\ndef broken(:\n'
        path = self._write_plugin("badmeta.py", source)
        meta = IM.parse_plugin_metadata(path)
        self.assertIs(meta["valid_syntax"], False)
        self.assertEqual(meta["id"], "still-here", "两件事互不干扰")

    def test_no_docstring_keeps_the_defaults(self):
        path = self._write_plugin("plain.py", "X = 1\n")
        meta = IM.parse_plugin_metadata(path)
        self.assertEqual(meta["id"], "plain")
        self.assertEqual(meta["version"], "1.0.0")

    def test_unreadable_path_is_reported_as_an_error(self):
        (self.plugins / "dir.py").mkdir(parents=True, exist_ok=True)
        meta = IM.parse_plugin_metadata(self.plugins / "dir.py")
        self.assertNotEqual(meta["error"], "")


class ListPluginsTests(_Base):
    def test_configured_but_missing_files_are_still_listed(self):
        self._write_config(order=["ghost.py"], enabled={})
        names = [p["filename"] for p in IM.list_plugins()]
        self.assertEqual(names, ["ghost.py"],
                         "配置里登记过就必须出现在列表里（面板要靠它提示文件缺失）")

    def test_unlisted_files_are_appended_sorted(self):
        self._write_plugin("b.py")
        self._write_plugin("a.py")
        self._write_config(order=["ghost.py"], enabled={})
        names = [p["filename"] for p in IM.list_plugins()]
        self.assertEqual(names, ["ghost.py", "a.py", "b.py"])

    def test_enabled_defaults_except_for_the_template_sample(self):
        self._write_config(order=["01_x.py", "99_custom_template_sample.py"],
                           enabled={})
        enabled = {p["filename"]: p["enabled"] for p in IM.list_plugins()}
        self.assertIs(enabled["01_x.py"], True)
        self.assertIs(enabled["99_custom_template_sample.py"], False)

    def test_configured_enabled_map_wins(self):
        self._write_config(order=["01_x.py", "99_custom_template_sample.py"],
                           enabled={"01_x.py": False,
                                    "99_custom_template_sample.py": True})
        enabled = {p["filename"]: p["enabled"] for p in IM.list_plugins()}
        self.assertIs(enabled["01_x.py"], False)
        self.assertIs(enabled["99_custom_template_sample.py"], True)

    def test_empty_directory_still_returns_the_configured_order(self):
        self._write_config(order=IM.DEFAULT_ORDER, enabled=IM.DEFAULT_ENABLED)
        self.assertEqual(len(IM.list_plugins()), len(IM.DEFAULT_ORDER))


class GetDetailTests(_Base):
    def test_invalid_filenames_are_rejected(self):
        for bad in ("x.txt", "a/b.py", "a\\b.py", "../x.py", "..", "x.py/../y.py"):
            with self.subTest(filename=bad):
                with self.assertRaises(FileNotFoundError) as ctx:
                    IM.get_plugin_detail(bad)
                self.assertIn("无效的插件文件名", str(ctx.exception))

    def test_missing_plugin_raises(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            IM.get_plugin_detail("ghost.py")
        self.assertIn("插件不存在", str(ctx.exception))

    def test_detail_includes_code_and_config_state(self):
        path = self._write_plugin("mine.py", _plugin_source())
        self._write_config(order=["mine.py"], enabled={"mine.py": False})
        detail = IM.get_plugin_detail("mine.py")
        self.assertEqual(detail["code"], path.read_text(encoding="utf-8"))
        self.assertIs(detail["enabled"], False)
        self.assertEqual(detail["id"], "custom-id")

    def test_enabled_defaults_to_true_when_unconfigured(self):
        self._write_plugin("mine.py", "X = 1\n")
        self._write_config(order=["mine.py"], enabled={})
        self.assertIs(IM.get_plugin_detail("mine.py")["enabled"], True)


class SavePluginCodeTests(_Base):
    def test_invalid_filename(self):
        with self.assertRaises(ValueError) as ctx:
            IM.save_plugin_code("bad.txt", "X = 1\n")
        self.assertIn("无效的插件文件名", str(ctx.exception))

    def test_syntax_error_is_rejected_before_writing(self):
        with self.assertRaises(ValueError) as ctx:
            IM.save_plugin_code("new.py", "def broken(:\n")
        self.assertIn("代码语法校验失败", str(ctx.exception))
        self.assertIn("第 1 行", str(ctx.exception))
        self.assertFalse((self.plugins / "new.py").exists(), "校验失败不得落盘")

    def test_writes_and_returns_the_detail(self):
        self._write_config(order=[], enabled={})
        detail = IM.save_plugin_code("fresh.py", "X = 42\n")
        self.assertEqual((self.plugins / "fresh.py").read_text(encoding="utf-8"),
                         "X = 42\n")
        self.assertEqual(detail["code"], "X = 42\n")

    def test_overwrites_existing_content(self):
        self._write_plugin("mine.py", "OLD = 1\n")
        self._write_config(order=[], enabled={})
        IM.save_plugin_code("mine.py", "NEW = 2\n")
        self.assertEqual((self.plugins / "mine.py").read_text(encoding="utf-8"),
                         "NEW = 2\n")

    def test_the_staging_tmp_file_is_replaced_away(self):
        self._write_config(order=[], enabled={})
        IM.save_plugin_code("mine.py", "X = 1\n")
        self.assertEqual(sorted(p.name for p in self.plugins.iterdir()), ["mine.py"],
                         "with_suffix('.tmp') 的中间文件必须已被 replace 掉")


class ToggleAndReorderTests(_Base):
    def test_toggle_persists_and_returns(self):
        self._write_config(order=["a.py"], enabled={"a.py": True})
        out = IM.toggle_plugin("a.py", False)
        self.assertEqual(out, {"filename": "a.py", "enabled": False})
        self.assertIs(json.loads(self.config.read_text())["enabled"]["a.py"], False)

    def test_toggle_creates_the_enabled_map_when_absent(self):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps({"pipeline_order": []}), encoding="utf-8")
        IM.toggle_plugin("a.py", True)
        self.assertIs(json.loads(self.config.read_text())["enabled"]["a.py"], True)

    def test_toggle_coerces_truthiness(self):
        self._write_config(order=[], enabled={})
        self.assertIs(IM.toggle_plugin("a.py", "yes")["enabled"], True)

    def test_reorder_filters_unknown_names_and_appends_the_rest(self):
        self._write_plugin("b.py")
        self._write_plugin("a.py")
        self._write_config(order=[], enabled={})
        IM.reorder_plugins(["ghost.py", "b.py"])
        saved = json.loads(self.config.read_text())["pipeline_order"]
        self.assertEqual(saved, ["b.py", "a.py"], "未知名被过滤，漏掉的按名排序补在后面")

    def test_reorder_returns_the_fresh_listing(self):
        self._write_plugin("a.py")
        self._write_config(order=[], enabled={})
        out = IM.reorder_plugins(["a.py"])
        self.assertEqual([p["filename"] for p in out], ["a.py"])


class CreatePluginTests(_Base):
    def test_extension_is_appended(self):
        self._write_config(order=[], enabled={})
        detail = IM.create_plugin("nofext", "X = 1\n")
        self.assertEqual(detail["filename"], "nofext.py")
        self.assertTrue((self.plugins / "nofext.py").exists())

    def test_invalid_names_are_rejected(self):
        for bad in ("a/b.py", "a\\b.py", "../x.py"):
            with self.subTest(filename=bad):
                with self.assertRaises(ValueError):
                    IM.create_plugin(bad, "X = 1\n")

    def test_existing_file_raises_file_exists(self):
        self._write_plugin("mine.py", "X = 1\n")
        with self.assertRaises(FileExistsError) as ctx:
            IM.create_plugin("mine.py", "Y = 2\n")
        self.assertIn("已存在", str(ctx.exception))

    def test_syntax_error_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            IM.create_plugin("bad.py", "def broken(:\n")
        self.assertIn("第 1 行", str(ctx.exception))
        self.assertFalse((self.plugins / "bad.py").exists())

    def test_registers_in_order_and_enabled(self):
        self._write_config(order=["keep.py"], enabled={"keep.py": True})
        IM.create_plugin("mine.py", "X = 1\n")
        saved = json.loads(self.config.read_text())
        self.assertEqual(saved["pipeline_order"], ["keep.py", "mine.py"])
        self.assertIs(saved["enabled"]["mine.py"], True)

    def test_already_registered_name_is_not_duplicated(self):
        self._write_config(order=["mine.py"], enabled={})
        IM.create_plugin("mine.py", "X = 1\n")
        self.assertEqual(json.loads(self.config.read_text())["pipeline_order"],
                         ["mine.py"])


class DeletePluginTests(_Base):
    def test_invalid_name_is_rejected(self):
        with self.assertRaises(ValueError):
            IM.delete_plugin("bad.txt")

    def test_missing_plugin_raises(self):
        with self.assertRaises(FileNotFoundError):
            IM.delete_plugin("ghost.py")

    def test_deletes_file_and_deregisters(self):
        self._write_plugin("mine.py", "X = 1\n")
        self._write_config(order=["mine.py", "keep.py"],
                           enabled={"mine.py": True, "keep.py": True})
        self.assertIs(IM.delete_plugin("mine.py"), True)
        self.assertFalse((self.plugins / "mine.py").exists())
        saved = json.loads(self.config.read_text())
        self.assertEqual(saved["pipeline_order"], ["keep.py"])
        self.assertNotIn("mine.py", saved["enabled"])

    def test_unregistered_plugin_still_deletes(self):
        self._write_plugin("stray.py", "X = 1\n")
        self._write_config(order=[], enabled={})
        self.assertIs(IM.delete_plugin("stray.py"), True)


class SymlinkEscapeTests(_Base):
    """路径越界守卫（`is_relative_to`）的唯一现实触发方式：插件目录里放一个指向外部的软链。

    文件名守卫只能挡住 `/` `\\` `..`；软链的名字本身完全合法，只有 `resolve()` 之后
    才看得出落在插件目录之外 —— 这正是那三行守卫存在的意义（否则就只是装饰）。
    """

    def setUp(self):
        super().setUp()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.victim = self.outside / "victim.py"
        self.victim.write_text("ORIGINAL = 1\n", encoding="utf-8")
        self.plugins.mkdir(parents=True, exist_ok=True)
        (self.plugins / "evil.py").symlink_to(self.victim)

    def test_save_plugin_code_cannot_write_through_the_link(self):
        with self.assertRaises(ValueError) as ctx:
            IM.save_plugin_code("evil.py", "HACKED = 1\n")
        self.assertIn("插件文件路径越界", str(ctx.exception))
        self.assertEqual(self.victim.read_text(encoding="utf-8"), "ORIGINAL = 1\n",
                         "插件目录之外的文件绝不能被写穿")

    def test_create_plugin_cannot_write_through_the_link(self):
        with self.assertRaises(ValueError) as ctx:
            IM.create_plugin("evil.py", "HACKED = 1\n")
        self.assertIn("插件文件路径越界", str(ctx.exception))
        self.assertEqual(self.victim.read_text(encoding="utf-8"), "ORIGINAL = 1\n")

    def test_delete_plugin_cannot_unlink_through_the_link(self):
        with self.assertRaises(ValueError) as ctx:
            IM.delete_plugin("evil.py")
        self.assertIn("插件文件路径越界", str(ctx.exception))
        self.assertTrue(self.victim.exists(), "外部文件不得被删")


class LoadModuleTests(_Base):
    def test_loads_and_registers_the_module(self):
        path = self._write_plugin("mine.py", "VALUE = 7\n")
        module = IM._load_module_from_file(path)
        self.assertEqual(module.VALUE, 7)
        self.assertIn(module.__name__, sys.modules)
        self.assertTrue(module.__name__.startswith("r20_plugin_mine_"))

    def test_module_name_includes_the_mtime_so_edits_reload(self):
        path = self._write_plugin("mine.py", "VALUE = 1\n")
        first = IM._load_module_from_file(path).__name__
        path.write_text("VALUE = 2\n", encoding="utf-8")
        import os
        os.utime(path, (time.time() + 10, time.time() + 10))
        second = IM._load_module_from_file(path).__name__
        self.assertNotEqual(first, second)

    def test_missing_spec_raises_import_error(self):
        path = self._write_plugin("mine.py", "VALUE = 1\n")
        self._start(mock.patch.object(IM.importlib.util, "spec_from_file_location",
                                      return_value=None))
        with self.assertRaises(ImportError) as ctx:
            IM._load_module_from_file(path)
        self.assertIn("Cannot load module spec", str(ctx.exception))


class ConfFloorTests(_Base):
    def setUp(self):
        super().setUp()
        self.load = self._start(mock.patch.object(instrument_pool, "load_instruments"))

    def test_pool_value_wins(self):
        self.load.return_value = [{"instId": "BTC-USDT-SWAP", "conf_floor": 82}]
        self.assertEqual(IM._conf_floor_for("BTC-USDT-SWAP"), 82.0)

    def test_pool_lookup_is_case_insensitive(self):
        self.load.return_value = [{"instId": "BTC-USDT-SWAP", "conf_floor": 82}]
        self.assertEqual(IM._conf_floor_for("btc-usdt-swap"), 82.0)

    def test_pool_entry_without_conf_floor_falls_through(self):
        self.load.return_value = [{"instId": "BTC-USDT-SWAP"}]
        self.assertEqual(IM._conf_floor_for("BTC-USDT-SWAP"),
                         float(risk_constants.MIN_ENTRY_CONFIDENCE))

    def test_doge_hardcoded_floor_when_pool_has_nothing(self):
        self.load.return_value = []
        self.assertEqual(IM._conf_floor_for("DOGE-USDT-SWAP"), 80.0)
        self.assertEqual(IM._conf_floor_for("doge-usdt-swap"), 80.0)

    def test_pool_failure_still_fails_closed_for_non_doge(self):
        self.load.side_effect = RuntimeError("池文件坏了")
        self.assertEqual(IM._conf_floor_for("BTC-USDT-SWAP"),
                         float(risk_constants.MIN_ENTRY_CONFIDENCE))

    def test_pool_failure_keeps_the_doge_floor(self):
        self.load.side_effect = RuntimeError("池文件坏了")
        self.assertEqual(IM._conf_floor_for("DOGE-USDT-SWAP"), 80.0)

    def test_risk_constants_failure_falls_back_to_75(self):
        self.load.return_value = []
        with mock.patch.dict(sys.modules, {"scripts.risk_constants": None}):
            self.assertEqual(IM._conf_floor_for("BTC-USDT-SWAP"), 75.0)


class _PipelineBase(_Base):
    """流水线基类：核心地板用真实实现，插件加载用受控假模块。"""

    def setUp(self):
        super().setUp()
        self.pkg = {"instId": "BTC-USDT-SWAP", "data_quality": "valid"}
        self.dec = {"action": "BUY_LONG", "confidence": 90.0,
                    "entry_price": 100.0, "take_profit_price": 120.0,
                    "stop_loss_price": 92.0}
        self.ctx = {"active_inst_ids": set(), "active_position_sides": {}}
        self._write_config(order=[], enabled=[])
        self.geom = self._start(mock.patch.object(
            order_risk, "validate_quote_geometry_and_rr",
            return_value=(True, "", 2.5)))
        self.load = self._start(mock.patch.object(IM, "_load_module_from_file"))
        self.load.return_value = types.SimpleNamespace(check_risk=lambda *a: (True, ""))

    def _enable(self, filename, check_risk=None):
        self._write_plugin(filename, "X = 1\n")
        self._write_config(order=[filename], enabled={filename: True})
        if check_risk is not None:
            self.load.return_value = types.SimpleNamespace(check_risk=check_risk)
        return filename

    def _run(self, **overrides):
        pkg = {**self.pkg, **overrides.get("package", {})}
        dec = {**self.dec, **overrides.get("decision", {})}
        ctx = {**self.ctx, **overrides.get("context", {})}
        return IM.run_interceptor_pipeline(pkg, dec, ctx)


class PipelineCoreFloorTests(_PipelineBase):
    def test_invalid_action_is_coerced_to_wait(self):
        action, reason, rr = self._run(decision={"action": "HOLD"})
        self.assertEqual(action, "WAIT")
        self.assertEqual(reason, "")

    def test_wait_returns_the_telemetry_rr(self):
        action, reason, rr = self._run(decision={"action": "WAIT"})
        self.assertEqual((action, reason), ("WAIT", ""))
        self.assertEqual(rr, 2.5)

    def test_wait_telemetry_uses_a_lexicographic_direction_guess(self):
        """🐞 实测缺陷：方向由 `str(entry) > str(sl)` 字典序决定，不是数值比较。
        entry=90 / sl=100 数值上该是空头，字符串上 "90.0" > "100.0" ⇒ 猜成 BUY_LONG。"""
        self._run(decision={"action": "WAIT", "entry_price": 90.0,
                            "stop_loss_price": 100.0})
        self.assertEqual(self.geom.call_args[0][0], "BUY_LONG")

        self._run(decision={"action": "WAIT", "entry_price": 100.0,
                            "stop_loss_price": 90.0})
        self.assertEqual(self.geom.call_args[0][0], "SELL_SHORT")

    def test_incomplete_market_data_is_rejected(self):
        action, reason, rr = self._run(package={"data_quality": "partial"})
        self.assertEqual(action, "WAIT")
        self.assertIn("关键原始行情不完整", reason)
        self.assertEqual(rr, 0.0)

    def test_same_direction_position_is_allowed(self):
        action, reason, _ = self._run(context={"active_inst_ids": {"BTC-USDT-SWAP"},
                                               "active_position_sides":
                                                   {"BTC-USDT-SWAP": "long"}})
        self.assertEqual((action, reason), ("BUY_LONG", ""))

    def test_opposite_direction_position_is_rejected(self):
        action, reason, rr = self._run(context={"active_inst_ids": {"BTC-USDT-SWAP"},
                                                "active_position_sides":
                                                    {"BTC-USDT-SWAP": "short"}})
        self.assertEqual(action, "WAIT")
        self.assertIn("已有反向或不兼容持仓", reason)
        self.assertEqual(rr, 0.0)

    def test_position_without_a_side_is_treated_as_incompatible(self):
        action, reason, _ = self._run(context={"active_inst_ids": {"BTC-USDT-SWAP"},
                                               "active_position_sides": {}})
        self.assertEqual(action, "WAIT")
        self.assertIn("反向或不兼容", reason)

    def test_other_instruments_do_not_collide(self):
        action, _, _ = self._run(context={"active_inst_ids": {"ETH-USDT-SWAP"},
                                          "active_position_sides":
                                              {"ETH-USDT-SWAP": "short"}})
        self.assertEqual(action, "BUY_LONG")

    def test_quote_geometry_is_checked_before_plugins(self):
        self.geom.return_value = (False, "几何关系非法", 0.8)
        action, reason, rr = self._run()
        self.assertEqual(action, "WAIT")
        self.assertEqual(reason, "几何关系非法")
        self.assertEqual(rr, 0.8)
        self.load.assert_not_called()

    def test_non_numeric_confidence_is_rejected(self):
        action, reason, rr = self._run(decision={"confidence": "很高"})
        self.assertEqual(action, "WAIT")
        self.assertIn("置信度必须是有效数字", reason)
        self.assertEqual(rr, 2.5)

    def test_none_confidence_counts_as_zero(self):
        action, reason, _ = self._run(decision={"confidence": None})
        self.assertEqual(action, "WAIT")
        self.assertIn("置信度低于安全底线", reason)

    def test_confidence_below_the_floor_is_rejected_with_numbers(self):
        self._start(mock.patch.object(IM, "_conf_floor_for", return_value=72.0))
        action, reason, _ = self._run(decision={"confidence": 71.9})
        self.assertEqual(action, "WAIT")
        self.assertIn("71.9%", reason)
        self.assertIn("72.0%", reason)

    def test_confidence_exactly_at_the_floor_passes(self):
        self._start(mock.patch.object(IM, "_conf_floor_for", return_value=72.0))
        action, _, _ = self._run(decision={"confidence": 72.0})
        self.assertEqual(action, "BUY_LONG")

    def test_per_instrument_floor_is_used(self):
        self._start(mock.patch.object(IM, "_conf_floor_for",
                                      side_effect=lambda inst: 95.0))
        action, reason, _ = self._run(decision={"confidence": 90.0})
        self.assertEqual(action, "WAIT")
        self.assertIn("95.0%", reason)

    def test_short_decisions_pass_the_floor(self):
        self.geom.return_value = (True, "", 3.0)
        action, reason, rr = self._run(decision={"action": "SELL_SHORT",
                                                 "confidence": 90.0})
        self.assertEqual((action, reason, rr), ("SELL_SHORT", "", 3.0))


class PipelinePluginTests(_PipelineBase):
    def test_disabled_plugins_are_skipped(self):
        self._write_plugin("off.py", "X = 1\n")
        self._write_config(order=["off.py"], enabled={"off.py": False})
        self.assertEqual(self._run()[0], "BUY_LONG")
        self.load.assert_not_called()

    def test_enabled_plugin_missing_on_disk_is_rejected(self):
        self._write_config(order=["ghost.py"], enabled={"ghost.py": True})
        action, reason, _ = self._run()
        self.assertEqual(action, "WAIT")
        self.assertIn("文件缺失", reason)
        self.assertIn("ghost.py", reason)

    def test_plugin_without_check_risk_is_rejected(self):
        self._enable("naked.py")
        self.load.return_value = types.SimpleNamespace()      # 没有 check_risk
        action, reason, _ = self._run()
        self.assertEqual(action, "WAIT")
        self.assertIn("缺少 check_risk 入口", reason)

    def test_plugin_rejection_is_propagated(self):
        self._enable("mine.py", check_risk=lambda p, d, c: (False, "宏观方向不符"))
        action, reason, _ = self._run()
        self.assertEqual(action, "WAIT")
        self.assertEqual(reason, "宏观方向不符")

    def test_empty_rejection_reason_gets_a_default(self):
        self._enable("mine.py", check_risk=lambda p, d, c: (False, ""))
        action, reason, _ = self._run()
        self.assertEqual(action, "WAIT")
        self.assertIn("触发风控拦截插件", reason)
        self.assertIn("mine.py", reason)

    def test_plugin_exception_is_fail_closed_and_logged(self):
        def _boom(p, d, c):
            raise RuntimeError("插件内部炸了")

        self._enable("mine.py", check_risk=_boom)
        with self.assertLogs(IM.logger, level="ERROR") as captured:
            action, reason, _ = self._run()
        self.assertEqual(action, "WAIT")
        self.assertIn("运行异常", reason)
        self.assertIn("插件内部炸了", reason)
        self.assertTrue(any("Error executing interceptor plugin" in m
                            for m in captured.output))

    def test_a_passing_plugin_keeps_the_raw_action(self):
        self._enable("mine.py", check_risk=lambda p, d, c: (True, ""))
        self.assertEqual(self._run(), ("BUY_LONG", "", 2.5))

    def test_plugins_receive_deep_copies_not_the_originals(self):
        """用户代码拿到的必须是副本 —— 改它不能影响核心判定（按身份断言）。"""
        seen = {}

        def _spy(pkg, dec, ctx):
            seen["pkg"], seen["dec"], seen["ctx"] = pkg, dec, ctx
            pkg["data_quality"] = "valid"        # 试图"修复"输入
            dec["confidence"] = 100.0
            return True, ""

        self._enable("mine.py", check_risk=_spy)
        self._run()
        self.assertIsNot(seen["pkg"], self.pkg)
        self.assertIsNot(seen["dec"], self.dec)
        self.assertIsNot(seen["ctx"], self.ctx)
        self.assertEqual(self.dec["confidence"], 90.0, "原决策对象未被插件改写")
        self.assertEqual(self.pkg["data_quality"], "valid")
        self.assertEqual(seen["pkg"], self.pkg, "只是副本，内容初始一致")

    def test_plugins_run_in_the_listed_order(self):
        self._write_plugin("a.py", "X = 1\n")
        self._write_plugin("b.py", "X = 1\n")
        self._write_config(order=["a.py", "b.py"], enabled={"a.py": True, "b.py": True})
        order = []
        self.load.side_effect = lambda path: types.SimpleNamespace(
            check_risk=lambda p, d, c: (order.append(path.name), (True, ""))[1])
        self._run()
        self.assertEqual(order, ["a.py", "b.py"])

    def test_first_rejection_stops_the_pipeline(self):
        self._write_plugin("a.py", "X = 1\n")
        self._write_plugin("b.py", "X = 1\n")
        self._write_config(order=["a.py", "b.py"], enabled={"a.py": True, "b.py": True})
        calls = []

        def _load(path):
            calls.append(path.name)
            if path.name == "a.py":
                return types.SimpleNamespace(check_risk=lambda p, d, c: (False, "拦了"))
            return types.SimpleNamespace(check_risk=lambda p, d, c: (True, ""))

        self.load.side_effect = _load
        self.assertEqual(self._run()[1], "拦了")
        self.assertEqual(calls, ["a.py"], "被拦后不再执行后续插件")

    def test_plugin_without_name_falls_back_to_the_filename(self):
        self._enable("mine.py", check_risk=lambda p, d, c: (False, ""))
        action, reason, _ = self._run()
        self.assertIn("mine.py", reason)


class SandboxTestTests(_Base):
    def setUp(self):
        super().setUp()
        self.run = self._start(mock.patch.object(
            IM, "run_interceptor_pipeline", return_value=("WAIT", "拦了", 2.3456)))
        self._write_config(order=IM.DEFAULT_ORDER, enabled=IM.DEFAULT_ENABLED)

    def test_default_scenarios_are_four_and_summarised(self):
        out = IM.run_sandbox_test()
        self.assertEqual(out["status"], "success")
        self.assertEqual(len(out["results"]), 4)
        self.assertEqual(out["total_plugins_count"], len(IM.DEFAULT_ORDER))
        self.assertEqual(out["enabled_plugins_count"], 4)
        self.assertEqual(self.run.call_count, 4)

    def test_custom_scenario_is_appended(self):
        out = IM.run_sandbox_test({"name": "自定义", "package": {}, "decision":
                                   {"action": "BUY_LONG"}, "context": {}})
        self.assertEqual(len(out["results"]), 5)
        self.assertEqual(out["results"][-1]["scenario"], "自定义")

    def test_intercepted_flag_requires_a_non_wait_raw_action(self):
        out = IM.run_sandbox_test()
        self.assertTrue(all(r["intercepted"] for r in out["results"]))

        self.run.return_value = ("WAIT", "", 0.0)
        out = IM.run_sandbox_test({"name": "本来就是 WAIT",
                                   "package": {}, "decision": {"action": "WAIT"},
                                   "context": {}})
        self.assertIs(out["results"][-1]["intercepted"], False)

    def test_risk_reward_formatting(self):
        self.run.return_value = ("BUY_LONG", "", 2.3456)
        out = IM.run_sandbox_test()
        self.assertEqual(out["results"][0]["risk_reward"], "2.35R")
        self.assertEqual(out["results"][0]["final_action"], "BUY_LONG")
        self.assertIs(out["results"][0]["intercepted"], False)

        self.run.return_value = ("WAIT", "x", 0.0)
        out = IM.run_sandbox_test()
        self.assertEqual(out["results"][0]["risk_reward"], "--")

    def test_timing_fields_are_present(self):
        out = IM.run_sandbox_test()
        self.assertIn("duration_total_ms", out)
        for result in out["results"]:
            self.assertIn("duration_ms", result)
            self.assertIn("raw_action", result)

    def test_raw_action_comes_from_the_scenario(self):
        out = IM.run_sandbox_test()
        self.assertEqual([r["raw_action"] for r in out["results"]],
                         ["SELL_SHORT", "BUY_LONG", "BUY_LONG", "BUY_LONG"])


if __name__ == "__main__":
    unittest.main()

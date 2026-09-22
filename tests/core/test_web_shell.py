"""Web 外壳：**HTML 永不缓存、静态资源长缓存、条件挂载、以及"外壳不许绑回库"的边界**（第二百八十四刀，开新面 web_shell.py）。

先打印整个文件（108 行）再动笔。它是从 `dashboard_cache.py` 里拆出来的**外壳**：
静态目录挂载 + SPA 壳渲染 + 长缓存静态处理器。

| 语义 | 口径 |
|---|---|
| ★ **HTML 壳严格不缓存** | `no-cache, no-store, must-revalidate, max-age=0` + `Pragma`/`Expires` 三件套 ⇒ 用户永远立刻拿到最新 Vite 包（否则改完前端用户还在看旧包）|
| ★ **静态资源长缓存** | `assets` 走 `immutable` 一年（文件名带 hash，可安全 immutable）；`coins`/`docs/images` 走 7 天 + `stale-while-revalidate` |
| ★ **条件挂载，缺目录不报错** | `/static` 无条件；`/assets`、`/coins`、`/docs/images`、`/images` 仅在目录存在时挂 —— 镜像/裸仓库里没有 `dist` 时应用仍能起 |
| ★ **单一 SPA 构建** | `VUE_ADMIN_DIST_DIR is VUE_DIST_DIR`：`/` 与 `/admin/*` 共用同一份构建产物 |
| ★ **外壳不许绑回库（边界）** | 模块 docstring 明写：本模块**不 import `r20_backend.dashboard_cache`**，也不被它 import —— 否则又把外壳绑回库文件。本刀用 **AST** 验（docstring 里提到它很多次，纯文本 grep 会误判）|
| ★ **不用任何全局可变态** | 路径常量全部由本文件位置推导，不 import `dependencies`，避免与 config/settings 的导入链互相牵扯 |
"""

import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from r20_backend import web_shell as WS


class _FakeApp:
    """只记录 mount 调用（外壳的全部落点）。"""

    def __init__(self):
        self.mounts = []

    def mount(self, path, app=None, name=None):
        self.mounts.append({"path": path, "app": app, "name": name,
                            "cls": type(app).__name__,
                            "cache_control": getattr(app, "cache_control", None)})

    def paths(self):
        return [m["path"] for m in self.mounts]


def _isdir_with(overrides):
    real = os.path.isdir

    def _isdir(path, *args, **kwargs):
        if path in overrides:
            return overrides[path]
        return real(path, *args, **kwargs)

    return _isdir


class PathConstantTests(unittest.TestCase):
    def test_workspace_dir_is_the_repo_root(self):
        self.assertEqual(WS.WORKSPACE_DIR, str(Path(WS.__file__).resolve().parents[1]))

    def test_web_root_dir_is_this_package_directory(self):
        """第 143 刀：`dashboard/` 并入本包，所以这里必须落在 `r20_backend/`。"""
        self.assertEqual(WS.WEB_ROOT_DIR, str(Path(WS.__file__).resolve().parent))
        self.assertTrue(WS.WEB_ROOT_DIR.endswith("r20_backend"))

    def test_the_vue_paths_hang_off_the_workspace(self):
        self.assertEqual(WS.VUE_DIST_DIR, os.path.join(WS.WORKSPACE_DIR, "frontend", "dist"))
        self.assertEqual(WS.VUE_ASSETS_DIR, os.path.join(WS.VUE_DIST_DIR, "assets"))
        self.assertEqual(WS.VUE_COINS_DIR, os.path.join(WS.VUE_DIST_DIR, "coins"))
        self.assertEqual(WS.DOCS_IMAGES_DIR, os.path.join(WS.WORKSPACE_DIR, "docs", "images"))

    def test_one_build_serves_both_root_and_admin(self):
        self.assertIs(WS.VUE_ADMIN_DIST_DIR, WS.VUE_DIST_DIR)

    def test_the_legacy_admin_file_is_under_the_admin_folder(self):
        self.assertEqual(WS.VUE_ADMIN_LEGACY_FILE,
                         os.path.join(WS.VUE_DIST_DIR, "admin", "legacy.html"))

    def test_every_constant_is_an_absolute_string(self):
        for name in ("WORKSPACE_DIR", "WEB_ROOT_DIR", "VUE_DIST_DIR", "VUE_ASSETS_DIR",
                     "VUE_COINS_DIR", "DOCS_IMAGES_DIR", "VUE_ADMIN_DIST_DIR",
                     "VUE_ADMIN_LEGACY_FILE"):
            with self.subTest(name=name):
                value = getattr(WS, name)
                self.assertIsInstance(value, str)
                self.assertTrue(os.path.isabs(value), f"{name} 必须是绝对路径")

    def test_the_public_surface_is_declared(self):
        for name in ("CachedStaticFiles", "templates", "serve_vue_spa",
                     "mount_static_assets", "WORKSPACE_DIR"):
            self.assertIn(name, WS.__all__)

    def test_templates_point_at_the_package_templates(self):
        self.assertIsInstance(WS.templates, Jinja2Templates)
        self.assertEqual(WS.templates.env.loader.searchpath,
                         [os.path.join(WS.WEB_ROOT_DIR, "templates")])

    def test_the_module_does_not_import_its_library(self):
        """★ 边界契约：外壳**不 import** `r20_backend.dashboard_cache`（也不被它 import）。

        ⚠️ 这里必须用 AST 而不是文本搜索 —— 该模块的 docstring 里"`dashboard_cache.py`"
        出现多次，纯 grep 会把这层注释当成违规。
        """
        tree = ast.parse(Path(WS.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offenders = {m for m in imported if "dashboard_cache" in m}
        self.assertEqual(offenders, set(), f"外壳不许绑回库：{offenders}")
        self.assertFalse(any(m.startswith("r20_backend.dependencies") for m in imported),
                         "路径常量必须自推导，不 import dependencies（避免导入链互相牵扯）")

    def test_the_module_only_needs_fastapi_and_the_stdlib(self):
        tree = ast.parse(Path(WS.__file__).read_text(encoding="utf-8"))
        tops = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                tops.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                tops.update(a.name.split(".")[0] for a in node.names)
        self.assertTrue(tops <= {"os", "pathlib", "fastapi", "__future__"},
                        f"出现了预期外的依赖：{tops - {'os', 'pathlib', 'fastapi', '__future__'}}")


class CachedStaticFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "app.js"
        self.file.write_text("console.log(1)", encoding="utf-8")
        self.scope = {"type": "http", "method": "GET", "headers": [],
                      "path": "/assets/app.js", "query_string": b""}

    def _response(self, cache_control=None):
        if cache_control is None:
            handler = WS.CachedStaticFiles(directory=self.tmp.name)
        else:
            handler = WS.CachedStaticFiles(directory=self.tmp.name,
                                           cache_control=cache_control)
        return handler.file_response(str(self.file), os.stat(self.file), self.scope)

    def test_it_is_a_static_files_subclass(self):
        self.assertTrue(issubclass(WS.CachedStaticFiles, StaticFiles))

    def test_the_default_cache_control_is_one_year_immutable(self):
        self.assertEqual(WS.CachedStaticFiles(directory=self.tmp.name).cache_control,
                         "public, max-age=31536000, immutable")

    def test_a_custom_cache_control_is_stored(self):
        handler = WS.CachedStaticFiles(directory=self.tmp.name,
                                       cache_control="public, max-age=60")
        self.assertEqual(handler.cache_control, "public, max-age=60")

    def test_file_response_injects_the_header(self):
        response = self._response()
        self.assertEqual(response.headers["Cache-Control"],
                         "public, max-age=31536000, immutable")

    def test_a_custom_value_reaches_the_response(self):
        response = self._response("public, max-age=604800, stale-while-revalidate=86400")
        self.assertEqual(response.headers["Cache-Control"],
                         "public, max-age=604800, stale-while-revalidate=86400")

    def test_the_header_overwrites_whatever_the_parent_set(self):
        with mock.patch.object(StaticFiles, "file_response",
                               return_value=Response(headers={"Cache-Control": "old-value"})):
            response = self._response("public, max-age=1")
        self.assertEqual(response.headers["Cache-Control"], "public, max-age=1")

    def test_the_parent_response_is_returned_not_replaced(self):
        marker = Response(content="x")
        with mock.patch.object(StaticFiles, "file_response", return_value=marker) as spy:
            out = WS.CachedStaticFiles(directory=self.tmp.name).file_response(
                str(self.file), os.stat(self.file), self.scope)
        self.assertIs(out, marker)
        spy.assert_called_once_with(str(self.file), os.stat(self.file), self.scope)

    def test_extra_kwargs_are_forwarded_to_staticfiles(self):
        handler = WS.CachedStaticFiles(directory=self.tmp.name, html=True)
        self.assertTrue(handler.html)

    def test_it_still_serves_the_real_bytes(self):
        response = self._response()
        self.assertEqual(Path(response.path).read_text(encoding="utf-8"), "console.log(1)")


class ServeVueSpaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.html = Path(self.tmp.name) / "index.html"
        self.html.write_text("<!doctype html><title>R20</title>", encoding="utf-8")

    def _serve(self, **kwargs):
        return WS.serve_vue_spa(str(self.html), **kwargs)

    def test_it_returns_the_file_content(self):
        self.assertEqual(self._serve().body.decode("utf-8"),
                         "<!doctype html><title>R20</title>")

    def test_it_is_an_html_response(self):
        self.assertIsInstance(self._serve(), HTMLResponse)
        self.assertTrue(self._serve().media_type.startswith("text/html"))

    def test_the_shell_must_never_be_cached(self):
        headers = self._serve().headers
        self.assertEqual(headers["Cache-Control"],
                         "no-cache, no-store, must-revalidate, max-age=0")
        self.assertEqual(headers["Pragma"], "no-cache")
        self.assertEqual(headers["Expires"], "0")

    def test_utf8_content_survives(self):
        self.html.write_text("<!doctype html><title>R20量子交易系统</title>",
                             encoding="utf-8")
        self.assertIn("R20量子交易系统", self._serve().body.decode("utf-8"))

    def test_the_file_is_read_fresh_each_call(self):
        """壳不许在进程内缓存 —— 否则重新部署后用户拿不到新壳。"""
        first = self._serve().body
        self.html.write_text("新壳", encoding="utf-8")
        self.assertNotEqual(self._serve().body, first)
        self.assertEqual(self._serve().body.decode("utf-8"), "新壳")

    def test_a_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            WS.serve_vue_spa(str(Path(self.tmp.name) / "nope.html"))

    def test_is_public_is_currently_ignored(self):
        """⚠️ 实测：`is_public` 形参目前**完全没被用到** —— 传 False 也照样返回
        `is_public=True` 时那份公开壳（同样的不缓存头、同样的内容）。
        按实际行为钉住，免得调用方以为它区分了公开/非公开壳。"""
        self.assertEqual(self._serve(is_public=False).body, self._serve(is_public=True).body)
        self.assertEqual(self._serve(is_public=False).headers["Cache-Control"],
                         self._serve(is_public=True).headers["Cache-Control"])

    def test_it_reads_as_utf8(self):
        self.html.write_bytes("中文".encode("utf-8"))
        self.assertEqual(self._serve().body.decode("utf-8"), "中文")


class MountStaticAssetsTests(unittest.TestCase):
    def _mount(self, overrides=None):
        app = _FakeApp()
        with mock.patch.object(os.path, "isdir", _isdir_with(overrides or {})):
            WS.mount_static_assets(app)
        return app

    def test_static_is_always_mounted(self):
        app = self._mount({WS.VUE_ASSETS_DIR: False, WS.VUE_COINS_DIR: False,
                           WS.DOCS_IMAGES_DIR: False})
        self.assertEqual(app.paths(), ["/static"])
        self.assertEqual(app.mounts[0]["name"], "static")
        self.assertEqual(app.mounts[0]["cls"], "StaticFiles")

    def test_assets_is_mounted_when_present_with_immutable_caching(self):
        app = self._mount({WS.VUE_ASSETS_DIR: True, WS.VUE_COINS_DIR: False,
                           WS.DOCS_IMAGES_DIR: False})
        self.assertIn("/assets", app.paths())
        entry = next(m for m in app.mounts if m["path"] == "/assets")
        self.assertEqual(entry["cls"], "CachedStaticFiles")
        self.assertEqual(entry["cache_control"], "public, max-age=31536000, immutable")

    def test_assets_is_skipped_when_absent(self):
        app = self._mount({WS.VUE_ASSETS_DIR: False, WS.VUE_COINS_DIR: False,
                           WS.DOCS_IMAGES_DIR: False})
        self.assertNotIn("/assets", app.paths())

    def test_coins_uses_a_week_with_stale_while_revalidate(self):
        app = self._mount({WS.VUE_ASSETS_DIR: False, WS.VUE_COINS_DIR: True,
                           WS.DOCS_IMAGES_DIR: False})
        entry = next(m for m in app.mounts if m["path"] == "/coins")
        self.assertEqual(entry["cache_control"],
                         "public, max-age=604800, stale-while-revalidate=86400")

    def test_coins_is_skipped_when_absent(self):
        app = self._mount({WS.VUE_ASSETS_DIR: False, WS.VUE_COINS_DIR: False,
                           WS.DOCS_IMAGES_DIR: False})
        self.assertNotIn("/coins", app.paths())

    def test_both_image_paths_are_mounted_together(self):
        app = self._mount({WS.VUE_ASSETS_DIR: False, WS.VUE_COINS_DIR: False,
                           WS.DOCS_IMAGES_DIR: True})
        self.assertIn("/docs/images", app.paths())
        self.assertIn("/images", app.paths())
        for path in ("/docs/images", "/images"):
            entry = next(m for m in app.mounts if m["path"] == path)
            self.assertEqual(entry["cache_control"],
                             "public, max-age=604800, stale-while-revalidate=86400")

    def test_both_image_paths_are_skipped_when_absent(self):
        app = self._mount({WS.VUE_ASSETS_DIR: False, WS.VUE_COINS_DIR: False,
                           WS.DOCS_IMAGES_DIR: False})
        self.assertNotIn("/docs/images", app.paths())
        self.assertNotIn("/images", app.paths())

    def test_everything_present_mounts_all_five(self):
        app = self._mount({WS.VUE_ASSETS_DIR: True, WS.VUE_COINS_DIR: True,
                           WS.DOCS_IMAGES_DIR: True})
        self.assertEqual(app.paths(),
                         ["/static", "/assets", "/coins", "/docs/images", "/images"])

    def test_a_bare_checkout_still_starts(self):
        """没有 dist ⇒ 只剩 `/static`，**不抛异常**（镜像里不带前端产物也能起）。"""
        app = self._mount({WS.VUE_ASSETS_DIR: False, WS.VUE_COINS_DIR: False,
                           WS.DOCS_IMAGES_DIR: False})
        self.assertEqual(len(app.mounts), 1)

    def test_mount_names_are_unique(self):
        app = self._mount({WS.VUE_ASSETS_DIR: True, WS.VUE_COINS_DIR: True,
                           WS.DOCS_IMAGES_DIR: True})
        names = [m["name"] for m in app.mounts]
        self.assertEqual(len(names), len(set(names)))

    def test_the_registered_names_are_stable(self):
        """挂载名是 `app.mount(..., name=...)` 的对外契约（反向路由/测试都按名找）。"""
        app = self._mount({WS.VUE_ASSETS_DIR: True, WS.VUE_COINS_DIR: True,
                           WS.DOCS_IMAGES_DIR: True})
        self.assertEqual([m["name"] for m in app.mounts],
                         ["static", "vue_assets", "vue_coins", "docs_images", "images"])

    def test_the_real_workspace_matches_its_own_directory_state(self):
        """环境无关的交叉验证：`/static` 恒 1；assets/coins 各算 1；
        ⚠️ `DOCS_IMAGES_DIR` 存在时一口气挂 **两条**路径（`/docs/images` 与 `/images`）⇒ 算 2。"""
        app = self._mount()
        expected = (1
                    + int(os.path.isdir(WS.VUE_ASSETS_DIR))
                    + int(os.path.isdir(WS.VUE_COINS_DIR))
                    + (2 if os.path.isdir(WS.DOCS_IMAGES_DIR) else 0))
        self.assertEqual(len(app.mounts), expected)

    def test_static_points_at_the_package_static_dir(self):
        app = self._mount()
        entry = app.mounts[0]
        self.assertEqual(entry["app"].directory, os.path.join(WS.WEB_ROOT_DIR, "static"))

    def test_it_returns_none(self):
        app = _FakeApp()
        with mock.patch.object(os.path, "isdir", _isdir_with({})):
            self.assertIsNone(WS.mount_static_assets(app))

    def test_a_mount_failure_propagates(self):
        """挂载失败必须炸出来 —— 静默降级会让前端整站 404 却看起来"启动成功"。"""
        class _Boom(_FakeApp):
            def mount(self, *a, **k):
                raise RuntimeError("挂载失败")

        with mock.patch.object(os.path, "isdir", _isdir_with({})):
            with self.assertRaises(RuntimeError):
                WS.mount_static_assets(_Boom())


if __name__ == "__main__":
    unittest.main()

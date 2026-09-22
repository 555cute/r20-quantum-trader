"""仪表盘/行情/外壳路由：**数据诚实——错误绝不伪装成真 K 线、路径不许逃逸**（第二百六十刀，开新面 routers/dashboard.py）。

先打印整个文件（324 行）再动笔。15 个处理器，按三条主线钉住：

| 语义 | 口径 |
|---|---|
| ★ **错误不伪装成数据** | `market_candles` 行情全链失败时，**不再**返回合成的 OHLC 爬升曲线（与真 K 线不可分辨）；改为有界陈旧缓存（≤300s，显式 `stale_cache`+`warn`）否则 `source:"error"` + 空数组 |
| ★ **路径不许逃逸** | `docs_images` 用 `Path(img_name).name` 剥离目录；`admin_page` 真实文件优先但必须 `is_relative_to(admin_root)`，逃逸/目录/未知仍回 Vue 壳 |
| ★ **公开面边界要说清** | `/api/v1/cache/{resource}` 只有 `ledger` 要管理员头，其余公开；未知资源 ⇒ **404**（不是空对象）|
| K 线形状 | bar 过 `normalize_bar` 后必须在合法集合内否则回落 `1H`；`limit` 钳到 **[10,300]**；上游行按时间**反转**成升序并强转 float，坏行跳过 |
| 权益曲线 | 只认显式 `closed/已平仓/completed`；**环境隔离**：异环境行排除并计数、无环境列旧行保留但计数暴露；`days` 钳到 **[5,60]** |
| 外壳 | 真实文件优先（Vue dist → frontend 回退 → 内联/SDK 兜底），dist 缺失时回退模板或 503 |

⚠️ 如实记录：`equity_history` 直接用 `ROOT/data/...` 读**生产文件**（不经 `DATA_DIR`
也不经沙箱缝）—— 本刀测试把 `ROOT` 钉到临时目录，未改代码。
"""

import asyncio
import datetime as dt
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

from r20_backend.routers import dashboard as A


def _run(coro):
    return asyncio.run(coro)


def _body(response):
    return json.loads(bytes(response.body).decode("utf-8"))


class _Base(unittest.TestCase):
    def setUp(self):
        def _start(patcher):
            patcher.start()
            self.addCleanup(patcher.stop)
            return patcher

        def _patch(target, new=mock.DEFAULT, **kwargs):
            return _start(mock.patch.object(A, target, new, **kwargs))

        self._start = _start
        self._patch = _patch
        self.admin = mock.Mock()
        _patch("require_admin_header", self.admin)
        self.read_json = mock.Mock(return_value={"k": 1})
        _patch("read_json", self.read_json)
        self.okx = mock.Mock()
        _patch("okx", self.okx)
        A._CANDLES_CACHE.clear()
        self.addCleanup(A._CANDLES_CACHE.clear)


class ThinShellTests(_Base):
    def test_all_data_is_a_thin_async_shell_over_the_cache_module(self):
        impl = mock.AsyncMock(return_value={"ok": 1})
        self._start(mock.patch.object(A.dash_app, "get_all_data", impl))
        self.assertEqual(_run(A.get_all_data(full=True)), {"ok": 1})
        impl.assert_awaited_once_with(full=True)

    def test_overview_is_a_thin_async_shell(self):
        impl = mock.AsyncMock(return_value={"ov": 1})
        self._start(mock.patch.object(A.dash_app, "get_overview", impl))
        self.assertEqual(_run(A.get_overview()), {"ov": 1})
        impl.assert_awaited_once_with()


class CacheRouteTests(_Base):
    def test_unknown_resource_is_404_not_an_empty_object(self):
        with self.assertRaises(HTTPException) as ctx:
            A.cache("nope")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertNotIn("nope", ctx.exception.detail)

    def test_public_resources_do_not_require_an_admin_header(self):
        out = A.cache("decisions")
        self.assertIsInstance(out, JSONResponse)
        self.admin.assert_not_called()
        self.read_json.assert_called_once_with("ai_brain_decisions.json", {})
        self.assertEqual(_body(out), {"k": 1})

    def test_ledger_requires_admin_and_defaults_to_a_list(self):
        self.read_json.return_value = []
        out = A.cache("ledger", x_r20_admin_token="tok", x_r20_session="s")
        self.admin.assert_called_once_with("tok", "s")
        self.read_json.assert_called_once_with("trading_ledger.json", [])
        self.assertEqual(_body(out), [])

    def test_brain_history_is_a_public_resource(self):
        A.cache("brain-history")
        self.read_json.assert_called_once_with("ai_brain_history.json", {})


class MarketRouteTests(_Base):
    def test_non_swap_symbol_is_400(self):
        with self.assertRaises(HTTPException) as ctx:
            A.market("BTC-USDT")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_ticker_first_row_is_returned(self):
        self.okx.ticker.return_value = [{"last": "1"}, {"last": "2"}]
        out = A.market("BTC-USDT-SWAP")
        self.assertEqual(out["ticker"], {"last": "1"})
        self.assertEqual(out["source"], "OKX REST")
        self.okx.ticker.assert_called_once_with("BTC-USDT-SWAP")

    def test_empty_ticker_is_an_empty_object_not_a_crash(self):
        self.okx.ticker.return_value = []
        self.assertEqual(A.market("BTC-USDT-SWAP")["ticker"], {})

    def test_upstream_failure_is_502(self):
        self.okx.ticker.side_effect = RuntimeError("network down")
        with self.assertRaises(HTTPException) as ctx:
            A.market("BTC-USDT-SWAP")
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("network down", ctx.exception.detail)


class CandlesRouteTests(_Base):
    def setUp(self):
        super().setUp()
        self.fetch = mock.Mock(return_value=[[100, "1", "2", "0.5", "1.5", "9"]])
        self._start(mock.patch("scripts.market_data_service.fetch_candles", self.fetch))

    def test_non_swap_symbol_is_400_and_headers_are_still_set(self):
        resp = Response()
        with self.assertRaises(HTTPException) as ctx:
            A.market_candles("BTC-USDT", response=resp)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("no-store", resp.headers["Cache-Control"])

    def test_rows_are_reversed_to_ascending_and_coerced_to_floats(self):
        self.fetch.return_value = [[1, "1", "2", "0.5", "1.5", "9"],
                                   [2, "3", "4", "2.5", "3.5", "8"]]
        out = A.market_candles("BTC-USDT-SWAP", bar="1h", limit=50)
        self.assertEqual(out["source"], "OKX REST")
        self.assertEqual(out["bar"], "1H", "真实 normalize_bar 把 1h 归成 1H")
        self.assertEqual(out["candles"][0]["ts"], 2, "上游倒序 ⇒ 归一成升序（旧→新）")
        self.assertEqual(out["candles"][1]["ts"], 1)
        self.assertIsInstance(out["candles"][0]["close"], float)

    def test_illegal_bar_falls_back_to_one_hour_and_limit_is_clamped(self):
        A.market_candles("BTC-USDT-SWAP", bar="7m", limit=1)
        self.assertEqual(self.fetch.call_args.kwargs["bar"], "1H")
        self.assertEqual(self.fetch.call_args.kwargs["limit"], 10)
        A.market_candles("BTC-USDT-SWAP", bar="1H", limit=9999)
        self.assertEqual(self.fetch.call_args.kwargs["limit"], 300)

    def test_recent_cache_hit_skips_the_upstream(self):
        A.market_candles("BTC-USDT-SWAP", bar="1H", limit=50)
        self.fetch.reset_mock()
        out = A.market_candles("BTC-USDT-SWAP", bar="1H", limit=50)
        self.assertEqual(out["source"], "cache")
        self.fetch.assert_not_called()

    def test_bad_rows_are_skipped_and_a_valid_row_survives(self):
        self.fetch.return_value = [[1, "1", "2", "0.5", "1.5", "9"],
                                   ["bad", "x"], "not-a-row"]
        out = A.market_candles("BTC-USDT-SWAP", bar="1H", limit=50)
        self.assertEqual(len(out["candles"]), 1)

    def test_all_bad_rows_use_a_bounded_stale_cache_with_an_explicit_warn(self):
        A.market_candles("BTC-USDT-SWAP", bar="1H", limit=50)   # 先灌一条缓存
        key = "BTC-USDT-SWAP:1H:50"
        A._CANDLES_CACHE[key] = (time.time() - 120.0, A._CANDLES_CACHE[key][1])
        self.fetch.return_value = []
        out = A.market_candles("BTC-USDT-SWAP", bar="1H", limit=50)
        self.assertEqual(out["source"], "stale_cache")
        self.assertIn("warn", out)
        self.assertGreaterEqual(out["cache_age_seconds"], 100)

    def test_no_cache_and_no_upstream_is_an_honest_error(self):
        self.fetch.side_effect = RuntimeError("all levels exhausted")
        out = A.market_candles("BTC-USDT-SWAP", bar="1H", limit=50)
        self.assertEqual(out["source"], "error")
        self.assertEqual(out["candles"], [])
        self.assertIn("all levels exhausted", out["detail"])

    def test_expired_cache_is_not_used_as_a_silent_fallback(self):
        A.market_candles("BTC-USDT-SWAP", bar="1H", limit=50)
        key = "BTC-USDT-SWAP:1H:50"
        A._CANDLES_CACHE[key] = (time.time() - 400.0, A._CANDLES_CACHE[key][1])
        self.fetch.side_effect = RuntimeError("down")
        out = A.market_candles("BTC-USDT-SWAP", bar="1H", limit=50)
        self.assertEqual(out["source"], "error", "超过 300s 的陈旧缓存不得冒充兜底")

    def test_normalize_import_failure_keeps_the_requested_bar(self):
        import sys as _sys
        with mock.patch.dict(_sys.modules, {"scripts.market_data_service": None}):
            out = A.market_candles("BTC-USDT-SWAP", bar="1H", limit=50)
        self.assertEqual(out["bar"], "1H", "归一器导入失败 ⇒ 保留原样，不臆造")
        self.assertEqual(out["source"], "error")


class EquityHistoryTests(_Base):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-dash-equity-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "data").mkdir(parents=True, exist_ok=True)
        self._patch("ROOT", self.tmp)
        self.env = mock.Mock(mode="live")
        self._start(mock.patch("scripts.okx_runtime.current_environment",
                               return_value=self.env))
        self.tz8 = dt.timezone(dt.timedelta(hours=8))
        self.now = dt.datetime.now(self.tz8)

    def _write(self, name, payload):
        (self.tmp / "data" / name).write_text(json.dumps(payload), encoding="utf-8")

    def _ledger_row(self, *, status="closed", env="live", pnl=10.0, when=None, key="net_pnl"):
        return {"status": status, "environment": env,
                "close_time": (when or self.now).isoformat(), key: pnl}

    def test_closed_only_with_environment_isolation_and_disclosed_counts(self):
        self._write("account_initial_state.json", {"initial_capital": 1000})
        self._write("trading_ledger.json", [
            self._ledger_row(pnl=10.0),                                   # 计入
            self._ledger_row(pnl=999.0, env="demo"),                      # 异环境 ⇒ 排除
            self._ledger_row(pnl=5.0, env=""),                            # 旧行 ⇒ 计入但计数
            self._ledger_row(status="open", pnl=777.0),                   # 未结清 ⇒ 跳过
            self._ledger_row(when=dt.datetime(2020, 1, 1)),               # 太旧但仍是有效结清行
            {"status": "closed", "environment": "live", "close_time": "bad"},
            "not-a-dict",
        ])
        out = A.equity_history(days=5)
        self.assertEqual(out["excluded_rows"],
                         {"other_environment": 1, "unlabeled_environment": 1})
        self.assertEqual(out["initial_capital"], 1000)
        self.assertEqual(out["environment"], "live")
        self.assertEqual(out["source"], "trading_ledger")
        self.assertEqual(len(out["days"]), 5)
        # 10（今日 live）+ 5（今日旧行）+ 10（2020 年那条，先计入 base） = 1025
        self.assertEqual(out["days"][-1]["equity"], 1025.0)

    def test_missing_initial_capital_stays_none_and_bad_ledger_reports_an_error(self):
        self._write("trading_ledger.json", [])
        out = A.equity_history(days=5)
        self.assertIsNone(out["initial_capital"])
        self.assertEqual(out["days"][-1]["equity"], 0.0)

        (self.tmp / "data" / "trading_ledger.json").write_text("{not json", encoding="utf-8")
        out = A.equity_history(days=5)
        self.assertIn("error", out)

    def test_days_are_clamped_and_invalid_input_falls_back_to_fourteen(self):
        self._write("trading_ledger.json", [])
        self.assertEqual(len(A.equity_history(days=1)["days"]), 5)
        self.assertEqual(len(A.equity_history(days=999)["days"]), 60)
        self.assertEqual(len(A.equity_history(days="abc")["days"]), 14)

    def test_pnl_fallback_field_and_non_numeric_rows(self):
        self._write("account_initial_state.json", {"initial_capital": 100})
        self._write("trading_ledger.json", [
            self._ledger_row(pnl=7.0, key="pnl"),
            self._ledger_row(pnl="oops"),
        ])
        out = A.equity_history(days=5)
        self.assertEqual(out["days"][-1]["equity"], 107.0,
                         "非数值 pnl 只跳过该行，不影响其它行")

    def test_empty_environment_mode_leaves_rows_unfiltered(self):
        self.env.mode = ""  # 读不到环境 ⇒ 不做隔离
        self._write("trading_ledger.json", [self._ledger_row(pnl=3.0, env="demo")])
        out = A.equity_history(days=5)
        self.assertEqual(out["excluded_rows"],
                         {"other_environment": 0, "unlabeled_environment": 0})
        self.assertEqual(out["days"][-1]["equity"], 3.0)

    def test_environment_lookup_failure_degrades_to_none(self):
        self._start(mock.patch("scripts.okx_runtime.current_environment",
                               side_effect=RuntimeError("no env")))
        self._write("trading_ledger.json", [])
        self.assertIsNone(A.equity_history(days=5)["environment"])

    def test_corrupt_initial_state_degrades_the_capital_to_none(self):
        (self.tmp / "data" / "account_initial_state.json").write_text(
            "{not json", encoding="utf-8")
        self._write("trading_ledger.json", [])
        out = A.equity_history(days=5)
        self.assertIsNone(out["initial_capital"])
        self.assertEqual(out["days"][-1]["equity"], 0.0)

    def test_unparseable_close_time_rows_are_skipped_not_defaulted(self):
        self._write("account_initial_state.json", {"initial_capital": 100})
        self._write("trading_ledger.json", [self._ledger_row(pnl=50.0)])
        self._start(mock.patch.object(A, "beijing_day", return_value=""))
        out = A.equity_history(days=5)
        self.assertEqual(out["days"][-1]["equity"], 100.0, "时点读不出来 ⇒ 不猜、不计入")

    def test_a_non_iso_day_key_before_start_is_tolerated(self):
        self._write("account_initial_state.json", {"initial_capital": 100})
        self._write("trading_ledger.json", [self._ledger_row(pnl=50.0)])
        # earliest 看起来早于窗口起点但无法解析成日期 ⇒ 不得把 50 加进 base
        self._start(mock.patch.object(A, "beijing_day", return_value="0000-99-99"))
        out = A.equity_history(days=5)
        self.assertNotIn("error", out)
        self.assertEqual(out["days"][-1]["equity"], 100.0)


class StaticAssetTests(_Base):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-dash-static-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.vue = self.tmp / "dist"
        self.root = self.tmp / "root"
        (self.vue / "admin").mkdir(parents=True, exist_ok=True)
        (self.root / "frontend" / "public").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "images").mkdir(parents=True, exist_ok=True)
        self._patch("VUE_DIST", self.vue)
        self._patch("ROOT", self.root)
        self.spa = mock.Mock(return_value=HTMLResponse("spa"))
        self._patch("serve_vue_spa", self.spa)
        self.templates = mock.Mock()
        self._patch("templates", self.templates)

    # ── robots / sitemap ─────────────────────────────────
    def test_robots_prefers_dist_then_public_then_default(self):
        (self.vue / "robots.txt").write_text("from-dist", encoding="utf-8")
        out = _run(A.robots_txt())
        self.assertIsInstance(out, FileResponse)
        self.assertEqual(out.path, str(self.vue / "robots.txt"))

        (self.vue / "robots.txt").unlink()
        (self.root / "frontend" / "public" / "robots.txt").write_text("pub", encoding="utf-8")
        out = _run(A.robots_txt())
        self.assertEqual(out.path, str(self.root / "frontend" / "public" / "robots.txt"))

        (self.root / "frontend" / "public" / "robots.txt").unlink()
        out = _run(A.robots_txt())
        self.assertIsInstance(out, PlainTextResponse)
        self.assertIn("Disallow: /admin/", out.body.decode())

    def test_sitemap_falls_back_to_the_inline_document(self):
        out = _run(A.sitemap_xml())
        self.assertIn("urlset", out.body.decode())

        (self.vue / "sitemap.xml").write_text("<urlset/>", encoding="utf-8")
        out = _run(A.sitemap_xml())
        self.assertIsInstance(out, FileResponse)

    def test_sitemap_uses_the_public_folder_before_the_inline_document(self):
        public = self.root / "frontend" / "public" / "sitemap.xml"
        public.write_text("<urlset/>", encoding="utf-8")
        out = _run(A.sitemap_xml())
        self.assertIsInstance(out, FileResponse)
        self.assertEqual(out.path, str(public))

    # ── docs images ──────────────────────────────────────
    def test_docs_images_serves_known_extensions_with_the_right_media_type(self):
        (self.root / "docs" / "images" / "a.png").write_bytes(b"\x89PNG")
        out = A.docs_images("a.png")
        self.assertIsInstance(out, FileResponse)
        self.assertEqual(out.media_type, "image/png")

    def test_docs_images_strips_directory_traversal(self):
        # 逃逸尝试被 Path(...).name 剥成 "passwd"，在 docs/images 下不存在 ⇒ 404
        (self.root / "secret.txt").write_text("secret", encoding="utf-8")
        with self.assertRaises(HTTPException) as ctx:
            A.docs_images("../../secret.txt")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_docs_images_missing_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            A.docs_images("nope.png")
        self.assertEqual(ctx.exception.status_code, 404)

    # ── SPA shell ────────────────────────────────────────
    def test_spa_subroutes_prefer_dist_then_frontend_fallback(self):
        (self.vue / "index.html").write_text("dist", encoding="utf-8")
        A.serve_vue_spa_subroutes()
        self.assertEqual(self.spa.call_args[0][0], str(self.vue / "index.html"))
        self.assertTrue(self.spa.call_args.kwargs["is_public"])

        (self.vue / "index.html").unlink()
        A.serve_vue_spa_subroutes()
        self.assertEqual(self.spa.call_args[0][0], str(self.root / "frontend" / "index.html"))

    def test_index_uses_the_template_when_dist_is_missing(self):
        _run(A.index(request=mock.Mock()))
        self.templates.TemplateResponse.assert_called_once()
        headers = self.templates.TemplateResponse.call_args.kwargs["headers"]
        self.assertIn("stale-while-revalidate", headers["Cache-Control"])

        (self.vue / "index.html").write_text("dist", encoding="utf-8")
        _run(A.index(request=mock.Mock()))
        self.assertEqual(self.spa.call_args[0][0], str(self.vue / "index.html"))

    def test_docs_spa_root_is_503_without_a_build(self):
        out = _run(A.docs_spa_root(request=mock.Mock()))
        self.assertEqual(out.status_code, 503)
        self.assertIn("npm run build", out.body.decode())

    def test_docs_spa_root_serves_the_shell_when_dist_exists(self):
        (self.vue / "index.html").write_text("dist", encoding="utf-8")
        _run(A.docs_spa_root(request=mock.Mock()))
        self.assertEqual(self.spa.call_args[0][0], str(self.vue / "index.html"))

    def test_login_redirects_to_the_admin_login_page(self):
        out = _run(A.login_redirect(request=mock.Mock()))
        self.assertEqual(out.status_code, 307)
        self.assertEqual(out.headers["location"], "/admin/login")

    def test_favicon_prefers_dist_then_public_then_404(self):
        (self.vue / "favicon.svg").write_text("<svg/>", encoding="utf-8")
        out = _run(A.favicon_svg())
        self.assertIsInstance(out, FileResponse)
        self.assertEqual(out.path, str(self.vue / "favicon.svg"))

        (self.vue / "favicon.svg").unlink()
        (self.root / "frontend" / "public" / "favicon.svg").write_text("<svg/>", encoding="utf-8")
        out = _run(A.favicon_svg())
        self.assertIsInstance(out, FileResponse)

        (self.root / "frontend" / "public" / "favicon.svg").unlink()
        out = _run(A.favicon_svg())
        self.assertEqual(out.status_code, 404)

    # ── admin page ───────────────────────────────────────
    def test_admin_page_serves_a_real_file_before_the_vue_shell(self):
        real = self.vue / "admin" / "legacy.html"
        real.write_text("<html>legacy</html>", encoding="utf-8")
        out = A.admin_page(subpath="legacy.html")
        self.assertIsInstance(out, FileResponse)
        self.assertEqual(out.path, str(real))
        self.assertEqual(out.headers["cache-control"], "no-cache")
        self.spa.assert_not_called()

    def test_admin_page_refuses_path_escape_and_falls_back_to_the_shell(self):
        (self.root / "secret.html").write_text("secret", encoding="utf-8")
        (self.vue / "index.html").write_text("dist", encoding="utf-8")
        out = A.admin_page(subpath="../secret.html")
        self.assertNotIsInstance(out, FileResponse)
        self.assertEqual(self.spa.call_args[0][0], str(self.vue / "index.html"))
        self.assertFalse(self.spa.call_args.kwargs["is_public"], "后台壳不是公开面")

    def test_admin_page_survives_a_path_that_cannot_be_resolved(self):
        (self.vue / "index.html").write_text("dist", encoding="utf-8")
        out = A.admin_page(subpath="bad\x00name")
        self.assertNotIsInstance(out, FileResponse)
        self.assertEqual(self.spa.call_args[0][0], str(self.vue / "index.html"))

    def test_admin_page_uses_the_frontend_fallback_when_dist_missing(self):
        A.admin_page(subpath="")
        self.assertEqual(self.spa.call_args[0][0], str(self.root / "frontend" / "index.html"))


if __name__ == "__main__":
    unittest.main()

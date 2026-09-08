"""Import and exercise the dashboard in an isolated filesystem without market I/O."""
import asyncio
import importlib.util
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


class DashboardLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        directory = root / "dashboard"
        (directory / "static").mkdir(parents=True)
        (directory / "templates").mkdir()
        source = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
        target = directory / "app.py"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("isolated_dashboard", target)
        assert spec and spec.loader
        self.module = importlib.util.module_from_spec(spec)
        with patch.object(threading.Thread, "start", side_effect=AssertionError("import started background activity")):
            spec.loader.exec_module(self.module)

    def tearDown(self):
        self.module.stop_dashboard_background_worker()
        self.module.SYNC_EXECUTOR.shutdown(wait=True)
        self.temp.cleanup()

    async def test_disabled_lifespan_performs_no_market_refresh(self):
        with patch.dict(os.environ, {"R20_DASHBOARD_WORKER_ENABLED": "0"}), patch.object(self.module, "update_cache_cycle") as refresh:
            async with self.module.app.router.lifespan_context(self.module.app):
                await asyncio.sleep(0.02)
            refresh.assert_not_called()
            self.assertIsNone(self.module._BG_WORKER_THREAD)

    async def test_lifespan_owns_and_joins_refresh_worker(self):
        refreshed = threading.Event()
        with patch.dict(os.environ, {"R20_DASHBOARD_WORKER_ENABLED": "1"}), patch.object(self.module, "update_cache_cycle", side_effect=refreshed.set):
            async with self.module.app.router.lifespan_context(self.module.app):
                self.assertTrue(await asyncio.to_thread(refreshed.wait, 3))
                worker = self.module._BG_WORKER_THREAD
                self.assertTrue(worker.is_alive())
            self.assertFalse(worker.is_alive())

    async def test_disabled_get_all_does_not_hit_exchange(self):
        class Env:
            exchange = "okx"
            mode = "demo"
            identity = "okx:demo:test"
            configured = False
            fingerprint = "test"
        with patch.dict(os.environ, {"R20_DASHBOARD_WORKER_ENABLED": "0"}), \
             patch.object(self.module, "update_cache_cycle") as refresh, \
             patch.object(self.module, "selected_environment", return_value=Env), \
             patch.object(self.module, "get_exchange", side_effect=AssertionError("network forbidden")):
            async with self.module.app.router.lifespan_context(self.module.app):
                payload = self.module.serve_cached_dashboard()
            refresh.assert_not_called()
            self.assertEqual(payload["exchange"], "okx")
            self.assertEqual(payload["quantity_unit"], "base")
            self.assertEqual(payload.get("account"), {})

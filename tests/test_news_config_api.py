"""News-source configuration persists without contacting providers or touching account keys."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from r20_backend import app as app_module, news_config, settings_store
from r20_backend.admin_auth import AdminAuthStore


class NewsConfigApiTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.env_file = self.root / "config" / ".env"
        self.cache = self.root / "news_sentiment.json"
        self.store = AdminAuthStore(self.root / "admin.db")
        self.store.create_user("admin", "InitialPassword123", "superadmin")
        self.store.create_user("reader", "ReaderPassword123", "admin")
        self.root_headers = {"X-R20-Session": self.store.login("admin", "InitialPassword123")["session_token"]}
        self.reader_headers = {"X-R20-Session": self.store.login("reader", "ReaderPassword123")["session_token"]}
        self.stack.enter_context(patch.object(app_module, "admin_auth", self.store))
        self.stack.enter_context(patch.object(app_module, "DATA_DIR", self.root))
        self.stack.enter_context(patch.object(app_module, "audit_record"))
        self.stack.enter_context(patch.object(settings_store, "ENV_FILE", self.env_file))
        self.stack.enter_context(patch.object(settings_store, "refresh_settings"))
        self.stack.enter_context(patch.object(news_config, "ENV_FILE", self.env_file))
        self.stack.enter_context(patch.dict(os.environ, {"R20_NEWS_SOURCES": "okx", "R20_EXCHANGE": "binance", "BINANCE_DEMO_API_KEY": "fixture-key"}))
        self.stack.enter_context(patch("urllib.request.urlopen", side_effect=AssertionError("news save must not contact providers")))
        self.stack.enter_context(patch("requests.Session.request", side_effect=AssertionError("news save must not contact exchanges")))
        self.client = TestClient(app_module.app)
        self.addCleanup(self.client.close)

    def seed_feed(self):
        self.cache.write_text(json.dumps({
            "latest_news": [
                {"id": "okx:1", "source": "okx", "title": "OKX_ONLY_SENTINEL", "time": "2026-09-08 10:00:00"},
                {"id": "binance:1", "source": "binance", "title": "BINANCE_ONLY_SENTINEL", "time": "2026-09-08 09:00:00"},
            ],
            "coins_sentiment": {"BTC": {"mentions": 4, "sentiment_factor_score": 0.3}},
            "macro_sentiment": "sample sentiment",
            "source_status": {"okx": {"status": "ok"}, "binance": {"status": "ok"}},
        }), encoding="utf-8")

    def test_anonymous_denied_and_operator_read_only(self):
        self.assertEqual(self.client.get("/api/v1/admin/news/config").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/admin/news/config", headers=self.reader_headers).status_code, 200)
        response = self.client.put("/api/v1/admin/news/config", headers=self.reader_headers, json={"sources": ["binance"]})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.env_file.exists())

    def test_binance_only_persists_and_excludes_disabled_okx_cache(self):
        self.seed_feed()
        response = self.client.put("/api/v1/admin/news/config", headers=self.root_headers, json={"sources": ["binance"]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["sources"], ["binance"])
        self.assertEqual([row["source"] for row in body["feed"]["latest_news"]], ["binance"])
        self.assertEqual(body["feed"]["coins_sentiment"], {})
        self.assertIn("R20_NEWS_SOURCES=binance", self.env_file.read_text(encoding="utf-8"))
        self.assertEqual(os.environ["R20_EXCHANGE"], "binance")
        self.assertEqual(os.environ["BINANCE_DEMO_API_KEY"], "fixture-key")
        with patch.dict(os.environ, {"R20_NEWS_SOURCES": "okx"}):
            self.assertEqual(news_config.selected_sources(), ("binance",))
        self.assertEqual(self.client.get("/api/v1/admin/news/config", headers=self.root_headers).json()["sources"], ["binance"])

    def test_empty_selection_stops_using_all_cached_sources(self):
        self.seed_feed()
        response = self.client.put("/api/v1/admin/news/config", headers=self.root_headers, json={"sources": []})
        self.assertEqual(response.status_code, 200, response.text)
        feed = response.json()["feed"]
        self.assertEqual(feed["status"], "disabled")
        self.assertEqual(feed["latest_news"], [])
        self.assertEqual(feed["coins_sentiment"], {})
        self.assertEqual(news_config.selected_sources(), ())

    def test_unknown_source_cannot_change_saved_selection(self):
        self.client.put("/api/v1/admin/news/config", headers=self.root_headers, json={"sources": ["okx", "binance"]})
        before = self.env_file.read_bytes()
        response = self.client.put("/api/v1/admin/news/config", headers=self.root_headers, json={"sources": ["https://untrusted.example/feed"]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.env_file.read_bytes(), before)

    def test_public_dashboard_filters_old_in_memory_news_after_source_change(self):
        from dashboard import app as dashboard
        from r20_exchange.runtime import ExchangeEnvironment
        self.seed_feed()
        self.client.put("/api/v1/admin/news/config", headers=self.root_headers, json={"sources": ["binance"]})
        env = ExchangeEnvironment("binance", "demo", "fixture-key", "fixture-secret")
        with patch.object(dashboard, "CACHE_DATA", {"news_intelligence": {"latest_news": [{"title": "OLD_CACHED_NEWS"}]}}), \
             patch.object(dashboard, "NEWS_SENTIMENT_FILE", str(self.cache)), \
             patch.object(dashboard, "bind_account_scope", return_value=env):
            response = self.client.get("/api/all")
        self.assertEqual(response.status_code, 200)
        titles = [row["title"] for row in response.json()["news_intelligence"]["latest_news"]]
        self.assertEqual(titles, ["BINANCE_ONLY_SENTINEL"])

    def test_selecting_binance_uses_its_cache_even_when_global_top_ten_were_okx(self):
        snapshot = {
            "latest_news": [{"id": "okx:new", "source": "okx", "title": "OKX_ONLY"}],
            "source_news": {
                "okx": [{"id": "okx:new", "source": "okx", "title": "OKX_ONLY", "published_at": 2000}],
                "binance": [{"id": "binance:older", "title": "BINANCE_OLDER", "published_at": 1000}],
            },
            "source_status": {"okx": {"status": "ok"}, "binance": {"status": "ok"}},
        }
        filtered = news_config.filter_snapshot(snapshot, ("binance",))
        self.assertEqual([row["title"] for row in filtered["latest_news"]], ["BINANCE_OLDER"])
        self.assertEqual(set(filtered["source_news"]), {"binance"})

    def test_final_prompt_excludes_disabled_source_and_labels_cached_news(self):
        from scripts import ai_brain_trader as brain, evolution_shield, prompt_library
        self.seed_feed()
        snapshot = json.loads(self.cache.read_text(encoding="utf-8"))

        snapshot["source_status"]["binance"] = {"status": "stale"}
        self.cache.write_text(json.dumps(snapshot), encoding="utf-8")
        self.client.put("/api/v1/admin/news/config", headers=self.root_headers, json={"sources": ["binance"]})
        with patch.object(brain, "NEWS_SENTIMENT_FILE", str(self.cache)), \
             patch.object(brain, "AI_MEMORY_MD_FILE", str(self.root / "memory.md")), \
             patch.object(brain, "AI_MEMORY_FILE", str(self.root / "memory.json")), \
             patch.object(evolution_shield, "STRUCTURED_MEMORY_FILE", self.root / "structured.json"), \
             patch.object(brain, "active_profile", return_value=prompt_library.resolve_profile(prompt_library.PRESETS["stable"])):
            prompt = brain.construct_full_market_prompt([], "0", [], usdt_available=100)
        self.assertIn("BINANCE_ONLY_SENTINEL", prompt)
        self.assertNotIn("OKX_ONLY_SENTINEL", prompt)
        self.assertIn("旧缓存", prompt)

    def test_high_frequency_okx_cannot_hide_binance_in_feed_or_model_prompt(self):
        from scripts import ai_brain_trader as brain, evolution_shield, prompt_library
        buckets = {
            source: [
                {"id": f"{source}:{index}", "source": source, "title": f"{source.upper()}_NEWS_{index:02d}",
                 "published_at": newest - index, "time": "2026-09-08 10:00:00"}
                for index in range(count)
            ]
            for source, newest, count in (("okx", 2000, 20), ("binance", 1000, 5))
        }
        self.cache.write_text(json.dumps({
            "source_news": buckets, "source_status": {"okx": {"status": "ok"}, "binance": {"status": "ok"}},
        }), encoding="utf-8")
        response = self.client.put("/api/v1/admin/news/config", headers=self.root_headers,
                                   json={"sources": ["okx", "binance"]})
        rows = response.json()["feed"]["latest_news"]
        self.assertEqual([row["title"] for row in rows],
                         [f"OKX_NEWS_{index:02d}" for index in range(7)] +
                         [f"BINANCE_NEWS_{index:02d}" for index in range(3)])
        with patch.object(brain, "NEWS_SENTIMENT_FILE", str(self.cache)), \
             patch.object(brain, "AI_MEMORY_MD_FILE", str(self.root / "memory.md")), \
             patch.object(brain, "AI_MEMORY_FILE", str(self.root / "memory.json")), \
             patch.object(evolution_shield, "STRUCTURED_MEMORY_FILE", self.root / "structured.json"), \
             patch.object(brain, "active_profile", return_value=prompt_library.resolve_profile(prompt_library.PRESETS["stable"])):
            prompt = brain.construct_full_market_prompt([], "0", [], usdt_available=100)
        for source in ("OKX", "BINANCE"):
            for index in range(3):
                self.assertIn(f"{source}_NEWS_{index:02d}", prompt)
            self.assertNotIn(f"{source}_NEWS_03", prompt)


    def test_prompt_labels_media_as_unverified_reference(self):
        from scripts import ai_brain_trader as brain, evolution_shield, prompt_library
        self.cache.write_text(json.dumps({
            "source_news": {
                "binance": [
                    {"id": "binance:off", "source": "binance", "source_name": "币安情报中心",
                     "title": "OFFICIAL_HEADLINE", "summary": "", "time": "2026-09-09 12:00:00",
                     "stale": False, "authority": "official", "published_at": 20},
                    {"id": "binance:rss", "source": "binance", "source_name": "币安情报中心",
                     "title": "MEDIA_HEADLINE", "summary": "rss", "time": "2026-09-09 11:00:00",
                     "stale": False, "authority": "media", "published_at": 10},
                ],
            },
            "source_status": {"okx": {"status": "disabled"}, "binance": {"status": "ok"}},
            "macro_sentiment": "无可验证情绪数据",
        }), encoding="utf-8")
        self.client.put("/api/v1/admin/news/config", headers=self.root_headers, json={"sources": ["binance"]})
        with patch.object(brain, "NEWS_SENTIMENT_FILE", str(self.cache)), \
             patch.object(brain, "AI_MEMORY_MD_FILE", str(self.root / "memory.md")), \
             patch.object(brain, "AI_MEMORY_FILE", str(self.root / "memory.json")), \
             patch.object(evolution_shield, "STRUCTURED_MEMORY_FILE", self.root / "structured.json"), \
             patch.object(brain, "active_profile", return_value=prompt_library.resolve_profile(prompt_library.PRESETS["stable"])):
            prompt = brain.construct_full_market_prompt([], "0", [], usdt_available=100)
        self.assertIn("OFFICIAL_HEADLINE", prompt)
        self.assertIn("MEDIA_HEADLINE", prompt)
        self.assertIn("官方公告/已验证源", prompt)
        self.assertIn("媒体参考/未交叉验证，不得单独形成交易结论", prompt)
        official_line = next(line for line in prompt.splitlines() if "OFFICIAL_HEADLINE" in line)
        media_line = next(line for line in prompt.splitlines() if "MEDIA_HEADLINE" in line)
        self.assertIn("官方公告/已验证源", official_line)
        self.assertNotIn("不得单独形成交易结论", official_line)
        self.assertIn("媒体参考/未交叉验证，不得单独形成交易结论", media_line)



    def test_sparse_source_releases_unused_quota_without_fabricating_items(self):
        rows = [{"id": f"okx:{index}", "source": "okx", "published_at": 100-index} for index in range(20)]
        rows.append({"id": "binance:only", "source": "binance", "published_at": 1})
        selected = news_config.select_news_items(rows, ("okx", "binance"), 10)
        self.assertEqual([row["id"] for row in selected], [f"okx:{index}" for index in range(9)] + ["binance:only"])
        self.assertEqual(news_config.select_news_items(rows, (), 10), [])
        self.assertEqual(len(news_config.select_news_items(rows, ("okx",), 10)), 10)

    def test_legacy_cache_belongs_to_okx_and_unsafe_links_are_not_exposed(self):
        snapshot = {"latest_news": [{"id": "old-1", "title": "legacy", "url": "javascript:alert(1)"}]}
        self.assertEqual(news_config.filter_snapshot(snapshot, ("binance",))["latest_news"], [])
        row = news_config.filter_snapshot(snapshot, ("okx",))["latest_news"][0]
        self.assertEqual(row["source"], "okx")
        self.assertEqual(row["url"], "")
        self.assertTrue(row["stale"])


if __name__ == "__main__":
    unittest.main()

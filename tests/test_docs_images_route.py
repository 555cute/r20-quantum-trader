"""站内文档配图同源托管路由回归测试（防 raw.githubusercontent 外链在国内加载失败）。"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
import r20_backend.app as app_module


class DocsImagesRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)

    def test_serves_real_png_same_origin(self):
        resp = self.client.get("/docs/images/v760_risk_control.png")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.headers["content-type"].startswith("image/png"))
        self.assertEqual(resp.content[:8], b"\x89PNG\r\n\x1a\n", "返回的不是合法 PNG 头")
        self.assertIn("max-age", resp.headers.get("cache-control", ""))

    def test_docs_page_still_serves_spa_html(self):
        # 图片路由不得抢走 /docs SPA 页面
        resp = self.client.get("/docs")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])

    def test_spa_subpages_never_require_img_name_query(self):
        # v7.6.1 回归：/trading 等页面路由曾被堆叠装饰到 docs_image 上，
        # 刷新非主页时 FastAPI 把 img_name 当必填 query 参数返回 422。
        for path in ("/trading", "/factors", "/news", "/lab", "/history", "/docs"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, f"{path}: {resp.text[:200]}")
            self.assertIn("text/html", resp.headers["content-type"], path)
            self.assertNotIn("img_name", resp.text[:500], path)

    def test_path_traversal_and_non_png_never_leak_files(self):
        for bad in ("/docs/images/..%2f..%2f.env", "/docs/images/../../.env",
                    "/docs/images/app.py", "/docs/images/nope.png"):
            r = self.client.get(bad)
            # 核心安全属性：绝不以 image/png 形式泄露 docs/images 之外的文件
            self.assertNotEqual(r.headers.get("content-type", "").split(";")[0], "image/png", bad)
            if r.status_code == 200:  # 落到 SPA catch 返回 HTML 也需确保没回显密钥
                self.assertNotIn("OKX_API", r.text[:2000], bad)


if __name__ == "__main__":
    unittest.main()

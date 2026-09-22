"""`/api/all` 出口：**默认瘦身留痕**、`?full=1` 逐字节一致（第二百四十五刀）。

路由 `routers/dashboard.py` 只是一层薄壳（`return await dash_app.get_all_data(full=full)`），
真正的接线在 `dashboard_cache.get_all_data`：

| 语义 | 口径 |
|---|---|
| ★ `?full=1` | `payload = data`（**原对象**）⇒ 与旧版**逐字节一致**，且**不**注入 `_meta` |
| ★ 默认 | `payload = slim_payload(data)` ⇒ 含 `_meta.slim=True` 与逐项 `omitted` 留痕，而 `CACHE_DATA` **本身不被改动**（纯函数）|
| ★ 新鲜度阈值 | 内存快照新鲜（<**5.0s**）⇒ 直接返回；过期或空 ⇒ `refresh_cache_if_needed(1.5)` |
| `/api/overview` | 同样有阈值（**12.0s**，`refresh_cache_if_needed(2.5)`），但**不**做瘦身 |
| 响应头 | `max-age=0`（浏览器不缓存）+ `s-maxage=2` + `stale-while-revalidate=5` |

## 跨层补充（本刀以 `grep` 核实，非断言）

「留痕」过去**没人读**（`slim.py` 注释：省略留痕有，但**前端从不读**）——现已闭环：
`frontend/src/views/dashboard/LedgerView.vue` 里明确读 `_meta.omitted.trades` 的
`kept` / `total` 来渲染截断提示，并写明「**UI 不说谎**」。本刀不动前端，只记录这条链路已接通。
"""

import asyncio
import json
import time
import unittest
from unittest import mock

from r20_backend import dashboard_cache as DC


def _body(response):
    return json.loads(response.body)


class AllDataExitTest(unittest.TestCase):
    def setUp(self):
        self.saved = (DC.CACHE_DATA, DC.LAST_CACHE_TIME)
        self.addCleanup(self._restore)
        self.refresh = mock.AsyncMock(return_value={"fresh": True})

    def _restore(self):
        DC.CACHE_DATA, DC.LAST_CACHE_TIME = self.saved

    def _patch_refresh(self):
        p = mock.patch.object(DC, "refresh_cache_if_needed", self.refresh)
        p.start()
        self.addCleanup(p.stop)

    def test_full_true_is_byte_identical_to_the_cache_and_adds_no_meta(self):
        DC.CACHE_DATA = {"ai_brain_history": [{"i": i, "top_opportunities": [1]}
                                              for i in range(9)]}
        DC.LAST_CACHE_TIME = time.time()
        out = _body(asyncio.run(DC.get_all_data(full=True)))
        self.assertEqual(out, DC.CACHE_DATA, "full=1 ⇒ 与旧版逐字节一致")
        self.assertNotIn("_meta", out, "完整载荷不注入瘦身元数据")

    def test_default_slims_without_mutating_the_cache(self):
        DC.CACHE_DATA = {"ai_brain_history": [{"i": i, "top_opportunities": [1]}
                                              for i in range(9)]}
        DC.LAST_CACHE_TIME = time.time()
        before = json.loads(json.dumps(DC.CACHE_DATA))
        out = _body(asyncio.run(DC.get_all_data(full=False)))
        self.assertIs(out["_meta"]["slim"], True)
        self.assertIn("ai_brain_history", out["_meta"]["omitted"], "省略逐项留痕")
        self.assertEqual(DC.CACHE_DATA, before, "**CACHE_DATA 本身不许被瘦身改动**")

    def test_fresh_cache_is_served_without_refreshing(self):
        self._patch_refresh()
        DC.CACHE_DATA = {"x": 1}
        DC.LAST_CACHE_TIME = time.time()
        _body(asyncio.run(DC.get_all_data()))
        self.refresh.assert_not_awaited()

    def test_stale_cache_refreshes_with_the_micro_ttl(self):
        self._patch_refresh()
        DC.CACHE_DATA = {"x": 1}
        DC.LAST_CACHE_TIME = time.time() - 9.0
        asyncio.run(DC.get_all_data())
        self.refresh.assert_awaited_once_with(1.5)

    def test_empty_cache_forces_a_refresh_even_when_the_clock_is_fresh(self):
        self._patch_refresh()
        DC.CACHE_DATA = {}
        DC.LAST_CACHE_TIME = time.time()
        _body(asyncio.run(DC.get_all_data()))
        self.refresh.assert_awaited_once_with(1.5)

    def test_real_time_cache_headers(self):
        DC.CACHE_DATA = {"x": 1}
        DC.LAST_CACHE_TIME = time.time()
        response = asyncio.run(DC.get_all_data())
        headers = {k.lower(): v for k, v in response.headers.items()}
        self.assertEqual(headers["cache-control"],
                         "public, max-age=0, s-maxage=2, stale-while-revalidate=5",
                         "浏览器绝不缓存实时看板")


class OverviewExitTest(unittest.TestCase):
    def setUp(self):
        self.saved = (DC.CACHE_DATA, DC.LAST_CACHE_TIME)
        self.addCleanup(self._restore)
        self.refresh = mock.AsyncMock(return_value={"fresh": True})

    def _restore(self):
        DC.CACHE_DATA, DC.LAST_CACHE_TIME = self.saved

    def test_overview_is_not_slimmed_and_uses_its_own_ttl(self):
        with mock.patch.object(DC, "refresh_cache_if_needed", self.refresh):
            DC.CACHE_DATA = {"ai_brain_history": [{"i": i, "policy_snapshot": {}} for i in range(9)]}
            DC.LAST_CACHE_TIME = time.time() - 20.0
            out = _body(asyncio.run(DC.get_overview()))
        self.refresh.assert_awaited_once_with(2.5, )
        self.assertNotIn("_meta", out, "/api/overview **不**瘦身")


if __name__ == "__main__":
    unittest.main()

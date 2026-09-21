"""看板缓存周期：**失败的查询绝不把已知良好数据覆盖成零**（第一百九十二刀）。

`dashboard_cache.update_cache_cycle` 是全仓最靠近那条红线的地方 —— 看板显示的每一个数字，
都要先在这里决定「是新的、是旧的、还是根本没有」。

| 分支 | 语义 |
|---|---|
| 核心账户查询失败 **且**手上有「有意义的上次成功载荷」| 用**上次成功载荷的拷贝**（不写零）+ ★ **显式**把 `is_stale` 改真 + `status` 为 `STALE` 或 `NOT_READY` + `last_success_at` 保留上次时间 |
| 核心账户查询失败 **且**手上没有可用载荷 | 出一份**明确的骨架**（`OFFLINE` / `NOT_READY` + `partial`），**不伪装成正常数据** |
| 凭证未配置 | `status` 为 `NOT_READY` 且 `message` 是**人话文案**（不是空、也不是 traceback）|

★ 那条 `is_stale` 注释值得原样记住：陈旧分支是**上次成功载荷的拷贝**，天然带着 `is_stale=False`
⇒ 若不**显式**改真，前端**永远看不出这是旧数据**（「UI 不说谎」的反面教材）。
"""

import unittest
from unittest.mock import patch

import r20_backend.dashboard_cache as dc

_STATE_KEYS = ("_private_not_ready", "avail_eq", "balance_ok", "cash_bal", "long_count",
               "orders_data", "pending_orders_list", "positions", "positions_ok", "short_count",
               "total_eq", "total_pos_upl", "trackers", "upl_acc")


def _state(**over):
    base = dict(zip(_STATE_KEYS, (False, 100.0, True, 100.0, 1, [], [], [{"instId": "BTC"}], True,
                                  0, 100.0, 0.0, [], 0.0)))
    base.update(over)
    return tuple(base[k] for k in _STATE_KEYS)


class StaleFallbackTest(unittest.TestCase):
    def setUp(self):
        self.old = {"timestamp": "2026-09-20 08:00:00 (北京时间)", "account": {"equity": 12345.0},
                    "positions_summary": {"items": [{"instId": "BTC"}]}}
        self.addCleanup(setattr, dc, "CACHE_DATA", dc.CACHE_DATA)

    def _cycle(self, state, meaningful=True, local_injected=None):
        with patch.object(dc, "collect_core_account_state", return_value=state), \
             patch.object(dc, "_is_meaningful_dashboard_snapshot", return_value=meaningful), \
             patch.object(dc, "_inject_local_data_into_stale",
                          side_effect=lambda *a, **k: (local_injected.update({"injected": True})
                                       if local_injected is not None else None)), \
             patch.object(dc, "enrich_position_risk_fields", return_value=None):
            dc.CACHE_DATA = dict(self.old)
            dc.update_cache_cycle()

    def test_failed_query_keeps_last_known_good_and_flags_it_stale(self):
        """★ 失败 ⇒ **不零覆盖**；且 `is_stale` 必须被**显式**改真（拷贝自带上一次的值）。"""
        self.old["is_stale"] = False           # 上次成功载荷本就带 False ⇒ 最危险的情形
        self._cycle(_state(balance_ok=False))
        out = dc.CACHE_DATA
        self.assertEqual(out["account"], {"equity": 12345.0}, "已知良好数据**不得**被零覆盖")
        self.assertIs(out["is_stale"], True, "陈旧标记必须显式置真，否则前端看不出是旧数据")
        health = out["data_health"]
        self.assertEqual(health["status"], "STALE")
        self.assertIs(health["partial"], True)
        self.assertEqual(health["last_success_at"], "2026-09-20 08:00:00 (北京时间)",
                         "保留**上次成功**时间，而不是本次尝试时间")
        self.assertEqual(health["attempted_at"][:4], "2026", "本次尝试时间另存")
        self.assertEqual(health["message"], None, "非凭据问题 ⇒ 不给 NOT READY 文案")

    def test_not_configured_is_labelled_and_explained(self):
        self._cycle(_state(balance_ok=False, _private_not_ready=True))
        health = dc.CACHE_DATA["data_health"]
        self.assertEqual(health["status"], "NOT_READY", "未配置凭证 ≠ 陈旧")
        self.assertEqual(health["message"], dc._NOT_READY_TEXT, "给人话文案")
        self.assertIn("NOT READY", health["message"])

    def test_local_only_data_is_still_injected_in_stale_mode(self):
        """陈旧 ≠ 一切都旧：本地文件（因子/新闻/日志）应当在陈旧模式下**照样新鲜注入**。"""
        seen = {}
        self._cycle(_state(balance_ok=False), local_injected=seen)
        self.assertTrue(seen.get("injected"), "陈旧分支也必须注入本地数据")

    def test_no_usable_cache_yields_an_honest_skeleton_not_fake_data(self):
        self._cycle(_state(balance_ok=False), meaningful=False)
        out = dc.CACHE_DATA
        self.assertEqual(out["data_health"]["status"], "OFFLINE")
        self.assertIs(out["data_health"]["partial"], True)
        self.assertEqual(out["account"], {}, "没有可用数据 ⇒ 空，而不是编造的零账户")
        self.assertEqual(out["positions_summary"]["total"], 0)
        self.assertNotIn("equity", out.get("account", {}))

    def test_positions_failure_alone_also_triggers_the_stale_path(self):
        """两个条件里**任一**失败即走陈旧路径（不是只看余额）。"""
        self._cycle(_state(positions_ok=False))
        self.assertIs(dc.CACHE_DATA["is_stale"], True)


if __name__ == "__main__":
    unittest.main()

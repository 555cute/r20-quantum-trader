"""场所查询的失败语义（第二百五十二刀）。

这一族决定「**这一轮能不能开新仓**」以及「**跨所敞口算得准不准**」，纪律只有一条主旋律：
**读不到 ≠ 没有；读不到一律按最坏处理，绝不编造"干净"**。

| 函数 | 铁律 |
|---|---|
| `query_positions` | 查询失败必须与「确认空仓」区分开（失败返回 `ok=False`，绝不返回"空列表 ⇒ 无仓"）|
| `venue_execution_ready` | 能力表读失败 ⇒ **按不可执行处理**；`_BROKEN_VENUES` 所一并否决（密钥已死不该赢下评分）|
| `fetch_other_venue_positions` | 任一开闸所读失败 ⇒ `ok=False`（**宁可不计数错杀，不可漏计超卖**）；只计数不处置孤儿仓；每所单次读取、异常带场所名返回 |
| `_venue_health_stamp` | 缺文件/坏文件 ⇒ `(None, {})` —— **绝不编造新鲜度** |
"""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.trader.venue_query import (
    query_positions, venue_execution_ready, fetch_other_venue_positions,
    _venue_health_stamp,
)


class QueryPositionsTest(unittest.TestCase):
    def test_failure_is_not_an_empty_account(self):
        """**读不到 ≠ 没有**：查询失败必须与「确认空仓」区分（否则会被当成无仓去加仓）。"""
        class _Boom:
            def positions(self):
                raise RuntimeError("502")

        ok, rows, err = query_positions(okx_rest=_Boom())
        self.assertFalse(ok)
        self.assertEqual(rows, [])
        self.assertIn("invalid positions response", err)

    def test_confirmed_rows_are_returned_with_ok(self):
        rows = [{"instId": "BTC-USDT-SWAP", "pos": 1}]
        ok, got, err = query_positions(okx_rest=SimpleNamespace(positions=lambda: rows))
        self.assertTrue(ok)
        self.assertEqual(got, rows)
        self.assertEqual(err, "")


class VenueExecutionReadyTest(unittest.TestCase):
    """就绪判定一律读 registry/能力表；**读失败按不可执行**（fail-closed）。"""

    def _registry(self, *, registered=True, broken_lookup=False, execution_open=True,
                  raises_on_execution_open=False):
        def is_registered(key):
            if broken_lookup:
                raise RuntimeError("能力表炸了")
            return registered

        def _open(key, env):
            if raises_on_execution_open:
                raise RuntimeError("旗标读不了")
            return execution_open

        return SimpleNamespace(is_registered=is_registered, execution_open=_open)

    def _call(self, venue, *, environment="live", env=None, broken=frozenset(), **kw):
        env = env or SimpleNamespace(configured=True, mode="live")
        return venue_execution_ready(
            venue, environment, venue_registry=self._registry(**kw),
            current_environment=lambda: env, _BROKEN_VENUES=set(broken))

    def test_unregistered_venue_is_not_ready(self):
        self.assertFalse(self._call("gate", registered=False))

    def test_capability_lookup_failure_means_not_executable(self):
        """⚠️ 能力表读失败 ⇒ **按不可执行处理**（不是"默认放行"）。"""
        self.assertFalse(self._call("gate", broken_lookup=True))

    def test_flag_read_failure_means_not_executable(self):
        self.assertFalse(self._call("gate", raises_on_execution_open=True))

    def test_okx_requires_configured_credentials_in_matching_mode(self):
        self.assertTrue(self._call("okx", environment="live",
                                   env=SimpleNamespace(configured=True, mode="live")))
        self.assertFalse(self._call("okx", environment="live",
                                    env=SimpleNamespace(configured=True, mode="demo")),
                         "档位不一致 ⇒ 不就绪")
        self.assertFalse(self._call("okx", environment="live",
                                    env=SimpleNamespace(configured=False, mode="live")),
                         "凭证未配置 ⇒ 不就绪")

    def test_broken_venue_is_vetoed_even_when_flag_is_on(self):
        """执行旗开着但密钥已死 ⇒ 一并否决。

        否则它会以最低费率**赢下评分**，信号派过去死在下单阶段白白烧掉。
        """
        self.assertFalse(self._call("gate", broken={"gate"}), "坏键所必须被摘除")
        self.assertTrue(self._call("binance", broken={"gate"}), "只摘除坏掉的那一所")

    def test_flag_on_is_ready(self):
        self.assertTrue(self._call("binance"))
        self.assertFalse(self._call("binance", execution_open=False))


class CrossVenueSnapshotTest(unittest.TestCase):
    """跨所快照：**任一开闸所读失败 ⇒ ok=False**（宁可不计数错杀，不可漏计超卖）。"""

    def _call(self, *, names=("okx", "gate", "binance"), ready=("gate", "binance"),
              adapters=None, registry_raises=False):
        adapters = adapters or {}
        # 未显式打桩的「已开闸所」补一个良性桩：否则取适配器会 KeyError →
        # 触发 fail-closed（那测的就不是本用例想测的东西了）
        for _n in ready:
            adapters.setdefault(_n, SimpleNamespace(positions=lambda: []))

        def registered_venues():
            if registry_raises:
                raise RuntimeError("清单不可用")
            return list(names)

        reg = SimpleNamespace(registered_venues=registered_venues,
                              get_adapter=lambda name, environment=None: adapters[name])
        return fetch_other_venue_positions(
            "live", venue_registry=reg,
            venue_execution_ready=lambda v, e: v in ready)

    def test_registry_listing_failure_is_fail_closed(self):
        ok, snap, err = self._call(registry_raises=True)
        self.assertFalse(ok)
        self.assertEqual(snap, {})
        self.assertIn("场所清单不可用", err)

    def test_okx_is_never_included(self):
        """OKX 走本 trader 直签链路，跨所快照**只统计外所**。"""
        ok, snap, _ = self._call(adapters={})
        self.assertTrue(ok)
        self.assertNotIn("okx", snap)

    def test_unopened_venue_is_not_read_and_not_a_failure(self):
        """未开闸所不参与读取，也不构成失败（结构性无仓位来源）。"""
        ok, snap, err = self._call(ready=(), adapters={})
        self.assertTrue(ok, "没有开闸所 ⇒ 空快照是**确认的**，不是失败")
        self.assertEqual(snap, {})
        self.assertEqual(err, "")

    def test_read_failure_of_an_open_venue_is_fail_closed_and_names_it(self):
        def _boom():
            raise RuntimeError("502")

        ok, snap, err = self._call(adapters={"gate": SimpleNamespace(positions=_boom)})
        self.assertFalse(ok, "任一开闸所读失败 ⇒ 本周期禁止新增开仓")
        self.assertEqual(snap, {}, "失败时不得返回部分快照（那会被当成真实敞口）")
        self.assertIn("gate", err, "异常必须带场所名，不许静默吞")

    def test_zero_size_rows_are_filtered_but_position_count_is_kept(self):
        rows = [{"instId": "A", "size_signed": 0.0}, {"instId": "B", "size_signed": -2.0}]
        ok, snap, _ = self._call(adapters={"gate": SimpleNamespace(positions=lambda: rows)})
        self.assertTrue(ok)
        self.assertEqual([p["instId"] for p in snap["gate"]], ["B"],
                         "零仓不计入敞口（但也不是失败）")

    def test_orphan_positions_are_counted_not_disposed(self):
        """孤儿仓（来源不明，可能是用户手动仓）**只计数不处置** —— 绝不清算。"""
        rows = [{"instId": "MYSTERY", "size_signed": 5.0}]
        ok, snap, _ = self._call(adapters={"gate": SimpleNamespace(positions=lambda: rows)})
        self.assertTrue(ok)
        self.assertEqual(len(snap["gate"]), 1, "占额度、纳入敞口")
        self.assertEqual(snap["gate"][0]["instId"], "MYSTERY", "但原样保留、不改动")


class VenueHealthStampTest(unittest.TestCase):
    """观测新鲜度：**缺文件/坏文件 ⇒ 没有观测**，绝不编造。"""

    def _stamp(self, content):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            if content is not None:
                fh.write(content)
            path = fh.name
        try:
            return _venue_health_stamp(VENUE_HEALTH_FILE=path)
        finally:
            os.unlink(path)

    def test_missing_file_means_no_observation(self):
        stamp, venues = _venue_health_stamp(
            VENUE_HEALTH_FILE=os.path.join(tempfile.gettempdir(), "r20_no_health_xyz.json"))
        self.assertIsNone(stamp, "缺文件 ⇒ 跨所观测不存在，不得编造新鲜度")
        self.assertEqual(venues, {})

    def test_corrupt_file_means_no_observation(self):
        stamp, venues = self._stamp("{不是 json")
        self.assertIsNone(stamp)
        self.assertEqual(venues, {})

    def test_valid_file_normalises_stamp_to_utc_iso(self):
        stamp, venues = self._stamp(json.dumps(
            {"updated_utc": "2026-09-21 10:00:00", "venues": {"gate": {"ok": True}}}))
        self.assertEqual(stamp, "2026-09-21T10:00:00Z", "空格换 T 并补 Z（UTC ISO）")
        self.assertEqual(venues, {"gate": {"ok": True}})

    def test_venues_must_be_a_dict_else_empty(self):
        stamp, venues = self._stamp(json.dumps(
            {"updated_utc": "2026-09-21T10:00:00Z", "venues": ["不是字典"]}))
        self.assertEqual(venues, {}, "结构不对 ⇒ 当作没有观测表（不猜）")

    def test_missing_stamp_is_none(self):
        stamp, venues = self._stamp(json.dumps({"venues": {"gate": {}}}))
        self.assertIsNone(stamp)


if __name__ == "__main__":
    unittest.main()

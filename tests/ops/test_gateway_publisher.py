"""公共投递门面（第二百九十八刀，开新面 publisher.py）。

先打印整个文件（33 行）再动笔。它是策略/排程进程**唯一**该用的发消息入口。

| 语义 | 口径 |
|---|---|
| ★ **测试期泄漏闸门** | 未显式设置 `R20_GATEWAY_DB` 且进程里已 import `unittest`/`pytest` ⇒ **直接返回 `"test-noop"`，一个字节都不落库**。这道闸门的存在理由写在源码注释里："never leak mock/fixture events into the production gateway queue"——否则跑一次测试就往**生产**投递队列里灌一堆假事件，worker 会真的把它们发给用户 |
| ★ **闸门必须显式解除** | 只有 `R20_ALLOW_TEST_PUBLISH` 非空才放行。专测"不设它仍是 noop" |
| ★ **库路径在调用时解析** | 第 30 行**重新**读一次 `R20_GATEWAY_DB`，而不是用 import 期算好的 `DB_PATH` —— 故运行期改环境变量是生效的 |
| ★ **`channels` 的两种语义** | 传 `None` ⇒ 问 `enabled_channels()`（用户配置）；传列表 ⇒ **原样使用**，连 `enabled_channels()` 都不调用（否则就给不出"强制发到某渠道"的能力了）|
| ★ **返回值是 `event_id`** | 调用方靠它去 `/api` 查投递状态 |
| ★ **`payload` 缺省为空字典** | 不许把 `None` 落进库（`json.dumps(None)` 会变成 `null`，读方要额外判空）|

## 封闭性

`DB_PATH` 默认指向**生产 `data/r20_gateway.db`**，而线上 worker 此刻正在读写它。
故本文件 `setUp` 把 `DB_PATH` 打到临时目录，并保证**每条**用例都显式指定
`R20_GATEWAY_DB`（或先 pop 掉它、再靠已打桩的 `DB_PATH` 兜底），
绝不出现"既没环境变量、`DB_PATH` 又还是生产路径"的中间态。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from r20_gateway import publisher as PUB
from r20_gateway.store import GatewayStore


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "gateway.db"
        # ★ 先兜底：即使某条用例漏设环境变量，落到也是临时库而不是生产库
        self._start(mock.patch.object(PUB, "DB_PATH", self.db))
        self.env = self._start(mock.patch.dict(os.environ))
        os.environ["R20_GATEWAY_DB"] = str(self.db)
        os.environ.pop("R20_ALLOW_TEST_PUBLISH", None)
        self.channels = self._start(mock.patch.object(PUB, "enabled_channels",
                                                      return_value=["qq"]))

    # --- 便捷断言 -------------------------------------------------------

    def _rows(self):
        return GatewayStore(self.db).recent(50)

    def _events(self):
        with GatewayStore(self.db).connect() as connection:
            return [dict(r) for r in connection.execute("SELECT * FROM events").fetchall()]


class TestLeakGuardTests(_Base):
    """★ 这道闸门是"测试不许污染生产投递队列"的最后一道保险。"""

    def test_without_an_explicit_db_it_is_a_noop_under_pytest(self):
        os.environ.pop("R20_GATEWAY_DB", None)
        self.assertEqual(PUB.publish("trade.opened", "开仓", "BTC"), "test-noop")

    def test_the_noop_guard_creates_no_database_at_all(self):
        os.environ.pop("R20_GATEWAY_DB", None)
        PUB.publish("trade.opened", "开仓", "BTC")
        self.assertFalse(self.db.exists(), "noop 必须连库都不建")

    def test_the_noop_guard_does_not_even_resolve_the_channels(self):
        os.environ.pop("R20_GATEWAY_DB", None)
        PUB.publish("trade.opened", "开仓", "BTC")
        self.channels.assert_not_called()

    def test_an_explicit_db_lifts_the_guard(self):
        event_id = PUB.publish("trade.opened", "开仓", "BTC")
        self.assertEqual(event_id, self._only_event_id())
        self.assertTrue(self.db.exists())

    def _only_event_id(self):
        rows = self._events()
        self.assertEqual(len(rows), 1)
        return rows[0]["event_id"]

    def test_the_allow_flag_lifts_the_guard_even_without_the_db_variable(self):
        os.environ.pop("R20_GATEWAY_DB", None)
        os.environ["R20_ALLOW_TEST_PUBLISH"] = "1"
        event_id = PUB.publish("trade.opened", "开仓", "BTC")
        self.assertNotEqual(event_id, "test-noop")
        self.assertTrue(self.db.exists())

    def test_an_empty_allow_flag_does_not_lift_the_guard(self):
        os.environ.pop("R20_GATEWAY_DB", None)
        os.environ["R20_ALLOW_TEST_PUBLISH"] = ""
        self.assertEqual(PUB.publish("trade.opened", "开仓", "BTC"), "test-noop")

    def test_the_guard_is_off_when_the_db_variable_is_present(self):
        self.assertNotEqual(PUB.publish("trade.opened", "开仓", "BTC"), "test-noop")

    def test_the_guard_reads_the_process_module_table(self):
        """闸门靠 `sys.modules` 判"是否在测试进程里"——记录这个实现方式。"""
        source = Path(PUB.__file__).read_text(encoding="utf-8")
        self.assertIn('"unittest" in sys.modules or "pytest" in sys.modules', source)


class PublishPathTests(_Base):
    def test_the_event_id_is_returned(self):
        event_id = PUB.publish("trade.opened", "开仓", "BTC LONG")
        self.assertNotEqual(event_id, "test-noop")
        self.assertEqual(self._events()[0]["event_id"], event_id)

    def test_the_event_fields_are_persisted(self):
        PUB.publish("risk.triggered", "风险", "spread 扩大", priority=95)
        row = self._events()[0]
        self.assertEqual(row["event_type"], "risk.triggered")
        self.assertEqual(row["title"], "风险")
        self.assertEqual(row["message"], "spread 扩大")
        self.assertEqual(row["priority"], 95)

    def test_the_default_priority_is_fifty(self):
        PUB.publish("x", "t", "m")
        self.assertEqual(self._events()[0]["priority"], 50)

    def test_the_payload_is_persisted_as_json(self):
        PUB.publish("x", "t", "m", payload={"inst": "BTC", "方向": "多"})
        self.assertIn("BTC", self._events()[0]["payload_json"])

    def test_a_missing_payload_becomes_an_empty_object(self):
        PUB.publish("x", "t", "m")
        self.assertEqual(self._events()[0]["payload_json"], "{}")

    def test_a_delivery_row_is_created_per_channel(self):
        PUB.publish("x", "t", "m", channels=["qq", "telegram"])
        self.assertEqual(len(self._rows()), 2)

    def test_explicit_channels_bypass_the_configuration(self):
        PUB.publish("x", "t", "m", channels=["telegram"])
        self.channels.assert_not_called()
        self.assertEqual([r["channel"] for r in self._rows()], ["telegram"])

    def test_omitting_channels_consults_the_configuration(self):
        PUB.publish("x", "t", "m")
        self.channels.assert_called_once()
        self.assertEqual([r["channel"] for r in self._rows()], ["qq"])

    def test_an_empty_channel_list_creates_no_delivery(self):
        """显式传空列表 ≠ 传 None：前者是"不投任何渠道"，事件仍然入库。"""
        PUB.publish("x", "t", "m", channels=[])
        self.assertEqual(len(self._events()), 1)
        self.assertEqual(self._rows(), [])
        self.channels.assert_not_called()

    def test_the_database_path_is_resolved_at_call_time(self):
        """★ 第 30 行重新读环境变量 ⇒ 运行期改路径是生效的。"""
        other = Path(self.tmp.name) / "other.db"
        os.environ["R20_GATEWAY_DB"] = str(other)
        PUB.publish("x", "t", "m")
        self.assertTrue(other.exists())
        self.assertFalse(self.db.exists())

    def test_it_falls_back_to_the_module_db_path_without_the_variable(self):
        """⚠️ 顺序很关键：泄漏闸门（第 26 行）**在**路径解析（第 30 行）之前。

        所以"没有环境变量"这一件事**同时**触发两道逻辑 —— 想验证 `DB_PATH` 兜底，
        必须先把闸门用 `R20_ALLOW_TEST_PUBLISH` 解除，否则拿到的永远是 `test-noop`。
        （第一版就是漏了这层，断言"库该被建出来"直接假红。）
        """
        os.environ.pop("R20_GATEWAY_DB", None)
        os.environ["R20_ALLOW_TEST_PUBLISH"] = "1"
        PUB.publish("x", "t", "m")
        self.assertTrue(self.db.exists(), "应落到已打桩的 DB_PATH")

    def test_without_the_variable_the_guard_wins_over_the_path_fallback(self):
        """上一条的**反证**：不解除闸门时，`DB_PATH` 兜底根本不会被执行。"""
        os.environ.pop("R20_GATEWAY_DB", None)
        self.assertEqual(PUB.publish("x", "t", "m"), "test-noop")
        self.assertFalse(self.db.exists())

    def test_two_publishes_produce_two_events(self):
        first = PUB.publish("x", "t", "m")
        second = PUB.publish("x", "t", "m")
        self.assertNotEqual(first, second)
        self.assertEqual(len(self._events()), 2)


if __name__ == "__main__":
    unittest.main()

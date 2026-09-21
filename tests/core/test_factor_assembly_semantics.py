"""因子装配的**取值语义**（第二百零七刀）—— 先读实现再断言。

上一刀我按「每持仓一行」的**臆想模型**写断言被两条失败打回；本刀改成**先逐行读实现**，
只钉读到的分支，且**只断言已见的键**（未见的键名一律不猜）。

| 语义 | 取值 |
|---|---|
| 动作派发 | `ai_dec.action` ＞ 状态文件 `ins.action` ＞ `WAIT`（**三级回退**）|
| ★ 分数 | `BUY_LONG` ⇒ **2.5**；`SELL_SHORT` ⇒ **−2.5**；其余（含观望）⇒ **0.0** |
| 价格 | 状态文件 `price`（且不得是 `None`/占位符）优先；两处都没有 ⇒ 占位符。⚠️「退到因子库」那一路**本轮未验证**（库文件的装载形状我还没读到）⇒ 不声称 |
| 24h 涨跌 | 实时 ticker 的 `chg24h` ＞ 因子库；★ 判据是 `is not None` ⇒ **0 不会被短路** |
| 名称/类型 | `name`：池 ＞ 状态文件；`type` 缺省 `crypto` |
| 持仓槽 | `position` = 该标的的持仓行（无持仓则为空）|
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r20_backend.dashboard_payload import factors as F


class AssemblySemanticsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)
        self.pool = [{"instId": "BTC-USDT-SWAP", "price": 100.0, "name": "BTC"}]
        p = patch.object(F, "load_instruments", return_value=self.pool)
        p.start()
        self.addCleanup(p.stop)

    def _cleanup(self):
        for f in self.dir.iterdir():
            f.unlink()
        self.dir.rmdir()

    def _file(self, payload, name):
        p = self.dir / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def _run(self, decisions=None, state=None, lib=None, pool=None):
        if pool is not None:
            F.load_instruments.return_value = pool
        rows, _ = F._build_factors_from_local_files(
            str(self._file(lib or {}, "lib.json")), str(self._file(decisions or {}, "dec.json")),
            str(self._file(state or {}, "st.json")), [], "2026-09-21 12:00:00")
        return rows

    def test_score_follows_the_ai_action(self):
        for action, expect in (("BUY_LONG", 2.5), ("SELL_SHORT", -2.5), ("WAIT", 0.0),
                               ("HOLD", 0.0)):
            with self.subTest(action=action):
                rows = self._run(decisions={"BTC-USDT-SWAP": {"decision": {"action": action}}})
                self.assertEqual(rows[0]["score"], expect,
                                 "分数是方向强度：多 +2.5 / 空 −2.5 / 观望 0")

    def test_missing_action_falls_back_to_zero_score(self):
        self.assertEqual(self._run(decisions={})[0]["score"], 0.0,
                         "没有动作 ⇒ 0.0（不猜方向）")

    def test_ai_action_wins_over_the_state_file_action(self):
        """★ 三级回退：AI 决策 ＞ 状态文件 ＞ WAIT。"""
        rows = self._run(decisions={"BTC-USDT-SWAP": {"decision": {"action": "BUY_LONG"}}},
                         state={"instruments": [{"instId": "BTC-USDT-SWAP",
                                                 "action": "SELL_SHORT"}]})
        self.assertEqual(rows[0]["score"], 2.5, "AI 决策优先于状态文件")
        rows2 = self._run(decisions={},
                          state={"instruments": [{"instId": "BTC-USDT-SWAP",
                                                  "action": "SELL_SHORT"}]})
        self.assertEqual(rows2[0]["score"], -2.5, "AI 没给 ⇒ 用状态文件的动作")

    def test_price_prefers_state_then_library_then_dash(self):
        rows = self._run(state={"instruments": [{"instId": "BTC-USDT-SWAP", "price": 123.0}]})
        self.assertEqual(rows[0]["price"], 123.0)
        rows = self._run(pool=[{"instId": "BTC-USDT-SWAP"}])
        self.assertEqual(rows[0]["price"], "--", "两处都没有 ⇒ 占位符（不编造价格）")

    def test_zero_change_is_not_short_circuited_by_falsiness(self):
        """★ 判据是 `is not None` ⇒ **0% 涨跌必须照发 0**，不能掉到库里的旧值。"""
        rows = self._run(decisions={"BTC-USDT-SWAP": {"raw_ticker": {"chg24h": 0}}},
                         lib={"BTC-USDT-SWAP": {"chg24h": 9.9}})
        self.assertEqual(rows[0]["chg24h"], 0, "0 是真实值（判 falsy 会把它当成「没有」）")

    def test_name_type_and_position_slot(self):
        rows = self._run()
        self.assertEqual(rows[0]["name"], "BTC", "name 取池里的值")
        self.assertEqual(rows[0]["type"], "crypto", "type 缺省 crypto")
        self.assertIsNone(rows[0]["position"], "空池时持仓槽为空（不是 {} —— 空字典会被当成有持仓）")


if __name__ == "__main__":
    unittest.main()

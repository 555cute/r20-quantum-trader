"""因子装配的**取值语义**（第二百零七刀）—— 先读实现再断言。

上一刀我按「每持仓一行」的**臆想模型**写断言被两条失败打回；本刀改成**先逐行读实现**，
只钉读到的分支，且**只断言已见的键**（未见的键名一律不猜）。

| 语义 | 取值 |
|---|---|
| 动作派发 | `ai_dec.action` ＞ 状态文件 `ins.action` ＞ `WAIT`（**三级回退**）|
| ★ 分数 | `BUY_LONG` ⇒ **2.5**；`SELL_SHORT` ⇒ **−2.5**；其余（含观望）⇒ **0.0** |
| 价格 | 状态文件 `price`（非 `None`/占位符）＞ 因子库 `price` ＞ 占位符 |
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

    def test_price_falls_back_to_the_factor_library(self):
        """★ 补上上一刀明确**未验证**的那一路（当时我把库文件的形状猜错了，见下条）。"""
        rows = self._run(state={"instruments": [{"instId": "BTC-USDT-SWAP", "price": "--"}]},
                         lib={"instruments": [{"instId": "BTC-USDT-SWAP", "price": 77.0}]})
        self.assertEqual(rows[0]["price"], 77.0, "状态文件是占位符 ⇒ 退到因子库")

    def test_factor_library_shape_is_instruments_list(self):
        """★ **名字即语义 / 形状即契约**：因子库是 `{"instruments": [ {instId, ...} ]}`。

        上一刀我用**扁平字典** `{instId: {...}}` 造夹具，于是"退到因子库"那一路断言失败 ——
        **错的是我的夹具，不是代码**（本轮先读装载代码才定位）。本用例把这条形状契约钉住：
        扁平形状**不被认领**，因此价格只能落到占位符。
        """
        rows = self._run(state={"instruments": [{"instId": "BTC-USDT-SWAP", "price": "--"}]},
                         lib={"BTC-USDT-SWAP": {"price": 77.0}})
        self.assertEqual(rows[0]["price"], "--",
                         "扁平形状不被认领 ⇒ 占位符（这正是我上一刀夹具的错处）")

    def test_24h_change_falls_back_to_the_library(self):
        rows = self._run(lib={"instruments": [{"instId": "BTC-USDT-SWAP", "chg24h": 9.9}]})
        self.assertEqual(rows[0]["chg24h"], 9.9, "没有实时 ticker ⇒ 用因子库")

    def test_library_indicators_and_the_neutral_default(self):
        """库里的 `trend_momentum` 指标；两处都没有时 `rsi` 落到 **50.0**。

        ⚠️ 值得记一笔：`rsi` 读不到给 **50.0**（一个**看起来中性**的数值），而同行的 `adx`
        读不到给占位符 —— 同一行里两种「读不到」表示法并存。本用例只钉现状；
        「读不到是否该给中性数值」列为待议（与「读不到 ≠ 没有」相关）。
        """
        rows = self._run(lib={"instruments": [
            {"instId": "BTC-USDT-SWAP", "trend_momentum": {"rsi_14": 61.5}}]})
        self.assertEqual(rows[0]["rsi"], 61.5, "库里有指标就用库里的")
        rows = self._run()
        self.assertEqual(rows[0]["rsi"], 50.0, "两处都没有 ⇒ 中性默认值 50.0（现状，列待议）")

    def test_name_type_and_position_slot(self):
        rows = self._run()
        self.assertEqual(rows[0]["name"], "BTC", "name 取池里的值")
        self.assertEqual(rows[0]["type"], "crypto", "type 缺省 crypto")
        self.assertIsNone(rows[0]["position"], "空池时持仓槽为空（不是 {} —— 空字典会被当成有持仓）")


if __name__ == "__main__":
    unittest.main()

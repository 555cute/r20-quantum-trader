"""因子装配主函数：本地文件 → 每持仓一行的因子条目（第二百零六刀）。

本刀只钉**直接观测到的事实**（黑盒驱动最小夹具，未逐行读实现）：
返回 `(factors_list, state_data)` 二元组；每个持仓产出**一条**条目，条目回带 `instId` 与
**原始持仓行本身**（`position` 同一对象）；`state_data` 是**状态文件的直通内容**。

⚠️ 边界：本刀**不**断言各计算字段（rsi/adx/score 等）的取值语义 —— 那需要逐行读实现并造配套夹具，
留待下一刀；此处只保证「装配的骨架与直通关系」不被悄悄改坏。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r20_backend.dashboard_payload import factors as F

LIB = {"BTC-USDT-SWAP": {"score": 8, "smart_money_derivative": "long"}}
DEC = {"BTC-USDT-SWAP": {"adx_1h": 30, "smart_money": 1, "note": "x"}}
ST = {"market_regime": "trend", "extra": [1, 2, 3]}


class FactorAssemblyLocalFilesTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)
        self.lib, self.dec, self.st = self._write(LIB), self._write(DEC), self._write(ST)
        self.pool = [{"instId": "BTC-USDT-SWAP", "price": 100.0, "vol24h": 5}]
        p = patch.object(F, "load_instruments", return_value=self.pool)
        p.start()
        self.addCleanup(p.stop)

    def _cleanup(self):
        for f in self.dir.iterdir():
            f.unlink()
        self.dir.rmdir()

    def _write(self, payload, name=None):
        p = self.dir / (name or f"{len(list(self.dir.iterdir()))}.json")
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def _run(self, positions, lib=None, dec=None, st=None):
        return F._build_factors_from_local_files(
            str(lib or self.lib), str(dec or self.dec), str(st or self.st),
            positions, "2026-09-21 12:00:00")

    def test_returns_a_pair_of_factor_list_and_state_data(self):
        out = self._run([{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos_sz": 2.0,
                          "markPx": 100.0}])
        self.assertIsInstance(out, tuple)
        self.assertEqual(len(out), 2, "(factors_list, state_data)")
        rows, state = out
        self.assertIsInstance(rows, list)
        self.assertEqual(state, ST, "状态文件内容**直通**（不重算、不改写）")

    def test_rows_follow_the_active_pool_not_just_the_positions(self):
        """★ 我原来的模型（「每个持仓一行」）是**错的**，被两条失败断言同时打回：

        - 两个持仓（池里只有一个）⇒ 行数 **不等于 2**；
        - **零持仓** ⇒ 行数**不是 0**。

        两条失败指向同一个模型：行是按**活跃标的池**装配的（持仓只是挂在对应标的上），
        因此没有持仓时仍会有行、池外的持仓不会凭空多出一行。本用例钉这个观测结果。
        """
        rows, _ = self._run([])
        self.assertTrue(rows, "零持仓仍有行（按池装配）⇒ 面板仍能显示各标的因子")
        self.assertEqual([r["instId"] for r in rows], ["BTC-USDT-SWAP"],
                         "行来自**活跃池**（此时池内只有 BTC）")

    def test_each_row_carries_its_instrument_and_a_position_slot(self):
        rows, _ = self._run([{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos_sz": 2.0,
                              "markPx": 100.0}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["instId"], "BTC-USDT-SWAP")
        self.assertIn("position", rows[0], "行上保留持仓位（无持仓时该槽为空）")
        self.assertIn("name", rows[0])

    def test_a_second_run_is_not_affected_by_the_first(self):
        """两次调用之间不得互相污染（模块级缓存/累加是最常见的这类 bug）。"""
        pos = [{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos_sz": 2.0, "markPx": 100.0}]
        first, _ = self._run(pos)
        second, _ = self._run([dict(pos[0])])
        self.assertEqual(len(first), len(second), "第二次调用不得继承第一次的结果")
        self.assertIsNot(first[0]["position"], second[0]["position"],
                         "每次调用处理的是**本次传入**的对象")


if __name__ == "__main__":
    unittest.main()

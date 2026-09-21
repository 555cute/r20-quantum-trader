"""损坏的本地文件：三处 `except: pass` ⇒ **静默当空，不炸**（第二百一十刀）。

`_build_factors_from_local_files` 读三个本地文件（因子库 / AI 决策 / 交易状态），每处都是
`try: ... except Exception: pass`。本刀用**非 JSON 内容**驱动这三条分支，钉住：

| 期望 | 理由 |
|---|---|
| **不抛异常** | 看板降级模式下这三个文件本来就可能残缺；读不动也不能让页面 500 |
| 状态数据退回 `{}` | 损坏 ⇒ 当空，而不是半截解析结果 |
| 因子条**照样有行** | 行按**活跃标的池**装配（与持仓/文件是否可用无关）|
| 字段退回占位符 | 库读不到 ⇒ 价格占位符、资金费率占位符（不编造数值）|

⚠️ 与 `load_json_dict_disclosed` 那条通路的**分工**：那条会**披露缺失**（进 `data_health.errors`），
这里是**纯本地**读取，静默当空即「这一项没有」。两者不可混为一谈；但如果哪天要让面板知道
「本地文件损坏」，需要在这三处补披露 —— 列为待议。
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r20_backend.dashboard_payload import factors as F


class CorruptLocalFilesTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)
        self.pool = [{"instId": "BTC-USDT-SWAP", "name": "BTC"}]
        p = patch.object(F, "load_instruments", return_value=self.pool)
        p.start()
        self.addCleanup(p.stop)

    def _cleanup(self):
        for f in self.dir.iterdir():
            f.unlink()
        self.dir.rmdir()

    def _broken(self, name):
        p = self.dir / name
        p.write_text("{ 这不是合法 JSON", encoding="utf-8")
        return p

    def test_corrupt_library_decisions_and_state_do_not_raise(self):
        rows, state = F._build_factors_from_local_files(
            str(self._broken("lib.json")), str(self._broken("dec.json")),
            str(self._broken("st.json")), [], "2026-09-21 12:00:00")
        self.assertEqual(state, {}, "损坏的状态文件 ⇒ 当空（不是半截结果）")
        self.assertTrue(rows, "因子条照样按活跃池装配")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["instId"], "BTC-USDT-SWAP")

    def test_corrupt_files_degrade_fields_to_placeholders(self):
        rows, _ = F._build_factors_from_local_files(
            str(self._broken("lib.json")), str(self._broken("dec.json")),
            str(self._broken("st.json")), [], "2026-09-21 12:00:00")
        row = rows[0]
        self.assertEqual(row["price"], "--", "库读不到 ⇒ 占位符（不编造价格）")
        self.assertEqual(row["fundingRate"], "--", "资金费率同样占位符")
        self.assertEqual(row["score"], 0.0, "没有动作 ⇒ 观望 0.0")
        self.assertEqual(row["name"], "BTC", "池里的名称仍可用（本地文件与池无关）")

    def test_missing_files_behave_like_corrupt_ones(self):
        """★ 不存在与损坏走**同一条**收敛路径（都当空）—— 免得只有一种被处理。"""
        rows, state = F._build_factors_from_local_files(
            str(self.dir / "nope1.json"), str(self.dir / "nope2.json"),
            str(self.dir / "nope3.json"), [], "2026-09-21 12:00:00")
        self.assertEqual(state, {})
        self.assertEqual(rows[0]["price"], "--")
        self.assertTrue(rows)


if __name__ == "__main__":
    unittest.main()

"""挂单键**跨所契约**：生产侧产出什么、消费侧拿它比什么（第一百一十六刀）。

## 为什么单开这个门（我上一刀的教训）

第一百一十五刀我修"外所未成交挂单的预留被误释放"时，把成因写成
"`pending_inst_ids` 混装三种拼写（OKX `XRP-USDT-SWAP`、币安 `XRPUSDT`、Gate `DOGE_USDT`）"
—— **这是错的**。真读产出方 `cycle_snapshot.collect_pending_inst_ids`：

```python
pending_inst_ids.add(f"{_gbase}-USDT-SWAP")     # 末段：所有外所统一归一到 OKX 拼写
```

即：**产出方已经把三所拼写统一成 OKX 拼写**，消费侧（对账器、挂单去重守卫）
拿到的一律是 `XRP-USDT-SWAP` 这种形态。真正的缺陷只是对账器那条
`venue == "okx"` 把外所整体排除在外（已修）。

**本机真机核对**（只读，2026-09-20）：当时 binance 挂着 XRP/DOGE 两张空单，
产出方实际给出 `['DOGE-USDT-SWAP', 'XRP-USDT-SWAP']` —— 逐字确认了这条契约。

教训：**消费侧的比较规则必须对着产出方的真实契约写**，否则测试会在一片
"自洽的错误心智模型"里全绿。故本门钉三件事：

1. 产出方契约：原生形状（币安 `symbol=XRPUSDT`、Gate `contract=DOGE_USDT`）
   → 统一产出 OKX 拼写；
2. 消费侧契约：去重守卫（`inst_id not in pending_inst_ids`）与预留对账器
   对**同一份产出**给出一致判断；
3. 产出方的**报价币假设**（硬编码 `-USDT-SWAP`）与真实准入清单一致 ——
   清单一旦出现非 USDT 合约，本门立刻翻红，逼人回来改产出方。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.trader.cycle_snapshot import collect_pending_inst_ids  # noqa: E402
from scripts.trader.reservation_reconcile import reconcile_reservation_ledger  # noqa: E402
from r20_backend import risk_reservation  # noqa: E402


_READ_SCOPE = None


def setUpModule():
    """显式声明生产读（第二百三十六刀）：
    本文件核对**线上准入清单**里每个标的都是 USDT 永续 —— 有意的线上守卫。

    只读、不改；声明在此是为了把「依赖线上配置内容」从**静默**变成**可审计**
    （守卫见 `tests/__init__.py`；`R20_TESTS_STRICT_READS=1` 下未声明的读会报错）。
    """
    global _READ_SCOPE
    from tests import allow_real_data_reads
    _READ_SCOPE = allow_real_data_reads()
    _READ_SCOPE.__enter__()


def tearDownModule():
    global _READ_SCOPE
    if _READ_SCOPE is not None:
        _READ_SCOPE.__exit__(None, None, None)
        _READ_SCOPE = None


class _Ad:
    """桩适配器：返回**适配器归一后的真实形状**。

    ⚠️ 本门的第一次版本按"顶层只有 `symbol`"造桩，四个用例全红 —— 查真适配器才发现
    `BinanceAdapter.open_orders()` 归一后同时给 `inst_id`（`XRPUSDT`）与
    `base`（`XRP`），`GateAdapter._normalize_order_item()` 同样给 `base`。
    产出方优先读 `o["base"]`，故真机不会走"裸 `symbol`"那条兜底。这条形状差异
    本身就是本门要钉的契约之一（见 `AdapterShapeRequirementTest`）。
    """

    def __init__(self, rows):
        self._rows = rows

    def open_orders(self):
        return list(self._rows)

    def list_open_orders(self, symbol=None):
        return [r for r in self._rows
                if symbol and symbol.upper() in str(r.get("contract") or "").upper()]


class _Reg:
    def __init__(self, adapters):
        self._a = adapters

    def execution_open(self, venue, mode):
        return True

    def get_adapter(self, venue, environment=None):
        return self._a[venue]


class ProducerContractTest(unittest.TestCase):
    """产出方：三所原生形状 → 统一 OKX 拼写。"""

    def _collect(self, adapters, pool=None):
        pool = pool or [{"instId": "XRP-USDT-SWAP"}, {"instId": "DOGE-USDT-SWAP"}]
        return collect_pending_inst_ids(venues=tuple(adapters), venue_mode="demo",
                                        broken_venues=set(), venue_registry=_Reg(adapters),
                                        load_instruments=lambda: pool,
                                        auth_markers=(), warn=None)[0]

    def test_binance_native_symbol_becomes_okx_spelling(self):
        """币安 `symbol=XRPUSDT` ⇒ `XRP-USDT-SWAP`（不是 `XRPUSDT`）。"""
        got = self._collect({"binance": _Ad([
            {"inst_id": "XRPUSDT", "base": "XRP", "side": "sell", "reduce_only": False}])})
        self.assertEqual(got, {"XRP-USDT-SWAP"})

    def test_gate_native_contract_becomes_okx_spelling(self):
        """Gate `contract=DOGE_USDT` ⇒ `DOGE-USDT-SWAP`（不是 `DOGE_USDT`）。"""
        got = self._collect({"gate": _Ad([
            {"contract": "DOGE_USDT", "base": "DOGE", "side": "sell",
             "reduce_only": False}])})
        self.assertEqual(got, {"DOGE-USDT-SWAP"})

    def test_okx_style_input_is_idempotent(self):
        """已经是 OKX 拼写时不得被二次加工（不得变成 `BTC-USDT-SWAP-USDT-SWAP`）。

        ⚠️ Gate 分支是**逐标的**查询（池子里每个 instId 调一次 `list_open_orders`），
        所以桩行必须带 `contract` 才能被那一路取到 —— 真机 Gate 行也确实带 `contract`。
        """
        got = self._collect({"gate": _Ad([
            {"contract": "BTC-USDT-SWAP", "side": "buy", "reduce_only": False}])},
            pool=[{"instId": "BTC-USDT-SWAP"}])
        self.assertEqual(got, {"BTC-USDT-SWAP"})
        self.assertNotIn("BTC-USDT-SWAP-USDT-SWAP", got)


class AdapterShapeRequirementTest(unittest.TestCase):
    """产出方要求行带 `base`（或 `inst_id`/`contract`）—— 只有裸 `symbol` 的行会被丢弃。"""

    def test_row_without_base_or_inst_id_is_dropped(self):
        got = collect_pending_inst_ids(
            venues=("binance",), venue_mode="demo", broken_venues=set(),
            venue_registry=_Reg({"binance": _Ad([
                {"symbol": "XRPUSDT", "side": "sell", "reduce_only": False}])}),
            load_instruments=lambda: [{"instId": "XRP-USDT-SWAP"}],
            auth_markers=(), warn=None)[0]
        self.assertEqual(got, set(),
                         "只有裸 symbol 的行读不出基名 ⇒ 会被丢弃（真适配器已归一，故生产不走这条）")

    def test_binance_raw_symbol_is_accepted_through_raw(self):
        """若行只把 symbol 放在 `raw` 里，产出方走 `_graw['symbol']` 兜底仍可识别。"""
        got = collect_pending_inst_ids(
            venues=("binance",), venue_mode="demo", broken_venues=set(),
            venue_registry=_Reg({"binance": _Ad([
                {"raw": {"symbol": "XRPUSDT"}, "side": "sell", "reduce_only": False}])}),
            load_instruments=lambda: [{"instId": "XRP-USDT-SWAP"}],
            auth_markers=(), warn=None)[0]
        self.assertEqual(got, {"XRP-USDT-SWAP"})


class ConsumerAgreementTest(unittest.TestCase):
    """消费侧：对**同一份产出**，去重守卫与预留对账器必须一致。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pending-key-")
        self.db = str(Path(self.tmp) / "res.db")
        self.mgr = risk_reservation.get_manager(db_path=self.db)

    def _produce(self):
        return collect_pending_inst_ids(
            venues=("binance",), venue_mode="demo", broken_venues=set(),
            venue_registry=_Reg({"binance": _Ad([
                {"inst_id": "XRPUSDT", "base": "XRP", "side": "sell",
                 "reduce_only": False}])}),
            load_instruments=lambda: [{"instId": "XRP-USDT-SWAP"}],
            auth_markers=(), warn=None)[0]

    def test_duplicate_guard_sees_the_signal_as_pending(self):
        pending = self._produce()
        signal_inst = "XRP-USDT-SWAP"          # 信号/意图用的是 OKX 拼写（准入清单同源）
        self.assertIn(signal_inst, pending,
                      "去重守卫若看不见这笔外所挂单 ⇒ 同信号逐轮重复下单")

    def test_reconciler_keeps_the_same_intent_alive(self):
        pending = self._produce()
        intent = "XRP-USDT-SWAP:SELL_SHORT:1"
        self.mgr.reserve(("binance", "demo", "fp"), intent, 100.0, state="confirmed")
        con = sqlite3.connect(self.db)
        con.execute("UPDATE risk_reservations SET updated_at = '2020-01-01 00:00:00' "
                    "WHERE intent_id = ?", (intent,))
        con.commit(); con.close()
        n = reconcile_reservation_ledger({}, pending, "demo",
                                         reservation_manager=lambda: self.mgr,
                                         fetch_other_venue_positions=lambda e: (True, {"binance": []}, ""),
                                         state_closed="closed", default_ttl_s=7200.0,
                                         venue_snapshot={"binance": []})
        self.assertEqual(n, 0, "同一份产出下，对账器必须与去重守卫一样认为它仍在挂")


class QuoteAssumptionTest(unittest.TestCase):
    """产出方硬编码 `-USDT-SWAP`：与真实准入清单的报价币必须一致（防腐烂）。"""

    def test_every_pool_instrument_is_usdt_swap(self):
        """准入清单若出现非 USDT 合约，产出方的键会错（去重/对账双双失配）。

        本门故意读**真实** `data/instrument_pool.json`：清单一旦加非 USDT 合约就翻红，
        逼人回来把产出方改成"保留真实报价币"，而不是悄悄留一个永远不匹配的键。
        """
        p = ROOT / "data" / "instrument_pool.json"
        if not p.exists():
            self.skipTest("本机无准入清单（离线/冷仓）")
        data = json.loads(p.read_text(encoding="utf-8"))
        insts = data.get("allowed_inst_ids") or data.get("instruments") or []
        self.assertTrue(insts, "准入清单为空 ⇒ 请确认读取路径")
        bad = []
        for item in insts:
            inst = str(item.get("instId") if isinstance(item, dict) else item)
            if not inst.endswith("-USDT-SWAP"):
                bad.append(inst)
        self.assertEqual(bad, [], 
                         "准入清单出现非 USDT 合约 ⇒ collect_pending_inst_ids 的 "
                         "f'{base}-USDT-SWAP' 会产出错键（去重失效），必须先修产出方")


if __name__ == "__main__":
    unittest.main()

class SideInferenceTest(unittest.TestCase):
    """在途挂单**没有 `side`** 时按数量符号推断方向；推不出来就**跳过**（不猜）。

    外所归一形状里 `size` 是有符号的（正=买/开多，负=卖/开空）。少了这一步，
    这些单会因 `_gs not in ("buy","sell")` 被静默跳过 ⇒ 槽位/同向占用少算。
    """

    def _rows(self, rows):
        return collect_pending_inst_ids(venues=("gate",), venue_mode="demo",
                                        broken_venues=set(),
                                        venue_registry=_Reg({"gate": _Ad(rows)}),
                                        load_instruments=lambda: [{"instId": "BTC-USDT-SWAP"}],
                                        auth_markers=(), warn=None)

    def test_positive_size_infers_buy(self):
        """正数量 ⇒ 推断为买（开多），并计入多头。"""
        ids, longs, shorts = self._rows([{"contract": "BTC_USDT", "base": "BTC", "size": 2}])
        self.assertEqual(ids, {"BTC-USDT-SWAP"}, "数量为正 ⇒ 方向推得出来，不该被丢掉")
        self.assertEqual((longs, shorts), (1, 0))

    def test_negative_size_infers_sell(self):
        ids, longs, shorts = self._rows([{"contract": "BTC_USDT", "base": "BTC", "size": -3}])
        self.assertEqual(ids, {"BTC-USDT-SWAP"})
        self.assertEqual((longs, shorts), (0, 1), "负数量 ⇒ 卖（开空）")

    def test_non_numeric_size_is_tolerated_and_skipped(self):
        """数量不是数字 ⇒ 容忍（不抛）但**跳过该单**：方向推不出来就不许硬猜。"""
        ids, longs, shorts = self._rows([{"contract": "BTC_USDT", "base": "BTC", "size": "abc"}])
        self.assertEqual((ids, longs, shorts), (set(), 0, 0),
                         "方向不可判定 ⇒ 跳过，绝不当成买或卖")

    def test_zero_size_is_skipped(self):
        ids, _, _ = self._rows([{"contract": "BTC_USDT", "base": "BTC", "size": 0}])
        self.assertEqual(ids, set(), "数量为 0 的挂单不占槽位")


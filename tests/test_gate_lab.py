"""Gate 试验田编排与分流策略单测（全 mock、零网络、零生产文件）。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import gate_lab_trader as lab  # noqa: E402
from r20_backend.exchanges import routing_policy  # noqa: E402
from r20_backend.exchanges.gate import GateAdapter  # noqa: E402


class _StubAd(GateAdapter):
    def __init__(self, positions_ok=True):
        self.calls = []
        self._positions_ok = positions_ok
        self.price_orders = [{"id": "tpA"}, {"id": "slA"}]
        self.open_orders = []          # list_open_orders 返回（G7 清扫源）

    def fetch_instrument_spec(self, symbol, refresh=False):
        from r20_backend.exchanges import InstrumentSpec
        return InstrumentSpec(venue="gate", inst_id=f"{symbol}_USDT", base=symbol,
                              tick_size=0.1, step_size=0.0001, ct_val=0.0001, min_size=1)

    def fetch_ticker(self, symbol):
        return {"last": 79000.0, "mark_price": 79000.0}

    def _keys(self):
        return ("k", "s")

    def set_leverage(self, s, lev, margin_mode="cross"):
        self.calls.append(("lev", s, lev)); return {}

    def place_order(self, s, side, c, price=None, tif="gtc", text=""):
        self.calls.append(("place", s, side, c, price)); return {"id": 777}

    def attach_protective_orders(self, s, side, tp_px=None, sl_px=None, expiration=604800, price_type=0):
        self.calls.append(("attach", s, side)); return {"tp": "tpA", "sl": "slA"}

    def list_protective_orders(self, s):
        return list(self.price_orders)

    def list_open_orders(self, s):
        self.calls.append(("list_open", s))
        return list(self.open_orders)

    def cancel_order(self, s, oid):
        self.calls.append(("cancel_order", s, str(oid))); return {}

    def cancel_price_order(self, oid):
        self.calls.append(("cancel_price", str(oid))); return {}

    def positions(self):
        self.calls.append(("positions",))
        return [{"base": "BTC", "side": "long", "size_signed": 57, "mark_price": 79500.0,
                 "entry_price": 79000.0}]

    def amend_stop_loss(self, s, side, old_id, new_sl, expiration=604800):
        self.calls.append(("amend", s, side, old_id, new_sl)); return "slB"

    def fast_close_position(self, s, text=""):
        self.calls.append(("close", s)); return {"id": 888}


class LabCase(unittest.TestCase):
    """基类：所有数据写口（lab 三文件、OKX 敞口只读源、主台账 DB）全部钉到临时目录。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.df = os.path.join(self.tmp.name, "decisions.json")
        self.tf = os.path.join(self.tmp.name, "trackers.json")
        self.lf = os.path.join(self.tmp.name, "ledger.json")
        self.db = os.path.join(self.tmp.name, "r20_quant.test.db")
        self.okx_tf = os.path.join(self.tmp.name, "okx_trackers.json")   # 默认不存在→(0, True)
        self.pool_f = os.path.join(self.tmp.name, "instrument_pool.json")
        p1 = patch.object(lab, "DECISION_FILE", self.df)
        p2 = patch.object(lab, "LAB_TRACKER_FILE", self.tf)
        p3 = patch.object(lab, "LAB_LEDGER_FILE", self.lf)
        p4 = patch.object(lab, "OKX_TRACKER_FILE", self.okx_tf)
        p5 = patch.object(lab, "INSTRUMENT_POOL_FILE", self.pool_f)
        import db_manager as dbm
        p6 = patch.object(dbm, "DB_PATH", self.db)
        for p in (p1, p2, p3, p4, p5, p6):
            p.start()
        self._patches = [p1, p2, p3, p4, p5, p6]
        self.dbm = dbm

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def main_db_rows(self):
        import sqlite3
        if not os.path.exists(self.db):
            return []
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute("SELECT * FROM trades")]
        con.close()
        return rows

    def write_decisions(self, **per_asset):
        now = int(time.time())
        cache = {}
        for asset, dec in per_asset.items():
            cache[f"{asset}-USDT-SWAP"] = {"timestamp": now, "decision": dec}
        with open(self.df, "w") as f:
            json.dump(cache, f)

    def pool(self, assets, dry=True, **over):
        p = {"assets": assets, "margin_per_trade_usdt": 50.0, "max_open": 2,
             "min_confidence": 80.0, "dry_run": dry}
        p.update(over)
        return p

    def use_pool(self, pool, mode):
        pe = patch.object(lab, "effective_mode", lambda: mode)
        pl = patch.object(lab, "load_gate_pool", lambda: pool)
        pe.start(); pl.start()
        self._patches += [pe, pl]


class TestRoutingPolicy(unittest.TestCase):
    def test_default_missing_file_is_off_and_dry(self):
        with patch.object(routing_policy, "ROUTING_FILE", Path("/nonexistent/venue_routing.json")), \
                patch.dict(os.environ, {}, clear=True):
            pool = routing_policy.load_gate_pool()
            self.assertEqual(pool["assets"], [])
            self.assertEqual(routing_policy.effective_mode(), "off")

    def test_no_credentials_forces_dry_run(self):
        import r20_gateway.secrets as gw
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "vr.json"
            f.write_text(json.dumps({"gate": {"assets": ["btc", "BTC", "eth"],
                                              "margin_per_trade_usdt": 30}}))
            with patch.object(routing_policy, "ROUTING_FILE", f), \
                    patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}), \
                    patch.object(gw, "load_secrets", lambda: {}):
                pool = routing_policy.load_gate_pool()
                mode = routing_policy.effective_mode()
            self.assertEqual(pool["assets"], ["BTC", "ETH"])   # 归一+去重+排序
            self.assertTrue(pool["dry_run"])                   # 无凭证强制演算
            self.assertEqual(mode, "dry_run")


class TestLabDryRun(LabCase):
    def test_dry_entry_writes_tracker_and_detail(self):
        self.write_decisions(BTC={"action": "BUY_LONG", "confidence": 90, "leverage": 3,
                                  "margin_usdt": 40.0, "entry_price": 79000.0,
                                  "take_profit_price": 85000.0, "stop_loss_price": 77000.0})
        self.use_pool(self.pool(["BTC"]), "dry_run")
        acts = lab.run_lab_cycle(ad=_StubAd())
        self.assertEqual(len(acts), 1)
        self.assertIn("[DRY] 若开闸将执行", acts[0])
        self.assertIn("限价多 15张", acts[0])   # 40×3/79000/0.0001 = 15.19 → 15
        tr = json.load(open(self.tf))
        self.assertEqual(tr["BTC"]["mode"], "dry")
        self.assertEqual(tr["BTC"]["contracts"], 15)

    def test_geometry_reject_recorded_not_silent(self):
        self.write_decisions(BTC={"action": "BUY_LONG", "confidence": 95, "leverage": 3,
                                  "margin_usdt": 40.0, "entry_price": 79000.0,
                                  "take_profit_price": 80000.0, "stop_loss_price": 79500.0})
        self.use_pool(self.pool(["BTC"]), "dry_run")
        acts = lab.run_lab_cycle(ad=_StubAd())
        self.assertIn("risk_gate", acts[0])
        self.assertEqual(json.load(open(self.tf)), {})

    def test_stale_and_low_conf_skipped(self):
        old = int(time.time()) - 999
        with open(self.df, "w") as f:
            json.dump({"BTC-USDT-SWAP": {"timestamp": old, "decision": {"action": "BUY_LONG", "confidence": 99}},
                       "ETH-USDT-SWAP": {"timestamp": int(time.time()), "decision": {"action": "BUY_LONG", "confidence": 60}}}, f)
        self.use_pool(self.pool(["BTC", "ETH"]), "dry_run")
        self.assertEqual(lab.run_lab_cycle(ad=_StubAd()), [])

    def test_max_open_cap(self):
        self.write_decisions(BTC={"action": "BUY_LONG", "confidence": 90, "leverage": 3,
                                  "margin_usdt": 40.0, "entry_price": 79000.0,
                                  "take_profit_price": 85000.0, "stop_loss_price": 77000.0},
                             ETH={"action": "BUY_LONG", "confidence": 90, "leverage": 3,
                                  "margin_usdt": 40.0, "entry_price": 2500.0,
                                  "take_profit_price": 2700.0, "stop_loss_price": 2400.0})
        self.use_pool(self.pool(["BTC", "ETH"], max_open=1), "dry_run")
        acts = lab.run_lab_cycle(ad=_StubAd())
        self.assertEqual(len(acts), 2)          # 一开一满仓跳过
        self.assertTrue(any("满仓" in a for a in acts))


class TestLabLive(LabCase):
    def test_live_open_through_real_router(self):
        self.write_decisions(BTC={"action": "BUY_LONG", "confidence": 90, "leverage": 3,
                                  "margin_usdt": 40.0, "entry_price": 79000.0,
                                  "take_profit_price": 85000.0, "stop_loss_price": 77000.0})
        self.use_pool(self.pool(["BTC"], dry=False), "live")
        ad = _StubAd()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            acts = lab.run_lab_cycle(ad=ad)
        self.assertIn("开仓:", acts[0])
        self.assertEqual([c[0] for c in ad.calls if c[0] in ("lev", "place", "attach")],
                         ["lev", "place", "attach"])
        tr = json.load(open(self.tf))
        self.assertEqual(tr["BTC"]["mode"], "live")
        self.assertEqual(tr["BTC"]["tp_id"], "tpA")

    def test_live_reconcile_gone_position_ledgers(self):
        tr = {"SOL": {"mode": "live", "asset": "SOL", "entry_px": 100.0, "contracts": 5,
                      "side": "long", "tp_id": "t1", "sl_id": "s1", "tp_px": 110.0, "sl_px": 95.0}}
        json.dump(tr, open(self.tf, "w"))
        self.write_decisions(BTC={"action": "WAIT", "confidence": 50})
        self.use_pool(self.pool(["BTC", "SOL"], dry=False), "live")
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            acts = lab.run_lab_cycle(ad=_StubAd())   # stub positions 只有 BTC → SOL 消失
        self.assertTrue(any("对账落账" in a for a in acts))
        self.assertEqual(json.load(open(self.tf)), {})
        ledger = json.load(open(self.lf))
        self.assertEqual(ledger[-1]["asset"], "SOL")

    def test_live_ratchet_amends_and_protection_repair(self):
        tr = {"BTC": {"mode": "live", "asset": "BTC", "side": "long", "entry_px": 79000.0,
                      "contracts": 57, "tp_px": 85000.0, "sl_px": 77000.0,
                      "tp_id": "tpA", "sl_id": "OLD", "order_id": "777"}}
        json.dump(tr, open(self.tf, "w"))
        self.write_decisions(BTC={"action": "HOLD", "confidence": 70,
                                  "stop_loss_price": 79300.0})   # 收紧（<mark 79500）
        self.use_pool(self.pool(["BTC"], dry=False), "live")
        ad = _StubAd()
        ad.price_orders = [{"id": "tpA"}]   # sl 腿缺失 → 需补挂
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            acts = lab.run_lab_cycle(ad=ad)
        self.assertTrue(any("止损上移" in a for a in acts))
        self.assertTrue(any("保护缺口已补挂" in a for a in acts))
        tr2 = json.load(open(self.tf))
        self.assertEqual(tr2["BTC"]["sl_px"], 79300.0)
        # amend 换发 slB 后巡检见缺口 → 补挂收敛到在册腿
        self.assertIn(tr2["BTC"]["sl_id"], ("slA", "slB"))

    def test_live_close_market(self):
        tr = {"BTC": {"mode": "live", "asset": "BTC", "side": "long", "entry_px": 79000.0,
                      "contracts": 57, "tp_px": 85000.0, "sl_px": 77000.0,
                      "tp_id": "tpA", "sl_id": "slA"}}
        json.dump(tr, open(self.tf, "w"))
        self.write_decisions(BTC={"action": "CLOSE_MARKET", "confidence": 92})
        self.use_pool(self.pool(["BTC"], dry=False), "live")
        ad = _StubAd()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            acts = lab.run_lab_cycle(ad=ad)
        self.assertIn(("close", "BTC"), ad.calls)
        self.assertTrue(any("平仓: True" in a for a in acts))

    def test_live_position_query_failure_conservative(self):
        class Dead(_StubAd):
            def positions(self):
                raise RuntimeError("502")
        self.write_decisions(BTC={"action": "BUY_LONG", "confidence": 95})
        self.use_pool(self.pool(["BTC"], dry=False), "live")
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            acts = lab.run_lab_cycle(ad=Dead())
        self.assertTrue(any("保守跳过" in a for a in acts))

    def test_dry_and_live_trackers_isolated(self):
        tr = {"BTC": {"mode": "live", "asset": "BTC", "side": "long", "entry_px": 1,
                      "contracts": 1, "tp_px": 2, "sl_px": 1, "tp_id": "t", "sl_id": "s"}}
        json.dump(tr, open(self.tf, "w"))
        self.write_decisions(BTC={"action": "BUY_LONG", "confidence": 90, "leverage": 3,
                                  "margin_usdt": 40.0, "entry_price": 79000.0,
                                  "take_profit_price": 85000.0, "stop_loss_price": 77000.0})
        self.use_pool(self.pool(["BTC"]), "dry_run")
        lab.run_lab_cycle(ad=_StubAd())
        # dry 轮不得对账/覆盖 live tracker，也不得重复开
        tr2 = json.load(open(self.tf))
        self.assertEqual(tr2["BTC"]["mode"], "live")


class TestUS005LedgerAndExposure(LabCase):
    """主台账归一 / 敞口合并 / G7 孤儿清扫。"""

    LIVE_T = {"mode": "live", "asset": "BTC", "side": "long", "entry_px": 79000.0,
              "contracts": 57, "size_signed": 57, "margin_usdt": 40.0, "leverage": 3.0,
              "tp_px": 85000.0, "sl_px": 77000.0, "tp_id": "tpA", "sl_id": "slA",
              "entry_ts": 1788000000}

    def test_close_market_writes_main_ledger(self):
        json.dump({"BTC": dict(self.LIVE_T)}, open(self.tf, "w"))
        self.write_decisions(BTC={"action": "CLOSE_MARKET", "confidence": 92})
        self.use_pool(self.pool(["BTC"], dry=False), "live")
        ad = _StubAd()   # mark 79000 → pnl=0
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            acts = lab.run_lab_cycle(ad=ad)
        self.assertTrue(any("平仓: True" in a for a in acts))
        rows = self.main_db_rows()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["venue"], "gate")
        self.assertEqual(r["inst"], "BTC_USDT")
        self.assertEqual(r["action"], "closed")
        self.assertEqual(r["direction"], "多")
        self.assertTrue(r["bill_id"].startswith("gatelib-live-BTC-1788000000"))
        self.assertRegex(r["time"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertIn("pnl待交易所账单核对", r["comment"])
        self.assertIn("mark近似", r["comment"])
        self.assertEqual(json.load(open(self.tf)), {})   # tracker 已清

    def test_reconcile_sweeps_orphans_before_ledger(self):
        json.dump({"BTC": dict(self.LIVE_T)}, open(self.tf, "w"))
        self.write_decisions(BTC={"action": "WAIT", "confidence": 50})
        self.use_pool(self.pool(["BTC"], dry=False), "live")

        class NoPos(_StubAd):
            def positions(self):
                self.calls.append(("positions",))
                return []      # 仓位消失
        ad = NoPos()
        ad.open_orders = [{"id": 501}, {"id": 502}]
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            acts = lab.run_lab_cycle(ad=ad)
        seq = [c[0] for c in ad.calls]
        self.assertLess(seq.index("list_open"), seq.index("cancel_order"))
        self.assertEqual(sorted([c[2] for c in ad.calls if c[0] == "cancel_order"]),
                         ["501", "502"])
        self.assertIn(("cancel_price", "tpA"), ad.calls)
        self.assertIn(("cancel_price", "slA"), ad.calls)
        rows = self.main_db_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn("reconcile_no_position", rows[0]["comment"])
        self.assertTrue(any("已撤孤儿挂单" in a for a in acts))
        ledger = json.load(open(self.lf))
        # order 腿 2 笔 + 触发单 tp/sl 各 1 = 4 项清扫记录
        self.assertEqual(ledger[-1]["orphan_swept"],
                         ["order:501", "order:502", "tp_id:tpA", "sl_id:slA"])

    def test_dry_close_does_not_touch_main_db(self):
        json.dump({"BTC": {**self.LIVE_T, "mode": "dry"}}, open(self.tf, "w"))
        self.write_decisions(BTC={"action": "CLOSE_MARKET", "confidence": 92})
        self.use_pool(self.pool(["BTC"]), "dry_run")
        lab.run_lab_cycle(ad=_StubAd())
        self.assertEqual(self.main_db_rows(), [])   # dry 非真成交，不落主账

    def test_exposure_cap_blocks_open_with_component_detail(self):
        # OKX 侧已有 BTC 多仓名义 400 + 本单 150 → >500 拒开
        json.dump({"BTC-USDT-SWAP_long": {"instId": "BTC-USDT-SWAP", "side": "long",
                                           "currentSz": 40, "entryPx": 10.0}},
                  open(self.okx_tf, "w"))
        json.dump([{"name": "BTC", "ctVal": 1.0}], open(self.pool_f, "w"))
        self.write_decisions(BTC={"action": "BUY_LONG", "confidence": 90, "leverage": 5,
                                  "margin_usdt": 150.0, "entry_price": 79000.0,
                                  "take_profit_price": 85000.0, "stop_loss_price": 77000.0})
        self.use_pool(self.pool(["BTC"], margin_per_trade_usdt=150.0), "dry_run")
        with patch.dict(os.environ, {"R20_MAX_TOTAL_EXPOSURE_USDT": "500"}):
            acts = lab.run_lab_cycle(ad=_StubAd())
        line = next(a for a in acts if "EXPOSURE" in a)
        self.assertIn("跨所同向敞超限拒开", line)
        # 分量断言：40张×ctVal1.0×entryPx10 = 400；lab=0；本单=150×5=750 → total 1150>500
        self.assertIn("OKX同向=400.0", line)
        self.assertIn("lab同向=0.0", line)
        self.assertIn("= 1150.0", line)
        self.assertIn("cap 500.0", line)

    def test_okx_source_broken_live_refuses_dry_passes(self):
        # OKX tracker 文件损坏（非法 JSON）→ _okx_notional_for 返回 (0, False)
        with open(self.okx_tf, "w") as f:
            f.write("{not-json!!")
        dec = {"action": "BUY_LONG", "confidence": 90, "leverage": 3,
               "margin_usdt": 40.0, "entry_price": 79000.0,
               "take_profit_price": 85000.0, "stop_loss_price": 77000.0}
        self.write_decisions(BTC=dec)
        # live：fail-closed 拒开
        self.use_pool(self.pool(["BTC"], dry=False), "live")
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            acts = lab.run_lab_cycle(ad=_StubAd())
        self.assertTrue(any("fail-closed 拒开" in a for a in acts))
        self.assertEqual(json.load(open(self.tf)), {})
        # dry：放行（敞口查询失败不构成演算阻断）
        self.use_pool(self.pool(["BTC"]), "dry_run")
        acts2 = lab.run_lab_cycle(ad=_StubAd())
        self.assertTrue(any("开仓演算" in a for a in acts2))


    def test_main_ledger_idempotent_same_bill_id(self):
        # 同一 tracker 重复落账（对账竞态/重跑）→ INSERT OR REPLACE 幂等，只 1 行
        t = dict(self.LIVE_T)
        ok1, bill1 = lab._record_main_ledger(t, close_px=79100.0, pnl=5.7, reason="idempotency")
        ok2, bill2 = lab._record_main_ledger(t, close_px=79100.0, pnl=5.7, reason="idempotency")
        self.assertTrue(ok1 and ok2)
        self.assertEqual(bill1, bill2)
        rows = self.main_db_rows()
        self.assertEqual(len(rows), 1)          # 绝不重复成行
        self.assertEqual(rows[0]["venue"], "gate")

    def test_exposure_cap_env_override(self):
        # OKX 同向名义=100张×1.0×2.0=200U，本单=40×3=120U → 合计 320U
        json.dump({"BTC-USDT-SWAP_long": {"instId": "BTC-USDT-SWAP", "side": "long",
                                          "currentSz": 100.0, "entryPx": 2.0}},
                  open(self.okx_tf, "w"))
        json.dump([{"name": "BTC", "ctVal": 1.0}], open(self.pool_f, "w"))
        dec = {"action": "BUY_LONG", "confidence": 90, "leverage": 3,
               "margin_usdt": 40.0, "entry_price": 79000.0,
               "take_profit_price": 85000.0, "stop_loss_price": 77000.0}
        self.write_decisions(BTC=dec)
        self.use_pool(self.pool(["BTC"]), "dry_run")
        # 默认 cap 500：放行
        acts = lab.run_lab_cycle(ad=_StubAd())
        self.assertTrue(any("开仓演算" in a for a in acts))
        # env 压到 300：拒开且明细含三组件（先清第一轮 tracker，避免管理分支短路）
        json.dump({}, open(self.tf, "w"))
        acts2 = []
        with patch.dict(os.environ, {"R20_MAX_TOTAL_EXPOSURE_USDT": "300"}):
            acts2 = lab.run_lab_cycle(ad=_StubAd())
        line = next(a for a in acts2 if "EXPOSURE" in a)
        self.assertIn("200.0", line)
        self.assertIn("120.0", line)
        self.assertIn("> cap 300.0", line)
        self.assertEqual(json.load(open(self.tf)), {})   # 拒开不留 tracker
        # 非法 env 值回退默认 500（fail-safe 不炸）
        with patch.dict(os.environ, {"R20_MAX_TOTAL_EXPOSURE_USDT": "banana"}):
            self.assertEqual(lab._exposure_cap(), 500.0)
        from r20_backend.settings_store import MANAGED_KEYS
        self.assertIn("R20_MAX_TOTAL_EXPOSURE_USDT", MANAGED_KEYS)


class TestTighterRule(unittest.TestCase):
    def test_long_short_semantics(self):
        self.assertTrue(lab._is_tighter("long", 78000, 77000, 79000))
        self.assertFalse(lab._is_tighter("long", 76000, 77000, 79000))   # 放宽拒绝
        self.assertFalse(lab._is_tighter("long", 79500, 77000, 79000))   # 越过现价拒绝
        self.assertTrue(lab._is_tighter("short", 80000, 81000, 79000))
        self.assertTrue(lab._is_tighter("short", 80000, 0, 79000))       # 首次设置
        self.assertFalse(lab._is_tighter("long", 0, 77000, 79000))


if __name__ == "__main__":
    unittest.main()

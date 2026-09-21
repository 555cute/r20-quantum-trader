"""己仓对账门：交易所实况 × 本方台账 → 归属判定（第一百一十刀）。

## 本门钉住的**真实事故**（2026-09-20 实盘日志 + 线上台账，逐字复现）

主链每轮都在拒同一批单，且**理由是错的**：

```
[ARB] AI限价空单提交失败: BINANCE 下单失败: ARB 交易所存在非本系统在管既有仓
      size_signed=-2416.7(short)，lab 无在管记录——外部仓连坐拒开   (08:00/08:15/09:00…)
[UNI] … UNI 交易所存在非本系统在管既有仓 size_signed=-82(short) …      (10:30/10:45…)
```

而线上台账里：

- `UNI/binance/空/82.0` → **`status=holding`**：这是我方仓，被报成"外部仓"；
- `ARB/binance/空/2416.7` → **`status=closed`**，而交易所**仍持有** -2416.7：
  **账实不符**（台账说已平），系统对这笔在持敞口既盲又永久拒开。

根因：`open_protected_position(own_position=…)` 全仓**没有一个生产调用方传它**，
于是 `own_match` 恒 False、文案恒为"外部仓"。

## 本门的两条铁律

1. **`untracked` / `ledger_unavailable` 是"归属不可判定"，不是"外部仓"**：
   本方记录缺失与"真是别人的仓"在台账上无从区分，宣称外部仓=说谎的诊断
   （运维会照假线索去找不存在的仓）；
2. 判定**只改诊断、不改行为**：所有既有仓分支仍**一律拒开**（本刀不放开任何新下单）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from r20_backend.execution import own_records as O  # noqa: E402

# ── 线上真实台账形状（逐字取自 data/trading_ledger.json，2026-09-20）─────────
REAL_HOLDING_UNI = {"id": "holding_binance_UNI_空", "inst": "UNI", "venue": "binance",
                    "side": "空", "sz": 82.0, "status": "holding", "open_time": "--"}
REAL_CLOSED_ARB = {"id": "binance_closed_66353266_1789875209000", "inst": "ARB",
                   "venue": "binance", "side": "空", "sz": 2416.7, "status": "closed",
                   "open_time": "2026-09-20 11:33:29"}


class ClassifyTest(unittest.TestCase):
    def _c(self, asset, size, *, ledger, venue="binance", side=None):
        return O.classify_exchange_position(venue=venue, asset=asset, size_signed=size,
                                            side=side, ledger=ledger)

    def test_uni_is_recognized_as_our_own_position(self):
        """UNI：台账 holding 82 空 = 交易所 -82 short ⇒ **本方在管**（不是外部仓）。"""
        r = self._c("UNI", -82.0, ledger=[REAL_HOLDING_UNI])
        self.assertEqual(r["verdict"], "own")
        self.assertIn("本方在管", r["reason"])
        self.assertEqual(r["matched"]["status"], "holding")
        self.assertEqual(r["matched"]["sz"], 82.0)

    def test_arb_is_ledger_vs_exchange_mismatch(self):
        """ARB：交易所仍持 -2416.7，台账同尺寸同方向那行**已 closed** ⇒ 账实不符。"""
        r = self._c("ARB", -2416.7, ledger=[REAL_CLOSED_ARB])
        self.assertEqual(r["verdict"], "stale_closed")
        self.assertIn("账实不符", r["reason"])
        self.assertIn("同尺寸同方向", r["reason"])
        self.assertEqual(r["matched"]["status"], "closed")

    def test_mismatch_when_holding_row_differs(self):
        row = dict(REAL_HOLDING_UNI, sz=51.0)          # 线上确有一条 UNI 多 51 的 closed 行
        r = self._c("UNI", -82.0, ledger=[row])
        self.assertEqual(r["verdict"], "mismatch")
        self.assertIn("与交易所不符", r["reason"])

    def test_untracked_is_not_declared_foreign(self):
        r = self._c("XYZ", -5.0, ledger=[REAL_HOLDING_UNI])
        self.assertEqual(r["verdict"], "untracked")
        self.assertIn("不可判定", r["reason"])
        self.assertNotIn("外部仓连坐", r["reason"])
        self.assertIn("外部仓或本方记录缺失", r["reason"])

    def test_unreadable_ledger_is_undecidable_not_absent(self):
        """台账**读不到**与"读到但没有该合约"是两件事，都不得判外部仓。"""
        r = self._c("UNI", -82.0, ledger=None)
        self.assertEqual(r["verdict"], "ledger_unavailable")
        self.assertIn("不可判定", r["reason"])
        self.assertIn("不得当成外部仓", r["reason"])

    def test_verdict_and_side_inference(self):
        self.assertEqual(self._c("UNI", -82.0, ledger=[REAL_HOLDING_UNI])["side"], "short")
        self.assertEqual(self._c("UNI", 82.0, ledger=[REAL_HOLDING_UNI])["verdict"], "mismatch",
                         "方向相反不得算 own")
        self.assertEqual(self._c("UNI", -82.0, ledger=[REAL_HOLDING_UNI],
                                 side="long")["verdict"], "mismatch",
                         "显式 side 与符号矛盾时以显式 side 为准（不得自我说服）")

    def test_venue_isolation(self):
        """OKX 的 holding 行不能给 binance 的仓背书（跨所张数语义不同）。"""
        r = self._c("UNI", -82.0, ledger=[REAL_HOLDING_UNI], venue="okx")
        self.assertEqual(r["verdict"], "untracked")

    def test_inst_normalization(self):
        for raw in ("UNI", "UNI-USDT-SWAP", "uniusdt", "UNI_USDT"):
            with self.subTest(raw=raw):
                self.assertEqual(O.canonical_inst(raw), "UNI")
        self.assertEqual(O.canonical_inst(""), "")
        self.assertEqual(O.canonical_inst(None), "")

    def test_all_verdicts_declared(self):
        for v in ("own", "stale_closed", "mismatch", "untracked", "ledger_unavailable"):
            self.assertIn(v, O.OWN_VERDICTS)


class LedgerReadTest(unittest.TestCase):
    def test_reads_real_ledger_shape_and_finds_the_uni_holding(self):
        """真实台账形状必须读得出 UNI/binance 的 holding 行；线上台账只查**结构**自洽。

        ⚠️ 第二百三十一刀：原断言要求「线上台账里**必须**存在 UNI/binance 的 holding 行」——
        那是对**生产数据**的断言：仓一平、台账一更新，本门立刻红（本轮就是这么红的，
        而代码一行没改）。测试依赖生产数据的**内容**＝定时炸弹。

        改为两段：
        ① 用**真实形状的固定数据**（`REAL_HOLDING_UNI`，从线上台账抄下来的形状）验证
           「读得出 UNI/binance」——确定性的、真正在测读取逻辑的那一半；
        ② 线上台账（若存在）只查**结构性**事实：读得出来、且 holding 行状态自洽。
           它仍然守着"线上文件坏了/格式变了能被发现"，但**不再要求某个标的在场**。
        """
        rows = O.read_holding_rows([REAL_HOLDING_UNI])
        self.assertTrue(any(O.canonical_inst(r.get("inst")) == "UNI"
                            and str(r.get("venue")).lower() == "binance" for r in rows),
                        "真实形状的 holding 行必须被读成 UNI/binance（确定性用例）")

        ledger = O.load_ledger()
        if ledger is None:
            self.skipTest("无 data/trading_ledger.json（干净检出）")
        live_rows = O.read_holding_rows(ledger)
        self.assertTrue(all(str(r.get("status")).lower() == "holding" for r in live_rows),
                        "线上台账的 holding 行必须自洽（读得出来、状态一致）")

    def test_missing_file_is_none_not_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(O.load_ledger(Path(d) / "nope.json"),
                              "缺失必须 None（不可当成『无仓』）")

    def test_corrupt_file_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "ledger.json"
            bad.write_text("{not json", encoding="utf-8")
            self.assertIsNone(O.load_ledger(bad))

    def test_accepts_dict_and_list_ledgers(self):
        self.assertEqual(len(O.read_ledger_rows([REAL_HOLDING_UNI])), 1)
        self.assertEqual(len(O.read_ledger_rows({"a": REAL_HOLDING_UNI})), 1)
        self.assertEqual(O.read_ledger_rows({"a": REAL_HOLDING_UNI})[0]["id"],
                         "holding_binance_UNI_空", "行内已有 id 时以其为准")
        self.assertEqual(O.read_ledger_rows({"a": {"inst": "UNI"}})[0]["id"], "a",
                         "行内无 id 时才用映射键补")
        self.assertEqual(O.read_ledger_rows("junk"), [])
        self.assertEqual(O.read_ledger_rows(None), [])

    def test_evidence_is_minimal(self):
        """证据字段白名单：不得整行外泄（台账行含费用/策略等与本判定无关的内容）。"""
        fat = dict(REAL_HOLDING_UNI, fee=1.23, strategy="secret", net_pnl=99.0)
        r = O.classify_exchange_position(venue="binance", asset="UNI", size_signed=-82.0,
                                         ledger=[fat])
        self.assertEqual(set(r["matched"]),
                         {"id", "inst", "venue", "side", "sz", "status", "open_time"})


class RouterPrecheckIntegrationTest(unittest.TestCase):
    """预检接线：判定如实进 detail，**行为仍是拒开**（本刀不放开下单）。"""

    def setUp(self):
        # ⚠️ 已知的**生产数据依赖**（第二百三十二刀登记，待办：换成夹具池后撤销本次放开）：
        # 预检链会经 `load_instruments()` 读生产 `data/instrument_pool.json`，而走到
        # `stage == "precheck"` 这一支的形状取决于线上池的内容；我试过注入夹具池，
        # 结果直接跳到 `venue_dry_run`（夹具与线上池不同形），说明本类**确实依赖线上池**。
        # 与其猜测线上池字段、不如显式登记：此处放开生产读守卫，并把"改夹具"列为待办。
        from tests import allow_real_data_reads
        self._read_scope = allow_real_data_reads()
        self._read_scope.__enter__()
        self.addCleanup(self._read_scope.__exit__, None, None, None)

        from r20_backend import execution_router
        from tests.test_gate_execution_router import _StubAdapter, _decision
        self.router = execution_router
        self.Adapter = _StubAdapter
        self._decision = _decision
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _ledger_file(self, rows):
        path = os.path.join(self.tmp.name, "ledger.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        return path

    #: 多向夹具（`_decision` 的几何是 BUY_LONG 口径：tp>entry>sl；
    #: 用 SELL_SHORT 会先撞"卖空几何不合法"的 risk_gate，根本走不到预检）。
    #: ⚠️ `venue` 换成 gate 只是为了让台账行与**注入的 Gate 形状桩**同场所 ——
    #: 分类器严格按场所隔离（binance 的 holding 行不给 gate 背书），真实 binance
    #: 行已在 `ClassifyTest` 逐字钉住，跨所隔离另有专测。
    HOLDING_LONG = dict(REAL_HOLDING_UNI, venue="gate", side="多")
    CLOSED_LONG = dict(REAL_CLOSED_ARB, inst="UNI", venue="gate", side="多", sz=82.0)
    POS_LONG = {"base": "UNI", "side": "long", "size_signed": 82.0}

    def _run(self, rows, *, position):
        ad = self.Adapter(positions_rows=[position])
        # 复用本仓已校准过风控闸门的决策助手（手搓决策会先撞 risk_gate，测不到预检）
        decision = self._decision(asset="UNI", venue="gate")
        with patch.object(self.router, "OWN_POSITION_LEDGER_FILE", self._ledger_file(rows)), \
                patch.dict(os.environ, {"R20_MAX_PRICE_CROSS_PCT": "0.05"}, clear=False):
            return self.router.open_protected_position(decision, adapter=ad, price_ref=79000.0), ad

    def test_own_position_is_reported_truthfully_and_still_refused(self):
        r, ad = self._run([self.HOLDING_LONG], position=self.POS_LONG)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "precheck")
        self.assertEqual(r["own_verdict"], "own")
        self.assertIn("本方已在管该仓", r["detail"])
        self.assertNotIn("外部仓", r["detail"])
        self.assertEqual([c for c in ad.calls if c[0] == "place"], [],
                         "拒开时不得留下任何委托")

    def test_stale_closed_is_reported_as_account_reality_mismatch(self):
        r, _ = self._run([self.CLOSED_LONG], position=self.POS_LONG)
        self.assertFalse(r["ok"])
        self.assertEqual(r["own_verdict"], "stale_closed")
        self.assertIn("账实不符", r["detail"])
        self.assertIn("人工核对", r["detail"])

    def test_caller_supplied_own_position_contract_preserved(self):
        """调用方显式传入时行为不变（自有记录优先，不查台账）。"""
        ad = self.Adapter(positions_rows=[self.POS_LONG])
        decision = self._decision(asset="UNI", venue="gate")
        with patch.object(self.router, "OWN_POSITION_LEDGER_FILE",
                          os.path.join(self.tmp.name, "absent.json")), \
                patch.dict(os.environ, {"R20_MAX_PRICE_CROSS_PCT": "0.05"}, clear=False):
            r = self.router.open_protected_position(
                decision, adapter=ad, price_ref=79000.0,
                own_position={"size_signed": 82.0, "side": "long"})
        self.assertNotEqual(r.get("own_verdict"), "untracked",
                            "显式传入在管记录时应走一致性判定，而不是去查台账")

    def test_ledger_unreadable_still_refuses_and_says_undecidable(self):
        r, _ = self._run([], position=self.POS_LONG)
        # 空台账 → 该合约无记录 → untracked（不可判定），仍拒开
        self.assertFalse(r["ok"])
        self.assertIn(r["own_verdict"], ("untracked", "ledger_unavailable"))
        self.assertIn("不可判定", r["detail"])


if __name__ == "__main__":
    unittest.main()

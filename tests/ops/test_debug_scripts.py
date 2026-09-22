"""两个一次性取证脚本的收口（`debug_aggregate_orders.py` / `debug_audit_bills.py`）—— 第 318 刀。

这两个是**账单排查工具**：把 OKX 账单按 `subType` 分类、按重启时刻过滤、
按 `ordId` 聚合出订单级战绩。它们此前各缺**同一行** ——
`raise SystemExit(3)`（API Key 未配置时的 fail-closed 分支）。

## 为什么是"直接执行整个脚本"

两者都是 `__main__` 级脚本，**没有任何函数**（全是模块级语句）。所以测试手法是
`runpy.run_path(..., run_name="__main__")` 配上打好桩的两个接缝
（`scripts.okx_rest.bills` 与 `scripts.okx_runtime.current_environment`），
再用 `redirect_stdout` 收输出。**不改一行源码。**

## 顺带钉住一个潜在崩溃

`debug_aggregate_orders.py` 结尾用 `wins/(wins+losses)*100` 算胜率 ——
账单里**一笔已结订单都没有**时（刚重启、或当周期无成交）会 `ZeroDivisionError`。
按本战役纪律只记录、未改。
"""
from __future__ import annotations

import contextlib
import datetime
import io
import runpy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.okx_rest as okx_rest  # noqa: E402
import scripts.okx_runtime as okx_runtime  # noqa: E402

AGGREGATE = ROOT / "scripts" / "debug_aggregate_orders.py"
AUDIT = ROOT / "scripts" / "debug_audit_bills.py"

BJ = datetime.timezone(datetime.timedelta(hours=8))
#: 脚本里硬编码的重启时刻（北京时间）
CUTOFF = "2026-08-29 01:11:20"


def _ts(bj_text: str) -> int:
    """把北京时间文本转成 OKX 风格的毫秒时间戳。"""
    dt = datetime.datetime.strptime(bj_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJ)
    return int(dt.timestamp() * 1000)


class _Harness(unittest.TestCase):
    def _env(self, configured=True):
        return SimpleNamespace(configured=configured, mode="live", fingerprint="FP")

    def _run(self, script, bills, *, configured=True):
        out = io.StringIO()
        with patch.object(okx_rest, "bills", lambda limit=100: list(bills)), \
             patch.object(okx_runtime, "current_environment",
                          lambda: self._env(configured)):
            try:
                with contextlib.redirect_stdout(out):
                    runpy.run_path(str(script), run_name="__main__")
            except SystemExit as exc:
                return exc.code, out.getvalue()
        return None, out.getvalue()

    def _bill(self, when=CUTOFF, **over):
        bill = {"ts": str(_ts(when)), "subType": "5", "ordId": "O1",
                "instId": "BTC-USDT-SWAP", "pnl": "10.0", "fee": "-0.5",
                "balChg": "9.5", "bal": "1000.0", "type": "2"}
        bill.update(over)
        return bill


class FailClosedTests(_Harness, unittest.TestCase):
    """两个脚本共用同一段 fail-closed 头（第 12–15 行）。"""

    def test_aggregate_exits_three_when_keys_are_missing(self):
        # ★ 第 15 行 —— 未配置 Key 必须**拒绝运行**，不许静默打印空报表
        code, out = self._run(AGGREGATE, [], configured=False)
        self.assertEqual(code, 3)
        self.assertIn("[NOT READY]", out)

    def test_audit_exits_three_when_keys_are_missing(self):
        # ★ 第 15 行（同一个分支）
        code, out = self._run(AUDIT, [], configured=False)
        self.assertEqual(code, 3)
        self.assertIn("[NOT READY]", out)

    def test_the_refusal_message_names_the_fail_closed_reason(self):
        _, out = self._run(AGGREGATE, [], configured=False)
        self.assertIn("fail-closed", out)
        self.assertIn("无 CLI 回退", out)

    def test_no_bills_are_even_requested_when_unconfigured(self):
        # 未配置时必须在**取账单之前**就退出（fail-closed 不许先出网）
        asked = []
        with patch.object(okx_rest, "bills", lambda limit=100: asked.append(limit) or []), \
             patch.object(okx_runtime, "current_environment",
                          lambda: self._env(False)):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    runpy.run_path(str(AGGREGATE), run_name="__main__")
        self.assertEqual(asked, [], "未配置 Key 时不得调用 bills()")


class AggregateOrdersTests(_Harness, unittest.TestCase):
    def test_settled_bills_are_aggregated_by_order_id(self):
        bills = [self._bill(ordId="A", pnl="10.0", fee="-0.5"),
                 self._bill(ordId="A", pnl="2.0", fee="-0.5"),
                 self._bill(ordId="B", pnl="-5.0", fee="-0.5")]
        _, out = self._run(AGGREGATE, bills)
        self.assertIn("总订单数: 2", out)

    def test_only_close_sub_types_are_counted(self):
        # `subType in ["5", "6"]`（平仓结算）才算订单级战绩
        bills = [self._bill(subType="5", ordId="CLOSE"),
                 self._bill(subType="6", ordId="CLOSE2"),
                 self._bill(subType="3", ordId="OPEN"),
                 self._bill(subType="173", ordId="FUNDING")]
        _, out = self._run(AGGREGATE, bills)
        self.assertIn("总订单数: 2", out)

    def test_bills_before_the_cutoff_are_ignored(self):
        bills = [self._bill(when="2026-08-29 01:11:19", ordId="OLD"),
                 self._bill(when=CUTOFF, ordId="NEW")]
        _, out = self._run(AGGREGATE, bills)
        self.assertIn("总订单数: 1", out)
        self.assertIn("NEW", out)
        self.assertNotIn("OLD", out)

    def test_cutoff_is_inclusive(self):
        # `dt >= cutoff` —— 恰好落在重启时刻那笔要算进去
        _, out = self._run(AGGREGATE, [self._bill(when=CUTOFF, ordId="EDGE")])
        self.assertIn("EDGE", out)

    def test_wins_and_losses_are_counted_from_net_pnl(self):
        # net = pnl + fee；两个 10-1=9（胜）、一个 -5-1=-6（负）
        bills = [self._bill(ordId="W1", pnl="10.0", fee="-1.0"),
                 self._bill(ordId="W2", pnl="10.0", fee="-1.0"),
                 self._bill(ordId="L1", pnl="-5.0", fee="-1.0")]
        _, out = self._run(AGGREGATE, bills)
        self.assertIn("2 胜 / 1 负", out)
        self.assertIn("66.7%", out)

    def test_a_flat_order_also_triggers_the_division_crash(self):
        # `elif p < 0` 只数亏损 ⇒ 净额恰好 0 的订单**两边都不计**
        # ⇒ `wins+losses == 0` ⇒ 胜率除法崩。想看到 "0 胜 / 0 负" 是**看不到的**，
        # 因为 f-string 先求值除法、再打印。
        with self.assertRaises(ZeroDivisionError):
            self._run(AGGREGATE, [self._bill(pnl="1.0", fee="-1.0")])

    def test_inst_name_has_the_settlement_suffix_stripped(self):
        _, out = self._run(AGGREGATE, [self._bill(instId="ETH-USDT-SWAP")])
        self.assertIn("ETH", out)
        self.assertNotIn("ETH-USDT-SWAP", out)

    def test_win_loss_line_is_printed_when_there_is_at_least_one_side(self):
        _, out = self._run(AGGREGATE, [self._bill(pnl="10.0")])
        self.assertIn("1 胜 / 0 负", out)
        self.assertIn("100.0%", out)

    def test_bill_id_is_used_when_there_is_no_order_id(self):
        bill = self._bill()
        bill.pop("ordId")
        bill["billId"] = "BILL-9"
        _, out = self._run(AGGREGATE, [bill])
        self.assertIn("BILL-9", out)

    def test_empty_result_crashes_on_the_win_rate_division(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：结尾 `wins/(wins+losses)*100`
        #    在"一笔已结订单都没有"时 `ZeroDivisionError`
        #    —— 刚重启或当周期无成交时跑这个工具就会炸，而不是打印 `0/0`
        with self.assertRaises(ZeroDivisionError):
            self._run(AGGREGATE, [self._bill(subType="3")])

    def test_header_mentions_the_reboot_moment(self):
        # 不能传空账单 —— 空账单会在表头之后崩在胜率除法上
        _, out = self._run(AGGREGATE, [self._bill()])
        self.assertIn("SINCE 01:11:20 REBOOT", out)
        self.assertIn("AGGREGATED REAL ORDERS", out)


class AuditBillsTests(_Harness, unittest.TestCase):
    def test_funding_rows_are_reported_from_bal_change(self):
        bills = [self._bill(type="8", subType="173", balChg="-1.25", pnl="0")]
        _, out = self._run(AUDIT, bills)
        self.assertIn("资金费", out)
        self.assertIn("-1.2500", out)

    def test_funding_row_and_summary_disagree_when_bal_change_is_zero(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：汇总用的是回落值
        #    `total_funding += (bal_chg if bal_chg != 0 else pnl)`
        #    但**行内显示**直接打 `bal_chg` ⇒ `balChg==0 而 pnl!=0` 时
        #    行里写"资金费扣除=+0.0000"、汇总却把它算成 -2.50 —— 同一份报表里两个口径。
        bills = [self._bill(type="8", subType="173", balChg="0", pnl="-2.5")]
        _, out = self._run(AUDIT, bills)
        self.assertIn("资金费扣除=+0.0000", out, "行内显示的是 balChg 原值")
        self.assertIn("资金费=-2.50 U", out, "但汇总用的是 pnl 回落值")

    def test_funding_row_uses_bal_change_when_it_is_non_zero(self):
        bills = [self._bill(type="8", subType="173", balChg="-1.25", pnl="0")]
        _, out = self._run(AUDIT, bills)
        self.assertIn("资金费扣除=-1.2500", out)

    def test_open_fee_rows_are_reported(self):
        bills = [self._bill(subType="3", fee="-0.42")]
        _, out = self._run(AUDIT, bills)
        self.assertIn("开仓扣费", out)
        self.assertIn("-0.4200", out)

    def test_close_rows_are_reported_with_gross_fee_and_net(self):
        bills = [self._bill(subType="5", pnl="10.0", fee="-0.5", balChg="9.5")]
        _, out = self._run(AUDIT, bills)
        self.assertIn("平仓结算", out)
        self.assertIn("毛盈亏=+10.0000", out)
        self.assertIn("净变动=+9.5000", out)

    def test_bills_before_the_cutoff_are_ignored(self):
        bills = [self._bill(when="2026-08-29 01:11:19", fee="-99.0"),
                 self._bill(when=CUTOFF, fee="-1.0")]
        _, out = self._run(AUDIT, bills)
        self.assertNotIn("-99.0", out)
        self.assertIn("-1.0000", out)

    def test_totals_are_summed_over_the_filtered_window(self):
        bills = [self._bill(pnl="10.0", fee="-0.5", subType="5"),
                 self._bill(pnl="-4.0", fee="-0.5", subType="6")]
        _, out = self._run(AUDIT, bills)
        self.assertIn("平仓毛盈亏总和=+6.00 U", out)
        self.assertIn("累计手续费=-1.00 U", out)

    def test_net_account_change_is_gross_plus_fee(self):
        bills = [self._bill(pnl="10.0", fee="-0.5", subType="5")]
        _, out = self._run(AUDIT, bills)
        self.assertIn("真实账户净变动 = +9.50 U", out)

    def test_unrecognised_sub_types_are_silently_skipped(self):
        bills = [self._bill(subType="99", fee="-7.0")]
        _, out = self._run(AUDIT, bills)
        self.assertNotIn("平仓结算", out)
        self.assertNotIn("开仓扣费", out)

    def test_empty_bill_list_prints_zeroed_totals_instead_of_crashing(self):
        # 与 aggregate 那个不同：本脚本没有除法 ⇒ 空账单是安全的
        _, out = self._run(AUDIT, [])
        self.assertIn("平仓毛盈亏总和=+0.00 U", out)


class SharedContractTests(unittest.TestCase):
    def test_both_scripts_are_registered_in_the_scripts_readme(self):
        # 归档/删除它们之前必须先过这道登记门
        doc = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
        self.assertIn("debug_aggregate_orders.py", doc)
        self.assertIn("debug_audit_bills.py", doc)

    def test_neither_script_defines_a_function(self):
        # 这就是本刀必须用 `runpy` 整套执行的原因（没有可单测的函数边界）
        import ast
        for script in (AGGREGATE, AUDIT):
            with self.subTest(script=script.name):
                tree = ast.parse(script.read_text(encoding="utf-8"))
                defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
                self.assertEqual(defs, [])

    def test_both_hardcode_the_same_reboot_moment(self):
        # ⚠️ 记录：两个"调试工具"都把过滤时刻**写死**成一次具体重启
        #    ⇒ 对任何其它时间窗都不可用。按 AGENTS.md「一次性任务归档到 .archive/」
        #    它们本属归档对象；是否归档/参数化留给用户拍板，本刀只补覆盖。
        for script in (AGGREGATE, AUDIT):
            with self.subTest(script=script.name):
                self.assertIn(CUTOFF, script.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

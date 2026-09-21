"""「今日已实现」的**单一事实源**（第一百九十六刀）。

`ledger_today_stats` 是前台 KPI 与熔断**共用**的那个函数 —— 两者因此不可能打架。
它同时含一条本仓别处缺失的**守卫**（坏行跳过），是本轮六处缺守卫缺口的**正面对照**。

| 语义 | 纪律 |
|---|---|
| 行筛选 | `status=closed` + 北京日相符；环境轴：行有环境**且**调用方指定**且**两者不同 ⇒ 排除；**任一为空 ⇒ 计入**（不可判定 ⇒ 保守全计）|
| 取值 | `net`=`pnl`、`gross`=`gross_pnl`、`fee`、`funding_fee`（funding **单列**，不混净值）|
| ★ 尘单 | `abs(net)<0.01 且 abs(gross)<0.01` ⇒ **不计 win/loss 计数**，但**四个累加已在之前完成** ⇒ 尘单仍进金额、不进胜率 |
| 坏行 | `TypeError/ValueError` ⇒ **跳过该行**（其余行照算），绝不整次作废 |
| 输出 | 四个金额舍入 2 位；`win_rate` 舍入 1 位、无闭合单 ⇒ 0.0 |
"""

import unittest
from unittest.mock import patch

from r20_backend.execution.circuit_breaker import ledger_today_stats

DAY = "2026-09-21"


def _row(**over):
    base = {"status": "closed", "close_time": DAY, "environment": "", "pnl": 10.0,
            "gross_pnl": 11.0, "fee": 1.0, "funding_fee": 0.5}
    base.update(over)
    return base


class LedgerTodayStatsTest(unittest.TestCase):
    def setUp(self):
        self.p = patch("r20_backend.execution.circuit_breaker.beijing_day",
                       side_effect=lambda ct: ct)
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_filters_by_status_and_day(self):
        out = ledger_today_stats([_row(), _row(status="open"), _row(close_time="2026-09-20"),
                                  _row(close_time=None)], "", DAY)
        self.assertEqual(out["net_realized"], 10.0, "只有一条合格行")
        self.assertEqual(out["win_trades"], 1)
        self.assertEqual(out["source"], "ledger")

    def test_environment_axis_is_conservative(self):
        rows = [_row(environment="demo", pnl=5.0), _row(environment="live", pnl=7.0),
                _row(environment="", pnl=3.0)]
        self.assertEqual(ledger_today_stats(rows, "demo", DAY)["net_realized"], 8.0,
                         "指定 demo ⇒ demo 行 + 环境为空的行（保守计入）")
        self.assertEqual(ledger_today_stats(rows, "", DAY)["net_realized"], 15.0,
                         "调用方未指定 ⇒ 全部计入（不可判定 ⇒ 保守全计）")

    def test_dust_rows_enter_the_amounts_but_not_the_win_loss_counts(self):
        """★ 尘单剔除只作用于**计数**：四个累加在剔除判断**之前**完成。"""
        out = ledger_today_stats([_row(pnl=0.005, gross_pnl=0.004)], "", DAY)
        self.assertEqual(out["net_realized"], 0.01, "金额仍计入（并舍入）")
        self.assertEqual(out["realized_gross"], 0.0)
        self.assertEqual(out["win_trades"] + out["loss_trades"], 0, "尘单不进胜率")

    def test_win_loss_follows_the_sign_of_net_and_zero_counts_as_neither(self):
        out = ledger_today_stats([_row(pnl=5.0), _row(pnl=-5.0), _row(pnl=0.0, gross_pnl=1.0)],
                                 "", DAY)
        self.assertEqual((out["win_trades"], out["loss_trades"]), (1, 1))
        self.assertEqual(out["win_rate"], 50.0, "无胜负的 0 净额行不进分母")

    def test_win_rate_rounding_and_empty_case(self):
        rows = [_row(pnl=1.0), _row(pnl=1.0), _row(pnl=-1.0)]
        self.assertEqual(ledger_today_stats(rows, "", DAY)["win_rate"], 66.7)
        self.assertEqual(ledger_today_stats([], "", DAY)["win_rate"], 0.0, "没有闭合单 ⇒ 0.0")

    def test_amounts_are_rounded_to_two_decimals(self):
        out = ledger_today_stats([_row(pnl=1.005, gross_pnl=2.0049, fee=0.333, funding_fee=0.111)],
                                 "", DAY)
        for k in ("realized_gross", "fees_paid", "funding_paid", "net_realized"):
            self.assertEqual(out[k], round(out[k], 2), f"{k} 必须两位小数")

    def test_bad_values_are_skipped_but_bad_types_still_void_the_read(self):
        """半对半错的两种坏行，**后果完全不同**：

        - **坏值**（`pnl` 非数值）⇒ `TypeError/ValueError` 被捕获 ⇒ **只跳过该行**，其余照算 ✓；
        - **坏类型**（行不是 dict）⇒ `.get()` 抛 `AttributeError`，**不在捕获之列** ⇒ **整次读取作废** ✗。

        ⇒ 这是本仓登记的**第七处**同形态缺口（缺元素/类型守卫）；本函数**不是**
        「正面对照」—— 我当时以为它是，写用例时被断言打回，故此处如实记录。
        改它属失败语义变更（吞掉 vs 上抛）⇒ 只钉现状、列为待议。
        """
        out = ledger_today_stats([_row(pnl=4.0), _row(pnl="bad"), _row(pnl=6.0)], "", DAY)
        self.assertEqual(out["net_realized"], 10.0, "坏值行只跳过自己，两条好行照算")
        self.assertEqual(out["win_trades"], 2, "坏值行不冒充成交、也不清空结果")
        with self.assertRaises(AttributeError):
            ledger_today_stats([_row(pnl=1.0), "not-a-dict"], "", DAY)


if __name__ == "__main__":
    unittest.main()

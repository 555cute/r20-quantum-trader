"""跨所保护单**只读预演**接口（第二百四十九刀，收口 routers/exchanges.py）。

先打印实现（554-608）再动笔。

| 语义 | 口径 |
|---|---|
| ★ **绝不下单、绝不撤单** | 一定以 `dry_run=True` 调用 `audit_cross_venue_protection` —— 这是本接口存在的全部理由（「开闸前先看这一轮会做什么」）|
| ★ **逐所隔离且如实登记** | gate/binance 各自 `try`：读不到 ⇒ `snapshot_errors[venue]` 记原因、`snapshot[venue] = []`。注释原话：「读不到就如实登记，**绝不假装"该所干净"**」（所以两处**必须同时**出现：空列表**加上**错误条目）|
| ★ **台账行做取证** | `ledger_rows=read_ledger_rows(...)`；**读不到 ⇒ None ⇒ 不产生证据**（腿留在「归属不可判定」，**绝不自动撤**）|
| ★ 适配器注册表 | `_Registry.get_adapter(v, environment=None)` ⇒ 未给环境时用 `adapter_environment(v, env.mode)` |
| ★ **`watchdog_enabled` 三态** | 读得到 ⇒ 布尔；**读不到 ⇒ `None`** —— 注释：为了区分「**巡检没开**」与「**开了但没发现问题**」。三态而不是两态，正是「不可判定 ≠ 否」|
"""

import builtins
import sys
import types
import unittest
from unittest import mock

from r20_backend.routers import exchanges as R


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.audit_kwargs = {}
        self.report = {"would": [], "critical": []}
        for name in ("require_admin_header",):
            p = mock.patch.object(R, name, mock.Mock(), create=True)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("scripts.okx_runtime.current_environment",
                       return_value=types.SimpleNamespace(mode="demo", configured=True,
                                                          fingerprint="fp"))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("r20_backend.close_intent.adapter_environment",
                       side_effect=lambda v, mode: f"{v}-{mode}")
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("scripts.trader.venue_protection.audit_cross_venue_protection",
                       side_effect=self._audit)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("scripts.trader.venue_protection.read_ledger_rows",
                       return_value=[{"row": 1}])
        self.ledger_reader = p.start()
        self.addCleanup(p.stop)

    def _audit(self, snapshot, **kw):
        self.audit_kwargs = dict(kw)
        self.audit_snapshot = snapshot
        return dict(self.report)

    def _adapters(self, mapping):
        self.adapter_calls = []

        def _get(v, environment=None):
            self.adapter_calls.append((v, environment))
            return mapping[v]
        p = mock.patch("r20_backend.exchanges.get_adapter", side_effect=_get)
        p.start()
        self.addCleanup(p.stop)

    def test_it_is_a_dry_run_and_forwards_evidence(self):
        """★ 只读预演：`dry_run=True` 是硬前提；台账行与所注册表都要交下去。"""
        self._adapters({"gate": types.SimpleNamespace(positions=lambda: []),
                        "binance": types.SimpleNamespace(positions=lambda: [])})
        out = R.venue_protection_scan(x_r20_admin_token="t")
        self.assertIs(self.audit_kwargs.get("dry_run"), True, "**绝不下单、绝不撤单**")
        self.assertEqual(self.audit_kwargs.get("environment"), "demo")
        self.assertEqual(self.audit_kwargs.get("ledger_rows"), [{"row": 1}])
        self.assertIsNotNone(self.audit_kwargs.get("venue_registry"))
        self.assertEqual(out["environment"], "demo")
        self.assertEqual(out["snapshot_errors"], {})

    def test_ledger_rows_none_is_forwarded_as_none(self):
        """★ 设计路径：读不到 ⇒ 调用方返回 `None` ⇒ 透传 ⇒ **不产生证据**。

        （绝不能用空列表冒充「没有已平记录」——那会让腿被当成"无归属"而放行。）
        """
        self.ledger_reader.return_value = None
        self._adapters({"gate": types.SimpleNamespace(positions=lambda: []),
                        "binance": types.SimpleNamespace(positions=lambda: [])})
        R.venue_protection_scan(x_r20_admin_token="t")
        self.assertIsNone(self.audit_kwargs.get("ledger_rows"))

    def test_a_raising_ledger_reader_is_currently_unguarded(self):
        """⚠️ **现状（待议 27）**：注释说「读不到 ⇒ None」，但**调用点没有 try** ——
        如果 `read_ledger_rows` 真的**抛错**（而不是返回 None），整个预演接口会 500。
        本用例钉**当前行为**，不声称这样是对的。
        """
        self.ledger_reader.side_effect = RuntimeError("台账读不到")
        self._adapters({"gate": types.SimpleNamespace(positions=lambda: []),
                        "binance": types.SimpleNamespace(positions=lambda: [])})
        with self.assertRaises(RuntimeError):
            R.venue_protection_scan(x_r20_admin_token="t")

    def test_registry_falls_back_to_the_adapter_environment(self):
        """★ 第 590 行：未显式给 environment 时，注册表自己按档位解析。"""
        self._adapters({"gate": types.SimpleNamespace(positions=lambda: []),
                        "binance": types.SimpleNamespace(positions=lambda: [])})
        R.venue_protection_scan(x_r20_admin_token="t")
        registry = self.audit_kwargs["venue_registry"]
        # ⚠️ 不能再 patch 一层：函数内的 `get_adapter` 是**函数作用域绑定**，外层 patch 盖不住。
        # 改为记录**实际调用**（谁被以什么环境叫过）。
        registry.get_adapter("gate")
        self.assertEqual(self.adapter_calls[-1], ("gate", "gate-demo"),
                         "未给环境 ⇒ 按档位解析")
        registry.get_adapter("binance", environment="live")
        self.assertEqual(self.adapter_calls[-1], ("binance", "live"), "显式环境优先")

    def test_a_failing_venue_is_recorded_and_marked_empty_together(self):
        """★ 逐所隔离 + 如实登记：**空列表必须与错误条目同时出现**。"""
        def _boom():
            raise RuntimeError("gate 网络断了")
        self._adapters({"gate": types.SimpleNamespace(positions=_boom),
                        "binance": types.SimpleNamespace(
                            positions=lambda: [{"size_signed": 2}])})
        out = R.venue_protection_scan(x_r20_admin_token="t")
        self.assertIn("gate", out["snapshot_errors"])
        self.assertIn("gate 网络断了", out["snapshot_errors"]["gate"])
        self.assertEqual(out["snapshot_errors"]["gate"] != "", True)
        self.assertEqual(self.audit_snapshot["gate"], [], "空列表 + 错误条目（不是「干净」）")
        self.assertEqual(self.audit_snapshot["binance"], [{"size_signed": 2}],
                         "另一所不受牵连")

    def test_zero_sized_positions_are_filtered_out(self):
        self._adapters({"gate": types.SimpleNamespace(
            positions=lambda: [{"size_signed": 0}, {"size_signed": -1.5}]),
            "binance": types.SimpleNamespace(positions=lambda: [])})
        R.venue_protection_scan(x_r20_admin_token="t")
        self.assertEqual(self.audit_snapshot["gate"], [{"size_signed": -1.5}])

    def test_watchdog_flag_is_three_state(self):
        """★ 第 604 行：读不到开关 ⇒ `None`（**不可判定 ≠ 否**）。"""
        self._adapters({"gate": types.SimpleNamespace(positions=lambda: []),
                        "binance": types.SimpleNamespace(positions=lambda: [])})
        out = R.venue_protection_scan(x_r20_admin_token="t")
        self.assertIn(out["watchdog_enabled"], (True, False), "读得到就是布尔")

        real_import = builtins.__import__

        def _blocked(name, *a, **kw):
            # ⚠️ `fromlist` 在 `__import__(name, globals, locals, fromlist, level)` 里是**位置参数**：
            # 我第一版只看 kw ⇒ 拦不住（实测仍拿到 True）。
            fromlist = a[2] if len(a) > 2 else (kw.get("fromlist") or ())
            if name == "scripts" and "ai_factor_trader" in (fromlist or ()):
                raise ImportError("blocked for test")
            return real_import(name, *a, **kw)

        with mock.patch.object(builtins, "__import__", side_effect=_blocked):
            out2 = R.venue_protection_scan(x_r20_admin_token="t")
        self.assertIsNone(out2["watchdog_enabled"],
                          "读不到 ⇒ None（区分「巡检没开」与「开了没发现问题」）")


if __name__ == "__main__":
    unittest.main()

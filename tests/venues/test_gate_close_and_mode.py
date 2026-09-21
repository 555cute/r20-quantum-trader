"""Gate 市价全平、触发单方向与只读模式探测（第二百七十六刀）。

| 语义 | 纪律 |
|---|---|
| 非整数张数 | ★ **拒绝截断抹零**（`int()` 抹零会**留下残仓却谎报全平**）⇒ `closed:False` + 明确原因 |
| 双向同存 | 未指定方向 ⇒ **拒绝盲平**（不猜平哪条腿）|
| 全平单 | `size = -int(signed)`、`price=0`、`tif=ioc`、`reduce_only=True`、`text=t-r20c*` |
| 触发方向 | 多 TP 上式、多 SL 下式、空 TP 下式、空 SL 上式（`RULE_ABOVE/BELOW`）|
| 模式探测 | **只读**：读不到 ⇒ `unknown`（调用方据此禁新开仓）；**绝不自动切换账户模式**（审计 §2）|
"""

import ast
import inspect
import unittest
from unittest.mock import patch

import r20_backend.exchanges.gate as gate_mod
from r20_backend.exchanges.gate import GateAdapter, RULE_ABOVE, RULE_BELOW


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = GateAdapter.__new__(GateAdapter)
        self.ad.base_url = "https://api.gateio.ws"
        self.ad.native_symbol = lambda s: f"{s}_USDT" if s else ""
        self.bodies = []

    def _positions(self, rows):
        self.ad.positions = lambda: rows

    def _send(self, payload=None):
        def _req(method, path, params=None, body=None, **kw):
            self.bodies.append(dict(body or {}))
            return {"id": 1} if payload is None else payload
        self.ad.signed_request = _req
        return self.ad

    @staticmethod
    def _pos(signed, base="BTC"):
        return {"inst_id": f"{base}_USDT", "base": base, "size_signed": signed}


class FastClosePositionTest(_Base):
    def test_no_position_and_zero_position_do_not_order(self):
        self._positions([])
        res = self.ad.fast_close_position("BTC")
        self.assertFalse(res["closed"])
        self.assertEqual(res["reason"], "无持仓")
        self._positions([self._pos(0)])
        res2 = self.ad.fast_close_position("BTC")
        self.assertEqual(res2["reason"], "持仓为0")
        self.assertEqual(self.bodies, [], "没仓/零仓 ⇒ 一张单都不发")

    def test_multiple_rows_without_pos_side_refuses_to_blind_close(self):
        self._positions([self._pos(3), self._pos(-2)])
        res = self.ad.fast_close_position("BTC")
        self.assertFalse(res["closed"])
        self.assertIn("拒绝盲平", res["reason"])
        self.assertEqual(self.bodies, [])

    def test_pos_side_selects_the_leg_by_signed_size(self):
        self._positions([self._pos(3), self._pos(-2)])
        self._send()
        self.ad.fast_close_position("BTC", pos_side="short")
        self.assertEqual(self.bodies[-1]["size"], 2, "平空 = 买入 +abs(张数)")

    def test_non_integer_size_is_refused_not_truncated(self):
        """★★ 非整数张数 ⇒ **拒绝**（`int()` 抹零会留残仓却谎报全平）。"""
        self._positions([self._pos(2.5)])
        res = self.ad.fast_close_position("BTC")
        self.assertFalse(res["closed"])
        self.assertIn("张数非整数", res["reason"])
        self.assertEqual(self.bodies, [], "宁可不平，也不留半张残仓")

    def test_happy_path_builds_the_closing_order(self):
        self._positions([self._pos(3)])
        self._send()
        out = self.ad.fast_close_position("BTC")
        body = self.bodies[-1]
        self.assertEqual(body["size"], -3, "反向市价全平")
        self.assertEqual(body["price"], "0")
        self.assertEqual(body["tif"], "ioc")
        self.assertTrue(body["reduce_only"], "只减不增")
        self.assertTrue(body["text"].startswith("t-r20c"))
        self.assertEqual(out, {"id": 1})

    def test_non_dict_receipt_is_wrapped_not_dropped(self):
        self._positions([self._pos(1)])
        self._send(payload=["unexpected"])
        self.assertEqual(self.ad.fast_close_position("BTC"), {"raw": ["unexpected"]})


class TriggerRuleTest(unittest.TestCase):
    def test_four_quadrants(self):
        self.assertEqual(GateAdapter.trigger_rule("long", "tp"), RULE_ABOVE, "多 TP：价≥tp")
        self.assertEqual(GateAdapter.trigger_rule("long", "sl"), RULE_BELOW, "多 SL：价≤sl")
        self.assertEqual(GateAdapter.trigger_rule("short", "tp"), RULE_BELOW, "空 TP：价≤tp")
        self.assertEqual(GateAdapter.trigger_rule("short", "sl"), RULE_ABOVE, "空 SL：价≥sl")

    def test_prefix_matching_and_default_branch(self):
        self.assertEqual(GateAdapter.trigger_rule("LONG", "tp"), RULE_ABOVE, "只看首字母，大小写无关")
        self.assertEqual(GateAdapter.trigger_rule("long", "anything-else"), RULE_BELOW,
                         "非 tp 一律按 sl 处理（保守：止损方向）")


class DetectPositionModeTest(_Base):
    def test_read_failure_is_unknown_never_raises(self):
        """★ 探测**永不抛**：读不到 ⇒ `unknown`（调用方据此禁新开仓）。"""
        self.ad.account_snapshot = lambda: (_ for _ in ()).throw(RuntimeError("读不动"))
        self.assertEqual(self.ad.detect_position_mode(), "unknown")

    def test_payload_is_delegated_to_the_pure_interpreter(self):
        self.ad.account_snapshot = lambda: {"position_mode": "dual_plus", "in_dual_mode": True}
        self.assertEqual(self.ad.detect_position_mode(), "dual_plus")

    def test_the_module_never_switches_the_account_mode(self):
        """★「声明 ≠ 执行」：文档说**只读**，就用 **AST 调用点**证明它没有切换调用。

        ⚠️ 不能用 `assertNotIn("set_position_mode", src)` —— 该符号**出现在注释里**
        （注释正是写着「本系统绝不调用 set_position_mode」的那句），字符串扫描必然误报。
        必须找真正的**调用表达式**。
        """
        tree = ast.parse(inspect.getsource(gate_mod))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and ((isinstance(n.func, ast.Attribute) and n.func.attr == "set_position_mode")
                      or (isinstance(n.func, ast.Name) and n.func.id == "set_position_mode"))]
        self.assertEqual(calls, [], "本系统绝不自动切换用户账户模式（审计 §2）")


if __name__ == "__main__":
    unittest.main()

"""触发价类型**不得**参与保护/风控判定（第一百七十七刀）。

## 为什么要有这条边界（以及它防的是两个方向的错）

保护腿的"触发价类型"（`mark`/`last`/`index`/交易所原始码）回答的是**"按什么价触发"**；
而"保护够不够"回答的是**"腿在不在、是不是 live、量够不够、有没有过期"**。
这是**两个不同的问题**，本仓一律分开回答。代码里把它们混起来会错在两个方向：

| 混法 | 后果 |
|---|---|
| 类型未上报 ⇒ 把保护状态降级为"不可判定" | **过度保守**：腿明明挂在交易所（只是没说按哪种价触发），面板/提示词却说"保护不可判定" —— 把"说不清触发依据"说成"没有保护" |
| 类型是 `mark` ⇒ 当作"保护更强" | **虚假安心**：类型只影响触发时点，不改变覆盖量；它不该给覆盖背书 |

本门把"分开回答"钉成**可执行断言**：

1. **行为**：两条除类型外完全相同的腿 ⇒ `scan_protective_orders` 的判定**逐字相同**；
   而类型字段仍如实一个 `mark`、一个 `unknown`（读不到不猜）；
2. **结构**：在**判定函数**里，类型标识符**不得出现在任何条件/比较里**
   （只允许出现在"写入披露字段"的位置）—— 这条是防"将来有人顺手拿类型当判据"。

牙齿自检：合成的"按类型降级"源码必须被抓出来。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 做**保护/风控判定**的函数（这些地方读类型＝把两个问题混起来）
VERDICT_FUNCS = {
    "scripts/trader/venue_protection.py": ("scan_protective_orders", "attribute_protective_orders",
                                           "ensure_venue_protection"),
    "r20_backend/dashboard_payload/multi_venue.py": ("_protection_verdict",),
    "r20_backend/dashboard_payload/algo_protection.py": ("collect_algo_protection",),
}
TYPE_TOKENS = ("trigger_px_type", "TriggerPxType", "protectionSlTriggerPxType",
               "protectionTpTriggerPxType", "protection_trigger_type_fields")


def type_used_in_a_condition(src: str, func_name: str) -> "list[str]":
    """返回"在条件/比较里读了触发价类型"的位置（空 = 干净）。"""
    problems: "list[str]" = []
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if not (isinstance(fn, ast.FunctionDef) and fn.name == func_name):
            continue
        for node in ast.walk(fn):
            # ⚠️ 只看**条件表达式**（`ast.unparse(整个 If)` 会把 if 的**函数体**也算进来 ⇒
            # 假阳性：`if not readable:` 的体里正当地调用了披露函数）。
            if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
                tests = [node.test]
            else:
                continue
            for test in tests:
                text = ast.unparse(test)
                if any(tok in text for tok in TYPE_TOKENS):
                    problems.append(f"{func_name}:{getattr(node, 'lineno', '?')}: "
                                    f"条件里出现触发价类型: {text[:90]}")
    return problems


class TriggerTypeStaysOutOfVerdictsTest(unittest.TestCase):
    def test_no_verdict_function_reads_the_type_in_a_condition(self):
        problems: "list[str]" = []
        for rel, funcs in VERDICT_FUNCS.items():
            src = (ROOT / rel).read_text(encoding="utf-8")
            for fn in funcs:
                problems += type_used_in_a_condition(src, fn)
        self.assertEqual(problems, [], "触发价类型被当成判定依据（两个问题混在一起了）：\n"
                                       + "\n".join(problems))

    def test_scan_is_not_vacuous(self):
        """非空自检：这些函数必须真的存在（否则本门在空转）。"""
        found = 0
        for rel, funcs in VERDICT_FUNCS.items():
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
            for fn in funcs:
                self.assertIn(fn, names, f"{rel} 里找不到判定函数 {fn}（门已过期）")
                found += 1
        self.assertGreaterEqual(found, 4)

    def test_leg_type_does_not_change_the_coverage_verdict(self):
        """行为：除类型外完全相同的两条腿 ⇒ 判定逐字相同。"""
        from scripts.trader.venue_protection import scan_protective_orders
        base = {"symbol": "XRPUSDT", "side": "buy", "type": "STOP_MARKET",
                "raw": {"orderType": "STOP_MARKET", "triggerPrice": "1.30",
                        "quantity": "100", "reduceOnly": "true"}}
        with_type = dict(base, raw=dict(base["raw"], slTriggerPxType="mark"))
        a = scan_protective_orders([base], symbol="XRP", pos_side="long",
                                   position_size=100.0, now_s=1_700_000_000.0)
        b = scan_protective_orders([with_type], symbol="XRP", pos_side="long",
                                   position_size=100.0, now_s=1_700_000_000.0)
        for key in ("coverage_ok", "covered_size", "has_live_sl", "missing"):
            self.assertEqual(a.get(key), b.get(key), f"类型改变了判定字段 {key}")

    def test_payload_status_ignores_type_but_discloses_unknown(self):
        """行为：状态判定不受类型影响；类型字段如实 `unknown`（不猜）。"""
        from r20_backend.dashboard_payload.algo_protection import collect_algo_protection
        def _run(leg):
            pos = {"instId": "ETH-USDT-SWAP", "posSide": "long", "pos_sz": 100.0}
            collect_algo_protection([pos], [], lambda fn, *a, **k: (True, [leg], ""),
                                    lambda *a, **k: None, {})
            return pos
        plain = {"algoId": "a1", "state": "live", "posSide": "long", "reduceOnly": "true",
                 "sz": "100", "slTriggerPx": "90", "tpTriggerPx": "110"}
        typed = dict(plain, slTriggerPxType="mark", tpTriggerPxType="mark")
        r_plain, r_typed = _run(plain), _run(typed)
        self.assertEqual(r_plain["protectionStatus"], r_typed["protectionStatus"],
                         "类型改变了保护状态（两个问题混在一起）")
        self.assertEqual(r_plain["protectionCoveragePct"], r_typed["protectionCoveragePct"])
        self.assertEqual(r_typed["protectionSlTriggerPxType"], "mark")
        self.assertEqual(r_plain["protectionSlTriggerPxType"], "unknown",
                         "未上报必须是 unknown（不得猜成 mark，也不得降级保护状态）")

    def test_gate_has_teeth(self):
        """牙齿：合成的"按类型降级"必须被抓。"""
        bad = (
            "def scan_protective_orders(rows):\n"
            "    coverage_ok = True\n"
            "    for r in rows:\n"
            "        if r.get('slTriggerPxType') == 'last':\n"
            "            coverage_ok = None\n"
            "    return coverage_ok\n"
        )
        self.assertTrue(type_used_in_a_condition(bad, "scan_protective_orders"),
                        "按类型改判定的代码没被抓出来 ⇒ 本门没有牙齿")
        also_bad = (
            "def _protection_verdict(a):\n"
            "    return 'unknown' if a.get('protectionSlTriggerPxType') == 'unknown' else 'ok'\n"
        )
        self.assertTrue(type_used_in_a_condition(also_bad, "_protection_verdict"))
        good = (
            "def scan_protective_orders(rows):\n"
            "    return {'coverage_ok': True, 'protectionSlTriggerPxType': 'unknown'}\n"
        )
        self.assertEqual(type_used_in_a_condition(good, "scan_protective_orders"), [],
                         "把类型写进披露字段不该被误判为'拿它当判据'")


if __name__ == "__main__":
    unittest.main(verbosity=2)

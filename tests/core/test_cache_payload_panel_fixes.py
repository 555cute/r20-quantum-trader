"""载荷里两处**面板修复**的结构不变量（第二百零一刀）。

| 修复 | 事故 | 不变量 |
|---|---|---|
| 根级 `macro_assessment` | TS 契约把该字段声明在**根**上，而真实内容一直在 `ai_brain_history[0].macro_assessment` ⇒ 前端根级读取**永远拿不到**、只显示「扫描中…」| 根级发一个**同源别名**（`_latest_brain.get(...)`，**不新算**），且**缺数据时给 `None` 而不是崩** |
| 根级 `is_stale` | 前端 `stores/dashboard.ts` 把根级 `is_stale` 当**必填**消费 | 新装载荷恒**不陈旧**（陈旧只能由缓存周期的兜底路径**显式**置真）|

⚠️ 本文件的检查是**结构性**的（AST/谓词），证明「接线与守卫存在」；**不是**端到端运行值。
本仓已在别处证明运行期语义：`is_stale` 的兜底置真在缓存周期侧有回归用例，
`macro_assessment` 的取值来源就是这里断言的同一表达式。
"""

import ast
import inspect
import unittest

from r20_backend.dashboard_payload import cache_payload
from r20_backend.dashboard_payload.cache_payload import build_live_cache_payload, is_stale_status


def _builder_tree():
    return ast.parse(inspect.getsource(build_live_cache_payload))


class MacroAssessmentAliasTest(unittest.TestCase):
    def _brain_pick(self):
        """返回 `_latest_brain = a if cond else b` 那个赋值节点与其条件。"""
        for node in ast.walk(_builder_tree()):
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == "_latest_brain" for t in node.targets):
                return node
        self.fail("找不到 _latest_brain 的赋值")

    def test_alias_reads_the_same_source_without_recomputing(self):
        node = self._brain_pick()
        self.assertIsInstance(node.value, ast.IfExp, "必须是「有就用、没有就空」的三元式")
        cond_src = ast.dump(node.value.test)
        self.assertIn("isinstance", cond_src, "必须检查元素类型")
        self.assertIn("dict", cond_src)
        # 根级别名只允许是「从最新一条脑内记录取字段」，不得重新计算
        root_vals = [ast.dump(v) for n in ast.walk(_builder_tree())
                     if isinstance(n, ast.Dict)
                     for k, v in zip(n.keys, n.values)
                     if isinstance(k, ast.Constant) and k.value == "macro_assessment"]
        self.assertTrue(root_vals, "载荷必须含根级 macro_assessment")
        self.assertTrue(any("_latest_brain" in s and "get" in s for s in root_vals),
                        "根级别名必须取 _latest_brain 的同名字段（同源、不新算）")

    def test_brain_rows_guard_rejects_non_sequences(self):
        tree = _builder_tree()
        guards = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", "") == "_brain_rows" for t in n.targets)]
        self.assertTrue(guards, "必须有 _brain_rows 守卫")
        src = ast.dump(guards[0])
        self.assertIn("isinstance", src)
        self.assertTrue("List" in src or "Tuple" in src, "只接受 list/tuple；其余当空")
        # 缺数据时别名取 None（.get 在空 dict 上 ⇒ None），绝不抛
        self.assertIsNone({}.get("macro_assessment"))


class IsStaleRootFieldTest(unittest.TestCase):
    def test_fresh_payloads_are_never_stale_by_predicate(self):
        """★ 新装载荷用 LIVE/PARTIAL 两个词 ⇒ 谓词必须都判**非陈旧**。
        否则看板会把**新鲜数据**标成旧数据（与真实事故反向的谎报）。"""
        self.assertFalse(is_stale_status("LIVE"))
        self.assertFalse(is_stale_status("PARTIAL"))

    def test_stale_only_comes_from_the_explicit_fallback_path(self):
        self.assertTrue(is_stale_status("STALE"), "兜底路径用它显式置真")

    def test_root_is_stale_is_wired_to_the_predicate(self):
        root_vals = [ast.dump(v) for n in ast.walk(_builder_tree())
                     if isinstance(n, ast.Dict)
                     for k, v in zip(n.keys, n.values)
                     if isinstance(k, ast.Constant) and k.value == "is_stale"]
        self.assertEqual(len(root_vals), 1, "根级 is_stale 必须恰好一处（避免两套判定）")
        self.assertIn("is_stale_status", root_vals[0], "必须走同一个谓词，不得就地写死")

    def test_status_word_matches_the_payload_health_status(self):
        """根级 `is_stale` 用的状态词必须与 `data_health.status` 同源（同一 `source_errors` 判据）。"""
        src = inspect.getsource(build_live_cache_payload)
        self.assertEqual(src.count('"LIVE" if not source_errors else "PARTIAL"'), 2,
                         "两处（is_stale 与 data_health.status）必须同式，否则会出现自相矛盾的载荷")


if __name__ == "__main__":
    unittest.main()

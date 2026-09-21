"""「口径降级自曝」的**接线**与陈旧判定谓词（第二百刀）。

上一刀（199）我在手册里明确写了「**前台是否逐字展示未验证，故不声称**」。本刀把**接线**这一段
补上证据：单所降级标记从缓存周期一路**作为参数**进入载荷构造器，并**被写进**
`today_stats.source` ⇒ 面板拿得到、能区分「三所口径」与「单所兜底」。

⚠️ 本刀证明的是**接线存在**（参数名 ↔ 键名 ↔ 调用方实参），**不是**某次运行的实际取值；
运行期取值由「成功才改多所标记、失败保持降级标记」那段分支逻辑决定（已在 199 刀逐行核对）。
"""

import ast
import inspect
import unittest

from r20_backend.dashboard_cache import update_cache_cycle
from r20_backend.dashboard_payload import cache_payload
from r20_backend.dashboard_payload.cache_payload import (STALE_STATUSES, build_live_cache_payload,
                                                         is_stale_status)


class IsStaleStatusTest(unittest.TestCase):
    def test_stale_statuses_are_recognised_case_insensitively(self):
        for s in STALE_STATUSES:
            with self.subTest(status=s):
                self.assertTrue(is_stale_status(s))
                self.assertTrue(is_stale_status(f"  {str(s).lower()}  "), "大小写/空白无关")

    def test_fresh_or_missing_status_is_not_stale(self):
        for s in ("OK", "LIVE", "NOT_READY" if "NOT_READY" not in STALE_STATUSES else "X",
                  None, "", "   "):
            with self.subTest(status=s):
                self.assertFalse(is_stale_status(s), "读不出 ⇒ 不得当成陈旧（也不得当成就新鲜）")

    def test_the_predicate_is_not_truthy_coercion(self):
        """★ 只有**白名单内的词**算陈旧：一个恰好为真的对象不算。"""
        class Weird:
            def __bool__(self):
                return True
            def __str__(self):
                return "weird"
        self.assertFalse(is_stale_status(Weird()))


class SourceLabelWiringTest(unittest.TestCase):
    def test_builder_takes_the_label_as_a_parameter(self):
        params = list(inspect.signature(build_live_cache_payload).parameters)
        self.assertIn("_today_stats_source", params, "标记必须是**参数**（不是模块全局）")

    def test_builder_writes_the_label_into_today_stats_source_key(self):
        """AST 检查：载荷里 `today_stats` 这个字典的 `source` 键**绑定的是那个参数名**。"""
        tree = ast.parse(inspect.getsource(cache_payload))
        wired = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "source"
                        and isinstance(v, ast.Name) and v.id == "_today_stats_source"):
                    wired.append(node)
        self.assertEqual(len(wired), 1, "`source` 必须恰好一处绑定到该参数（多一处就是两套口径）")

    def test_the_caller_passes_the_label_through(self):
        """调用方也必须**按关键字**传它（漏传就会退化成默认值而不自知）。"""
        callers = [n for n in ast.walk(ast.parse(inspect.getsource(update_cache_cycle)))
                   if isinstance(n, ast.Call) and any(k.arg == "_today_stats_source"
                                                      for k in n.keywords)]
        self.assertTrue(callers, "缓存周期必须把标记显式传给载荷构造器")

    def test_degraded_label_default_is_the_single_venue_one(self):
        """★ 默认值即**降级标记**：新代码忘了设标记时，面板看到的是「单所兜底」而不是「三所口径」。"""
        src = inspect.getsource(update_cache_cycle)
        assign = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", "") == "_today_stats_source" for t in n.targets)]
        self.assertTrue(assign, "应有初始赋值")
        first = ast.literal_eval(assign[0].value)
        self.assertEqual(first, "okx_bills_degraded",
                         "初值必须是降级口径（失败时保持它 = 自曝），不能预置成多所口径")


if __name__ == "__main__":
    unittest.main()

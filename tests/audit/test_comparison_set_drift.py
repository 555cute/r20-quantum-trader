"""钉住"字面量层面的 net 容错不一致"（第二百二十二刀）。

## 背景与边界（先读，免得把闸当成它做不到的事）

本会话修过一处"同文件同一语义两种写法"（第一百八十六刀）：`posSide` 在该处用
`in {pos_side, "net"}` 容错、在另一处只认精确相等 ⇒ 净持仓账户（OKX one-way）永远找不到
活止损单 ⇒ "云端止损收紧"**静默失效**。

做完普查（`tests/comparison_set_census.py`）并**逐组人工判读**后决定**不做全仓闸**：
105 组"同主语多口径"里绝大多数是同处按分支派发（`venue == "okx"`/`== "binance"`/`== "gate"`）
与嵌套校验（先查合法性、再判具体值），25 组侧/状态类主语同样如此，**没有一处是真漂移**。
硬做闸就要背 25-105 条白名单，违背"白名单必须少且写明理由"的纪律。

本闸因此**只钉子集**：两边**都写字面量**、但一边含 `net` 一边不含 ⇒ 报。
**已知盲区（如实）**：历史那处比较对象是**变量**（`== pos_side`）⇒ 本闸扫不到；
它防的是"以后有人写成字面量、一边漏了 net"这一类。
"""

import unittest

from tests.comparison_set_census import census, files, net_tolerance_drift


class TeethTest(unittest.TestCase):
    def test_detects_literal_net_tolerance_drift(self):
        """一边 `in ("net","long")`、一边 `== "short"` ⇒ 正是"净容错只做了一半"的形状。"""
        src = (
            "def f(ps):\n"
            "    if ps in ('net', 'long'):\n"
            "        return 'open'\n"
            "    if ps == 'short':\n"
            "        return 'close'\n"
            "    return None\n")
        hits = net_tolerance_drift(src)
        self.assertEqual(len(hits), 1, f"应当捕获 net 容错不一致：{hits}")
        self.assertIn("ps", hits[0][1])

    def test_must_not_mis_kill_per_branch_dispatch(self):
        """同一主语按分支派发（`venue == "okx"` / `== "binance"`）不是漂移。"""
        src = (
            "def f(venue):\n"
            "    if venue == 'okx':\n"
            "        return 1\n"
            "    if venue == 'binance':\n"
            "        return 2\n"
            "    if venue == 'gate':\n"
            "        return 3\n"
            "    return 0\n")
        self.assertEqual(net_tolerance_drift(src), [],
                         "按场所分支派发被误杀 ⇒ 这个闸会成为噪声源")

    def test_must_not_mis_kill_a_variable_comparator(self):
        """比较对象是变量时不猜（诚实：这是本闸的盲区，不是它的能力）。"""
        src = (
            "def f(ps, want):\n"
            "    if ps in ('net', 'long'):\n"
            "        return 1\n"
            "    if ps == want:\n"
            "        return 2\n"
            "    return 0\n")
        self.assertEqual(net_tolerance_drift(src), [],
                         "字面量口径不该拿变量比较去凑一个假阳性（盲区写进文档）")


class RepoPinTest(unittest.TestCase):
    def test_scan_surface_is_real(self):
        self.assertGreaterEqual(len(files()), 200, "扫描面太小 ⇒ 闸本身可能是空的")

    def test_census_has_signal(self):
        rows = census(only_risky=True)
        self.assertGreaterEqual(len(rows), 10,
                                f"侧/状态类主语应当有不少分组（当前 {len(rows)}）——"
                                "若骤降说明扫描逻辑坏了")

    def test_repo_has_no_literal_net_tolerance_drift(self):
        rows = census(only_net_drift=True)
        self.assertEqual(rows, [],
                         "出现字面量层面的 net 容错不一致（一边含 net、一边不含）⇒ "
                         "请统一口径或登记理由：\n" + "\n".join(f"{f} 「{s}」 {l}" for f, s, l in rows))


if __name__ == "__main__":
    unittest.main()

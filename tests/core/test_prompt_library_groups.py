"""编辑器模块编组：把非标题模块挂到**前面最近的可见分节**（第二百一十三刀）。

`_trading_user_parent_groups(base_modules, layout_titles)` 决定编辑器里「哪些模块属于哪个分节」。
语义（本刀实测，共三点值得一提）：

1. 遇到 `layout_titles` 里的标题 ⇒ 它**自己开一节**并且**把自己放进这一节**（父项含在组内）；
2. 其后名字**不在**标题表里的模块 ⇒ 挂到**前面最近的那个父节**下；
3. ★ **第一个父节之前**的模块**不会出现在任何组里**（静默丢弃）；
4. ★ 同名标题**再次出现**时不会开新组，而是**并回原组**（相隔很远的同名分节被合并）。

3 与 4 都是**实测现状**，是否符合编辑意图我不在此判定 —— 只钉住行为，并把「前置模块被静默丢弃」
列为待议（与「读不到 ≠ 没有」无关，但属「静默」一族）。
"""

import unittest

from scripts.prompt_library import _trading_user_parent_groups

TITLES = {"A 节", "B 节"}


def _mod(title):
    return {"title": title, "content": title}


class ParentGroupsTest(unittest.TestCase):
    def test_a_title_starts_a_group_and_is_itself_included(self):
        groups = _trading_user_parent_groups([_mod("A 节")], TITLES)
        self.assertEqual(sorted(groups), ["A 节"])
        self.assertEqual([m["title"] for m in groups["A 节"]], ["A 节"],
                         "父项包含在自己的组里（否则渲染时会漏掉分节标题本身）")

    def test_following_modules_attach_to_the_nearest_preceding_title(self):
        groups = _trading_user_parent_groups(
            [_mod("A 节"), _mod("随手记"), _mod("B 节"), _mod("另一条")], TITLES)
        self.assertEqual([m["title"] for m in groups["A 节"]], ["A 节", "随手记"])
        self.assertEqual([m["title"] for m in groups["B 节"]], ["B 节", "另一条"],
                         "新父节之后挂到新父节")

    def test_modules_before_the_first_title_land_nowhere(self):
        """★ **实测现状**：第一个父节之前的模块**不属于任何组** —— 静默丢弃（列待议）。"""
        groups = _trading_user_parent_groups([_mod("序言"), _mod("A 节")], TITLES)
        self.assertEqual(sorted(groups), ["A 节"])
        self.assertNotIn("序言", [m["title"] for ms in groups.values() for m in ms],
                         "前置模块不出现在任何组里（现状；是否应当另立一组由产品决定）")

    def test_a_repeated_title_merges_back_into_the_same_group(self):
        """★ 同名标题再次出现 ⇒ **不开新组**，而是并回原组（相隔很远也会被合并）。"""
        groups = _trading_user_parent_groups(
            [_mod("A 节"), _mod("x"), _mod("B 节"), _mod("y"), _mod("A 节"), _mod("z")], TITLES)
        self.assertEqual([m["title"] for m in groups["A 节"]], ["A 节", "x", "A 节", "z"],
                         "第二次 A 节并回原组（顺序保持出现顺序）")
        self.assertEqual([m["title"] for m in groups["B 节"]], ["B 节", "y"])

    def test_empty_input(self):
        self.assertEqual(_trading_user_parent_groups([], TITLES), {})

    def test_missing_title_key_raises(self):
        """⚠️ **实测边界（列待议）**：模块缺 `title` 键时**直接 `KeyError`**，整次编组作废。

        这是本仓登记的**第八处**同形态缺口（缺键/类型守卫 ⇒ 单条坏数据把整次处理打成异常）。
        """
        with self.assertRaises(KeyError):
            _trading_user_parent_groups([{"content": "没有 title"}], TITLES)


if __name__ == "__main__":
    unittest.main()

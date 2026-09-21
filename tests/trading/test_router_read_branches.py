"""执行路由里「**读不到**」相关的分支（第二百零八刀，由运行时覆盖率探针发现）。

本仓铁律「**读不到 ≠ 没有**」「**不可判定 ≠ 安全**」：这几条分支都是把"读失败"
渲染成**保守 + 留痕**而不是"当作没有"的地方。它们此前未被任何用例执行过。
"""

import unittest
from unittest.mock import patch

from r20_backend import execution_router as router


class ExposureVenueReadBranchTest(unittest.TestCase):
    """`_exposure_venues`：核算跨所敞口时该统计哪些场所。"""

    def test_other_venues_without_credentials_are_skipped_with_a_trace(self):
        # ⚠️ 桩必须与真实现**同型**：`venue_credentials` 返回 `(api_key, secret_key)` 二元组，
        # 未配置是 `("", "")`（不是 `{}` —— 用空 dict 做桩会让 `all({})` 恒 True，
        # 于是"没凭证的场所"被误判成"有凭证"，我第一次就踩了这个坑）。
        with patch("r20_backend.exchanges.registry.registered_venues",
                   lambda: ["okx", "binance", "gate"]), \
             patch("r20_backend.exchanges.registry.venue_credentials",
                   lambda v, env: ("k", "s") if v == "binance" else ("", "")):
            counted, skipped = router._exposure_venues("okx", "demo")
        self.assertIn("binance", counted, f"凭证齐备的场所必须计入：{counted}")
        self.assertTrue(any("gate" in s and "凭证未配置" in s for s in skipped),
                        f"凭证未配置的场所必须进 skipped 留痕（不假装统计是全量的）：{skipped}")

    def test_credential_read_failure_is_skipped_not_treated_as_absent(self):
        def explode(_v, _env):
            raise RuntimeError("credential boom")
        with patch("r20_backend.exchanges.registry.registered_venues",
                   lambda: ["binance"]), \
             patch("r20_backend.exchanges.registry.venue_credentials", explode):
            counted, skipped = router._exposure_venues("okx", "demo")
        self.assertEqual(counted, ["okx"])
        self.assertTrue(any("凭证读取失败" in s for s in skipped),
                        f"凭证读取失败必须留痕（读不到 ≠ 没有）：{skipped}")

    def test_enumeration_failure_falls_back_to_this_venue_only(self):
        def explode():
            raise RuntimeError("registry boom")
        with patch("r20_backend.exchanges.registry.registered_venues", explode):
            counted, skipped = router._exposure_venues("okx", "demo")
        self.assertEqual(counted, ["okx"], "枚举失败 ⇒ 退回只算本次场所")
        self.assertEqual(skipped, [], "退回不等于谎报：这里没有可留痕的场所")


class ReadSymbolPositionBranchTest(unittest.TestCase):
    """`_read_symbol_position`：平仓前抓事实；抓不到就退化成只有 base/side（保守）。"""

    class _Ad:
        def __init__(self, rows):
            self._rows = rows

        def positions(self):
            return self._rows

    def test_non_dict_rows_and_other_bases_are_ignored(self):
        ad = self._Ad([None, "junk", {"base": "ETH", "size_signed": 9},
                       {"base": "BTC", "size_signed": -3, "side": "short"}])
        out = router._read_symbol_position(ad, "BTC", pos_side="short")
        self.assertEqual(out["base"], "BTC")
        self.assertEqual(out["size_signed"], -3)
        self.assertEqual(out["side"], "short")

    def test_matching_side_wins_over_earlier_rows(self):
        ad = self._Ad([{"base": "BTC", "size_signed": 5, "side": "long"},
                       {"base": "BTC", "size_signed": -2, "side": "short"}])
        out = router._read_symbol_position(ad, "BTC", pos_side="short")
        self.assertEqual(out["size_signed"], -2, "按 pos_side 匹配到的那一行才是要的事实")

    def test_read_failure_degrades_to_base_and_side_only(self):
        class Boom:
            def positions(self):
                raise RuntimeError("positions boom")
        out = router._read_symbol_position(Boom(), "BTC", pos_side="long")
        self.assertEqual(out, {"base": "BTC", "side": "long"},
                         f"读失败只保留 base/side（不臆造 size）：{out}")


class VerifySymbolFlatBranchTest(unittest.TestCase):
    """`_verify_symbol_flat`：按**合约整体**归零判；读不出量一律当作仍未归零。"""

    class _Ad:
        def __init__(self, rows):
            self._rows = rows

        def positions(self):
            return self._rows

    def test_non_dict_rows_do_not_break_the_scan(self):
        ad = self._Ad([None, "junk", {"base": "BTC", "size_signed": 0}])
        flat, remaining = router._verify_symbol_flat(ad, "BTC")
        self.assertTrue(flat, f"非 dict 行应被跳过、整体归零应判 flat：{(flat, remaining)}")

    def test_unreadable_size_counts_as_still_open(self):
        ad = self._Ad([{"base": "BTC", "size_signed": "不是数字"}])
        flat, remaining = router._verify_symbol_flat(ad, "BTC")
        self.assertFalse(flat, "读不出量 ⇒ 当作仍有仓（宁可不撤，也不误撤）")
        self.assertGreaterEqual(remaining, 1.0)

    def test_other_symbols_do_not_count(self):
        ad = self._Ad([{"base": "ETH", "size_signed": 7}])
        flat, _ = router._verify_symbol_flat(ad, "BTC")
        self.assertTrue(flat, "别的币的持仓不该让本合约判成未归零")


if __name__ == "__main__":
    unittest.main()

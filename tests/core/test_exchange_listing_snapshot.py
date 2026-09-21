"""场所级目录快照：**fail-open 但绝不填 0**、结构性错误**显式暴露**（第二百三十二刀）。

| 语义 | 口径 |
|---|---|
| ★ fail-open | 目录拉取失败 ⇒ **`ok=True`**（表示不阻塞交易）**但** `listed_count=None` + `source='unavailable'` + 中文 reason + 警告 |
| ★ **绝不填 0** | 未知数量就是 `None` —— 0 会被下游读成「**这个所一个合约都没有**」 |
| ★ 结构性错误显式 | venue/环境档未知 ⇒ `ok=False`（**须显式暴露不能装没事**）+ `unavailable` + `listed_count=None` |
| 目录可用 | `ok=True` + `reason=None` + `listed_count=len(directory)` + `source` 透传（cache/fresh）|
| 归一 | venue/环境档先小写去空白再匹配 |

## ★ 本刀**印证**了第 23 条待议（同一模块，两种写法）

同一个 fail-open 情形：**本函数**给 `source='unavailable'`（与数据类取值域一致），
而 `ensure_contract_listed` 给 `'cache'` ✗。**同一文件里两种写法** ⇒ 第 23 条不是"某处笔误"，
而是**模块内部口径不统一**。本刀把**两种**都钉住（对照式断言）。
"""

import unittest
import warnings
from unittest import mock

from r20_backend.exchanges import listing as L
from r20_backend.exchanges import ExchangeCapabilityError


class ListingSnapshotTest(unittest.TestCase):
    def setUp(self):
        L._CACHE.clear()
        self.addCleanup(L._CACHE.clear)

    def _profile_ok(self):
        p = mock.patch.object(L.env_profiles, "get_profile", return_value=object())
        p.start()
        self.addCleanup(p.stop)

    def test_fail_open_reports_unknown_count_as_none_never_zero(self):
        """★ fail-open 的两个要点：**不阻塞**（ok=True）与**不虚构数量**（None，不是 0）。"""
        self._profile_ok()
        with mock.patch.object(L, "_get_directory", side_effect=TimeoutError("超时")):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                out = L.listing_snapshot("OKX", "DEMO")
        self.assertIs(out.ok, True, "fail-open：不阻塞交易")
        self.assertIsNone(out.listed_count, "未知数量 = None（0 会被读成「一个合约都没有」）")
        self.assertEqual(out.source, "unavailable")
        self.assertEqual(out.reason, "行情目录不可用，跳过对账")
        self.assertTrue(any("listing-gate" in str(w.message) for w in caught), "要出声")

    def test_unknown_environment_profile_is_an_explicit_structural_error(self):
        """★ 未知场所/档位**不是**降级，而是结构性错误 ⇒ `ok=False`，且同样不填数字。"""
        self._profile_ok()
        with mock.patch.object(L.env_profiles, "get_profile",
                               side_effect=ExchangeCapabilityError("没有这个档")):
            out = L.listing_snapshot("kraken", "demo")
        self.assertIs(out.ok, False, "结构性错误必须显式暴露")
        self.assertIn("未知环境档", out.reason)
        self.assertEqual(out.source, "unavailable")
        self.assertIsNone(out.listed_count)
        self.assertIn("kraken", out.reason, "原因里点明是哪个场所/档位")

    def test_available_directory_counts_the_rows(self):
        self._profile_ok()
        directory = {"A": {}, "B": {}, "C": {}}
        with mock.patch.object(L, "_get_directory", return_value=(directory, "cache")):
            out = L.listing_snapshot("okx", "demo")
        self.assertIs(out.ok, True)
        self.assertIsNone(out.reason, "可用时不编造原因")
        self.assertEqual(out.listed_count, 3)
        self.assertEqual(out.source, "cache", "source 透传（cache/fresh 由缓存层决定）")

    def test_venue_and_environment_are_normalised_before_lookup(self):
        self._profile_ok()
        with mock.patch.object(L.env_profiles, "get_profile", return_value=object()) as gp:
            with mock.patch.object(L, "_get_directory", return_value=({}, "fresh")):
                L.listing_snapshot("  OKX  ", " DEMO ")
        self.assertEqual(gp.call_args.args, ("okx", "demo"), "归一后再查档案")

    def test_the_two_fail_open_paths_disagree_on_the_source_field(self):
        """★ **对照断言**（印证据 23）：同一模块里，同一个 fail-open 情形有两种 `source`。

        - `listing_snapshot` ⇒ `'unavailable'`（与数据类注释的取值域一致）
        - `ensure_contract_listed` ⇒ `'cache'`（**既非缓存也未拉到**）

        本条把**两者都钉住**：将来统一口径时它会红，正好提醒一起改。
        """
        self._profile_ok()
        with mock.patch.object(L, "_get_directory", side_effect=TimeoutError("超时")):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                snap = L.listing_snapshot("okx", "demo")
                check = L.ensure_contract_listed("okx", "demo", "BTC-USDT-SWAP")
        self.assertEqual(snap.source, "unavailable")
        self.assertEqual(check.source, "cache")
        self.assertNotEqual(snap.source, check.source,
                            "同一模块两种写法（列待议：统一到 unavailable）")


if __name__ == "__main__":
    unittest.main()

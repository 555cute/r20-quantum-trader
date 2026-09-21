"""下单前合约对账：**fail-open** 与「**环境措辞**」（第二百三十一刀）。

| 语义 | 口径 |
|---|---|
| TTL 缓存 | `_get_directory` 首次 `fresh`、TTL 内再取 `cache`（不重复出网）；键为 (venue, environment) 小写 |
| ★ **fail-open** | 目录拉取失败/超时 ⇒ **`ok=True`** + 警告 + reason「行情目录不可用，跳过对账」—— 注释写明理由：「对账是**增强**不是**风控闸门**」|
| ★ **环境措辞** | 目录里查不到该合约时：沙盒档说「**沙盒未上市**」，实盘档说「**合约已下架**」—— 同一事实，两种说法 |
| ★ **三所状态判定** | okx `state != live`、binance `status != TRADING`、gate `in_delisting == true` ⇒ `ok=False` + 具体 reason |
| 通过 | `ok=True` 且 `reason=None` |
| 归一 | 场所/档位/合约名都先小写去空白再匹配 |
| 时点 | `checked_at` 为 **UTC ISO8601**（带 `Z`）|

## ⚠️ 一处实测发现（新待议）

fail-open 分支返回的 `source` 是 **`"cache"`** —— 但**既没有命中缓存，也不是新拉的**。
数据类注释给的取值域是 `'cache' | 'fresh' | 'unavailable'` ⇒ 这里应当给 **`unavailable`**。
（「**名字即语义**」：`cache` 会让下游以为"这是缓存里的旧目录"，而事实是"根本没读到"。）
"""

import unittest
import warnings
from unittest import mock

from r20_backend.exchanges import listing as L


class GetDirectoryTest(unittest.TestCase):
    def setUp(self):
        L._CACHE.clear()
        self.addCleanup(L._CACHE.clear)

    def test_second_call_within_ttl_is_served_from_cache(self):
        calls = []

        def _fetch(venue, environment):
            calls.append((venue, environment))
            return {"BTC-USDT-SWAP": {"state": "live"}}
        d1, s1 = L._get_directory("OKX", "DEMO", fetch_fn=_fetch)
        d2, s2 = L._get_directory("okx", "demo", fetch_fn=_fetch)
        self.assertEqual((s1, s2), ("fresh", "cache"), "首次 fresh、其后 cache")
        self.assertEqual(len(calls), 1, "TTL 内不得重复出网")
        self.assertIs(d1, d2, "命中缓存返回同一个对象")
        self.assertEqual(calls[0], ("OKX", "DEMO"), "传给拉取函数的是原样参数")

    def test_cache_key_is_case_insensitive(self):
        calls = []
        L._get_directory("OKX", "demo", fetch_fn=lambda v, e: calls.append(1) or {})
        L._get_directory("okx", "DEMO", fetch_fn=lambda v, e: calls.append(1) or {})
        self.assertEqual(len(calls), 1, "大小写不同的同一场所/档位视为同一键")


class EnsureListedTest(unittest.TestCase):
    """⚠️ 注入点在 `_get_directory`（而不是伪造缓存条目）：我第一版往 `_CACHE` 里塞了
    时间戳 `0.0` 的条目，结果它**早就过期** ⇒ 代码真的去**出网拉目录**了 ⇒ 两条断言红，
    而且测试里发生了**真实网络调用**。改为在 `_get_directory` 处注入，全程不出网。
    """

    def setUp(self):
        L._CACHE.clear()
        self.addCleanup(L._CACHE.clear)

    def _run(self, directory, venue="okx", environment="live",
             contract="BTC-USDT-SWAP", source="fresh"):
        with mock.patch.object(L, "_get_directory",
                               return_value=(directory, source)):
            return L.ensure_contract_listed(venue, environment, contract)

    def _run_with_fetch_error(self, exc=None):
        with mock.patch.object(L, "_get_directory",
                               side_effect=exc or TimeoutError("超时")):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                out = L.ensure_contract_listed("okx", "live", "BTC-USDT-SWAP")
        return out, caught

    def test_fail_open_when_the_directory_cannot_be_fetched(self):
        """★ 拉不到目录 ⇒ **放行**（对账是增强不是风控闸门），但要**出声**。"""
        out, caught = self._run_with_fetch_error()
        self.assertTrue(out.ok, "fail-open：不阻塞下单")
        self.assertEqual(out.reason, "行情目录不可用，跳过对账")
        self.assertTrue(any("listing-gate" in str(w.message) for w in caught),
                        "必须出声（警告），不能悄悄放行")

    def test_fail_open_labels_the_source_as_cache_which_is_misleading(self):
        """⚠️ **实测发现（新待议）**：fail-open 时 `source` 标成 `cache` —— 实际既非缓存也未拉到。

        数据类注释给的取值域是 `cache | fresh | unavailable` ⇒ 此处应为 **`unavailable`**。
        """
        out, _caught = self._run_with_fetch_error()
        self.assertEqual(out.source, "cache", "现状：标成 cache（列待议，应为 unavailable）")

    def test_missing_contract_is_worded_by_environment(self):
        """★ 同一事实两种措辞：沙盒说「沙盒未上市」，实盘说「合约已下架」。"""
        live = self._run({}, environment="live")
        self.assertFalse(live.ok)
        self.assertIn("合约已下架", live.reason)
        sandbox = self._run({}, environment="sandbox")
        self.assertFalse(sandbox.ok)
        self.assertIn("沙盒未上市", sandbox.reason)

    def test_per_venue_status_rules(self):
        ok = self._run({"BTC-USDT-SWAP": {"state": "live"}})
        self.assertTrue(ok.ok)
        self.assertIsNone(ok.reason, "通过时不编造原因")
        bad_okx = self._run({"BTC-USDT-SWAP": {"state": "suspend"}})
        self.assertFalse(bad_okx.ok)
        self.assertIn("state=suspend", bad_okx.reason)
        bad_bn = self._run({"BTCUSDT": {"status": "BREAK"}}, venue="binance",
                           contract="btcusdt")
        self.assertFalse(bad_bn.ok)
        self.assertIn("status=BREAK", bad_bn.reason)
        good_bn = self._run({"BTCUSDT": {"status": "TRADING"}}, venue="binance",
                            contract="BTCUSDT")
        self.assertTrue(good_bn.ok)
        bad_gate = self._run({"BTC_USDT": {"in_delisting": "true"}}, venue="gate",
                             contract="btc_usdt")
        self.assertFalse(bad_gate.ok)
        self.assertIn("in_delisting=true", bad_gate.reason)
        good_gate = self._run({"BTC_USDT": {"in_delisting": "false"}}, venue="gate",
                              contract="BTC_USDT")
        self.assertTrue(good_gate.ok)

    def test_checked_at_is_utc_iso8601(self):
        out = self._run({"BTC-USDT-SWAP": {"state": "live"}})
        self.assertRegex(out.checked_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


if __name__ == "__main__":
    unittest.main()

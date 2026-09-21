"""多交易所状态汇总：**只报"有没有"，不报值**（第二百二十六刀）。

| 语义 | 口径 |
|---|---|
| ★ 安全红线 | 汇总里每个凭证字段都是**布尔**（`has_api_key` / `has_secret` / `has_passphrase`）——**绝不回传密钥值** |
| ★ 旧签名兼容垫 | `_read_creds` 先试 `venue_credentials(v, env)`，抛 `TypeError` 再退 `venue_credentials(v)`（兼容只接一个参数的旧签名）|
| 两级档位 | 每所同时报**通用档**与 `live`/`demo` 两级，外加 `testnet`、`execution_open` |
| OKX 特殊 | OKX 不进通用循环，单独报三级（含 **passphrase**）|

## ⚠️ 两处实测发现（列待议，只钉现状）

1. **`venue_passphrase` 读取失败 ⇒ 静默当空串** ⇒ 汇总显示 `has_passphrase: False` ——
   「**读不到**」被表示成「**没有**」（与第 7 条同族）。
2. ~~**未注册的所在 `accounts_status` 里是空 `{}`**，与已注册所形状不同~~
   ⇒ ★ **本条已作废（我自己看错了）**：未注册所是 `{"live": {}, "demo": {}}` ——
   `live`/`demo` 两个键**始终存在**，只是内容为空 ⇒ **形状是一致的**，前端无需分别处理。
"""

import pathlib
import tempfile
import types
import unittest
from unittest.mock import patch

from r20_backend.routers import exchanges as R

PKG = "r20_backend.exchanges"


class StatusAggregationTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(R, "require_admin_header")
        self.auth = p.start()
        self.addCleanup(p.stop)
        # ⚠️ 我第一版的环境桩少了 `.simulated`（代码用它算 OKX 的 testnet 标记）⇒ 6 例全红。
        p2 = patch("scripts.okx_runtime.current_environment",
                   return_value=types.SimpleNamespace(mode="demo", configured=True,
                                                      simulated=False))
        p2.start()
        self.addCleanup(p2.stop)
        # 健康度是从 DATA_DIR 下的真实文件读的 ⇒ 指到临时目录，**不碰生产数据**
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p3 = patch.object(R, "DATA_DIR", pathlib.Path(self.tmp.name))
        p3.start()
        self.addCleanup(p3.stop)

    def _run(self, venues, creds, passphrase=None, testnet=True, execution=False):
        """creds: {(venue, env): (ak, sk)}；passphrase: 可调用或异常。"""
        def _vc(v, env=None):
            return creds.get((v, env), ("", ""))
        patches = [
            patch(f"{PKG}.registered_venues", return_value=list(venues)),
            patch(f"{PKG}.venue_credentials", side_effect=_vc),
            patch(f"{PKG}.venue_testnet_enabled", return_value=testnet),
            patch(f"{PKG}.execution_open", return_value=execution),
        ]
        if isinstance(passphrase, Exception):
            patches.append(patch(f"{PKG}.venue_passphrase", side_effect=passphrase))
        else:
            patches.append(patch(f"{PKG}.venue_passphrase",
                                 side_effect=lambda v, e: passphrase or ""))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return R.admin_multi_exchange_status(None)

    def test_only_booleans_are_exposed_never_the_secret_values(self):
        """★ 安全红线：汇总只回答"有没有"，**任何密钥值都不得出现**。"""
        out = self._run(["binance", "gate"],
                        {("binance", None): ("AK-1", "SK-1"),
                         ("binance", "live"): ("LIVE-AK", "LIVE-SK"),
                         ("gate", "demo"): ("D-AK", "D-SK")},
                        passphrase="PP")
        blob = repr(out)
        for secret in ("AK-1", "SK-1", "LIVE-AK", "LIVE-SK", "D-AK", "D-SK", "PP"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, blob, "凭证值绝不能回传")
        bn = out["accounts_status"]["binance"]
        # ⚠️ 我原以为非 OKX 所也带 has_passphrase —— 错：passphrase 是 **OKX 独有**的第三件套，
        # 其余所是把 venues[v]["live"]（两个布尔）**原样搬过来**。
        self.assertEqual(bn["live"], {"has_api_key": True, "has_secret": True})
        self.assertNotIn("has_passphrase", bn["live"], "passphrase 只属于 OKX 段")

    def test_okx_is_reported_separately_with_passphrase(self):
        out = self._run(["okx"], {("okx", "live"): ("A", "B")}, passphrase="PP")
        okx = out["accounts_status"]["okx"]
        self.assertEqual(okx["live"],
                         {"has_api_key": True, "has_secret": True, "has_passphrase": True})
        self.assertEqual(okx["demo"],
                         {"has_api_key": False, "has_secret": False, "has_passphrase": True})
        self.assertNotIn("okx", out["venues"], "OKX 不进通用循环")

    def test_generic_and_two_tier_flags_plus_switches(self):
        out = self._run(["binance"], {("binance", None): ("A", "B")}, passphrase="")
        bn = out["venues"]["binance"]
        self.assertEqual(bn["has_api_key"], True)
        self.assertEqual(bn["has_secret"], True)
        self.assertEqual(bn["live"], {"has_api_key": False, "has_secret": False})
        self.assertEqual(bn["demo"], {"has_api_key": False, "has_secret": False})
        self.assertIs(bn["testnet"], True)
        self.assertIs(bn["execution_open"], False)

    def test_read_creds_falls_back_to_the_one_argument_signature(self):
        """★ 兼容垫：只接一个参数的旧签名（多传 env 会 `TypeError`）也必须能读到通用档。"""
        calls = []

        def _vc(v, env=None):
            calls.append(env)
            if env is not None:
                raise TypeError("venue_credentials() takes 1 positional argument")
            return ("AK", "SK")
        for name, value in (("registered_venues", lambda: ["binance"]),
                            ("venue_credentials", _vc),
                            ("venue_passphrase", lambda v, e: "")):
            p = patch(f"{PKG}.{name}", side_effect=value)
            p.start()
            self.addCleanup(p.stop)
        out = R.admin_multi_exchange_status(None)
        self.assertTrue(out["venues"]["binance"]["has_api_key"], "旧签名也要取到通用档")
        self.assertIn(None, calls)

    def test_passphrase_read_failure_is_reported_as_absence(self):
        """⚠️ **实测发现（列待议）**：读 passphrase 失败 ⇒ 静默当空 ⇒ 汇总显示「没有」。"""
        out = self._run(["okx"], {("okx", "live"): ("A", "B")},
                        passphrase=RuntimeError("读不到"))
        self.assertIs(out["accounts_status"]["okx"]["live"]["has_passphrase"], False,
                      "「读不到」被表示成「没有」（现状，列待议）")

    def test_unregistered_venue_keeps_the_same_shape_with_empty_tiers(self):
        """★ **更正我自己的判断**：未注册所**不是**空 `{}`，而是 `{"live": {}, "demo": {}}`
        —— 两级键**始终存在** ⇒ 形状与已注册所一致（前端不必分别处理）。"""
        out = self._run(["gate"], {("gate", None): ("A", "B")}, passphrase="")
        self.assertEqual(out["accounts_status"]["binance"], {"live": {}, "demo": {}},
                         "未注册 ⇒ 两级键在、内容空（**形状一致**）")
        self.assertIn("has_api_key", out["accounts_status"]["gate"]["live"])


if __name__ == "__main__":
    unittest.main()

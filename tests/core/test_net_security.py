"""出站端点校验与禁跳闸：**只准 HTTPS、不许嵌凭证、解析到内网/元数据一律拒**（第二百六十八刀，开新面 net_security.py）。

先打印整个文件（64 行）再动笔。本模块是**凭证类出站**（备份投递、通知推送）的唯一防线：
`routers/gateway/backups.py`、`backup_store.py`、`notifications.py` 都调用它。

| 语义 | 口径 |
|---|---|
| ★ **协议白名单** | 默认只许 HTTPS；`allow_private=True` 才放开 HTTP；`ftp://` / `file://` 等**任何情况下都拒** |
| ★ **URL 不许嵌凭证** | 主机为空、或 URL 里带 `user:pass@` 一律拒（否则凭证会随 URL 进日志）|
| ★ **解析到受保护地址一律拒** | 回环 / 链路本地（`169.254.169.254` 云元数据！）/ 组播 / 未指定 / 保留网络 —— 无条件拒；**私有网段需显式 `allow_private`** |
| ★ **任一地址命中即拒** | 一个域名解析出多个 A/AAAA 记录时，**只要有一条**落在受保护网段就整体拒（否则攻击者用多记录绕过）|
| ★ **禁跟随重定向** | `safe_urlopen` 走 `_NoRedirectHandler`：首跳合法但回 302→内网 = TOCTOU 借道 SSRF，故遇 3xx 直接抛 `_RedirectDenied` |
| 白名单 | `validate_wechat_base_url` 只放行官方域名 |
| 返回值 | 去掉结尾 `/`（避免调用方拼出双斜杠路径）|

⚠️ 如实登记两处：
1. **不可达**：`_NoRedirectHandler.redirect_request` 第 **56** 行 `return None` 紧跟在
   `raise` 之后，是**死代码**（`raise` 之后永不执行）—— 故意保留以示意"本处理器不返回新请求"。
   故 net_security.py 为 26/27。
2. **已知不对称**：校验用 `urlparse(str(url).strip())`，但返回值是 `str(url).rstrip("/")`
   —— **前后空白原样带回**（末尾不是 `/` 时 rstrip 不生效）。调用方拿到的是带空白的 URL；
   本刀按实际行为钉住，未擅自改语义。
"""

import socket
import unittest
import urllib.error
from unittest import mock

from r20_backend import net_security as NS


def _resolve(*addresses):
    """伪造 getaddrinfo 的返回形状（第 5 项是 sockaddr，[0] 为地址字符串）。"""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 443))
            for addr in addresses]


class SchemeAndHostTests(unittest.TestCase):
    def test_https_is_the_default_and_http_is_rejected(self):
        with mock.patch("socket.getaddrinfo", return_value=_resolve("93.184.216.34")):
            self.assertEqual(NS.validate_outbound_url("https://example.com/x"),
                             "https://example.com/x")
            with self.assertRaises(ValueError) as ctx:
                NS.validate_outbound_url("http://example.com/x")
        self.assertIn("HTTPS", str(ctx.exception))

    def test_allow_private_opens_http_but_not_other_schemes(self):
        with mock.patch("socket.getaddrinfo", return_value=_resolve("93.184.216.34")):
            self.assertEqual(
                NS.validate_outbound_url("http://example.com", allow_private=True),
                "http://example.com")
            for bad in ("ftp://example.com/x", "file:///etc/passwd",
                        "javascript:alert(1)", "example.com/x"):
                with self.subTest(url=bad):
                    with self.assertRaises(ValueError):
                        NS.validate_outbound_url(bad, allow_private=True)

    def test_embedded_credentials_are_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            NS.validate_outbound_url("https://user:pass@example.com/hook")
        self.assertIn("账号密码", str(ctx.exception))
        with self.assertRaises(ValueError):
            NS.validate_outbound_url("https://user@example.com/hook")

    def test_empty_host_is_rejected(self):
        for bad in ("https://", "https:///path", ""):
            with self.subTest(url=bad):
                with self.assertRaises(ValueError):
                    NS.validate_outbound_url(bad)

    def test_trailing_slashes_are_stripped(self):
        with mock.patch("socket.getaddrinfo", return_value=_resolve("93.184.216.34")):
            self.assertEqual(NS.validate_outbound_url("https://example.com/hook/"),
                             "https://example.com/hook")
            self.assertEqual(NS.validate_outbound_url("https://example.com/hook///"),
                             "https://example.com/hook")
            self.assertEqual(NS.validate_outbound_url("https://example.com"),
                             "https://example.com")

    def test_surrounding_whitespace_is_used_for_parsing_but_not_stripped_from_the_result(self):
        """⚠️ 已知不对称：校验时 `urlparse(str(url).strip())`，但返回值是
        `str(url).rstrip("/")` —— **前后空白原样带回**（末尾不是 `/` 时 rstrip 不生效）。
        调用方拿到的是**带空白的** URL，本刀按实际行为钉住，未擅自改语义。"""
        with mock.patch("socket.getaddrinfo", return_value=_resolve("93.184.216.34")):
            self.assertEqual(NS.validate_outbound_url("  https://example.com/hook  "),
                             "  https://example.com/hook  ")
            self.assertEqual(NS.validate_outbound_url("https://example.com/hook/  "),
                             "https://example.com/hook/  ")


class HostAllowListTests(unittest.TestCase):
    def test_allowlist_membership_is_enforced(self):
        with mock.patch("socket.getaddrinfo", return_value=_resolve("93.184.216.34")):
            self.assertEqual(
                NS.validate_outbound_url("https://ok.example.com/x",
                                         allowed_hosts={"ok.example.com"}),
                "https://ok.example.com/x")
            with self.assertRaises(ValueError) as ctx:
                NS.validate_outbound_url("https://evil.example.com/x",
                                         allowed_hosts={"ok.example.com"})
        self.assertIn("白名单", str(ctx.exception))

    def test_wechat_helper_pins_the_official_domain(self):
        with mock.patch("socket.getaddrinfo", return_value=_resolve("93.184.216.34")):
            self.assertEqual(
                NS.validate_wechat_base_url("https://ilinkai.weixin.qq.com/api"),
                "https://ilinkai.weixin.qq.com/api")
            with self.assertRaises(ValueError):
                NS.validate_wechat_base_url("https://evil.example.com/api")

    def test_host_case_and_trailing_dot_are_normalised_for_matching(self):
        with mock.patch("socket.getaddrinfo", return_value=_resolve("93.184.216.34")):
            self.assertEqual(
                NS.validate_outbound_url("https://OK.Example.com./x",
                                         allowed_hosts={"ok.example.com"}),
                "https://OK.Example.com./x")


class AddressClassTests(unittest.TestCase):
    def _check(self, address, **kwargs):
        with mock.patch("socket.getaddrinfo", return_value=_resolve(address)):
            return NS.validate_outbound_url("https://example.com/x", **kwargs)

    def test_dns_failure_is_a_clear_value_error(self):
        with mock.patch("socket.getaddrinfo",
                        side_effect=socket.gaierror("Name or service not known")):
            with self.assertRaises(ValueError) as ctx:
                NS.validate_outbound_url("https://nope.invalid/x")
        self.assertIn("无法解析", str(ctx.exception))
        self.assertIn("nope.invalid", str(ctx.exception))

    def test_loopback_and_metadata_endpoints_are_always_rejected(self):
        for address in ("127.0.0.1", "::1", "169.254.169.254"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError) as ctx:
                    self._check(address)
                self.assertIn("受保护", str(ctx.exception))

    def test_multicast_unspecified_and_reserved_are_rejected(self):
        for address in ("224.0.0.1", "0.0.0.0", "240.0.0.1"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError) as ctx:
                    self._check(address)
                self.assertIn("受保护", str(ctx.exception))

    def test_private_networks_need_an_explicit_opt_in(self):
        for address in ("10.1.2.3", "172.16.5.5", "192.168.1.1", "fd00::1"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError) as ctx:
                    self._check(address)
                self.assertIn("私有网络", str(ctx.exception))
                self.assertEqual(self._check(address, allow_private=True),
                                 "https://example.com/x",
                                 "显式启用后（自建 MinIO/NAS 场景）应放行")

    def test_one_bad_address_in_a_multi_record_answer_rejects_the_whole_host(self):
        bad = _resolve("93.184.216.34") + _resolve("127.0.0.1")
        with mock.patch("socket.getaddrinfo", return_value=bad):
            with self.assertRaises(ValueError) as ctx:
                NS.validate_outbound_url("https://example.com/x")
        self.assertIn("受保护", str(ctx.exception),
                      "多记录里只要有一条指向内网就必须整体拒，不能只取第一条放行")

    def test_public_addresses_pass_and_the_lookup_uses_the_right_port(self):
        with mock.patch("socket.getaddrinfo",
                        return_value=_resolve("93.184.216.34")) as resolver:
            NS.validate_outbound_url("https://example.com:8443/x")
        self.assertEqual(resolver.call_args[0][1], 8443)
        self.assertEqual(resolver.call_args[1]["type"], socket.SOCK_STREAM)

    def test_default_port_follows_the_scheme(self):
        with mock.patch("socket.getaddrinfo",
                        return_value=_resolve("93.184.216.34")) as resolver:
            NS.validate_outbound_url("https://example.com/x")
            NS.validate_outbound_url("http://example.com/x", allow_private=True)
        self.assertEqual([c[0][1] for c in resolver.call_args_list], [443, 80])


class RedirectGuardTests(unittest.TestCase):
    def test_redirects_are_refused_with_a_typed_error(self):
        handler = NS._NoRedirectHandler()
        with self.assertRaises(NS._RedirectDenied) as ctx:
            handler.redirect_request(mock.Mock(), None, 302, "Found", {},
                                     "http://169.254.169.254/latest/meta-data/")
        self.assertIsInstance(ctx.exception, urllib.error.HTTPError)
        self.assertIn("重定向被安全策略拒绝", str(ctx.exception))
        self.assertEqual(ctx.exception.code, 302)

    def test_safe_urlopen_uses_the_no_redirect_opener(self):
        opener = mock.Mock()
        opener.open.return_value = "RESP"
        with mock.patch.object(NS, "_SAFE_OPENER", opener):
            self.assertEqual(NS.safe_urlopen("REQ", timeout=7), "RESP")
        opener.open.assert_called_once_with("REQ", timeout=7)

    def test_the_default_opener_installs_the_guard(self):
        self.assertTrue(any(isinstance(h, NS._NoRedirectHandler)
                            for h in NS._SAFE_OPENER.handlers),
                        "模块级 opener 必须挂上禁跳处理器，否则 safe_urlopen 形同虚设")


if __name__ == "__main__":
    unittest.main()

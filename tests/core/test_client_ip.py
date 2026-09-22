"""客户端真实来源解析：**直连不可信就完全不信转发头**（第二百六十八刀，开新面 client_ip.py）。

先打印整个文件（87 行）再动笔。`X-Forwarded-For` 是**客户端可自填**的头：
若无条件取最左值，攻击者带一个 `X-Forwarded-For: 8.8.8.8` 就能伪造来源、
绕过按 IP 的登录限速与审计归因（`r20_admin_audit.jsonl` 的 `actor_ip` 正来自这里）。

| 语义 | 口径 |
|---|---|
| ★ **直连不可信 ⇒ 头一律不看** | 对端不在可信代理网段内时，直接返回对端地址，**完全忽略** XFF / X-Real-IP（这是本模块存在的理由）|
| ★ **直连可信 ⇒ 从右往左找第一个不可信地址** | 右侧条目由**我们自己的**代理追加、可信；左侧条目可被客户端伪造 |
| ★ **兜底顺序** | 全是可信跳 ⇒ 看 `x-real-ip`；仍无可信判定 ⇒ `hops[0]`；连跳都没有 ⇒ 对端地址 |
| ★ **绝不回退成可伪造的头值** | 无法判定时返回 `unknown`，而不是 XFF 里的任意值 |
| 长度 | 返回值截到 64 字符、`user_agent` 截到 200（防超长头污染审计行）|
| 地址清洗 | 去 IPv6 方括号与 `%zone` 后缀；非法地址当"不可信"而不是异常 |
| 配置 | `R20_TRUSTED_PROXIES`（逗号分隔 CIDR）可覆盖默认网段；**非法 CIDR 跳过而不是崩** |
"""

import ipaddress
import os
import types
import unittest
from unittest import mock

from r20_backend import client_ip as CI

DEFAULT = CI._DEFAULT_TRUSTED


class _Request:
    def __init__(self, peer=None, headers=None):
        self.client = types.SimpleNamespace(host=peer) if peer is not None else None
        self.headers = headers or {}


class TrustedNetworkTests(unittest.TestCase):
    def test_default_networks_cover_loopback_and_docker_bridges(self):
        nets = CI._trusted_networks()
        rendered = {str(n) for n in nets}
        self.assertIn("127.0.0.0/8", rendered)
        self.assertIn("172.16.0.0/12", rendered)

    def test_env_override_replaces_the_defaults(self):
        with mock.patch.dict(os.environ, {"R20_TRUSTED_PROXIES": "203.0.113.0/24"}):
            nets = CI._trusted_networks()
        self.assertEqual([str(n) for n in nets], ["203.0.113.0/24"])

    def test_blank_and_invalid_entries_are_skipped(self):
        with mock.patch.dict(os.environ,
                             {"R20_TRUSTED_PROXIES": " , not-a-cidr , 10.0.0.0/8 ,, "}):
            nets = CI._trusted_networks()
        self.assertEqual([str(n) for n in nets], ["10.0.0.0/8"],
                         "非法 CIDR 必须跳过，不能让整个解析炸掉")

    def test_empty_env_falls_back_to_defaults(self):
        expected = {str(ipaddress.ip_network(chunk.strip(), strict=False))
                    for chunk in DEFAULT.split(",") if chunk.strip()}
        with mock.patch.dict(os.environ, {"R20_TRUSTED_PROXIES": "   "}):
            rendered = {str(n) for n in CI._trusted_networks()}
        self.assertEqual(rendered, expected)
        self.assertIn("127.0.0.0/8", rendered)

    def test_is_trusted_ip(self):
        nets = [ipaddress.ip_network("10.0.0.0/8")]
        self.assertTrue(CI.is_trusted_ip("10.1.2.3", nets))
        self.assertFalse(CI.is_trusted_ip("8.8.8.8", nets))
        self.assertFalse(CI.is_trusted_ip("", nets))
        self.assertFalse(CI.is_trusted_ip("not-an-ip", nets))
        self.assertTrue(CI.is_trusted_ip("127.0.0.1"), "默认网段含回环")


class CleanHostTests(unittest.TestCase):
    def test_ipv6_brackets_and_zone_ids_are_stripped(self):
        self.assertEqual(CI._clean_host("[::1]"), "::1")
        self.assertEqual(CI._clean_host("fe80::1%eth0"), "fe80::1")
        self.assertEqual(CI._clean_host("  10.0.0.1  "), "10.0.0.1")
        self.assertEqual(CI._clean_host(None), "")


class ClientIpTests(unittest.TestCase):
    def test_untrusted_peer_ignores_forwarded_headers_entirely(self):
        req = _Request("203.0.113.9", {"x-forwarded-for": "8.8.8.8",
                                       "x-real-ip": "1.2.3.4"})
        self.assertEqual(CI.client_ip(req), "203.0.113.9",
                         "直连不是可信代理 ⇒ 头可被伪造，一律不看")

    def test_missing_peer_is_unknown_not_a_header_value(self):
        self.assertEqual(CI.client_ip(_Request(None)), "unknown")
        self.assertEqual(CI.client_ip(_Request("")), "unknown")
        self.assertEqual(CI.client_ip(_Request(None, {"x-forwarded-for": "8.8.8.8"})),
                         "unknown")

    def test_trusted_peer_walks_the_chain_from_the_right(self):
        req = _Request("10.0.0.5", {"x-forwarded-for": "8.8.8.8, 10.0.0.7"})
        self.assertEqual(CI.client_ip(req), "8.8.8.8",
                         "10.0.0.7 是我们自己的代理（可信）⇒ 继续向左找")

    def test_forged_leftmost_entry_cannot_win_when_a_trusted_hop_is_present(self):
        req = _Request("10.0.0.5",
                       {"x-forwarded-for": "1.1.1.1, 203.0.113.9, 10.0.0.7"})
        self.assertEqual(CI.client_ip(req), "203.0.113.9",
                         "最左的 1.1.1.1 是客户端伪造的，必须被右侧更近的真实地址挡住")

    def test_all_trusted_hops_fall_back_to_x_real_ip(self):
        req = _Request("10.0.0.5", {"x-forwarded-for": "10.0.0.1, 10.0.0.2",
                                    "x-real-ip": "8.8.4.4"})
        self.assertEqual(CI.client_ip(req), "8.8.4.4")

    def test_all_trusted_and_no_real_ip_falls_back_to_the_oldest_hop(self):
        req = _Request("10.0.0.5", {"x-forwarded-for": "10.0.0.1, 10.0.0.2"})
        self.assertEqual(CI.client_ip(req), "10.0.0.1")

    def test_trusted_peer_with_no_headers_returns_the_peer(self):
        self.assertEqual(CI.client_ip(_Request("10.0.0.5")), "10.0.0.5")

    def test_empty_and_whitespace_hops_are_dropped(self):
        req = _Request("10.0.0.5", {"x-forwarded-for": " , ,8.8.8.8, "})
        self.assertEqual(CI.client_ip(req), "8.8.8.8")

    def test_ipv6_loopback_peer_is_recognised_as_trusted(self):
        req = _Request("[::1]", {"x-forwarded-for": "8.8.8.8"})
        self.assertEqual(CI.client_ip(req), "8.8.8.8")

    def test_result_is_truncated_to_sixty_four_characters(self):
        long_peer = "z" * 200
        self.assertEqual(CI.client_ip(_Request(long_peer)), "z" * 64)

        long_hop = "y" * 200
        req = _Request("10.0.0.5", {"x-forwarded-for": long_hop})
        self.assertEqual(CI.client_ip(req), "y" * 64)

    def test_env_override_can_promote_a_public_proxy_to_trusted(self):
        req = _Request("203.0.113.9", {"x-forwarded-for": "8.8.8.8"})
        with mock.patch.dict(os.environ, {"R20_TRUSTED_PROXIES": "203.0.113.0/24"}):
            self.assertEqual(CI.client_ip(req), "8.8.8.8",
                             "显式信任该代理后，转发头才被采信")

    def test_invalid_peer_string_is_treated_as_untrusted_and_returned_as_is(self):
        self.assertEqual(CI.client_ip(_Request("proxy.internal")), "proxy.internal")


class UserAgentTests(unittest.TestCase):
    def test_user_agent_is_returned_and_truncated(self):
        req = _Request("10.0.0.5", {"user-agent": "Mozilla/5.0"})
        self.assertEqual(CI.user_agent(req), "Mozilla/5.0")
        self.assertEqual(CI.user_agent(_Request("10.0.0.5", {"user-agent": "x" * 500})),
                         "x" * 200)
        self.assertEqual(CI.user_agent(_Request("10.0.0.5"), limit=3), "")

    def test_missing_user_agent_is_empty(self):
        self.assertEqual(CI.user_agent(_Request(None)), "")


if __name__ == "__main__":
    unittest.main()

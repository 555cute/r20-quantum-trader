"""客户端来源解析与按 IP 登录限速的回归测试。

核心安全断言：不可信直连对端伪造 X-Forwarded-For 时，必须拿不到伪造值，
否则按 IP 的登录限速可被一个请求头轻松绕过。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r20_backend import client_ip as cip  # noqa: E402
from r20_backend import login_guard as lg  # noqa: E402


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, host=None, headers=None):
        self.client = _FakeClient(host) if host is not None else None
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


class ClientIpTests(unittest.TestCase):
    def test_spoofed_xff_from_untrusted_peer_is_ignored(self):
        """公网直连却自称来自 8.8.8.8：必须返回真实对端，绝不能信头。"""
        req = _FakeRequest("203.0.113.9", {"X-Forwarded-For": "8.8.8.8", "X-Real-IP": "1.1.1.1"})
        self.assertEqual(cip.client_ip(req), "203.0.113.9")

    def test_trusted_proxy_returns_rightmost_untrusted_hop(self):
        req = _FakeRequest("172.18.0.1", {"X-Forwarded-For": "9.9.9.9, 203.0.113.7, 10.0.0.2"})
        # 10.0.0.2 是内网(可信代理追加)，203.0.113.7 才是真实客户端；最左 9.9.9.9 可能是伪造的
        self.assertEqual(cip.client_ip(req), "203.0.113.7")

    def test_all_private_chain_falls_back_to_leftmost(self):
        req = _FakeRequest("127.0.0.1", {"X-Forwarded-For": "10.1.2.3, 192.168.1.5"})
        self.assertEqual(cip.client_ip(req), "10.1.2.3")

    def test_x_real_ip_used_when_no_xff(self):
        req = _FakeRequest("127.0.0.1", {"X-Real-IP": "203.0.113.42"})
        self.assertEqual(cip.client_ip(req), "203.0.113.42")

    def test_missing_client_information(self):
        self.assertEqual(cip.client_ip(_FakeRequest(None, {})), "unknown")
        self.assertEqual(cip.client_ip(_FakeRequest("", {})), "unknown")

    def test_ipv6_brackets_and_zone_id(self):
        req = _FakeRequest("[2001:db8::1%eth0]", {})
        self.assertEqual(cip.client_ip(req), "2001:db8::1")

    def test_loopback_is_trusted_by_default(self):
        self.assertTrue(cip.is_trusted_ip("127.0.0.1"))
        self.assertTrue(cip.is_trusted_ip("172.18.0.1"))  # Docker 桥网落在 172.16/12
        self.assertFalse(cip.is_trusted_ip("8.8.8.8"))
        self.assertFalse(cip.is_trusted_ip("not-an-ip"))

    def test_user_agent_truncated(self):
        req = _FakeRequest("127.0.0.1", {"User-Agent": "x" * 500})
        self.assertEqual(len(cip.user_agent(req)), 200)


class LoginGuardTests(unittest.TestCase):
    def setUp(self):
        lg._reset_for_tests()

    def tearDown(self):
        lg._reset_for_tests()

    def test_unknown_ip_is_exempt(self):
        for _ in range(lg._MAX_FAILURES + 5):
            lg.note_failure("unknown")
        self.assertEqual(lg.check("unknown"), (True, 0))

    def test_failures_trigger_block_with_retry_after(self):
        ip = "203.0.113.50"
        for _ in range(lg._MAX_FAILURES):
            lg.note_failure(ip)
        allowed, retry = lg.check(ip)
        self.assertFalse(allowed, "达到失败阈值后应按 IP 封锁")
        self.assertGreater(retry, 0)
        self.assertLessEqual(retry, lg._BLOCK_SECONDS + 1)

    def test_attempts_trigger_flood_block(self):
        ip = "203.0.113.51"
        for _ in range(lg._MAX_ATTEMPTS):
            lg.note_attempt(ip)
        self.assertFalse(lg.check(ip)[0], "窗口内总尝试超限应判为洪泛")

    def test_below_threshold_allows(self):
        ip = "203.0.113.52"
        for _ in range(lg._MAX_FAILURES - 1):
            lg.note_failure(ip)
        self.assertTrue(lg.check(ip)[0])

    def test_isolated_ips_do_not_interfere(self):
        for _ in range(lg._MAX_FAILURES):
            lg.note_failure("203.0.113.53")
        self.assertTrue(lg.check("203.0.113.54")[0], "封禁不应波及其他来源")

    def test_disabled_switch(self):
        import os
        os.environ["R20_LOGIN_RATE_LIMIT"] = "0"
        try:
            ip = "203.0.113.55"
            for _ in range(lg._MAX_FAILURES * 3):
                lg.note_failure(ip)
            self.assertTrue(lg.check(ip)[0], "关闭开关后不得再限速")
        finally:
            del os.environ["R20_LOGIN_RATE_LIMIT"]

    def test_stats_shape(self):
        s = lg.stats()
        for key in ("enabled", "tracked_ips", "blocked_ips", "window_seconds",
                    "max_attempts", "max_failures", "block_seconds"):
            self.assertIn(key, s)


if __name__ == "__main__":
    unittest.main()

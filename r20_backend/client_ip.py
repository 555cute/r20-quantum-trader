"""客户端真实来源解析：在反向代理之后正确取到 IP，且绝不信任可伪造的请求头。

设计要点
--------
`X-Forwarded-For` 是客户端可自填的头。若无条件取最左值，攻击者只要带上
`X-Forwarded-For: 8.8.8.8` 就能伪造来源、绕过按 IP 的登录限速。

因此这里的规则是：
1. 先看**直连 TCP 对端**是否在可信代理网段内（默认仅私网/回环，含 Docker 桥网）。
2. 直连不可信 -> 直接采用直连地址，**完全忽略** XFF / X-Real-IP。
3. 直连可信 -> 从 XFF **右侧向左**找第一个不可信地址作为真实客户端
   （右侧条目由我们自己控制的代理追加，左侧条目可被客户端伪造）。

可信代理网段可用 R20_TRUSTED_PROXIES（逗号分隔 CIDR）覆盖。
"""
from __future__ import annotations

import ipaddress
import os
from typing import Any

_DEFAULT_TRUSTED = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"
_MAX_LEN = 64


def _trusted_networks() -> list[Any]:
    raw = os.getenv("R20_TRUSTED_PROXIES", "").strip() or _DEFAULT_TRUSTED
    nets: list[Any] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            nets.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            continue
    return nets


def _clean_host(value: str) -> str:
    """去掉 IPv6 方括号与 zone id，返回可比较的地址字符串。"""
    host = str(value or "").strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if "%" in host:  # fe80::1%eth0
        host = host.split("%", 1)[0]
    return host


def is_trusted_ip(value: str, networks: list[Any] | None = None) -> bool:
    host = _clean_host(value)
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    for net in (networks if networks is not None else _trusted_networks()):
        if addr in net:
            return True
    return False


def client_ip(request: Any) -> str:
    """解析真实客户端 IP；无法判定时返回 'unknown'（绝不回退成可伪造的头值）。"""
    peer = _clean_host(getattr(getattr(request, "client", None), "host", "") or "")
    nets = _trusted_networks()

    # 直连对端不是我们控制的代理：头一律不信
    if not peer or not is_trusted_ip(peer, nets):
        return (peer or "unknown")[:_MAX_LEN]

    forwarded = str(request.headers.get("x-forwarded-for", "") or "")
    hops = [_clean_host(h) for h in forwarded.split(",") if h.strip()]
    for hop in reversed(hops):  # 右侧起：由可信代理追加，不可被客户端伪造
        if not is_trusted_ip(hop, nets):
            return hop[:_MAX_LEN]

    real_ip = _clean_host(str(request.headers.get("x-real-ip", "") or ""))
    if real_ip and not is_trusted_ip(real_ip, nets):
        return real_ip[:_MAX_LEN]

    return (hops[0] if hops else peer)[:_MAX_LEN]


def user_agent(request: Any, limit: int = 200) -> str:
    return str(request.headers.get("user-agent", "") or "")[:limit]

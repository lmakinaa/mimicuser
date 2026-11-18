"""Egress policy for the research browser.

The synthetic customer is untrusted input driving a real browser on a host that
also runs databases and an identity provider. Every destination the browser
reaches — typed, redirected to, or requested by page script — must resolve only
to globally routable addresses.

A hostname blocklist is not enough: a public name can resolve to 10.0.0.5, a
compose service name to 172.18.0.3, and `http://2130706433/` is loopback. So the
decision is made on resolved addresses, never on how the host is spelled.

This is one layer, not the whole boundary. Chromium resolves names again after
we check them, so a rebinding attacker can still race us; network isolation of
the browser container is the layer that closes that.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

Resolve = Callable[[str], Awaitable[list[str]]]
RouteHandler = Callable[[Any, Any], Awaitable[None]]

ABORT_CODE = "blockedbyclient"

_NAVIGABLE_SCHEMES = frozenset({"http", "https"})
# These never leave the browser process, so there is nothing to guard — and
# refusing them breaks ordinary pages (inline images, worker blobs).
_IN_BROWSER_SCHEMES = frozenset({"data", "blob", "about"})
# IPv6 forms that carry an IPv4 address inside them. Python's `is_global` reads
# these as public, so `64:ff9b::a9fe:a9fe` (the cloud metadata service behind a
# NAT64 prefix) would sail through a naive check.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIXTOFOUR = ipaddress.ip_network("2002::/16")
_V4_COMPATIBLE = ipaddress.ip_network("::/96")
# Refused outright: their embedded address is obfuscated (Teredo) or the layout
# varies with the prefix length (local-use NAT64).
_ALWAYS_REFUSED = (
    ipaddress.ip_network("2001::/32"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)


class BlockedDestination(RuntimeError):
    """The destination is not a public internet address."""


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address this IPv6 address carries, if it carries one."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    packed = ip.packed
    if ip in _NAT64 or ip in _V4_COMPATIBLE:
        return ipaddress.IPv4Address(packed[12:16])
    if ip in _SIXTOFOUR:
        return ipaddress.IPv4Address(packed[2:6])
    return None


def is_blocked_address(address: str) -> bool:
    """True unless `address` is a globally routable unicast address."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        if any(ip in net for net in _ALWAYS_REFUSED):
            return True
        # `::ffff:10.0.0.1` is 10.0.0.1 on the wire; judge the IPv4 it carries.
        embedded = _embedded_ipv4(ip)
        if embedded is not None:
            ip = embedded
    return (not ip.is_global) or ip.is_multicast


async def _system_resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return []
    return sorted({info[4][0] for info in infos})


def _as_literal(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


async def _host_is_public(host: str, resolve: Resolve) -> bool:
    literal = _as_literal(host)
    addresses = [literal] if literal else await resolve(host)
    # No answer is a refusal, and one private record refuses the whole host:
    # a split answer is what DNS rebinding looks like.
    return bool(addresses) and not any(is_blocked_address(a) for a in addresses)


async def check_destination(url: str, *, resolve: Resolve | None = None) -> None:
    """Raise `BlockedDestination` unless `url` is http(s) to a public host."""
    parsed = urlparse(url)
    if (parsed.scheme or "").lower() not in _NAVIGABLE_SCHEMES:
        raise BlockedDestination(f"only http(s) addresses can be opened, not {url!r}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise BlockedDestination(f"that address has no host: {url!r}")
    if not await _host_is_public(host, resolve or _system_resolve):
        raise BlockedDestination(f"that address is not reachable from here: {url!r}")


def websocket_guard(resolve: Resolve | None = None):
    """Playwright routes WebSockets separately; without this they skip the policy."""
    lookup = resolve or _system_resolve

    async def guard(route: Any) -> None:
        url = route.url or ""
        probe = re.sub(r"^ws", "http", url, count=1) if url.lower().startswith("ws") else url
        try:
            await check_destination(probe, resolve=lookup)
        except BlockedDestination:
            await route.close(code=1008, reason="destination not permitted")
            return
        route.connect_to_server()

    return guard


def route_guard(resolve: Resolve | None = None) -> RouteHandler:
    """A Playwright route handler enforcing the policy on every request.

    Navigation checks alone miss redirects, link clicks and page `fetch()`;
    installing this on the browser context covers all three.
    """
    lookup = resolve or _system_resolve

    async def allowed(url: str) -> bool:
        # Deliberately uncached. Caching a host's verdict turns DNS rebinding
        # from a race an attacker must win into one they always win: the first
        # answer is public, the record then flips to a private address, and
        # every later request is waved through without resolving again.
        try:
            await check_destination(url, resolve=lookup)
            return True
        except BlockedDestination:
            return False

    async def guard(route: Any, request: Any) -> None:
        scheme = (urlparse(request.url).scheme or "").lower()
        if scheme in _IN_BROWSER_SCHEMES:
            await route.continue_()
            return
        if scheme not in _NAVIGABLE_SCHEMES or not await allowed(request.url):
            await route.abort(ABORT_CODE)
            return
        await route.continue_()

    return guard

"""The browser's egress policy.

The synthetic customer is untrusted input steering a real browser on a host
that also runs databases and an identity provider. Every destination — typed,
redirected to, or requested by page script — must resolve only to globally
routable addresses, or the product under study becomes a proxy into our network.
"""
from __future__ import annotations

import pytest

from tools.browser.netguard import (
    BlockedDestination,
    check_destination,
    is_blocked_address,
    route_guard,
    websocket_guard,
)

pytestmark = pytest.mark.unit


def resolver(mapping: dict[str, list[str]]):
    """A DNS stand-in: unknown hosts resolve to nothing."""

    async def resolve(host: str) -> list[str]:
        return mapping.get(host, [])

    return resolve


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeRoute:
    def __init__(self) -> None:
        self.aborted: str | None = None
        self.continued = False

    async def abort(self, error_code: str | None = None) -> None:
        self.aborted = error_code or "failed"

    async def continue_(self) -> None:
        self.continued = True


# -- address policy ----------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "::1", "0.0.0.0", "::",
        "10.0.0.5", "172.16.3.4", "172.31.255.255", "192.168.1.10",
        "169.254.169.254", "fe80::1", "100.64.0.1", "fd00::1",
        "::ffff:127.0.0.1", "::ffff:10.0.0.1", "224.0.0.1",
        # IPv6 shapes carrying an IPv4 address that Python calls global.
        "::127.0.0.1", "64:ff9b::a9fe:a9fe", "64:ff9b::10.0.0.1",
        "2002:7f00:1::", "2002:a00:1::", "2001::1", "64:ff9b:1::1",
    ],
)
def test_non_global_addresses_are_blocked(ip: str) -> None:
    assert is_blocked_address(ip) is True


@pytest.mark.parametrize("ip", ["93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"])
def test_global_addresses_are_allowed(ip: str) -> None:
    assert is_blocked_address(ip) is False


def test_unparseable_address_is_blocked() -> None:
    assert is_blocked_address("not-an-ip") is True


# -- navigation --------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "javascript:alert(1)", "ftp://example.com/", "chrome://settings"],
)
async def test_non_http_schemes_are_refused(url: str) -> None:
    with pytest.raises(BlockedDestination):
        await check_destination(url, resolve=resolver({}))


async def test_url_without_a_host_is_refused() -> None:
    with pytest.raises(BlockedDestination):
        await check_destination("http:///path", resolve=resolver({}))


async def test_public_host_is_allowed() -> None:
    await check_destination(
        "https://valsea.ai/pricing", resolve=resolver({"valsea.ai": ["76.76.21.21"]})
    )


async def test_host_resolving_to_a_private_address_is_refused() -> None:
    with pytest.raises(BlockedDestination):
        await check_destination(
            "https://internal.example.com/",
            resolve=resolver({"internal.example.com": ["10.1.2.3"]}),
        )


async def test_one_private_record_refuses_the_whole_host() -> None:
    # A split answer is exactly what DNS rebinding looks like.
    with pytest.raises(BlockedDestination):
        await check_destination(
            "https://mixed.example.com/",
            resolve=resolver({"mixed.example.com": ["93.184.216.34", "127.0.0.1"]}),
        )


async def test_unresolvable_host_is_refused() -> None:
    with pytest.raises(BlockedDestination):
        await check_destination("https://nope.invalid/", resolve=resolver({}))


async def test_compose_service_name_is_refused() -> None:
    # Every other stack on the VPS is one hostname away.
    with pytest.raises(BlockedDestination):
        await check_destination(
            "http://postgres:5432/", resolve=resolver({"postgres": ["172.18.0.3"]})
        )


async def test_integer_encoded_loopback_is_refused() -> None:
    with pytest.raises(BlockedDestination):
        await check_destination(
            "http://2130706433/", resolve=resolver({"2130706433": ["127.0.0.1"]})
        )


async def test_literal_ip_hosts_are_judged_without_dns() -> None:
    with pytest.raises(BlockedDestination):
        await check_destination("http://[::ffff:192.168.0.1]/", resolve=resolver({}))
    await check_destination("http://1.1.1.1/", resolve=resolver({}))


# -- every request the page makes --------------------------------------------


async def test_route_guard_aborts_requests_to_private_destinations() -> None:
    route = FakeRoute()
    guard = route_guard(resolve=resolver({"metadata.internal": ["169.254.169.254"]}))

    await guard(route, FakeRequest("http://metadata.internal/latest/meta-data"))

    assert route.aborted == "blockedbyclient"
    assert route.continued is False


async def test_route_guard_lets_public_requests_through() -> None:
    route = FakeRoute()
    guard = route_guard(resolve=resolver({"cdn.example.com": ["93.184.216.34"]}))

    await guard(route, FakeRequest("https://cdn.example.com/app.js"))

    assert route.continued is True
    assert route.aborted is None


@pytest.mark.parametrize("url", ["data:image/png;base64,AAAA", "blob:https://valsea.ai/1234"])
async def test_route_guard_allows_in_browser_schemes(url: str) -> None:
    # data: and blob: never leave the browser; refusing them breaks real pages.
    route = FakeRoute()

    await route_guard(resolve=resolver({}))(route, FakeRequest(url))

    assert route.continued is True


async def test_route_guard_resolves_every_request_rather_than_caching() -> None:
    # A cached verdict is how DNS rebinding stops being a race.
    answers = {"flip.example.com": ["93.184.216.34"]}
    lookups: list[str] = []

    async def resolve(host: str) -> list[str]:
        lookups.append(host)
        return answers.get(host, [])

    guard = route_guard(resolve=resolve)
    first = FakeRoute()
    await guard(first, FakeRequest("https://flip.example.com/a"))

    answers["flip.example.com"] = ["169.254.169.254"]
    second = FakeRoute()
    await guard(second, FakeRequest("https://flip.example.com/b"))

    assert first.continued is True
    assert second.aborted == "blockedbyclient"
    assert lookups == ["flip.example.com", "flip.example.com"]


async def test_websocket_to_a_private_address_is_closed() -> None:
    class FakeWebSocketRoute:
        def __init__(self, url: str) -> None:
            self.url = url
            self.closed: tuple[int, str] | None = None
            self.connected = False

        async def close(self, code: int = 1000, reason: str = "") -> None:
            self.closed = (code, reason)

        def connect_to_server(self) -> None:
            self.connected = True

    blocked = FakeWebSocketRoute("ws://169.254.169.254/")
    await websocket_guard(resolve=resolver({}))(blocked)
    assert blocked.closed is not None and blocked.connected is False

    allowed = FakeWebSocketRoute("wss://echo.example.com/socket")
    await websocket_guard(resolve=resolver({"echo.example.com": ["93.184.216.34"]}))(allowed)
    assert allowed.connected is True and allowed.closed is None


async def test_route_guard_aborts_file_scheme_subresources() -> None:
    route = FakeRoute()

    await route_guard(resolve=resolver({}))(route, FakeRequest("file:///etc/passwd"))

    assert route.aborted == "blockedbyclient"

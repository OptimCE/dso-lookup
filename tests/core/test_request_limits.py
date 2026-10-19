"""Body-size cap and timeout of the request-limits middleware.

The dso-lookup API is GET-only and reads no body, so every route shares the
small ``MAX_BODY_BYTES`` cap, and a lookup (a dictionary read in memory) gets a
short timeout well under any caller's patience.
"""

from __future__ import annotations

from types import SimpleNamespace

from httpx import AsyncClient

from core.middleware import request_limits


def _request(method: str, path: str) -> SimpleNamespace:
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def test_cap_is_uniform_across_routes():
    for method, path in (
        ("GET", "/dso/resolve"),
        ("GET", "/dso/resolve-address"),
        ("GET", "/dso/operators"),
        ("GET", "/dso/ean/541448200000000008/check"),
        ("GET", "/health/readiness"),
    ):
        assert request_limits._max_body_for(_request(method, path)) == request_limits.MAX_BODY_BYTES


def test_the_limits_suit_a_get_only_in_memory_api():
    assert request_limits.MAX_BODY_BYTES == 16 * 1024
    # crm-backend gives up after 1 s (DSO_LOOKUP_TIMEOUT_MS) and KrakenD after 3 s.
    assert request_limits.TIMEOUT_SECONDS <= 5


async def test_an_oversized_body_is_refused_before_routing(client: AsyncClient):
    response = await client.request(
        "GET", "/dso/meta", content=b"x" * (request_limits.MAX_BODY_BYTES + 1)
    )
    assert response.status_code == 413

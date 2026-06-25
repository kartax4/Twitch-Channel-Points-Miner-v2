from __future__ import annotations

import httpx
import pytest

from twitch_miner.core.constants import GqlOperations
from twitch_miner.core.exceptions import RateLimitError
from twitch_miner.core.http import AsyncHttpClient


def _client_with(handler: httpx.MockTransport) -> AsyncHttpClient:
    raw = httpx.AsyncClient(transport=handler)
    return AsyncHttpClient(client=raw, max_attempts=3, initial_backoff=0.0, max_backoff=0.0)


async def test_retries_on_500_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    client = _client_with(httpx.MockTransport(handler))
    response = await client.get("https://example.com")
    assert response.status_code == 200
    assert calls["n"] == 2
    await client.aclose()


async def test_rate_limit_exhausts_and_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    client = _client_with(httpx.MockTransport(handler))
    with pytest.raises(RateLimitError):
        await client.get("https://example.com")
    await client.aclose()


def test_gql_operation_build_merges_variables() -> None:
    body = GqlOperations.GetIDFromLogin.build({"login": "abc"})
    assert body["operationName"] == "GetIDFromLogin"
    assert body["variables"]["login"] == "abc"
    assert body["extensions"]["persistedQuery"]["version"] == 1

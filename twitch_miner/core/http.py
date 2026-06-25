"""Async HTTP client factory with resilient retries.

Provides:

* :func:`build_async_client` - constructs a pre-configured
  :class:`httpx.AsyncClient` with browser-like headers and sane timeouts.
* :class:`AsyncHttpClient` - a thin wrapper that performs requests with
  tenacity-driven retries (exponential backoff + jitter) on transient network
  errors and on HTTP ``429`` / ``5xx`` responses.

The jitter is important for stealth: identical, perfectly-timed retries are an
easy bot tell, so randomised backoff makes traffic look more human while also
spreading load during Twitch outages.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from typing import Any, Final

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from twitch_miner.core.constants import BROWSER_HEADERS, DEFAULT_USER_AGENT
from twitch_miner.core.exceptions import RateLimitError
from twitch_miner.core.logger import logger

# Status codes that are safe (and worthwhile) to retry.
RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})


class _RetryableServerError(httpx.HTTPError):
    """Internal marker for a retryable 5xx response."""


# Exceptions that indicate a transient, retryable failure (hoisted to module
# scope so it is built once rather than per-request).
_RETRYABLE_EXC: Final = (
    httpx.TransportError,  # covers ConnectError, ReadError, TimeoutException, ...
    RateLimitError,
    _RetryableServerError,
)

DEFAULT_TIMEOUT: Final = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0)
DEFAULT_LIMITS: Final = httpx.Limits(max_connections=50, max_keepalive_connections=20)


def build_async_client(
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    http2: bool = True,
) -> httpx.AsyncClient:
    """Create a configured :class:`httpx.AsyncClient`.

    Args:
        user_agent: User-Agent header presented to servers.
        headers: Extra default headers merged over the browser defaults.
        timeout: Per-request timeout configuration.
        http2: Whether to negotiate HTTP/2 (matches modern browsers).
    """

    merged_headers: dict[str, str] = {
        **BROWSER_HEADERS,
        "User-Agent": user_agent,
        **(headers or {}),
    }
    return httpx.AsyncClient(
        headers=merged_headers,
        timeout=timeout,
        limits=DEFAULT_LIMITS,
        http2=http2,
        follow_redirects=True,
    )


def _parse_retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _make_wait(initial: float, maximum: float) -> Callable[[RetryCallState], float]:
    """Build a wait strategy that honours a 429 ``Retry-After`` hint.

    Falls back to exponential backoff with jitter, but never waits less than the
    server-provided ``Retry-After`` when present, so we don't hammer a
    rate-limited endpoint.
    """

    base = wait_exponential_jitter(initial=initial, max=maximum)

    def _wait(retry_state: RetryCallState) -> float:
        backoff = base(retry_state)
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(exc, RateLimitError) and exc.retry_after:
            return max(float(exc.retry_after), backoff)
        return backoff

    return _wait


def _log_before_sleep(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    sleep = getattr(retry_state.next_action, "sleep", 0.0)
    logger.warning(
        "HTTP request failed (attempt {n}); retrying in {s:.1f}s: {err}",
        n=retry_state.attempt_number,
        s=sleep,
        err=exc,
    )


class AsyncHttpClient:
    """Resilient async HTTP client wrapper.

    The wrapper owns an :class:`httpx.AsyncClient` and adds retry semantics. It
    is an async context manager and also exposes :meth:`aclose` for explicit
    lifecycle management within a ``TaskGroup``.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        max_attempts: int = 5,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ) -> None:
        self._client = client or build_async_client(user_agent=user_agent)
        self._max_attempts = max_attempts
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff

    @property
    def raw(self) -> httpx.AsyncClient:
        """Access the underlying httpx client (no retry wrapping)."""

        return self._client

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Perform an HTTP request with retry on transient failures.

        Retries cover network errors, HTTP 429 (rate limit), and HTTP 5xx. When
        Twitch sends ``Retry-After`` on a 429, that hint is honoured as the
        minimum wait for the next attempt.

        Raises:
            RateLimitError: If rate-limited and retries are exhausted.
            httpx.HTTPError: For other unrecoverable transport errors.
        """

        retrying = AsyncRetrying(
            reraise=True,
            stop=stop_after_attempt(self._max_attempts),
            wait=_make_wait(self._initial_backoff, self._max_backoff),
            retry=retry_if_exception_type(_RETRYABLE_EXC),
            before_sleep=_log_before_sleep,
        )

        async for attempt in retrying:
            with attempt:
                response = await self._client.request(method, url, **kwargs)
                self._raise_for_retryable(response)
                return response

        # AsyncRetrying with reraise=True always either returns or raises above.
        raise RuntimeError("unreachable: retry loop exited without result")

    @staticmethod
    def _raise_for_retryable(response: httpx.Response) -> None:
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            retry_after = _parse_retry_after(response)
            raise RateLimitError(
                f"Rate limited on {response.request.url}",
                retry_after=retry_after,
            )
        if response.status_code in RETRYABLE_STATUS:
            raise _RetryableServerError(
                f"Server error {response.status_code} on {response.request.url}"
            )

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Convenience wrapper for ``request('GET', ...)``."""

        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """Convenience wrapper for ``request('POST', ...)``."""

        return await self.request("POST", url, **kwargs)

    async def head(self, url: str, **kwargs: Any) -> httpx.Response:
        """Convenience wrapper for ``request('HEAD', ...)``."""

        return await self.request("HEAD", url, **kwargs)

    async def aclose(self) -> None:
        """Close the underlying client and release connections."""

        await self._client.aclose()

    async def __aenter__(self) -> AsyncHttpClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        await self.aclose()


__all__ = [
    "AsyncHttpClient",
    "RETRYABLE_STATUS",
    "build_async_client",
]

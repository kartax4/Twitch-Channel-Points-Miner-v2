"""Custom exception hierarchy for the miner.

All application errors derive from :class:`MinerError`, so callers can catch the
whole family with a single ``except`` while still being able to discriminate on
specific failure modes.

Hierarchy::

    MinerError
    +-- ConfigError
    +-- TwitchError
        +-- TwitchAuthError
        |   +-- TokenRefreshError
        +-- GqlError
        |   +-- GqlQueryError
        +-- RateLimitError
        +-- WebSocketError
"""

from __future__ import annotations


class MinerError(Exception):
    """Base class for every error raised by the application."""


class ConfigError(MinerError):
    """Raised when configuration is missing, malformed, or invalid."""


class TwitchError(MinerError):
    """Base class for all Twitch-interaction errors."""


class TwitchAuthError(TwitchError):
    """Authentication failed or the session/token is no longer valid."""


class TokenRefreshError(TwitchAuthError):
    """A token refresh attempt failed and re-login is required."""


class GqlError(TwitchError):
    """Base class for GraphQL transport/processing errors."""


class GqlQueryError(GqlError):
    """A GraphQL response contained an ``errors`` payload or unexpected shape.

    Args:
        message: Human-readable description.
        operation: The GQL operation name that failed, if known.
        errors: The raw ``errors`` list returned by Twitch, if any.
    """

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        errors: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.errors = errors or []


class RateLimitError(TwitchError):
    """Raised when Twitch responds with HTTP 429 (Too Many Requests).

    Args:
        message: Human-readable description.
        retry_after: Suggested wait time in seconds, parsed from the
            ``Retry-After`` header when present.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# Backwards-friendly alias matching the originally requested name.
RateLimitExceeded = RateLimitError


class WebSocketError(TwitchError):
    """Raised on unrecoverable PubSub WebSocket failures."""


__all__ = [
    "ConfigError",
    "GqlError",
    "GqlQueryError",
    "MinerError",
    "RateLimitError",
    "RateLimitExceeded",
    "TokenRefreshError",
    "TwitchAuthError",
    "TwitchError",
    "WebSocketError",
]

"""Identifier helpers used to mimic the Twitch web client.

The web client attaches a stable ``X-Device-Id`` and a per-session
``Client-Session-Id`` to its requests. Reproducing this shape (and keeping the
device id stable across restarts for a given account) helps the miner look like
a normal returning browser session.
"""

from __future__ import annotations

import hashlib
import secrets


def random_hex(length: int = 32) -> str:
    """Return a random lowercase hex string of ``length`` characters."""

    return secrets.token_hex(length // 2 + 1)[:length]


def random_session_id() -> str:
    """Generate a fresh client-session id (regenerated each process start)."""

    return random_hex(16)


def stable_device_id(seed: str) -> str:
    """Derive a deterministic 32-char device id from a stable ``seed``.

    Using the account username as the seed keeps the device id constant across
    restarts, so Twitch sees a consistent device rather than a new one each run.
    """

    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:32]


__all__ = ["random_hex", "random_session_id", "stable_device_id"]

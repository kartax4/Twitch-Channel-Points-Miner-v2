"""Live registry of tracked streamers.

The registry is the shared, mutable source of truth for which channels the miner
is currently tracking. It is safe for concurrent access and supports hot
add/remove so the drops/streamer config can change at runtime without a
restart. Mutations fire optional async callbacks so dependent subsystems (e.g.
the PubSub client) can subscribe/unsubscribe in response.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from twitch_miner.core.logger import logger
from twitch_miner.models.streamer import Streamer

StreamerCallback = Callable[[Streamer], Awaitable[None]]


class ChannelRegistry:
    """Thread/coroutine-safe collection of :class:`Streamer` objects."""

    def __init__(self) -> None:
        self._streamers: dict[str, Streamer] = {}
        self._by_channel_id: dict[str, Streamer] = {}
        self._lock = asyncio.Lock()
        self._on_add: list[StreamerCallback] = []
        self._on_remove: list[StreamerCallback] = []

    def on_add(self, callback: StreamerCallback) -> None:
        """Register a coroutine invoked after a streamer is added."""

        self._on_add.append(callback)

    def on_remove(self, callback: StreamerCallback) -> None:
        """Register a coroutine invoked after a streamer is removed."""

        self._on_remove.append(callback)

    async def add(self, streamer: Streamer) -> bool:
        """Add a streamer. Returns ``False`` if already present."""

        async with self._lock:
            if streamer.username in self._streamers:
                return False
            self._streamers[streamer.username] = streamer
            if streamer.channel_id:
                self._by_channel_id[streamer.channel_id] = streamer
        logger.info("Tracking channel {}", streamer.username)
        await self._fire(self._on_add, streamer)
        return True

    async def remove(self, username: str) -> Streamer | None:
        """Remove a streamer by login. Returns the removed object or ``None``."""

        async with self._lock:
            streamer = self._streamers.pop(username, None)
            if streamer and streamer.channel_id:
                self._by_channel_id.pop(streamer.channel_id, None)
        if streamer:
            logger.info("Stopped tracking channel {}", username)
            await self._fire(self._on_remove, streamer)
        return streamer

    def bind_channel_id(self, streamer: Streamer) -> None:
        """Index a streamer by its resolved channel id."""

        if streamer.channel_id:
            self._by_channel_id[streamer.channel_id] = streamer

    def get(self, username: str) -> Streamer | None:
        return self._streamers.get(username)

    def get_by_channel_id(self, channel_id: str) -> Streamer | None:
        return self._by_channel_id.get(channel_id)

    def all(self) -> list[Streamer]:
        return list(self._streamers.values())

    def online(self) -> list[Streamer]:
        return [s for s in self._streamers.values() if s.is_online]

    def usernames(self) -> set[str]:
        return set(self._streamers)

    def __len__(self) -> int:
        return len(self._streamers)

    @staticmethod
    async def _fire(callbacks: list[StreamerCallback], streamer: Streamer) -> None:
        for callback in callbacks:
            try:
                await callback(streamer)
            except Exception as exc:  # pragma: no cover - callback isolation
                logger.warning("Registry callback failed for {}: {}", streamer.username, exc)


__all__ = ["ChannelRegistry", "StreamerCallback"]

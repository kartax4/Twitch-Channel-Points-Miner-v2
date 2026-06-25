"""Bridge between decoded PubSub events and the domain services.

Implements :class:`~twitch_miner.services.pubsub.PubSubListener`, resolving the
``channel_id`` carried by each event to the corresponding :class:`Streamer` in
the registry and delegating to the points/watch services.
"""

from __future__ import annotations

import asyncio

from twitch_miner.core.logger import logger
from twitch_miner.models.events import Event
from twitch_miner.models.streamer import Streamer
from twitch_miner.services.points import PointsService
from twitch_miner.services.registry import ChannelRegistry
from twitch_miner.services.watch import WatchService


class EventListener:
    """Routes PubSub events to the appropriate streamer/service."""

    def __init__(
        self,
        *,
        registry: ChannelRegistry,
        points: PointsService,
        watch: WatchService,
    ) -> None:
        self._registry = registry
        self._points = points
        self._watch = watch

    async def on_points_earned(
        self, channel_id: str, *, balance: int, gained: int, reason: str
    ) -> None:
        streamer = self._registry.get_by_channel_id(channel_id)
        if streamer:
            await self._points.on_points_earned(
                streamer, balance=balance, gained=gained, reason=reason
            )

    async def on_points_spent(self, channel_id: str, *, balance: int) -> None:
        streamer = self._registry.get_by_channel_id(channel_id)
        if streamer:
            await self._points.on_points_spent(streamer, balance=balance)

    async def on_claim_available(self, channel_id: str, claim_id: str) -> None:
        streamer = self._registry.get_by_channel_id(channel_id)
        if streamer and claim_id:
            await self._points.claim_bonus(streamer, claim_id)

    async def on_stream_up(self, channel_id: str) -> None:
        streamer = self._registry.get_by_channel_id(channel_id)
        if streamer:
            streamer.set_online()
            logger.info(
                "{} went online", streamer.username, extra={"event": Event.STREAMER_ONLINE}
            )
            await self._watch.refresh_stream(streamer)

    async def on_stream_down(self, channel_id: str) -> None:
        streamer = self._registry.get_by_channel_id(channel_id)
        if streamer and streamer.is_online:
            streamer.set_offline()
            logger.info(
                "{} went offline", streamer.username, extra={"event": Event.STREAMER_OFFLINE}
            )

    async def on_viewcount(self, channel_id: str, viewers: int) -> None:
        streamer = self._registry.get_by_channel_id(channel_id)
        if streamer is None:
            return
        streamer.stream.viewers_count = viewers
        if streamer.is_online and not streamer.stream.broadcast_id:
            await self._watch.refresh_stream(streamer)

    async def on_raid(self, channel_id: str, raid_id: str) -> None:
        streamer = self._registry.get_by_channel_id(channel_id)
        if streamer:
            await self._points.join_raid(streamer, raid_id)

    async def on_moment(self, channel_id: str, moment_id: str) -> None:
        streamer = self._registry.get_by_channel_id(channel_id)
        if streamer:
            await self._points.claim_moment(streamer, moment_id)


async def initial_online_check(
    registry: ChannelRegistry, watch: WatchService
) -> None:
    """Poll initial online state for all registered streamers concurrently."""

    async def check(streamer: Streamer) -> None:
        try:
            await watch.refresh_stream(streamer)
            if streamer.stream.broadcast_id:
                streamer.set_online()
        except Exception as exc:  # pragma: no cover - resilience
            logger.debug("Initial online check failed for {}: {}", streamer.username, exc)

    await asyncio.gather(*(check(s) for s in registry.all()))


__all__ = ["EventListener", "initial_online_check"]

"""Points service: channel-points context refresh, bonus/moment claiming.

Most points activity is event-driven (the PubSub client calls into this service
when a bonus becomes claimable or points are earned). The periodic ``run`` loop
provides a safety net that re-reads the channel-points context for online
channels, catching anything missed between WebSocket events.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from twitch_miner.core.api import TwitchApi
from twitch_miner.core.logger import logger
from twitch_miner.models.events import Event
from twitch_miner.models.streamer import Streamer
from twitch_miner.services.registry import ChannelRegistry


class PointsRecorder(Protocol):
    """Minimal interface the points service needs from the analytics layer."""

    async def record(self, streamer: Streamer, label: str) -> None:
        """Persist a point-balance datapoint for ``streamer``."""


class PointsService:
    """Claims channel-point bonuses/moments and tracks balance changes."""

    def __init__(
        self,
        *,
        api: TwitchApi,
        registry: ChannelRegistry,
        recorder: PointsRecorder | None = None,
        refresh_interval: float = 1800.0,
    ) -> None:
        self._api = api
        self._registry = registry
        self._recorder = recorder
        self._refresh_interval = refresh_interval

    async def run(self) -> None:
        """Periodically reconcile channel-points context for online channels."""

        logger.info("Points service started")
        while True:
            await asyncio.sleep(self._refresh_interval)
            for streamer in self._registry.online():
                try:
                    await self.refresh_context(streamer)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover - resilience
                    logger.debug("Context refresh failed for {}: {}", streamer.username, exc)

    async def refresh_context(self, streamer: Streamer) -> None:
        """Read channel-points context; auto-claim a pending bonus if present."""

        context = await self._api.get_channel_points_context(streamer.username)
        streamer.channel_points = int(context.get("balance", streamer.channel_points))
        claim_id = context.get("claim_id")
        if claim_id:
            await self.claim_bonus(streamer, claim_id)

    async def claim_bonus(self, streamer: Streamer, claim_id: str) -> bool:
        """Claim the periodic channel-points bonus chest."""

        if not streamer.channel_id:
            return False
        ok = await self._api.claim_community_points(
            channel_id=streamer.channel_id, claim_id=claim_id
        )
        if ok:
            logger.info(
                "Claimed bonus for {}", streamer.username, extra={"event": Event.BONUS_CLAIM}
            )
            await self._record(streamer, "Claim")
        return ok

    async def claim_moment(self, streamer: Streamer, moment_id: str) -> bool:
        """Claim a community moment."""

        ok = await self._api.claim_moment(moment_id)
        if ok:
            logger.info(
                "Claimed moment for {}", streamer.username, extra={"event": Event.MOMENT_CLAIM}
            )
        return ok

    async def join_raid(self, streamer: Streamer, raid_id: str) -> bool:
        """Join a raid to collect raid points."""

        if not streamer.settings.follow_raid:
            return False
        ok = await self._api.join_raid(raid_id)
        if ok:
            logger.info(
                "Joined raid for {}", streamer.username, extra={"event": Event.JOIN_RAID}
            )
        return ok

    async def on_points_earned(
        self, streamer: Streamer, *, balance: int, gained: int, reason: str
    ) -> None:
        """Handle a ``points-earned`` PubSub event."""

        streamer.channel_points = balance
        streamer.update_history(reason, gained)
        logger.info(
            "+{} points for {} ({}) -> {}",
            gained,
            streamer.username,
            reason,
            balance,
            extra={"event": Event.GAIN_FOR_WATCH},
        )
        await self._record(streamer, reason)

    async def on_points_spent(self, streamer: Streamer, *, balance: int) -> None:
        """Handle a ``points-spent`` PubSub event."""

        streamer.channel_points = balance
        await self._record(streamer, "Spent")

    async def _record(self, streamer: Streamer, label: str) -> None:
        if self._recorder is not None:
            try:
                await self._recorder.record(streamer, label)
            except Exception as exc:  # pragma: no cover - analytics is non-critical
                logger.debug("Analytics record failed for {}: {}", streamer.username, exc)


__all__ = ["PointsRecorder", "PointsService"]

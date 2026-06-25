"""Watch service: simulates a viewer to accrue watch-time and drop progress.

Per cycle the service:

1. Refreshes stream metadata for online channels.
2. Ranks online channels by the configured :class:`Priority` order and selects
   up to ``max_concurrent`` to "watch" (Twitch only credits a couple at once).
3. For each selected channel sends a *minute-watched* telemetry event to the
   channel's spade endpoint (with a best-effort HLS segment HEAD to mirror real
   player behaviour).
"""

from __future__ import annotations

import asyncio
import random

from twitch_miner.config.models import WatchConfig
from twitch_miner.core.api import TwitchApi
from twitch_miner.core.auth import AuthManager
from twitch_miner.core.http import AsyncHttpClient
from twitch_miner.core.logger import logger
from twitch_miner.models.events import Priority
from twitch_miner.models.streamer import Streamer
from twitch_miner.services.registry import ChannelRegistry


class WatchService:
    """Sends watch telemetry for prioritised online streamers."""

    def __init__(
        self,
        *,
        api: TwitchApi,
        http: AsyncHttpClient,
        auth: AuthManager,
        registry: ChannelRegistry,
        config: WatchConfig,
    ) -> None:
        self._api = api
        self._http = http
        self._auth = auth
        self._registry = registry
        self._config = config
        self._priority = [self._coerce(p) for p in config.priority]

    @staticmethod
    def _coerce(value: str) -> Priority:
        try:
            return Priority(value.upper())
        except ValueError:
            return Priority.ORDER

    async def run(self) -> None:
        """Main watch loop; runs until cancelled."""

        logger.info("Watch service started (max_concurrent={})", self._config.max_concurrent)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - resilience
                logger.opt(exception=exc).warning("Watch tick failed: {}", exc)
            # Small jitter to avoid a perfectly periodic fingerprint.
            await asyncio.sleep(self._config.interval_seconds + random.uniform(-2, 2))

    async def _tick(self) -> None:
        online = [s for s in self._registry.online() if s.is_watch_ready]
        for streamer in online:
            if not streamer.stream.broadcast_id:
                await self.refresh_stream(streamer)

        selected = self._select(online)
        for streamer in selected:
            await self._send_watch(streamer)

    def _select(self, online: list[Streamer]) -> list[Streamer]:
        """Rank online streamers by configured priority, return the top N."""

        index = {s.username: i for i, s in enumerate(self._registry.all())}

        def sort_key(streamer: Streamer) -> tuple:
            key: list[float] = []
            for priority in self._priority:
                key.append(self._priority_score(streamer, priority, index))
            return tuple(key)

        ranked = sorted(online, key=sort_key)
        return ranked[: self._config.max_concurrent]

    @staticmethod
    def _priority_score(
        streamer: Streamer, priority: Priority, index: dict[str, int]
    ) -> float:
        # Lower score == higher priority (sorted ascending).
        match priority:
            case Priority.ORDER:
                return index.get(streamer.username, 1_000)
            case Priority.DROPS:
                return 0 if streamer.drops_condition() else 1
            case Priority.SUBSCRIBED:
                return 0 if streamer.settings.watch_streak else 1
            case Priority.POINTS_ASCENDING:
                return streamer.channel_points
            case Priority.POINTS_DESCENDING:
                return -streamer.channel_points
            case _:
                return index.get(streamer.username, 1_000)

    async def refresh_stream(self, streamer: Streamer) -> None:
        """Pull fresh broadcast metadata and (re)build the watch payload."""

        info = await self._api.get_stream_info(streamer.username)
        if info is None:
            streamer.set_offline()
            return
        stream = streamer.stream
        stream.broadcast_id = info["broadcast_id"]
        stream.title = info["title"]
        stream.game = info["game"]
        stream.game_id = info["game_id"]
        stream.viewers_count = info["viewers_count"]
        if stream.spade_url is None:
            stream.spade_url = await self._api.get_spade_url(streamer.username)
        if streamer.channel_id:
            stream.build_payload(channel_id=streamer.channel_id, user_id=self._auth.user_id)
        if streamer.settings.claim_drops and streamer.channel_id:
            stream.campaign_ids = await self._api.get_available_drop_campaign_ids(
                streamer.channel_id
            )

    async def _send_watch(self, streamer: Streamer) -> None:
        stream = streamer.stream
        if not stream.watch_due():
            return
        if not stream.has_payload or not stream.spade_url:
            await self.refresh_stream(streamer)
        if not stream.has_payload or not stream.spade_url:
            return

        await self._touch_hls(streamer.username)

        response = await self._http.post(stream.spade_url, data=stream.encode_payload())
        if response.status_code in (200, 204):
            stream.register_watch()
            logger.debug(
                "Watch sent: {} ({} min, game={})",
                streamer.username,
                stream.minute_watched,
                stream.game or "n/a",
            )
        else:
            logger.debug(
                "Watch event for {} returned {}", streamer.username, response.status_code
            )

    async def _touch_hls(self, login: str) -> None:
        """Best-effort HLS segment HEAD to mirror real player network activity."""

        try:
            token = await self._api.get_playback_access_token(login)
            if not token.get("value"):
                return
            usher = (
                f"https://usher.ttvnw.net/api/channel/hls/{login}.m3u8"
                f"?sig={token['signature']}&token={token['value']}"
                "&allow_source=true&fast_bread=true&player=twitchweb"
            )
            master = await self._http.get(usher)
            media_url = self._first_url(master.text)
            if not media_url:
                return
            media = await self._http.get(media_url)
            segment_url = self._first_url(media.text)
            if segment_url:
                await self._http.head(segment_url)
        except Exception as exc:  # pragma: no cover - best effort
            logger.trace("HLS touch failed for {}: {}", login, exc)

    @staticmethod
    def _first_url(playlist: str) -> str | None:
        for line in playlist.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
        return None


__all__ = ["WatchService"]

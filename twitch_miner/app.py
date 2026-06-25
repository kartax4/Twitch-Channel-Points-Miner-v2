"""Application orchestrator.

Wires the configuration, core clients (auth/GQL/HTTP), domain services, PubSub,
and analytics into a single asyncio application. Owns the lifecycle: dependency
construction, login, task supervision, and graceful shutdown on SIGINT/SIGTERM.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

from twitch_miner.analytics.service import AnalyticsService
from twitch_miner.analytics.store import AnalyticsStore
from twitch_miner.config.loader import load_config
from twitch_miner.config.models import AppConfig
from twitch_miner.config.watcher import ConfigEvent, ConfigWatcher, desired_streamers
from twitch_miner.core import logger as logger_module
from twitch_miner.core.api import TwitchApi
from twitch_miner.core.auth import AuthManager
from twitch_miner.core.gql import GqlClient
from twitch_miner.core.http import AsyncHttpClient
from twitch_miner.core.logger import logger
from twitch_miner.models.streamer import Streamer
from twitch_miner.services.drops import DropsService
from twitch_miner.services.listener import EventListener, initial_online_check
from twitch_miner.services.points import PointsService
from twitch_miner.services.pubsub import PubSubClient, build_topics_for, user_topic
from twitch_miner.services.registry import ChannelRegistry
from twitch_miner.services.watch import WatchService


class MinerApp:
    """Top-level application object."""

    def __init__(self, config_path: str | Path) -> None:
        self._config_path = Path(config_path)
        self._config: AppConfig = load_config(self._config_path)
        self._stop = asyncio.Event()

        # Configure logging as early as possible.
        logger_module.configure(
            self._config.logging, username=self._config.twitch.username
        )

        self._http = AsyncHttpClient()
        self._auth = AuthManager(self._config.twitch.username, self._http)
        self._gql = GqlClient(self._http, self._auth)
        self._api = TwitchApi(self._gql, self._http)
        self._registry = ChannelRegistry()

        self._store: AnalyticsStore | None = None
        self._analytics: AnalyticsService | None = None
        if self._config.analytics.enabled:
            self._store = AnalyticsStore(f"analytics/{self._config.twitch.username}")
            self._analytics = AnalyticsService(self._store, self._config.analytics)

        self._points = PointsService(
            api=self._api, registry=self._registry, recorder=self._store
        )
        self._watch = WatchService(
            api=self._api,
            http=self._http,
            auth=self._auth,
            registry=self._registry,
            config=self._config.watch,
        )
        self._drops = DropsService(
            api=self._api, registry=self._registry, config=self._config.drops
        )
        self._listener = EventListener(
            registry=self._registry, points=self._points, watch=self._watch
        )
        self._pubsub = PubSubClient(
            token_provider=self._auth.ensure_valid, listener=self._listener
        )
        self._config_queue: asyncio.Queue[ConfigEvent] = asyncio.Queue()
        self._registry.on_add(self._subscribe_streamer)
        self._registry.on_remove(self._unsubscribe_streamer)

    async def run(self) -> None:
        """Run the miner until a shutdown signal is received."""

        logger.info("Starting Twitch Miner for {}", self._config.twitch.username)
        await self._auth.login()
        await self._gql.refresh_client_version()

        await self._seed_registry()
        await initial_online_check(self._registry, self._watch)

        if self._auth.user_id:
            await self._pubsub.subscribe({user_topic(self._auth.user_id)})

        if self._analytics is not None:
            await self._analytics.start()

        self._install_signal_handlers()

        tasks = [
            asyncio.create_task(self._watch.run(), name="watch"),
            asyncio.create_task(self._points.run(), name="points"),
            asyncio.create_task(
                self._drops.consume_config_events(self._config_queue), name="config-consumer"
            ),
            asyncio.create_task(self._run_config_watcher(), name="config-watcher"),
        ]
        if self._config.drops.enabled:
            tasks.append(asyncio.create_task(self._drops.run(), name="drops"))

        await self._stop.wait()
        logger.info("Shutdown requested; stopping services...")
        await self._shutdown(tasks)

    async def _seed_registry(self) -> None:
        desired = desired_streamers(self._config)
        for config in desired.values():
            streamer = await self._drops.create_streamer(config)
            if streamer is not None:
                await self._registry.add(streamer)
        logger.info("Tracking {} channel(s)", len(self._registry))

    async def _run_config_watcher(self) -> None:
        watcher = ConfigWatcher(
            self._config_path, self._config_queue, initial=self._config
        )
        await watcher.run()

    async def _subscribe_streamer(self, streamer: Streamer) -> None:
        if not streamer.channel_id:
            return
        topics = build_topics_for(
            streamer.channel_id,
            follow_raid=streamer.settings.follow_raid,
            moments=streamer.settings.claim_moments,
        )
        await self._pubsub.subscribe(topics)

    async def _unsubscribe_streamer(self, streamer: Streamer) -> None:
        if not streamer.channel_id:
            return
        topics = build_topics_for(
            streamer.channel_id,
            follow_raid=streamer.settings.follow_raid,
            moments=streamer.settings.claim_moments,
        )
        await self._pubsub.unsubscribe(topics)

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:  # pragma: no cover - e.g. Windows
                signal.signal(sig, lambda *_: self._stop.set())

    async def _shutdown(self, tasks: list[asyncio.Task[None]]) -> None:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._pubsub.close()
        if self._analytics is not None:
            await self._analytics.stop()
        await self._http.aclose()
        await logger_module.shutdown()
        logger.info("Goodbye.")


__all__ = ["MinerApp"]

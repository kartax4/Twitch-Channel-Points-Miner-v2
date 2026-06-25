"""Hot-reload config watcher.

Watches the YAML config file and, on each change, recomputes the desired set of
tracked channels and emits add/remove events onto an :class:`asyncio.Queue`.
A consumer (the drops service) applies those events live, so channels can be
added or removed for watching/drops farming without restarting the process.

Both the ``streamers`` list and ``drops.channels`` list feed into the desired
set; a channel listed under ``drops`` is materialised as a streamer with
``claim_drops=True``. When a channel appears in both, the settings are merged.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from watchfiles import awatch

from twitch_miner.config.loader import load_config
from twitch_miner.config.models import AppConfig, StreamerConfig
from twitch_miner.core.exceptions import ConfigError
from twitch_miner.core.logger import logger


@dataclass(slots=True, frozen=True)
class StreamerAdded:
    """Emitted when a channel should start being tracked."""

    config: StreamerConfig


@dataclass(slots=True, frozen=True)
class StreamerRemoved:
    """Emitted when a channel should stop being tracked."""

    name: str


ConfigEvent = StreamerAdded | StreamerRemoved


def desired_streamers(config: AppConfig) -> dict[str, StreamerConfig]:
    """Compute the merged desired ``{login: StreamerConfig}`` from a config."""

    desired: dict[str, StreamerConfig] = {}
    for streamer in config.streamers:
        desired[streamer.name] = streamer
    if config.drops.enabled:
        for channel in config.drops.channels:
            existing = desired.get(channel)
            if existing is None:
                desired[channel] = StreamerConfig(name=channel, claim_drops=True)
            else:
                desired[channel] = existing.model_copy(update={"claim_drops": True})
    return desired


class ConfigWatcher:
    """Watches the config file and emits diff events to a queue."""

    def __init__(
        self,
        path: str | Path,
        queue: asyncio.Queue[ConfigEvent],
        *,
        initial: AppConfig,
    ) -> None:
        self._path = Path(path)
        self._queue = queue
        self._current = desired_streamers(initial)

    async def run(self) -> None:
        """Watch the config file until cancelled, emitting change events."""

        logger.info("Config watcher active on {}", self._path)
        async for _ in awatch(self._path):
            try:
                config = load_config(self._path)
            except ConfigError as exc:
                logger.warning("Ignoring invalid config reload: {}", exc)
                continue
            await self._apply(desired_streamers(config))

    async def _apply(self, new: dict[str, StreamerConfig]) -> None:
        added = new.keys() - self._current.keys()
        removed = self._current.keys() - new.keys()
        changed = {
            name
            for name in new.keys() & self._current.keys()
            if new[name] != self._current[name]
        }

        for name in removed:
            await self._queue.put(StreamerRemoved(name))
        # A settings change is applied as remove + re-add.
        for name in changed:
            await self._queue.put(StreamerRemoved(name))
            await self._queue.put(StreamerAdded(new[name]))
        for name in added:
            await self._queue.put(StreamerAdded(new[name]))

        if added or removed or changed:
            logger.info(
                "Config reload: +{} -{} ~{}", len(added), len(removed), len(changed)
            )
        self._current = new


__all__ = [
    "ConfigEvent",
    "ConfigWatcher",
    "StreamerAdded",
    "StreamerRemoved",
    "desired_streamers",
]

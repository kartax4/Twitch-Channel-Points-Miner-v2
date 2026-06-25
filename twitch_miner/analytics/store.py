"""Async per-streamer analytics persistence.

Each streamer's point history is stored as a small JSON document::

    {
      "series": [{"x": <epoch_ms>, "y": <points>, "z": "<label>"}, ...],
      "annotations": [{"x": <epoch_ms>, "text": "..."}, ...]
    }

Writes are serialised per-streamer with an :class:`asyncio.Lock` and the blocking
file I/O is dispatched to a worker thread so the event loop is never stalled.
This satisfies the :class:`~twitch_miner.services.points.PointsRecorder` protocol.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from twitch_miner.core.logger import logger
from twitch_miner.models.streamer import Streamer


class AnalyticsStore:
    """Reads/writes per-streamer analytics JSON documents."""

    def __init__(self, base_path: str | Path) -> None:
        self._base = Path(base_path)
        try:
            self._base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Analytics directory {} is not writable ({}); persistence may fail.",
                self._base,
                exc,
            )
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _path(self, username: str) -> Path:
        return self._base / f"{username}.json"

    async def record(self, streamer: Streamer, label: str) -> None:
        """Append a datapoint for ``streamer`` at the current point balance."""

        point = {
            "x": int(time.time() * 1000),
            "y": streamer.channel_points,
            "z": label,
        }
        async with self._locks[streamer.username]:
            data = await asyncio.to_thread(self._read, streamer.username)
            data.setdefault("series", []).append(point)
            await asyncio.to_thread(self._write, streamer.username, data)

    async def annotate(self, username: str, text: str) -> None:
        """Add a timestamped annotation marker for ``username``."""

        annotation = {"x": int(time.time() * 1000), "text": text}
        async with self._locks[username]:
            data = await asyncio.to_thread(self._read, username)
            data.setdefault("annotations", []).append(annotation)
            await asyncio.to_thread(self._write, username, data)

    async def read(self, username: str) -> dict[str, Any]:
        """Return the full analytics document for ``username``."""

        async with self._locks[username]:
            return await asyncio.to_thread(self._read, username)

    def list_streamers(self) -> list[str]:
        """Return the logins that have a persisted analytics file."""

        return sorted(p.stem for p in self._base.glob("*.json"))

    def _read(self, username: str) -> dict[str, Any]:
        path = self._path(username)
        if not path.exists():
            return {"series": [], "annotations": []}
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Could not read analytics for {}: {}", username, exc)
            return {"series": [], "annotations": []}

    def _write(self, username: str, data: dict[str, Any]) -> None:
        path = self._path(username)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)


__all__ = ["AnalyticsStore"]

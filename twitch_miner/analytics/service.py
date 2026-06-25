"""Analytics web dashboard served with aiohttp.

Exposes a small JSON API plus a single-page chart UI:

* ``GET /``                  - the dashboard page.
* ``GET /api/streamers``     - summary list (name, points, last activity).
* ``GET /api/json/{name}``   - one streamer's series/annotations (date-filterable).
* ``GET /api/json_all``      - every streamer's full document.

The server runs as an asyncio task and is started/stopped via :meth:`start` /
:meth:`stop` so it integrates cleanly with the app's ``TaskGroup`` lifecycle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp import web

from twitch_miner.analytics.store import AnalyticsStore
from twitch_miner.config.models import AnalyticsConfig
from twitch_miner.core.logger import logger

_STATIC_DIR = Path(__file__).parent / "static"


class AnalyticsService:
    """aiohttp-based analytics dashboard and JSON API."""

    def __init__(self, store: AnalyticsStore, config: AnalyticsConfig) -> None:
        self._store = store
        self._config = config
        self._runner: web.AppRunner | None = None

    def _build_app(self) -> web.Application:
        app = web.Application()
        app.add_routes(
            [
                web.get("/", self._index),
                web.get("/api/streamers", self._streamers),
                web.get("/api/json/{name}", self._streamer_json),
                web.get("/api/json_all", self._json_all),
                web.get("/api/config", self._client_config),
            ]
        )
        if _STATIC_DIR.exists():
            app.router.add_static("/static/", _STATIC_DIR, name="static")
        return app

    async def start(self) -> None:
        """Start serving (idempotent)."""

        if self._runner is not None:
            return
        self._runner = web.AppRunner(self._build_app())
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._config.host, self._config.port)
        await site.start()
        logger.info(
            "Analytics dashboard on http://{}:{}", self._config.host, self._config.port
        )

    async def stop(self) -> None:
        """Stop serving and release the socket."""

        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # --- handlers ---------------------------------------------------------- #
    async def _index(self, _request: web.Request) -> web.StreamResponse:
        index = _STATIC_DIR / "index.html"
        if index.exists():
            return web.FileResponse(index)
        return web.Response(text="Analytics dashboard assets missing.", status=404)

    async def _streamers(self, _request: web.Request) -> web.Response:
        summary = []
        for name in self._store.list_streamers():
            data = await self._store.read(name)
            series = data.get("series", [])
            last = series[-1] if series else {}
            summary.append(
                {
                    "name": name,
                    "points": last.get("y", 0),
                    "last_activity": last.get("x", 0),
                }
            )
        return web.json_response(summary)

    async def _streamer_json(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        data = await self._store.read(name)
        start = _to_int(request.query.get("startDate"))
        end = _to_int(request.query.get("endDate"))
        if start is not None or end is not None:
            data = _filter_by_date(data, start, end)
        return web.json_response(data)

    async def _json_all(self, _request: web.Request) -> web.Response:
        result = {name: await self._store.read(name) for name in self._store.list_streamers()}
        return web.json_response(result)

    async def _client_config(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "refresh_minutes": self._config.refresh_minutes,
                "days_ago": self._config.days_ago,
            }
        )


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _filter_by_date(
    data: dict[str, Any], start: int | None, end: int | None
) -> dict[str, Any]:
    def keep(point: dict[str, Any]) -> bool:
        x = point.get("x", 0)
        if start is not None and x < start:
            return False
        return not (end is not None and x > end)

    return {
        "series": [p for p in data.get("series", []) if keep(p)],
        "annotations": [a for a in data.get("annotations", []) if keep(a)],
    }


__all__ = ["AnalyticsService"]

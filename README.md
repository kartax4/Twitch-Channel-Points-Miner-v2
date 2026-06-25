# Twitch Miner (async rewrite)

A modern, fully asynchronous **Twitch channel points & drops miner**, rebuilt
from the ground up for Python 3.12+ with a clean, SOLID architecture.

> This project automates *watching* streams to accrue channel points and farm
> Twitch Drops. It uses Twitch's internal GraphQL API (there is no official
> endpoint for points/drops) and mimics the web client to behave like a normal
> browser session. **The betting/predictions system has been intentionally
> removed.** Use responsibly and at your own risk.

## Highlights

- **Async everywhere** — `asyncio` + `httpx` (HTTP) + `websockets` (PubSub) +
  `aiohttp` (analytics dashboard).
- **Dynamic Drops** — add/remove channels live by editing the config file; a
  `watchfiles` watcher diffs the change and applies it through an `asyncio.Queue`
  without restarting the process.
- **Resilient** — tenacity-based exponential backoff with jitter on `429`/`5xx`,
  PubSub heartbeat watchdog with auto-reconnect, and transparent OAuth token
  refresh.
- **Stealth** — browser User-Agents/headers, web Client-ID, dynamic client
  version, stable device id, and jittered timings.
- **Typed & linted** — strict `mypy`, `ruff`, and `loguru` structured logging.
- **Containerised** — multi-stage `Dockerfile` (non-root) and `docker-compose`
  with volume mounts for `analytics`, `cookies`, and `logs`.

## Architecture

```
twitch_miner/
  app.py            # MinerApp orchestrator (lifecycle, task supervision)
  __main__.py       # CLI entrypoint
  config/           # pydantic models, YAML+env loader, hot-reload watcher
  core/             # logger, constants, exceptions, http, auth, gql, api
  models/           # Streamer, Stream, Campaign, Drop, events
  services/         # watch, points, drops, pubsub, registry, listener
  analytics/        # async store + aiohttp dashboard (static/)
  utils/            # id helpers
```

The orchestrator builds dependencies once and supervises long-lived tasks:
the watch loop, points refresh, drops reconciliation, the config watcher, and
its consumer. PubSub events are bridged to the services by `EventListener`.

### Dynamic Drops flow

```
config.yaml  --(file change)-->  ConfigWatcher  --(StreamerAdded/Removed)-->
asyncio.Queue  -->  DropsService  -->  ChannelRegistry  -->  PubSub subscribe/unsubscribe
```

## Configuration

Copy the example and edit it:

```bash
cp config/config.example.yaml config/config.yaml
```

Key sections: `twitch.username`, `streamers`, `drops` (with hot-reloadable
`channels`), `watch`, `analytics`, and `logging`. Any value can be overridden
with environment variables, e.g. `TWITCH_MINER__ANALYTICS__PORT=8080`.

The `streamers` and `drops.channels` lists are **hot-reloaded** — edit them
while the miner is running and changes apply immediately.

## Running locally

```bash
pip install -e ".[dev]"
python -m twitch_miner --config config/config.yaml
```

On first run you will be prompted to authorize the miner via Twitch's device
flow (open the printed URL and enter the code). The resulting tokens are stored
in `cookies/<username>.json` and refreshed automatically thereafter.

## Running with Docker

```bash
cp .env.example .env
cp config/config.example.yaml config/config.yaml
docker compose up -d --build
```

Volumes `./cookies`, `./analytics`, and `./logs` are persisted on the host, and
the analytics dashboard is exposed on `http://localhost:5000`.

## Analytics

When enabled, a dashboard is served (default `0.0.0.0:5000`) with a JSON API:

- `GET /` — chart UI
- `GET /api/streamers` — summary list
- `GET /api/json/{name}` — per-streamer series (supports `startDate`/`endDate`)
- `GET /api/json_all` — full dump

## Development

```bash
ruff check twitch_miner      # lint
mypy twitch_miner            # type-check (strict)
pytest                       # tests
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

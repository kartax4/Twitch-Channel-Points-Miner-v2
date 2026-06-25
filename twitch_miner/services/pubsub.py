"""Twitch PubSub WebSocket client.

Subscribes to per-user and per-channel topics and dispatches decoded events to a
:class:`PubSubListener`. Handles the operational concerns of a long-lived
connection: periodic PING with a PONG watchdog, automatic reconnection with
exponential backoff, topic re-subscription after reconnect, and sharding across
multiple sockets (Twitch limits ~50 topics per connection).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import websockets
from websockets.asyncio.client import ClientConnection

from twitch_miner.core.constants import PUBSUB_WS_URL
from twitch_miner.core.logger import logger
from twitch_miner.utils.ids import random_hex

# Twitch caps topics per connection; keep headroom below the hard limit of 50.
_MAX_TOPICS_PER_SOCKET = 50
_PING_INTERVAL = 27.0
_PONG_TIMEOUT = 15.0
_MAX_BACKOFF = 120.0


class PubSubListener(Protocol):
    """Callbacks invoked for decoded PubSub events (channel_id-scoped)."""

    async def on_points_earned(
        self, channel_id: str, *, balance: int, gained: int, reason: str
    ) -> None: ...
    async def on_points_spent(self, channel_id: str, *, balance: int) -> None: ...
    async def on_claim_available(self, channel_id: str, claim_id: str) -> None: ...
    async def on_stream_up(self, channel_id: str) -> None: ...
    async def on_stream_down(self, channel_id: str) -> None: ...
    async def on_viewcount(self, channel_id: str, viewers: int) -> None: ...
    async def on_raid(self, channel_id: str, raid_id: str) -> None: ...
    async def on_moment(self, channel_id: str, moment_id: str) -> None: ...


TokenProvider = Callable[[], Awaitable[str]]


class _Connection:
    """A single PubSub WebSocket carrying up to ``_MAX_TOPICS_PER_SOCKET`` topics."""

    def __init__(self, client: PubSubClient, index: int) -> None:
        self._client = client
        self._index = index
        self.topics: set[str] = set()
        self._ws: ClientConnection | None = None
        self._last_pong = 0.0
        self._task: asyncio.Task[None] | None = None
        self._closing = False

    @property
    def has_room(self) -> bool:
        return len(self.topics) < _MAX_TOPICS_PER_SOCKET

    def start(self) -> None:
        self._task = asyncio.create_task(
            self._run_forever(), name=f"pubsub-conn-{self._index}"
        )

    async def stop(self) -> None:
        self._closing = True
        if self._ws is not None:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def add_topics(self, topics: set[str]) -> None:
        self.topics |= topics
        if self._ws is not None:
            await self._listen(topics, listen=True)

    async def remove_topics(self, topics: set[str]) -> None:
        self.topics -= topics
        if self._ws is not None:
            await self._listen(topics, listen=False)

    async def _run_forever(self) -> None:
        backoff = 1.0
        while not self._closing:
            try:
                async with websockets.connect(
                    PUBSUB_WS_URL, ping_interval=None, max_queue=64
                ) as ws:
                    self._ws = ws
                    self._last_pong = time.time()
                    logger.debug("PubSub connection {} established", self._index)
                    await self._listen(set(self.topics), listen=True)
                    backoff = 1.0
                    await asyncio.gather(self._reader(ws), self._heartbeat(ws))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closing:
                    break
                logger.warning(
                    "PubSub connection {} dropped ({}); reconnecting in {:.0f}s",
                    self._index,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff + random.uniform(0, 1))
                backoff = min(backoff * 2, _MAX_BACKOFF)
            finally:
                self._ws = None

    async def _reader(self, ws: ClientConnection) -> None:
        async for raw in ws:
            await self._handle(str(raw))

    async def _heartbeat(self, ws: ClientConnection) -> None:
        while True:
            await ws.send(json.dumps({"type": "PING"}))
            await asyncio.sleep(_PONG_TIMEOUT)
            if (time.time() - self._last_pong) > (_PING_INTERVAL + _PONG_TIMEOUT):
                logger.warning("PubSub {} missed PONG; forcing reconnect", self._index)
                await ws.close()
                return
            await asyncio.sleep(_PING_INTERVAL - _PONG_TIMEOUT + random.uniform(0, 3))

    async def _listen(self, topics: set[str], *, listen: bool) -> None:
        if not topics or self._ws is None:
            return
        token = await self._client.token_provider()
        message = {
            "type": "LISTEN" if listen else "UNLISTEN",
            "nonce": random_hex(16),
            "data": {"topics": sorted(topics), "auth_token": token},
        }
        await self._ws.send(json.dumps(message))

    async def _handle(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        kind = message.get("type")
        if kind == "PONG":
            self._last_pong = time.time()
            return
        if kind == "RECONNECT":
            logger.info("PubSub {} asked to reconnect", self._index)
            if self._ws is not None:
                await self._ws.close()
            return
        if kind == "RESPONSE" and message.get("error"):
            logger.warning("PubSub LISTEN error: {}", message["error"])
            return
        if kind == "MESSAGE":
            await self._client.dispatch(message.get("data", {}))


class PubSubClient:
    """Manages PubSub topic subscriptions across one or more connections."""

    def __init__(self, *, token_provider: TokenProvider, listener: PubSubListener) -> None:
        self.token_provider = token_provider
        self._listener = listener
        self._connections: list[_Connection] = []
        self._topic_owner: dict[str, _Connection] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, topics: set[str]) -> None:
        """Subscribe to a set of topic strings, sharding as needed."""

        async with self._lock:
            new = {t for t in topics if t not in self._topic_owner}
            for topic in new:
                conn = self._free_connection()
                await conn.add_topics({topic})
                self._topic_owner[topic] = conn

    async def unsubscribe(self, topics: set[str]) -> None:
        """Unsubscribe from a set of topic strings."""

        async with self._lock:
            for topic in topics:
                conn = self._topic_owner.pop(topic, None)
                if conn is not None:
                    await conn.remove_topics({topic})

    def _free_connection(self) -> _Connection:
        for conn in self._connections:
            if conn.has_room:
                return conn
        conn = _Connection(self, len(self._connections))
        conn.start()
        self._connections.append(conn)
        return conn

    async def close(self) -> None:
        """Close all connections."""

        async with self._lock:
            for conn in self._connections:
                await conn.stop()
            self._connections.clear()
            self._topic_owner.clear()

    async def dispatch(self, data: dict[str, Any]) -> None:
        """Decode a MESSAGE payload and invoke the appropriate listener method."""

        topic: str = data.get("topic", "")
        try:
            payload = json.loads(data.get("message", "{}"))
        except json.JSONDecodeError:
            return
        prefix, _, scope_id = topic.partition(".")
        try:
            await self._route(prefix, scope_id, payload)
        except Exception as exc:  # pragma: no cover - listener isolation
            logger.opt(exception=exc).warning("PubSub dispatch error on {}: {}", topic, exc)

    async def _route(self, prefix: str, scope_id: str, payload: dict[str, Any]) -> None:
        msg_type = payload.get("type", "")
        body = payload.get("data", {})
        match prefix:
            case "community-points-user-v1":
                await self._route_points(msg_type, body)
            case "video-playback-by-id":
                await self._route_playback(msg_type, scope_id, payload)
            case "raid":
                raid = payload.get("raid") or body.get("raid") or {}
                if raid.get("id"):
                    await self._listener.on_raid(scope_id, raid["id"])
            case "community-moments-channel-v1":
                if msg_type == "active" and body.get("moment_id"):
                    await self._listener.on_moment(scope_id, body["moment_id"])

    async def _route_points(self, msg_type: str, body: dict[str, Any]) -> None:
        if msg_type == "points-earned":
            balance = (body.get("balance") or {})
            point_gain = (body.get("point_gain") or {})
            channel_id = balance.get("channel_id") or point_gain.get("channel_id", "")
            await self._listener.on_points_earned(
                channel_id,
                balance=int(balance.get("balance", 0)),
                gained=int(point_gain.get("total_points", 0)),
                reason=point_gain.get("reason_code", "UNKNOWN"),
            )
        elif msg_type == "claim-available":
            claim = body.get("claim", {})
            await self._listener.on_claim_available(
                claim.get("channel_id", ""), claim.get("id", "")
            )
        elif msg_type == "points-spent":
            balance = body.get("balance", {})
            await self._listener.on_points_spent(
                balance.get("channel_id", ""), balance=int(balance.get("balance", 0))
            )

    async def _route_playback(
        self, msg_type: str, channel_id: str, payload: dict[str, Any]
    ) -> None:
        if msg_type == "stream-up":
            await self._listener.on_stream_up(channel_id)
        elif msg_type == "stream-down":
            await self._listener.on_stream_down(channel_id)
        elif msg_type == "viewcount":
            await self._listener.on_viewcount(channel_id, int(payload.get("viewers", 0)))


def build_topics_for(channel_id: str, *, follow_raid: bool, moments: bool) -> set[str]:
    """Return the channel-scoped topics to subscribe to for a streamer."""

    topics = {
        f"video-playback-by-id.{channel_id}",
        f"community-points-channel-v1.{channel_id}",
    }
    if follow_raid:
        topics.add(f"raid.{channel_id}")
    if moments:
        topics.add(f"community-moments-channel-v1.{channel_id}")
    return topics


def user_topic(user_id: str) -> str:
    """Return the per-user community-points topic."""

    return f"community-points-user-v1.{user_id}"


__all__ = [
    "PubSubClient",
    "PubSubListener",
    "build_topics_for",
    "user_topic",
]

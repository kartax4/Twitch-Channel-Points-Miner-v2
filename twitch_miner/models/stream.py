"""Stream entity: live broadcast state for a streamer."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from twitch_miner.models.campaign import Campaign

# Minimum seconds between minute-watched events (Twitch credits ~once/minute).
_MINUTE = 60.0


@dataclass(slots=True)
class Stream:
    """Mutable state describing a streamer's current live broadcast."""

    broadcast_id: str | None = None
    title: str = ""
    game: str = ""
    game_id: str | None = None
    tags: list[str] = field(default_factory=list)
    viewers_count: int = 0
    spade_url: str | None = None
    # Drop campaigns currently applicable to this stream.
    campaigns: list[Campaign] = field(default_factory=list)
    campaign_ids: list[str] = field(default_factory=list)
    minute_watched: int = 0
    _payload: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _last_watch_ts: float = field(default=0.0, repr=False)

    @property
    def drops_enabled(self) -> bool:
        """Whether this broadcast is flagged as drops-enabled."""

        return any("drops" in tag.lower() for tag in self.tags) or bool(self.campaign_ids)

    def build_payload(self, *, channel_id: str, user_id: str | None) -> None:
        """Construct the spade minute-watched event payload for this stream."""

        self._payload = [
            {
                "event": "minute-watched",
                "properties": {
                    "channel_id": channel_id,
                    "broadcast_id": self.broadcast_id,
                    "player": "site",
                    "user_id": int(user_id) if user_id and user_id.isdigit() else user_id,
                    "live": True,
                    "game": self.game,
                    "game_id": self.game_id,
                },
            }
        ]

    def encode_payload(self) -> dict[str, str]:
        """Return the base64-encoded form data expected by the spade endpoint."""

        raw = json.dumps(self._payload, separators=(",", ":")).encode("utf-8")
        return {"data": base64.b64encode(raw).decode("utf-8")}

    @property
    def has_payload(self) -> bool:
        return bool(self._payload)

    def watch_due(self) -> bool:
        """Whether enough time has elapsed to send another watch event."""

        return (time.time() - self._last_watch_ts) >= _MINUTE

    def register_watch(self) -> None:
        """Record that a minute-watched event was successfully sent."""

        self._last_watch_ts = time.time()
        self.minute_watched += 1

    def reset(self) -> None:
        """Clear per-broadcast state (used when a streamer goes offline)."""

        self.broadcast_id = None
        self.campaigns = []
        self.campaign_ids = []
        self._payload = []
        self._last_watch_ts = 0.0


__all__ = ["Stream"]

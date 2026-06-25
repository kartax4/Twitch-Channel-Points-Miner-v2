"""Streamer entity: aggregate runtime state for a tracked channel."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from twitch_miner.config.models import StreamerConfig
from twitch_miner.models.stream import Stream

# Grace period before a freshly-online stream is eligible for watching.
_ONLINE_GRACE = 30.0


@dataclass(slots=True)
class Streamer:
    """All mutable state the miner tracks for a single channel."""

    username: str
    settings: StreamerConfig
    channel_id: str | None = None
    is_online: bool = False
    online_at: float = 0.0
    offline_at: float = 0.0
    channel_points: int = 0
    stream: Stream = field(default_factory=Stream)
    # reason_code -> cumulative {counter, amount}
    history: dict[str, dict[str, int]] = field(default_factory=dict)
    active_multipliers: float = 1.0

    @property
    def display_name(self) -> str:
        return self.username

    @property
    def channel_url(self) -> str:
        return f"https://www.twitch.tv/{self.username}"

    def set_online(self) -> None:
        if not self.is_online:
            self.is_online = True
            self.online_at = time.time()

    def set_offline(self) -> None:
        if self.is_online:
            self.is_online = False
            self.offline_at = time.time()
            self.stream.reset()

    @property
    def online_elapsed(self) -> float:
        return time.time() - self.online_at if self.is_online else 0.0

    @property
    def is_watch_ready(self) -> bool:
        """Online long enough to begin accruing watch time."""

        return self.is_online and self.online_elapsed >= _ONLINE_GRACE

    def drops_condition(self) -> bool:
        """Whether this streamer should currently be farmed for drops."""

        return (
            self.settings.claim_drops
            and self.is_online
            and bool(self.stream.campaign_ids)
        )

    def update_history(self, reason: str, amount: int) -> None:
        entry = self.history.setdefault(reason, {"counter": 0, "amount": 0})
        entry["counter"] += 1
        entry["amount"] += amount

    def __str__(self) -> str:
        status = "ONLINE" if self.is_online else "offline"
        return f"{self.username} [{status}, {self.channel_points} pts]"


__all__ = ["Streamer"]

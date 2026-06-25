"""Typed configuration models.

The configuration is intentionally split into nested models so that individual
subsystems (logging, analytics, drops, ...) can depend only on the slice they
need. ``streamers`` and ``drops.channels`` are the hot-reloadable parts of the
config consumed by the :mod:`twitch_miner.config.watcher`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class TwitchConfig(BaseModel):
    """Core Twitch account identity."""

    username: str = Field(..., min_length=1, description="Twitch login of the account.")

    @field_validator("username")
    @classmethod
    def _normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class StreamerConfig(BaseModel):
    """Per-streamer mining options. Hot-reloadable."""

    name: str = Field(..., min_length=1)
    watch_streak: bool = True
    claim_drops: bool = False
    follow_raid: bool = True
    claim_moments: bool = True
    community_goals: bool = False

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return value.strip().lower()


class DropsConfig(BaseModel):
    """Twitch Drops farming configuration. ``channels`` is hot-reloadable."""

    enabled: bool = False
    claim_on_startup: bool = True
    # Interval between full inventory/dashboard reconciliation passes (seconds).
    sync_interval_seconds: float = Field(default=60.0, ge=15.0)
    # Channels dedicated to drops farming; may be edited live.
    channels: list[str] = Field(default_factory=list)

    @field_validator("channels")
    @classmethod
    def _normalize_channels(cls, value: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for raw in value:
            name = raw.strip().lower()
            if name:
                seen.setdefault(name, None)
        return list(seen)


class WatchConfig(BaseModel):
    """Watch-loop tuning."""

    # Maximum number of streamers to actively "watch" simultaneously. Twitch
    # only credits watch-time to a small number of concurrent streams.
    max_concurrent: int = Field(default=2, ge=1, le=2)
    # How often the watch loop sends a minute-watched event (seconds).
    interval_seconds: float = Field(default=60.0, ge=10.0)
    priority: list[str] = Field(
        default_factory=lambda: ["DROPS", "ORDER", "POINTS_DESCENDING"],
    )


class AnalyticsConfig(BaseModel):
    """Analytics dashboard / persistence options."""

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = Field(default=5000, ge=1, le=65535)
    refresh_minutes: int = Field(default=5, ge=1)
    days_ago: int = Field(default=7, ge=1)


class LoggingConfig(BaseModel):
    """Logging configuration consumed by :func:`twitch_miner.core.logger.configure`."""

    level: str = "INFO"
    file: bool = True
    # Rotation can be a size (e.g. "10 MB") or a time spec (e.g. "00:00").
    rotation: str = "10 MB"
    retention: str = "7 days"
    colored: bool = True

    @field_validator("level")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class AppConfig(BaseModel):
    """Root application configuration."""

    twitch: TwitchConfig
    streamers: list[StreamerConfig] = Field(default_factory=list)
    drops: DropsConfig = Field(default_factory=DropsConfig)
    watch: WatchConfig = Field(default_factory=WatchConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def streamer_names(self) -> set[str]:
        """All streamer logins the miner should currently track (watch + drops)."""

        names = {s.name for s in self.streamers}
        if self.drops.enabled:
            names.update(self.drops.channels)
        return names

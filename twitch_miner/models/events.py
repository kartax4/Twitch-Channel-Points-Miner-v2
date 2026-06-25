"""Enumerations for log events and watch prioritisation."""

from __future__ import annotations

from enum import StrEnum


class Priority(StrEnum):
    """Strategies for choosing which streamers to actively watch.

    The watch loop can only credit a couple of concurrent streams, so streamers
    are ranked according to the configured priority order.
    """

    ORDER = "ORDER"
    STREAK = "STREAK"
    DROPS = "DROPS"
    SUBSCRIBED = "SUBSCRIBED"
    POINTS_ASCENDING = "POINTS_ASCENDING"
    POINTS_DESCENDING = "POINTS_DESCENDING"


class Event(StrEnum):
    """Semantic tags attached to notable log records."""

    STREAMER_ONLINE = "STREAMER_ONLINE"
    STREAMER_OFFLINE = "STREAMER_OFFLINE"
    GAIN_FOR_WATCH = "GAIN_FOR_WATCH"
    GAIN_FOR_WATCH_STREAK = "GAIN_FOR_WATCH_STREAK"
    GAIN_FOR_CLAIM = "GAIN_FOR_CLAIM"
    GAIN_FOR_RAID = "GAIN_FOR_RAID"
    BONUS_CLAIM = "BONUS_CLAIM"
    MOMENT_CLAIM = "MOMENT_CLAIM"
    JOIN_RAID = "JOIN_RAID"
    DROP_PROGRESS = "DROP_PROGRESS"
    DROP_CLAIM = "DROP_CLAIM"
    CHANNEL_ADDED = "CHANNEL_ADDED"
    CHANNEL_REMOVED = "CHANNEL_REMOVED"


__all__ = ["Event", "Priority"]

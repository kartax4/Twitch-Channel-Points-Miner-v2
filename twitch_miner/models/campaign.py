"""Campaign entity: a Drops campaign containing one or more drops."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from twitch_miner.models.drop import Drop, _parse_dt


@dataclass(slots=True)
class Campaign:
    """A Twitch Drops campaign and its associated drops.

    ``channels`` is the set of channel ids eligible for this campaign (empty
    means "any channel playing the game"). ``in_inventory`` flips to true once
    the account has started making progress on the campaign.
    """

    id: str
    name: str = ""
    game: str = ""
    game_id: str | None = None
    status: str = ""
    start_at: datetime | None = None
    end_at: datetime | None = None
    channels: set[str] = field(default_factory=set)
    drops: list[Drop] = field(default_factory=list)
    in_inventory: bool = False

    @classmethod
    def from_details(cls, data: dict[str, Any]) -> Campaign:
        """Build a campaign from a ``DropCampaignDetails`` node."""

        game = data.get("game") or {}
        channels = {
            c["id"] for c in (data.get("allow", {}) or {}).get("channels") or [] if c.get("id")
        }
        drops = [
            Drop.from_definition(node)
            for node in data.get("timeBasedDrops") or []
            if node.get("id")
        ]
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            game=game.get("displayName", ""),
            game_id=game.get("id"),
            status=data.get("status", ""),
            start_at=_parse_dt(data.get("startAt")),
            end_at=_parse_dt(data.get("endAt")),
            channels=channels,
            drops=drops,
        )

    @property
    def is_active(self) -> bool:
        now = datetime.now(UTC)
        if self.start_at and now < self.start_at:
            return False
        return not (self.end_at and now > self.end_at)

    def allows_channel(self, channel_id: str) -> bool:
        """Whether ``channel_id`` is eligible for this campaign."""

        return not self.channels or channel_id in self.channels

    def clear_drops(self) -> None:
        """Drop expired or already-claimed drops from tracking."""

        self.drops = [d for d in self.drops if d.is_active and not d.is_claimed]

    async def sync_drops(
        self,
        inventory_drops: list[dict[str, Any]],
        claim: Callable[[Drop], Awaitable[bool]],
    ) -> None:
        """Reconcile drop progress against inventory and auto-claim when ready.

        Args:
            inventory_drops: ``timeBasedDrops`` nodes from the inventory query.
            claim: Async callback that claims a drop and returns success.
        """

        by_id = {node["id"]: node for node in inventory_drops if node.get("id")}
        for drop in self.drops:
            node = by_id.get(drop.id)
            if node is None:
                continue
            drop.update({"self": node.get("self", node)})
            if drop.is_claimable:
                await claim(drop)

    def __str__(self) -> str:
        return f"Campaign({self.name or self.id}, game={self.game}, drops={len(self.drops)})"


__all__ = ["Campaign"]

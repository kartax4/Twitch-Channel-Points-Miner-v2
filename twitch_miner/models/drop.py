"""Drop entity: a single time-based reward within a campaign."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(slots=True)
class Drop:
    """A time-based drop and its claim/progress state.

    Progress is reconciled from the GQL ``Inventory`` response; ``is_claimable``
    becomes true once the watch requirement is met, the drop is unclaimed, and a
    ``drop_instance_id`` (the claim handle) is present.
    """

    id: str
    name: str = ""
    benefit: str = ""
    minutes_required: int = 0
    current_minutes_watched: int = 0
    drop_instance_id: str | None = None
    has_preconditions_met: bool = True
    is_claimed: bool = False
    start_at: datetime | None = None
    end_at: datetime | None = None
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_definition(cls, data: dict[str, Any]) -> Drop:
        """Build a drop from a ``DropCampaignDetails`` time-based drop node."""

        benefits = data.get("benefitEdges") or []
        benefit_name = ""
        if benefits:
            benefit_name = (benefits[0].get("benefit") or {}).get("name", "")
        return cls(
            id=data["id"],
            name=data.get("name", benefit_name),
            benefit=benefit_name,
            minutes_required=int(data.get("requiredMinutesWatched", 0)),
            start_at=_parse_dt(data.get("startAt")),
            end_at=_parse_dt(data.get("endAt")),
            _raw=data,
        )

    def update(self, progress: dict[str, Any]) -> None:
        """Update progress/claim state from an inventory ``self`` node."""

        self_data = progress.get("self") or {}
        self.current_minutes_watched = int(self_data.get("currentMinutesWatched", 0))
        self.has_preconditions_met = bool(self_data.get("hasPreconditionsMet", True))
        self.is_claimed = bool(self_data.get("isClaimed", False))
        self.drop_instance_id = self_data.get("dropInstanceID")

    @property
    def percentage(self) -> float:
        """Watch progress as a 0-100 percentage."""

        if self.minutes_required <= 0:
            return 100.0
        pct = (self.current_minutes_watched / self.minutes_required) * 100.0
        return min(round(pct, 1), 100.0)

    @property
    def is_complete(self) -> bool:
        return self.current_minutes_watched >= self.minutes_required > 0

    @property
    def is_claimable(self) -> bool:
        return (
            not self.is_claimed
            and self.has_preconditions_met
            and self.drop_instance_id is not None
        )

    @property
    def is_active(self) -> bool:
        now = datetime.now(UTC)
        if self.start_at and now < self.start_at:
            return False
        return not (self.end_at and now > self.end_at)

    def __str__(self) -> str:
        return (
            f"{self.name or self.id} ({self.percentage}% - "
            f"{self.current_minutes_watched}/{self.minutes_required}min)"
        )


__all__ = ["Drop"]

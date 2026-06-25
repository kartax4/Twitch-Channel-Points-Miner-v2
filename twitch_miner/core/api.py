"""High-level Twitch API facade built on top of :class:`GqlClient`.

This is the single place that knows how to translate domain intentions
("is this streamer live?", "claim this drop") into concrete GQL operations and
parse their responses. Services depend on this facade rather than touching GQL
shapes directly, keeping them focused on orchestration.
"""

from __future__ import annotations

import re
from typing import Any

from twitch_miner.core.constants import GqlOperations
from twitch_miner.core.gql import GqlClient
from twitch_miner.core.http import AsyncHttpClient
from twitch_miner.core.logger import logger

_SETTINGS_URL_RE = re.compile(r'(https://static\.twitchcdn\.net/config/settings\.[0-9a-f]+\.js)')
_SPADE_URL_RE = re.compile(r'"spade_?url"\s*:\s*"(https?://[^"]+)"', re.IGNORECASE)
# Max campaigns per DropCampaignDetails batch request.
_CAMPAIGN_CHUNK = 20


class TwitchApi:
    """Domain-oriented wrapper over Twitch GQL + ancillary HTTP endpoints."""

    def __init__(self, gql: GqlClient, http: AsyncHttpClient) -> None:
        self._gql = gql
        self._http = http

    # --- identity ---------------------------------------------------------- #
    async def get_user_id(self, login: str) -> str | None:
        data = await self._gql.execute(GqlOperations.GetIDFromLogin, {"login": login})
        user = data.get("user")
        return user.get("id") if user else None

    # --- stream state ------------------------------------------------------ #
    async def is_stream_live(self, login: str) -> bool:
        data = await self._gql.execute(GqlOperations.WithIsStreamLiveQuery, {"id": login})
        user = data.get("user") or {}
        return bool(user.get("stream"))

    async def get_stream_info(self, login: str) -> dict[str, Any] | None:
        """Return broadcast metadata (title, game, viewers) or ``None`` if offline."""

        data = await self._gql.execute(
            GqlOperations.VideoPlayerStreamInfoOverlayChannel, {"channel": login}
        )
        user = data.get("user") or {}
        stream = user.get("stream")
        if not stream:
            return None
        game = (stream.get("game") or {}) if isinstance(stream.get("game"), dict) else {}
        broadcast = user.get("lastBroadcast") or {}
        return {
            "broadcast_id": stream.get("id"),
            "title": broadcast.get("title", ""),
            "game": game.get("displayName", ""),
            "game_id": game.get("id"),
            "viewers_count": stream.get("viewersCount", 0),
        }

    async def get_playback_access_token(self, login: str) -> dict[str, str]:
        """Return the signed playback token used to fetch the HLS playlist."""

        variables = {
            "isLive": True,
            "login": login,
            "isVod": False,
            "vodID": "",
            "playerType": "site",
        }
        data = await self._gql.execute(
            GqlOperations.PlaybackAccessTokenTemplate, variables
        )
        token = data.get("streamPlaybackAccessToken") or {}
        return {"signature": token.get("signature", ""), "value": token.get("value", "")}

    async def get_spade_url(self, login: str) -> str | None:
        """Scrape the spade telemetry URL for a channel page."""

        try:
            page = await self._http.get(f"https://www.twitch.tv/{login}")
            settings_match = _SETTINGS_URL_RE.search(page.text)
            if not settings_match:
                return None
            settings = await self._http.get(settings_match.group(1))
            spade_match = _SPADE_URL_RE.search(settings.text)
            return spade_match.group(1) if spade_match else None
        except Exception as exc:  # pragma: no cover - network passthrough
            logger.debug("Failed to resolve spade url for {}: {}", login, exc)
            return None

    # --- channel points ---------------------------------------------------- #
    async def get_channel_points_context(self, login: str) -> dict[str, Any]:
        data = await self._gql.execute(
            GqlOperations.ChannelPointsContext, {"channelLogin": login}
        )
        community = data.get("community") or {}
        channel = community.get("channel") or {}
        self_edge = channel.get("self") or {}
        balance = (self_edge.get("communityPoints") or {}).get("balance", 0)
        available = (self_edge.get("communityPoints") or {}).get("availableClaim") or {}
        return {"balance": balance, "claim_id": available.get("id")}

    async def claim_community_points(self, *, channel_id: str, claim_id: str) -> bool:
        data = await self._gql.execute(
            GqlOperations.ClaimCommunityPoints,
            {"input": {"channelID": channel_id, "claimID": claim_id}},
        )
        return "claimCommunityPoints" in data

    async def claim_moment(self, moment_id: str) -> bool:
        data = await self._gql.execute(
            GqlOperations.CommunityMomentCallout_Claim, {"input": {"momentID": moment_id}}
        )
        return "claimCommunityMoment" in data

    async def join_raid(self, raid_id: str) -> bool:
        data = await self._gql.execute(
            GqlOperations.JoinRaid, {"input": {"raidID": raid_id}}
        )
        return "joinRaid" in data

    # --- drops ------------------------------------------------------------- #
    async def get_inventory(self) -> dict[str, Any]:
        data = await self._gql.execute(GqlOperations.Inventory)
        return (data.get("currentUser") or {}).get("inventory") or {}

    async def get_drops_dashboard(self, *, status: str = "ACTIVE") -> list[dict[str, Any]]:
        data = await self._gql.execute(GqlOperations.ViewerDropsDashboard)
        campaigns = (data.get("currentUser") or {}).get("dropCampaigns") or []
        return [c for c in campaigns if not status or c.get("status") == status]

    async def get_campaign_details(
        self, campaign_ids: list[str], channel_id: str | None = None
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for start in range(0, len(campaign_ids), _CAMPAIGN_CHUNK):
            chunk = campaign_ids[start : start + _CAMPAIGN_CHUNK]
            batch = [
                (
                    GqlOperations.DropCampaignDetails,
                    {"channelLogin": channel_id or "0", "dropID": cid},
                )
                for cid in chunk
            ]
            for entry in await self._gql.execute_batch(batch):
                campaign = (entry.get("user") or {}).get("dropCampaign")
                if campaign:
                    results.append(campaign)
        return results

    async def get_available_drop_campaign_ids(self, channel_id: str) -> list[str]:
        data = await self._gql.execute(
            GqlOperations.DropsHighlightService_AvailableDrops, {"channelID": channel_id}
        )
        channel = data.get("channel") or {}
        campaigns = channel.get("viewerDropCampaigns") or []
        return [c["id"] for c in campaigns if c.get("id")]

    async def claim_drop(self, drop_instance_id: str) -> bool:
        data = await self._gql.execute(
            GqlOperations.DropsPage_ClaimDropRewards,
            {"input": {"dropInstanceID": drop_instance_id}},
        )
        result = data.get("claimDropRewards")
        if result is None:
            return False
        status = result.get("status", "")
        return status in {"ELIGIBLE_FOR_ALL", "DROP_INSTANCE_ALREADY_CLAIMED", "CLAIMED"}


__all__ = ["TwitchApi"]

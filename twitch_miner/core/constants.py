"""Static constants used to mimic the Twitch web client.

These values are deliberately chosen to match what a real browser sends so that
the miner blends in with legitimate web traffic. Keeping them in one place makes
it easy to refresh them when Twitch rotates persisted-query hashes or bumps the
client version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
TWITCH_URL: Final = "https://www.twitch.tv"
GQL_URL: Final = "https://gql.twitch.tv/gql"
GQL_INTEGRITY_URL: Final = "https://gql.twitch.tv/integrity"
USHER_URL: Final = "https://usher.ttvnw.net"
PUBSUB_WS_URL: Final = "wss://pubsub-edge.twitch.tv/v1"

# OAuth device-code flow endpoints.
OAUTH_DEVICE_URL: Final = "https://id.twitch.tv/oauth2/device"
OAUTH_TOKEN_URL: Final = "https://id.twitch.tv/oauth2/token"
OAUTH_VALIDATE_URL: Final = "https://id.twitch.tv/oauth2/validate"

IRC_HOST: Final = "irc.chat.twitch.tv"
IRC_PORT: Final = 6667

# --------------------------------------------------------------------------- #
# Client identity (Twitch web client)
# --------------------------------------------------------------------------- #
# The public web Client-ID. Using the web client-id (rather than a mobile/TV id)
# keeps GQL behaviour consistent with the browser headers below.
CLIENT_ID: Final = "kimne78kx3ncx6brgo4mv6wki5h1ko"

# The web Client-ID does not support the OAuth *device-code* grant, so the login
# flow uses the public Android-TV client id (which does). Once authenticated,
# the resulting OAuth token is used with the web ``CLIENT_ID`` for GQL calls.
DEVICE_CLIENT_ID: Final = "ue6666qo983tsx6so1t0vnawi233wa"

# Headers presented during the device-code OAuth flow (mimics the TV client).
DEVICE_AUTH_HEADERS: Final[dict[str, str]] = {
    "Accept": "application/json",
    "Origin": "https://android.tv.twitch.tv",
    "Referer": "https://android.tv.twitch.tv/",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 7.1; Smart Box C1) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# OAuth scopes requested during the device-code login flow.
OAUTH_SCOPES: Final = (
    "channel_read chat:read user_blocks_edit user_blocks_read "
    "user_follows_edit user_read"
)

# Fallback client version; the live value is scraped from the twilight build id
# (``window.__twilightBuildID``) at runtime by the GQL client.
DEFAULT_CLIENT_VERSION: Final = "ef928475-9403-42f2-8a34-55784bd08e16"

# --------------------------------------------------------------------------- #
# User agents (modern desktop browsers)
# --------------------------------------------------------------------------- #
USER_AGENTS: Final[dict[str, str]] = {
    "CHROME_WINDOWS": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "CHROME_LINUX": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "FIREFOX_WINDOWS": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
    "EDGE_WINDOWS": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
    ),
}

DEFAULT_USER_AGENT: Final = USER_AGENTS["CHROME_WINDOWS"]

# Default headers sent on web requests to twitch.tv / gql.
BROWSER_HEADERS: Final[dict[str, str]] = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": TWITCH_URL,
    "Referer": f"{TWITCH_URL}/",
}


# --------------------------------------------------------------------------- #
# GraphQL persisted operations
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class GqlOperation:
    """A Twitch GraphQL persisted query.

    Twitch's internal GQL API accepts an operation name plus a persisted-query
    SHA-256 hash instead of a full query body. ``build`` produces the JSON body
    expected by the endpoint, merged with any per-call variables.
    """

    name: str
    sha256: str
    version: int = 1
    default_variables: dict[str, Any] = field(default_factory=dict)

    def build(self, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return the request body for this operation.

        Args:
            variables: Per-call variables merged over ``default_variables``.
        """

        merged: dict[str, Any] = {**self.default_variables, **(variables or {})}
        body: dict[str, Any] = {
            "operationName": self.name,
            "extensions": {
                "persistedQuery": {
                    "version": self.version,
                    "sha256Hash": self.sha256,
                }
            },
        }
        if merged:
            body["variables"] = merged
        return body


class GqlOperations:
    """Registry of the persisted GQL operations used by the miner.

    Hashes are taken from the Twitch web client. ``MinuteWatched`` is included
    here for completeness, but note that minute-watched telemetry is actually
    delivered to the *spade* endpoint as a base64-encoded event rather than via
    GQL; ``PlaybackAccessTokenTemplate`` is what gates the watch flow.
    """

    # --- Stream / playback ------------------------------------------------- #
    PlaybackAccessTokenTemplate = GqlOperation(
        name="PlaybackAccessToken_Template",
        sha256="0828119ded1c13477966434e15800ff57ddacf13ba1911c129dc2200705b0712",
    )
    PlaybackAccessToken = GqlOperation(
        name="PlaybackAccessToken",
        sha256="3093517e37e4f4cb48906155bcd894150aef92617939236d2508f3375ab732ce",
    )
    WithIsStreamLiveQuery = GqlOperation(
        name="WithIsStreamLiveQuery",
        sha256="04e46329a6786ff3a81c01c50bfa5d725902507a0deb83b0edbf7abe7a3716ea",
    )
    VideoPlayerStreamInfoOverlayChannel = GqlOperation(
        name="VideoPlayerStreamInfoOverlayChannel",
        sha256="198492e0857f6aedead9665c81c5a06d67b25b58034649687124083ff288597d",
    )
    # Pseudo-operation kept for parity with the spade minute-watched event.
    MinuteWatched = GqlOperation(
        name="MinuteWatched",
        sha256="3093517e37e4f4cb48906155bcd894150aef92617939236d2508f3375ab732ce",
    )

    # --- Channel points ---------------------------------------------------- #
    ChannelPointsContext = GqlOperation(
        name="ChannelPointsContext",
        sha256="1530a003a7d374b0380b79db0be0534f30ff46e61cffa2bc0e2468a909fbc024",
    )
    ClaimCommunityPoints = GqlOperation(
        name="ClaimCommunityPoints",
        sha256="46aaeebe02c99afdf4fc97c7c0cba964124bf6b0af229395f1f6d1feed05b3d0",
    )
    CommunityMomentCallout_Claim = GqlOperation(
        name="CommunityMomentCallout_Claim",
        sha256="e2d67415aead910f7f9ceb45a77b750a1e1d9622c936d832328a0689e054db62",
    )

    # --- Drops ------------------------------------------------------------- #
    Inventory = GqlOperation(
        name="Inventory",
        sha256="d86775d0ef16a63a33ad52e80eaff963b2d5b72fada7c991504a57496e1d8e4b",
        default_variables={"fetchRewardCampaigns": True},
    )
    ViewerDropsDashboard = GqlOperation(
        name="ViewerDropsDashboard",
        sha256="5a4da2ab3d5b47c9f9ce864e727b2cb346af1e3ea8b897fe8f704a97ff017619",
        default_variables={"fetchRewardCampaigns": True},
    )
    DropCampaignDetails = GqlOperation(
        name="DropCampaignDetails",
        sha256="f6396f5ffdde867a8f6f6da18286e4baf02e5b98d14689a69b5af320a4c7b7b8",
    )
    DropsHighlightService_AvailableDrops = GqlOperation(
        name="DropsHighlightService_AvailableDrops",
        sha256="9a62a09bce5b53e26e64a671e530bc599cb6aab1e5ba3cbd5d85966d3940716f",
    )
    DropsPage_ClaimDropRewards = GqlOperation(
        name="DropsPage_ClaimDropRewards",
        sha256="a455deea71bdc9015b78eb49f4acfbce8baa7ccbedd28e549bb025bd0f751930",
    )

    # --- Misc / identity --------------------------------------------------- #
    GetIDFromLogin = GqlOperation(
        name="GetIDFromLogin",
        sha256="94e82a7b1e3c21e186daa73ee2afc4b8f23bade1fbbff6fe8ac133f50a2f58ca",
        default_variables={"login": None},
    )
    ChannelFollows = GqlOperation(
        name="ChannelFollows",
        sha256="eecf815273d3d949e5cf0085cc5084cd8a1b5b7b6f7990cf43cb0beadf546907",
        default_variables={"limit": 100, "order": "ASC"},
    )
    JoinRaid = GqlOperation(
        name="JoinRaid",
        sha256="c6a332a86d1087fbbb1a8623aa01bd1313d2386e7c63be60fdb2d1901f01a4ae",
    )


__all__ = [
    "BROWSER_HEADERS",
    "CLIENT_ID",
    "DEVICE_AUTH_HEADERS",
    "DEVICE_CLIENT_ID",
    "DEFAULT_CLIENT_VERSION",
    "DEFAULT_USER_AGENT",
    "GQL_INTEGRITY_URL",
    "GQL_URL",
    "GqlOperation",
    "GqlOperations",
    "IRC_HOST",
    "IRC_PORT",
    "OAUTH_DEVICE_URL",
    "OAUTH_SCOPES",
    "OAUTH_TOKEN_URL",
    "OAUTH_VALIDATE_URL",
    "PUBSUB_WS_URL",
    "TWITCH_URL",
    "USER_AGENTS",
    "USHER_URL",
]

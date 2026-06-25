"""Authentication: device-code OAuth, token refresh, and cookie persistence.

Login uses Twitch's OAuth *device-code* grant (the same flow the TV app uses):

1. Request a device/user code pair.
2. Prompt the user to visit ``https://www.twitch.tv/activate`` and enter the
   code.
3. Poll the token endpoint until the user authorises, yielding an
   ``access_token`` and a ``refresh_token``.

Unlike the legacy implementation, the ``refresh_token`` is persisted and used to
transparently renew the session, so the user only logs in interactively once.
Tokens are stored atomically as JSON under ``cookies/{username}.json``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from twitch_miner.core.constants import (
    CLIENT_ID,
    DEVICE_AUTH_HEADERS,
    OAUTH_DEVICE_URL,
    OAUTH_SCOPES,
    OAUTH_TOKEN_URL,
    OAUTH_VALIDATE_URL,
)
from twitch_miner.core.exceptions import TokenRefreshError, TwitchAuthError
from twitch_miner.core.http import AsyncHttpClient
from twitch_miner.core.logger import logger
from twitch_miner.utils.ids import stable_device_id

_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_REFRESH_GRANT = "refresh_token"
# Refresh proactively when fewer than this many seconds remain on the token.
_REFRESH_MARGIN = 300.0


@dataclass(slots=True)
class TokenBundle:
    """Persisted authentication state for an account."""

    access_token: str
    refresh_token: str | None = None
    user_id: str | None = None
    login: str | None = None
    device_id: str | None = None
    # Unix epoch when the access token expires (0 == unknown).
    expires_at: float = 0.0

    def is_expiring(self, margin: float = _REFRESH_MARGIN) -> bool:
        """Whether the token is unknown-expiry or close to expiring."""

        if self.expires_at <= 0:
            return False
        return time.time() >= (self.expires_at - margin)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenBundle:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


class TokenStore:
    """Atomic JSON persistence for a :class:`TokenBundle`."""

    def __init__(self, username: str, *, cookies_dir: str | Path = "cookies") -> None:
        self._dir = Path(cookies_dir)
        self._path = self._dir / f"{username}.json"

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> TokenBundle | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return TokenBundle.from_dict(data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Could not parse stored credentials at {}: {}", self._path, exc)
            return None

    def save(self, bundle: TokenBundle) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(bundle.to_dict(), indent=2), encoding="utf-8")
        # Atomic replace so a crash mid-write never corrupts the credentials.
        os.replace(tmp, self._path)
        with contextlib.suppress(OSError):  # best effort on restricted filesystems
            os.chmod(self._path, 0o600)


@dataclass(slots=True)
class _DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


class AuthManager:
    """Owns the OAuth token lifecycle for a single account.

    Args:
        username: Twitch login.
        http: Shared resilient HTTP client.
        cookies_dir: Directory for the persisted token file.
        on_device_prompt: Optional callback invoked with the verification URI
            and user code so a UI/notifier can surface them; defaults to logging.
    """

    def __init__(
        self,
        username: str,
        http: AsyncHttpClient,
        *,
        cookies_dir: str | Path = "cookies",
    ) -> None:
        self._username = username
        self._http = http
        self._store = TokenStore(username, cookies_dir=cookies_dir)
        self._device_id = stable_device_id(username)
        self._bundle: TokenBundle | None = None
        self._lock = asyncio.Lock()
        # Public device clients cannot refresh (Twitch demands a client secret).
        # Once a refresh is rejected we stop attempting it to avoid log spam and
        # fall back to the (long-lived) device token until it truly expires.
        self._refresh_disabled = False

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def user_id(self) -> str | None:
        return self._bundle.user_id if self._bundle else None

    def get_auth_token(self) -> str:
        """Return the current access token.

        Raises:
            TwitchAuthError: If no token is available (login not completed).
        """

        if not self._bundle or not self._bundle.access_token:
            raise TwitchAuthError("No access token available; call login() first.")
        return self._bundle.access_token

    async def login(self) -> TokenBundle:
        """Ensure a valid session, restoring from disk or running device login."""

        async with self._lock:
            stored = self._store.load()
            if stored is not None:
                self._bundle = stored
                if stored.device_id:
                    self._device_id = stored.device_id
                if await self._validate():
                    logger.info("Restored valid session for {}", self._username)
                    return self._bundle
                logger.info("Stored session invalid; attempting refresh.")
                if await self._try_refresh() and self._bundle is not None:
                    return self._bundle
                logger.warning("Refresh failed; interactive re-login required.")

            self._bundle = await self._device_login()
            await self._validate()
            self._persist()
            return self._bundle

    async def ensure_valid(self) -> str:
        """Return a token guaranteed to be valid, refreshing if necessary.

        Raises:
            TwitchAuthError: If the session cannot be revalidated.
        """

        async with self._lock:
            if self._bundle is None:
                raise TwitchAuthError("Not authenticated; call login() first.")
            # Fast path: a non-expiring (or unknown-expiry) token is used as-is.
            if not self._bundle.is_expiring():
                return self._bundle.access_token
            if await self._try_refresh():
                return self._bundle.access_token
            if await self._validate():
                return self._bundle.access_token
            raise TwitchAuthError("Session expired and could not be refreshed.")

    async def invalidate_and_refresh(self) -> str:
        """Force a refresh after an upstream ``ERR_BADAUTH``/401.

        Raises:
            TwitchAuthError: If refresh is unavailable or fails.
        """

        async with self._lock:
            if await self._try_refresh() and self._bundle is not None:
                return self._bundle.access_token
            raise TwitchAuthError(
                "Token rejected and refresh is unavailable; delete the cookies "
                "file and re-run to log in again."
            )

    # --- internal helpers -------------------------------------------------- #
    async def _device_login(self) -> TokenBundle:
        code = await self._request_device_code()
        logger.warning(
            "Authorize this miner: open {} and enter code {}",
            code.verification_uri,
            code.user_code,
        )
        token_data = await self._poll_for_token(code)
        return TokenBundle(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            device_id=self._device_id,
            expires_at=self._expiry_from(token_data),
        )

    async def _request_device_code(self) -> _DeviceCode:
        response = await self._http.post(
            OAUTH_DEVICE_URL,
            headers=self._device_headers(),
            data={"client_id": CLIENT_ID, "scopes": OAUTH_SCOPES},
        )
        if response.status_code != 200:
            raise TwitchAuthError(
                f"Device-code request failed ({response.status_code}): {response.text}"
            )
        data = response.json()
        return _DeviceCode(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_uri=data.get("verification_uri", "https://www.twitch.tv/activate"),
            interval=int(data.get("interval", 5)),
            expires_in=int(data.get("expires_in", 1800)),
        )

    async def _poll_for_token(self, code: _DeviceCode) -> dict[str, Any]:
        deadline = time.time() + code.expires_in
        interval = max(code.interval, 1)
        while time.time() < deadline:
            await asyncio.sleep(interval)
            response = await self._http.post(
                OAUTH_TOKEN_URL,
                headers=self._device_headers(),
                data={
                    "client_id": CLIENT_ID,
                    "device_code": code.device_code,
                    "grant_type": _DEVICE_GRANT,
                },
            )
            data: dict[str, Any] = response.json()
            if response.status_code == 200 and "access_token" in data:
                logger.info("Device authorization granted for {}", self._username)
                return data
            error = data.get("message") or data.get("error") or ""
            # "authorization_pending" / "slow down" are expected while waiting.
            if "slow" in error.lower():
                interval += 5
            logger.debug("Awaiting authorization ({})...", error or response.status_code)
        raise TwitchAuthError("Device authorization timed out; please retry.")

    async def _try_refresh(self) -> bool:
        # Skip silently once we've learned this client can't refresh.
        if self._refresh_disabled:
            return False
        try:
            await self.refresh()
            return True
        except TokenRefreshError as exc:
            # The public device client has no secret, so Twitch rejects the
            # refresh grant. Disable further attempts and rely on the long-lived
            # device token instead of spamming the endpoint.
            self._refresh_disabled = True
            logger.warning(
                "Token refresh unavailable for this client ({}); using the "
                "existing device token. Re-login only needed if it is rejected.",
                exc,
            )
            return False

    async def refresh(self) -> TokenBundle:
        """Refresh the access token using the stored refresh token.

        Raises:
            TokenRefreshError: If no refresh token exists or the request fails.
        """

        if not self._bundle or not self._bundle.refresh_token:
            raise TokenRefreshError("No refresh token available.")
        response = await self._http.post(
            OAUTH_TOKEN_URL,
            headers=self._device_headers(),
            data={
                "client_id": CLIENT_ID,
                "grant_type": _REFRESH_GRANT,
                "refresh_token": self._bundle.refresh_token,
            },
        )
        data = response.json()
        if response.status_code != 200 or "access_token" not in data:
            raise TokenRefreshError(
                f"Refresh rejected ({response.status_code}): {data.get('message', data)}"
            )
        self._bundle.access_token = data["access_token"]
        self._bundle.refresh_token = data.get("refresh_token", self._bundle.refresh_token)
        self._bundle.expires_at = self._expiry_from(data)
        self._persist()
        logger.info("Access token refreshed for {}", self._username)
        return self._bundle

    async def _validate(self) -> bool:
        """Validate the current token against Twitch, updating identity fields."""

        if not self._bundle:
            return False
        try:
            response = await self._http.get(
                OAUTH_VALIDATE_URL,
                headers={"Authorization": f"OAuth {self._bundle.access_token}"},
            )
        except Exception as exc:  # pragma: no cover - network passthrough
            logger.debug("Token validation request failed: {}", exc)
            return False
        if response.status_code != 200:
            return False
        data = response.json()
        self._bundle.user_id = data.get("user_id", self._bundle.user_id)
        self._bundle.login = data.get("login", self._bundle.login)
        if "expires_in" in data:
            self._bundle.expires_at = time.time() + float(data["expires_in"])
        self._persist()
        return True

    def _persist(self) -> None:
        if self._bundle is not None:
            self._store.save(self._bundle)

    @staticmethod
    def _expiry_from(data: dict[str, Any]) -> float:
        expires_in = data.get("expires_in")
        return time.time() + float(expires_in) if expires_in else 0.0

    @staticmethod
    def _device_headers() -> dict[str, str]:
        return dict(DEVICE_AUTH_HEADERS)


__all__ = ["AuthManager", "TokenBundle", "TokenStore"]

"""Twitch GraphQL client.

Centralises every GQL call: builds browser-equivalent headers (Authorization,
Client-Id, Client-Session-Id, dynamic Client-Version, X-Device-Id), supports
single and batched operations, and transparently refreshes the auth token once
on ``401`` / ``ERR_BADAUTH`` before retrying.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from twitch_miner.core.auth import AuthManager
from twitch_miner.core.constants import (
    CLIENT_ID,
    DEFAULT_CLIENT_VERSION,
    GQL_URL,
    TWITCH_URL,
    GqlOperation,
)
from twitch_miner.core.exceptions import GqlQueryError, TwitchAuthError
from twitch_miner.core.http import AsyncHttpClient
from twitch_miner.core.logger import logger
from twitch_miner.utils.ids import random_session_id

_BUILD_ID_RE = re.compile(r'"clientId":"[^"]+","build(?:ID|Id)":"([0-9a-f-]+)"')
_BUILD_ID_RE_ALT = re.compile(r"__twilightBuildID=\"([0-9a-f-]+)\"")
# Only treat a genuine bad-auth token marker as a trigger for token refresh.
# (Transient "service error" and integrity messages are handled elsewhere.)
_BADAUTH_MARKERS = ("ERR_BADAUTH",)


class GqlClient:
    """Thin async wrapper around Twitch's internal GraphQL endpoint."""

    def __init__(
        self,
        http: AsyncHttpClient,
        auth: AuthManager,
        *,
        client_version: str = DEFAULT_CLIENT_VERSION,
    ) -> None:
        self._http = http
        self._auth = auth
        self._client_version = client_version
        self._client_session = random_session_id()
        self._integrity_token: str | None = None

    @property
    def client_version(self) -> str:
        return self._client_version

    async def _headers(self) -> dict[str, str]:
        token = self._auth.get_auth_token()
        headers = {
            "Authorization": f"OAuth {token}",
            "Client-Id": CLIENT_ID,
            "Client-Session-Id": self._client_session,
            "Client-Version": self._client_version,
            "X-Device-Id": self._auth.device_id,
            "Content-Type": "text/plain;charset=UTF-8",
        }
        if self._integrity_token:
            headers["Client-Integrity"] = self._integrity_token
        return headers

    async def refresh_client_version(self) -> None:
        """Scrape the live twilight build id from the Twitch homepage.

        Failures are non-fatal; the previous/default version is retained.
        """

        try:
            response = await self._http.get(TWITCH_URL)
        except Exception as exc:  # pragma: no cover - network passthrough
            logger.debug("Could not fetch client version: {}", exc)
            return
        for pattern in (_BUILD_ID_RE, _BUILD_ID_RE_ALT):
            match = pattern.search(response.text)
            if match:
                self._client_version = match.group(1)
                logger.debug("Updated GQL client version to {}", self._client_version)
                return

    async def execute(
        self,
        operation: GqlOperation,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a single GQL operation and return its ``data`` payload.

        Raises:
            GqlQueryError: If the response contains errors or unexpected shape.
        """

        body = operation.build(variables)
        payload = await self._post(body)
        if not isinstance(payload, dict):
            raise GqlQueryError(
                "Unexpected batched response for single operation",
                operation=operation.name,
            )
        self._raise_on_errors(payload, operation.name)
        data: dict[str, Any] = payload.get("data", {})
        return data

    async def execute_batch(
        self,
        operations: Sequence[tuple[GqlOperation, dict[str, Any] | None]],
    ) -> list[dict[str, Any]]:
        """Run multiple operations in one request, returning their data payloads.

        Twitch caps batches; callers should chunk large lists (~20 per request).
        """

        body = [op.build(variables) for op, variables in operations]
        payload = await self._post(body)
        if not isinstance(payload, list):
            payload = [payload]
        results: list[dict[str, Any]] = []
        for op_variables, entry in zip(operations, payload, strict=False):
            self._raise_on_errors(entry, op_variables[0].name)
            results.append(entry.get("data", {}))
        return results

    async def _post(self, body: Any, *, _retried: bool = False) -> Any:
        response = await self._http.post(
            GQL_URL,
            headers=await self._headers(),
            json=body,
        )
        if response.status_code == 401 and not _retried:
            logger.info("GQL returned 401; refreshing token and retrying.")
            await self._auth.invalidate_and_refresh()
            return await self._post(body, _retried=True)

        text = response.text
        if not _retried and any(marker in text for marker in _BADAUTH_MARKERS):
            logger.info("GQL auth marker detected; refreshing token and retrying.")
            try:
                await self._auth.invalidate_and_refresh()
            except TwitchAuthError:
                pass
            else:
                return await self._post(body, _retried=True)

        try:
            return response.json()
        except ValueError as exc:
            raise GqlQueryError(f"Invalid GQL JSON response: {text[:200]}") from exc

    @staticmethod
    def _raise_on_errors(payload: dict[str, Any], operation: str) -> None:
        if not isinstance(payload, dict):
            raise GqlQueryError("Non-object GQL entry", operation=operation)
        errors = payload.get("errors")
        if errors:
            raise GqlQueryError(
                f"GQL operation '{operation}' returned errors",
                operation=operation,
                errors=errors,
            )


__all__ = ["GqlClient"]

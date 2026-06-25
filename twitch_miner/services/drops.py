"""Drops service: dynamic channel membership + campaign sync/claim.

Two responsibilities:

* **Dynamic membership** - consumes :class:`ConfigEvent` items produced by the
  :class:`~twitch_miner.config.watcher.ConfigWatcher` and adds/removes streamers
  from the live registry (resolving channel ids on the fly). Adding to the
  registry transparently triggers PubSub subscription via registry callbacks.
* **Campaign reconciliation** - periodically reads the drops dashboard and the
  account inventory, tracks per-drop progress, auto-claims completed drops, and
  attaches the relevant campaigns to each drops-enabled streamer.
"""

from __future__ import annotations

import asyncio

from twitch_miner.config.models import DropsConfig, StreamerConfig
from twitch_miner.config.watcher import ConfigEvent, StreamerAdded, StreamerRemoved
from twitch_miner.core.api import TwitchApi
from twitch_miner.core.logger import logger
from twitch_miner.models.campaign import Campaign
from twitch_miner.models.drop import Drop
from twitch_miner.models.events import Event
from twitch_miner.models.streamer import Streamer
from twitch_miner.services.registry import ChannelRegistry


class DropsService:
    """Manages dynamic channels and the Twitch Drops campaign lifecycle."""

    def __init__(
        self,
        *,
        api: TwitchApi,
        registry: ChannelRegistry,
        config: DropsConfig,
    ) -> None:
        self._api = api
        self._registry = registry
        self._config = config
        self._campaigns: dict[str, Campaign] = {}

    # --- dynamic membership ----------------------------------------------- #
    async def consume_config_events(self, queue: asyncio.Queue[ConfigEvent]) -> None:
        """Apply hot-reload channel add/remove events until cancelled."""

        while True:
            event = await queue.get()
            try:
                await self._apply_event(event)
            except Exception as exc:  # pragma: no cover - resilience
                logger.warning("Failed to apply config event {}: {}", event, exc)
            finally:
                queue.task_done()

    async def _apply_event(self, event: ConfigEvent) -> None:
        match event:
            case StreamerRemoved(name=name):
                await self._registry.remove(name)
            case StreamerAdded(config=streamer_config):
                if self._registry.get(streamer_config.name) is not None:
                    return
                streamer = await self.create_streamer(streamer_config)
                if streamer is not None:
                    await self._registry.add(streamer)
                    logger.info(
                        "Channel added via hot-reload: {}",
                        streamer.username,
                        extra={"event": Event.CHANNEL_ADDED},
                    )

    async def create_streamer(self, config: StreamerConfig) -> Streamer | None:
        """Resolve a channel id and build a :class:`Streamer`."""

        channel_id = await self._api.get_user_id(config.name)
        if channel_id is None:
            logger.warning("Could not resolve channel id for {}", config.name)
            return None
        return Streamer(username=config.name, settings=config, channel_id=channel_id)

    # --- campaign reconciliation ------------------------------------------ #
    async def run(self) -> None:
        """Periodic campaign reconciliation loop; runs until cancelled."""

        if not self._config.enabled:
            return
        logger.info("Drops service started")
        if self._config.claim_on_startup:
            await self.claim_inventory()
        while True:
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - resilience
                logger.opt(exception=exc).warning("Drops reconcile failed: {}", exc)
            await asyncio.sleep(self._config.sync_interval_seconds)

    async def reconcile(self) -> None:
        """Refresh campaign definitions, sync progress, and attach to streamers."""

        await self._refresh_campaigns()
        await self._sync_progress()
        await self.claim_inventory()
        self._attach_to_streamers()

    async def _refresh_campaigns(self) -> None:
        dashboard = await self._api.get_drops_dashboard(status="ACTIVE")
        campaign_ids = [c["id"] for c in dashboard if c.get("id")]
        if not campaign_ids:
            self._campaigns = {}
            return
        details = await self._api.get_campaign_details(campaign_ids)
        campaigns: dict[str, Campaign] = {}
        for node in details:
            campaign = Campaign.from_details(node)
            if campaign.is_active:
                campaigns[campaign.id] = campaign
        self._campaigns = campaigns
        logger.debug("Tracking {} active drop campaigns", len(campaigns))

    async def _sync_progress(self) -> None:
        inventory = await self._api.get_inventory()
        in_progress = inventory.get("dropCampaignsInProgress") or []
        progress_by_campaign = {
            entry["id"]: entry.get("timeBasedDrops") or []
            for entry in in_progress
            if entry.get("id")
        }
        for campaign_id, drops in progress_by_campaign.items():
            campaign = self._campaigns.get(campaign_id)
            if campaign is None:
                continue
            campaign.in_inventory = True
            await campaign.sync_drops(drops, self.claim_drop)
            campaign.clear_drops()

    async def claim_inventory(self) -> int:
        """Claim every claimable drop currently in the inventory.

        Returns:
            The number of drops claimed.
        """

        inventory = await self._api.get_inventory()
        claimed = 0
        for entry in inventory.get("dropCampaignsInProgress") or []:
            for node in entry.get("timeBasedDrops") or []:
                drop = Drop.from_definition(node)
                drop.update({"self": node.get("self", {})})
                if drop.is_claimable and await self.claim_drop(drop):
                    claimed += 1
        if claimed:
            logger.info("Claimed {} drop(s) from inventory", claimed)
        return claimed

    async def claim_drop(self, drop: Drop) -> bool:
        """Claim a single drop and mark it claimed on success."""

        if drop.drop_instance_id is None:
            return False
        ok = await self._api.claim_drop(drop.drop_instance_id)
        if ok:
            drop.is_claimed = True
            logger.info(
                "Claimed drop {} ({})",
                drop.name or drop.id,
                drop.benefit,
                extra={"event": Event.DROP_CLAIM},
            )
        return ok

    def _attach_to_streamers(self) -> None:
        """Attach matching campaigns to each drops-enabled online streamer."""

        for streamer in self._registry.online():
            if not streamer.settings.claim_drops or not streamer.channel_id:
                continue
            matching = [
                campaign
                for campaign in self._campaigns.values()
                if campaign.allows_channel(streamer.channel_id)
                and (
                    not streamer.stream.game
                    or campaign.game.lower() == streamer.stream.game.lower()
                )
            ]
            streamer.stream.campaigns = matching
            for campaign in matching:
                for drop in campaign.drops:
                    if drop.is_active and not drop.is_claimed:
                        logger.debug(
                            "{} drop progress: {}", streamer.username, drop,
                            extra={"event": Event.DROP_PROGRESS},
                        )


__all__ = ["DropsService"]

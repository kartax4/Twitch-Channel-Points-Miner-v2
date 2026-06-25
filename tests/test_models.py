from __future__ import annotations

import base64
import json

from twitch_miner.config.models import StreamerConfig
from twitch_miner.models.campaign import Campaign
from twitch_miner.models.drop import Drop
from twitch_miner.models.stream import Stream
from twitch_miner.models.streamer import Streamer


def test_drop_progress_and_claimable() -> None:
    drop = Drop(id="d1", minutes_required=60)
    drop.update({"self": {"currentMinutesWatched": 30, "isClaimed": False}})
    assert drop.percentage == 50.0
    assert drop.is_claimable is False  # no drop_instance_id yet

    drop.update(
        {"self": {"currentMinutesWatched": 60, "dropInstanceID": "x", "isClaimed": False}}
    )
    assert drop.is_complete is True
    assert drop.is_claimable is True


def test_campaign_allows_channel() -> None:
    campaign = Campaign(id="c1", channels={"123"})
    assert campaign.allows_channel("123") is True
    assert campaign.allows_channel("999") is False
    open_campaign = Campaign(id="c2")
    assert open_campaign.allows_channel("anything") is True


def test_stream_payload_encoding() -> None:
    stream = Stream(broadcast_id="b1", game="Test", game_id="42")
    stream.build_payload(channel_id="100", user_id="200")
    encoded = stream.encode_payload()
    decoded = json.loads(base64.b64decode(encoded["data"]))
    assert decoded[0]["event"] == "minute-watched"
    assert decoded[0]["properties"]["channel_id"] == "100"
    assert decoded[0]["properties"]["user_id"] == 200


def test_streamer_online_history() -> None:
    streamer = Streamer(username="x", settings=StreamerConfig(name="x"))
    assert streamer.is_online is False
    streamer.set_online()
    assert streamer.is_online is True
    streamer.update_history("WATCH", 10)
    streamer.update_history("WATCH", 5)
    assert streamer.history["WATCH"] == {"counter": 2, "amount": 15}
    streamer.set_offline()
    assert streamer.is_online is False

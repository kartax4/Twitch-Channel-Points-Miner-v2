from __future__ import annotations

from pathlib import Path

import pytest

from twitch_miner.config.loader import load_config
from twitch_miner.config.models import AppConfig
from twitch_miner.config.watcher import (
    StreamerAdded,
    StreamerRemoved,
    desired_streamers,
)
from twitch_miner.core.exceptions import ConfigError

_CONFIG = """
twitch:
  username: TestUser
streamers:
  - name: Alpha
    claim_drops: false
drops:
  enabled: true
  channels: [Bravo, alpha]
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_config_normalizes_and_merges(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, _CONFIG))
    assert config.twitch.username == "testuser"
    desired = desired_streamers(config)
    # alpha appears in both lists -> merged with claim_drops true.
    assert desired["alpha"].claim_drops is True
    assert desired["bravo"].claim_drops is True
    assert set(desired) == {"alpha", "bravo"}


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWITCH_MINER__ANALYTICS__PORT", "9999")
    config = load_config(_write(tmp_path, _CONFIG))
    assert config.analytics.port == 9999


def test_missing_file_raises() -> None:
    with pytest.raises(ConfigError):
        load_config("/nonexistent/path/config.yaml")


def test_invalid_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, "streamers: []\n"))  # missing twitch.username


def test_streamer_names_include_drops() -> None:
    config = AppConfig.model_validate(
        {
            "twitch": {"username": "u"},
            "streamers": [{"name": "a"}],
            "drops": {"enabled": True, "channels": ["b"]},
        }
    )
    assert config.streamer_names() == {"a", "b"}


@pytest.mark.parametrize(
    "event_type", [StreamerAdded, StreamerRemoved]
)
def test_event_types_exist(event_type: type) -> None:
    assert event_type is not None

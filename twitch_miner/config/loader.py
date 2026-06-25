"""Configuration loading: merge a YAML file with environment overrides.

Precedence (highest first):

1. Environment variables prefixed with ``TWITCH_MINER__`` (nested via ``__``).
2. Values from the YAML config file.
3. Model defaults.

Example env override::

    TWITCH_MINER__TWITCH__USERNAME=myuser
    TWITCH_MINER__ANALYTICS__PORT=8080
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from twitch_miner.config.models import AppConfig
from twitch_miner.core.exceptions import ConfigError

_ENV_PREFIX = "TWITCH_MINER__"
_ENV_DELIM = "__"


def _env_overrides() -> dict[str, Any]:
    """Build a nested override mapping from ``TWITCH_MINER__SECTION__KEY`` vars.

    Values remain strings; pydantic coerces them during validation.
    """

    overrides: dict[str, Any] = {}
    for raw_key, value in os.environ.items():
        if not raw_key.startswith(_ENV_PREFIX):
            continue
        path = raw_key[len(_ENV_PREFIX) :].lower().split(_ENV_DELIM)
        cursor = overrides
        for part in path[:-1]:
            cursor = cursor.setdefault(part, {})
            if not isinstance(cursor, dict):  # conflicting scalar/section
                break
        else:
            cursor[path[-1]] = value
    return overrides


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - passthrough
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Top-level config in {path} must be a mapping.")
    return raw


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` (override wins)."""

    result = dict(base)
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = _deep_merge(existing, value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path) -> AppConfig:
    """Load and validate the application configuration.

    Args:
        path: Path to the YAML configuration file.

    Raises:
        ConfigError: If the file is missing/invalid or validation fails.
    """

    config_path = Path(path).expanduser()
    file_data = _read_yaml(config_path)
    merged = _deep_merge(file_data, _env_overrides())

    try:
        return AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(f"Configuration validation failed:\n{exc}") from exc

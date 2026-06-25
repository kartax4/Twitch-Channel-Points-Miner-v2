"""Command-line entrypoint: ``python -m twitch_miner``."""

from __future__ import annotations

import argparse
import asyncio
import sys

from twitch_miner.app import MinerApp
from twitch_miner.core.exceptions import ConfigError, MinerError


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="twitch-miner",
        description="Modern asynchronous Twitch channel points & drops miner.",
    )
    parser.add_argument(
        "--config",
        "-c",
        default="config/config.yaml",
        help="Path to the YAML configuration file (default: config/config.yaml).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Console-script entrypoint."""

    args = _parse_args(argv)
    try:
        app = MinerApp(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        return 130
    except MinerError as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

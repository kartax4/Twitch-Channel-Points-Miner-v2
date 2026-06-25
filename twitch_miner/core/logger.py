"""Logging configuration built on loguru.

Design notes:

* loguru's ``enqueue=True`` routes every log record through an internal
  multiprocessing-safe queue that is drained by a dedicated writer thread. This
  keeps formatting and (potentially blocking) file I/O off the asyncio event
  loop, so logging never stalls the miner's coroutines.
* Standard-library logging emitted by dependencies (httpx, websockets, aiohttp)
  is intercepted and re-routed through loguru for a single, consistent stream.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from twitch_miner.config.models import LoggingConfig

_CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
    "<level>{level: <8}</level> "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan> "
    "- <level>{message}</level>"
)

_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line} - {message}"
)

# Quiet down very chatty third-party loggers.
_NOISY_LOGGERS = ("httpx", "httpcore", "websockets", "aiohttp.access", "asyncio")


class _InterceptHandler(logging.Handler):
    """Redirect stdlib ``logging`` records into loguru."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk back to the caller frame outside the logging machinery so the
        # reported source location is meaningful.
        frame: object = logging.currentframe()
        depth = 2
        while getattr(frame, "f_code", None) and frame.f_code.co_filename == logging.__file__:  # type: ignore[attr-defined]
            frame = frame.f_back  # type: ignore[attr-defined]
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def _install_intercept(level: str) -> None:
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger().setLevel(getattr(logging, level, logging.INFO))


def _add_file_sink(
    config: LoggingConfig, username: str, logs_path: Path
) -> Path | None:
    """Add the rotating file sink, degrading to console-only on failure.

    A non-writable log directory (a common Docker bind-mount footgun) must never
    crash startup, so any filesystem error is logged and swallowed.
    """

    try:
        logs_path.mkdir(parents=True, exist_ok=True)
        log_file = logs_path / f"{username}.log"
        logger.add(
            log_file,
            level=config.level,
            format=_FILE_FORMAT,
            rotation=config.rotation,
            retention=config.retention,
            compression="zip",
            encoding="utf-8",
            enqueue=True,
            backtrace=True,
            diagnose=False,
        )
        return log_file
    except OSError as exc:
        logger.warning(
            "File logging disabled; cannot write to {} ({}). "
            "Check volume permissions.",
            logs_path,
            exc,
        )
        return None


def configure(
    config: LoggingConfig,
    *,
    username: str,
    logs_dir: str | Path = "logs",
) -> Path | None:
    """Configure global logging sinks.

    Args:
        config: Logging configuration slice.
        username: Account login, used to name the per-account log file.
        logs_dir: Directory where rotating log files are written.

    Returns:
        The path to the active log file, or ``None`` if file logging is off.
    """

    logger.remove()

    logger.add(
        sys.stdout,
        level=config.level,
        format=_CONSOLE_FORMAT,
        colorize=config.colored,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )

    log_file: Path | None = None
    if config.file:
        log_file = _add_file_sink(config, username, Path(logs_dir))

    _install_intercept(config.level)
    logger.debug("Logging configured (level={}, file={})", config.level, log_file)
    return log_file


async def shutdown() -> None:
    """Flush and stop all loguru sinks.

    Call during graceful shutdown so the background writer thread drains the
    queue before the process exits.
    """

    await logger.complete()
    logger.remove()


__all__ = ["configure", "logger", "shutdown"]

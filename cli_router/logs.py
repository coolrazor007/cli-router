"""Persistent diagnostic logging and metrics for CLI-Router runs."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping

from .config import RouterConfig

LOGGER_NAME = "cli_router"
LOG_FILE_NAME = "cli-router.log"
METRICS_FILE_NAME = "metrics.jsonl"
DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_BACKUP_COUNT = 5


class _IsoFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds")


def configure_logging(config: RouterConfig) -> logging.Logger:
    """Configure the package logger with one rotating file handler."""

    log_dir = _log_dir(config)
    log_path = log_dir / LOG_FILE_NAME
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(_log_level(config))
    logger.propagate = False

    for handler in list(logger.handlers):
        if not getattr(handler, "_cli_router_log_handler", False):
            continue
        if getattr(handler, "_cli_router_log_path", None) == str(log_path) and not isinstance(handler, logging.NullHandler):
            handler.setLevel(logger.level)
            return logger
        logger.removeHandler(handler)
        handler.close()

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            log_path,
            maxBytes=_positive_int(config.defaults.get("log_max_bytes"), DEFAULT_MAX_BYTES),
            backupCount=_positive_int(config.defaults.get("log_backup_count"), DEFAULT_BACKUP_COUNT),
            encoding="utf-8",
        )
    except OSError:
        handler = logging.NullHandler()
    handler.setLevel(logger.level)
    handler.setFormatter(_IsoFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler._cli_router_log_handler = True  # type: ignore[attr-defined]
    handler._cli_router_log_path = str(log_path)  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return logger


def append_run_metrics(config: RouterConfig, metrics: Mapping[str, Any]) -> Path:
    """Append one run metrics object as JSON Lines."""

    log_dir = _log_dir(config)
    metrics_path = log_dir / METRICS_FILE_NAME
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with metrics_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(dict(metrics), sort_keys=True) + "\n")
    except OSError:
        return metrics_path
    return metrics_path


def key_values(**items: Any) -> str:
    return " ".join(f"{key}={_quote_value(value)}" for key, value in items.items() if value is not None)


def _log_dir(config: RouterConfig) -> Path:
    raw = config.defaults.get("log_dir", "~/.cli-router/logs")
    return Path(str(raw)).expanduser()


def _log_level(config: RouterConfig) -> int:
    raw = str(config.defaults.get("log_level", "INFO")).upper()
    level = logging.getLevelName(raw)
    return level if isinstance(level, int) else logging.INFO


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _quote_value(value: Any) -> str:
    if isinstance(value, float):
        value = f"{value:.6f}"
    text = str(value)
    if not text:
        return '""'
    if any(character.isspace() for character in text) or '"' in text:
        return json.dumps(text)
    return text

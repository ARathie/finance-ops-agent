"""JSON lines logging.

One object per line, so a person can read it and a script can grep it. What may
be logged is deliberately narrow (docs/technical-design.md): item ids, review
codes, statuses, counts. Never attachment contents, never email bodies, and
never a rate or an amount - the tracking sheet and the emails are where money
belongs.

This sits at the top level, beside config.py, rather than under adapters/: the
application logs, and `domain/` and `application/` never import an adapter. Only
`configure` touches a file, and only the command line calls it.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any

LOGGER_NAME = "fops"
# Anything whose name suggests money or content is dropped rather than logged.
FORBIDDEN_KEYS = frozenset(
    {
        "amount",
        "amount_cents",
        "attachment",
        "bill_rate",
        "bill_rate_cents",
        "body",
        "content",
        "invoice_amount",
        "pay_rate",
        "pay_rate_cents",
        "total",
    }
)


def safe_details(details: dict[str, Any]) -> dict[str, Any]:
    """Drop anything that must not reach a log file."""
    return {key: value for key, value in details.items() if key.casefold() not in FORBIDDEN_KEYS}


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "at": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "what": record.getMessage(),
        }
        details = getattr(record, "details", None)
        if isinstance(details, dict):
            entry.update(safe_details(details))
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info).splitlines()[-1]
        return json.dumps(entry, default=str)


def configure(path: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """Log JSON lines to `path` (and always to stderr, for a person watching)."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(JsonLinesFormatter())
    logger.addHandler(stream)

    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(JsonLinesFormatter())
        logger.addHandler(handler)
    return logger


def logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log(what: str, **details: Any) -> None:
    logger().info(what, extra={"details": details})

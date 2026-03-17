"""Structured JSON logging for ai-companion backend (M4 5.2)."""

from __future__ import annotations

import logging
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("LOG_FORMAT", "json")  # "json" | "text"

# Patterns to sanitize from logs (user content at INFO level)
_SENSITIVE_PATTERNS = [
    re.compile(r'"user_input"\s*:\s*"[^"]*"'),
    re.compile(r'"user_message"\s*:\s*"[^"]*"'),
]


class JsonFormatter(logging.Formatter):
    """Output each log record as a single JSON line."""

    def __init__(self, sanitize: bool = True) -> None:
        super().__init__()
        self.sanitize = sanitize

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        # Attach extra structured fields
        for key in ("session_id", "endpoint", "duration_ms", "status_code",
                     "tokens", "error_type"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val

        raw = json.dumps(entry, ensure_ascii=False)
        if self.sanitize and record.levelno <= logging.INFO:
            for pat in _SENSITIVE_PATTERNS:
                raw = pat.sub('"<redacted>"', raw)
        return raw


class TextFormatter(logging.Formatter):
    """Human-readable formatter for local dev."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        base = f"[{ts}] {record.levelname:<7} {record.name}: {record.getMessage()}"
        if record.exc_info and record.exc_info[0] is not None:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging() -> logging.Logger:
    """Configure root logger and return the app-level logger."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # Remove existing handlers to avoid duplicates
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    if LOG_FORMAT == "json":
        handler.setFormatter(JsonFormatter(sanitize=True))
    else:
        handler.setFormatter(TextFormatter())
    root.addHandler(handler)

    # Quieten noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return logging.getLogger("ai_companion")


# Singleton app logger - import this everywhere
logger = setup_logging()

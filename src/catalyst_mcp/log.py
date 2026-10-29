"""Structured logging: one record per line on standard error (standard output carries the stdio transport).

Every record is redacted before it is written: a field whose name says it holds a secret, a bearer token or a JWT
anywhere in a value, and any `SecretStr`. `event` is how the code logs: a short event name and its fields.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

from catalyst_mcp.settings import LogFormat, LogLevel

ROOT = "catalyst_mcp"
REDACTED = "[redacted]"
SECRET_KEY = re.compile(r"token|secret|password|passwd|authorization|credential|api[_-]?key|cookie", re.IGNORECASE)
BEARER = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")


def redact(value: Any, key: str | None = None) -> Any:
    """`value` with every secret it holds replaced; `key` is the name it is stored under, if any."""
    if key is not None and SECRET_KEY.search(key):
        return REDACTED
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return JWT.sub(REDACTED, BEARER.sub(f"Bearer {REDACTED}", value))
    return value


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    fields = getattr(record, "fields", None)
    return redact(fields) if isinstance(fields, dict) else {}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": redact(record.getMessage()),
            **_fields(record),
        }
        if record.exc_info:
            doc["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(doc, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        pairs = " ".join(f"{k}={json.dumps(v, default=str)}" for k, v in _fields(record).items())
        line = f"{record.levelname.lower():<7} {redact(record.getMessage())} {pairs}".rstrip()
        return f"{line}\n{redact(self.formatException(record.exc_info))}" if record.exc_info else line


class StderrHandler(logging.StreamHandler):
    """Writes to whatever `sys.stderr` is when a record is written, not when the handler was made."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, _value: Any) -> None:
        pass


def configure(level: LogLevel = "INFO", fmt: LogFormat = "json") -> None:
    """Send catalyst-mcp's records to standard error, replacing any handler an earlier call set."""
    handler = StderrHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    logger = logging.getLogger(ROOT)
    logger.handlers[:] = [handler]
    logger.setLevel(level)
    logger.propagate = False


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT}.{name}")


def event(logger: logging.Logger, name: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, name, extra={"fields": fields})

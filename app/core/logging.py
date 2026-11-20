"""Logging estruturado em JSON com mascaramento de dados sensíveis."""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|senha|token|secret|authorization|api_key|cookie)", re.IGNORECASE
)
REDACTED = "***"
STANDARD_LOG_RECORD_FIELDS = set(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
) | {"message", "asctime"}


def redact(value: Any) -> Any:
    """Substitui recursivamente valores de chaves sensíveis antes de registrar."""
    if isinstance(value, dict):
        return {
            key: REDACTED if SENSITIVE_KEY_PATTERN.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in STANDARD_LOG_RECORD_FIELDS
        }
        payload.update(redact(extras))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

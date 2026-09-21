import json
import logging
import os
from datetime import UTC, datetime

_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render each log record as a single JSON line, including `extra` fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize the record, its `extra` fields and any exception to JSON."""
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging() -> None:
    """Configure application-wide JSON logging. Safe to call multiple times."""
    root = logging.getLogger()
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(_LEVEL)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.DEBUG
        if os.getenv("SQL_ECHO", "false").lower() == "true"
        else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance by name."""
    setup_logging()
    return logging.getLogger(name)

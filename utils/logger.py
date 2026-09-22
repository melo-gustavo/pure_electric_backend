import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

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
    """Configure application-wide JSON logging. Safe to call multiple times.

    Logs go to stdout and, unless LOG_DIR is set to empty, to a JSON-lines
    file under LOG_DIR (default "logs/app.jsonl") so a log shipper such as
    Promtail can tail them without depending on the container runtime.
    """
    root = logging.getLogger()
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(JsonFormatter())
        root.addHandler(stream_handler)

        log_dir = os.getenv("LOG_DIR", "logs")
        if log_dir:
            try:
                Path(log_dir).mkdir(parents=True, exist_ok=True)
                file_handler = logging.FileHandler(
                    Path(log_dir) / "app.jsonl", encoding="utf-8"
                )
                file_handler.setFormatter(JsonFormatter())
                root.addHandler(file_handler)
            except OSError as exc:
                stream_handler.stream.write(
                    f"Could not open log file in {log_dir!r}: {exc}\n"
                )
    root.setLevel(_LEVEL)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.DEBUG
        if os.getenv("SQL_ECHO", "false").lower() == "true"
        else logging.WARNING
    )
    # Silence watchfiles' "N changes detected": under `fastapi dev` (reload
    # watcher), every line this module writes to LOG_DIR touches a file inside
    # the watched project tree, which watchfiles logs, which writes another
    # line, which watchfiles detects again — a self-sustaining loop.
    logging.getLogger("watchfiles").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance by name."""
    setup_logging()
    return logging.getLogger(name)

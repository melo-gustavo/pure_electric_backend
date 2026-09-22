import atexit
import json
import logging
import os
import threading
import urllib.request
from datetime import UTC, datetime
from queue import Empty, Queue

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


class LokiHandler(logging.Handler):
    """Push formatted records to Loki's HTTP push API on a background thread.

    Best-effort and non-blocking: `emit` only queues, a daemon thread batches
    and POSTs every `flush_interval` seconds, and every failure is swallowed —
    logging must never be able to slow down or crash the app it instruments.
    """

    def __init__(
        self, url: str, labels: dict[str, str], flush_interval: float = 2.0
    ) -> None:
        """Start the background flush thread for the given Loki push endpoint."""
        super().__init__()
        self._push_url = url.rstrip("/") + "/loki/api/v1/push"
        self._labels = labels
        self._flush_interval = flush_interval
        self._queue: Queue[tuple[str, str]] = Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        atexit.register(self.close)

    def emit(self, record: logging.LogRecord) -> None:
        """Queue the formatted line with its own timestamp; never raises."""
        try:
            timestamp_ns = str(int(record.created * 1_000_000_000))
            self._queue.put_nowait((timestamp_ns, self.format(record)))
        except Exception:
            pass

    def _run(self) -> None:
        """Flush the queue on a timer until stopped, then flush once more."""
        while not self._stop.wait(self._flush_interval):
            self._flush()
        self._flush()

    def _flush(self) -> None:
        """Drain the queue and push everything in it as one Loki batch."""
        values = []
        while True:
            try:
                values.append(self._queue.get_nowait())
            except Empty:
                break
        if values:
            self._push(values)

    def _push(self, values: list[tuple[str, str]]) -> None:
        """POST one batch to Loki; swallow any error, this must stay silent."""
        payload = {"streams": [{"stream": self._labels, "values": values}]}
        try:
            request = urllib.request.Request(
                self._push_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=5)
        except Exception:
            pass

    def close(self) -> None:
        """Stop the background thread, flushing whatever is left first."""
        self._stop.set()
        self._thread.join(timeout=self._flush_interval + 3)
        super().close()


def setup_logging() -> None:
    """Configure application-wide JSON logging. Safe to call multiple times.

    Logs always go to stdout. When LOKI_URL is set, they are also pushed
    straight to Loki's push API from a background thread (LOKI_JOB labels the
    stream, default "pure_electric_app") — no shared volume or sidecar log
    shipper needed, which matters once API and worker run as separate,
    filesystem-isolated containers (e.g. on Railway).
    """
    root = logging.getLogger()
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(JsonFormatter())
        root.addHandler(stream_handler)

        loki_url = os.getenv("LOKI_URL", "")
        if loki_url:
            loki_handler = LokiHandler(
                loki_url, labels={"job": os.getenv("LOKI_JOB", "pure_electric_app")}
            )
            loki_handler.setFormatter(JsonFormatter())
            root.addHandler(loki_handler)
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

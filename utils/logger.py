import logging
import os

_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def setup_logging() -> None:
    """Configure application-wide logging. Safe to call multiple times."""
    logging.basicConfig(level=_LEVEL, format=_FORMAT)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.DEBUG
        if os.getenv("SQL_ECHO", "false").lower() == "true"
        else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance by name."""
    setup_logging()
    return logging.getLogger(name)

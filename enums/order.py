from enum import StrEnum


class OrderStatus(StrEnum):
    """Enum for order processing states."""

    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"

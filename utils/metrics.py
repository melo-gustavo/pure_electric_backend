from prometheus_client import Counter

orders_received_total = Counter(
    "orders_received_total", "Orders newly persisted with status RECEIVED."
)

orders_duplicate_total = Counter(
    "orders_duplicate_total",
    "Requests for an external_id that already existed (idempotent hits).",
)

orders_processed_total = Counter(
    "orders_processed_total",
    "Orders that finished processing, by final status.",
    ["status"],
)

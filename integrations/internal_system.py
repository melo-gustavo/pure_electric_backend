import asyncio
import os
import random
from decimal import Decimal

LATENCY_SECONDS = float(os.getenv("PAYMENT_MOCK_DELAY_SECONDS", "5"))
FAILURE_RATE = float(os.getenv("INTERNAL_SYSTEM_FAILURE_RATE", "0.3"))


class InternalSystemError(Exception):
    """Base error for failures reported by the internal system."""


class InternalSystemRejected(InternalSystemError):
    """The internal system refused the order; retrying will not help."""


class InternalSystemUnavailable(InternalSystemError):
    """The internal system is down or overloaded; retrying may help."""


async def send_order(external_id: str, customer: str, amount: Decimal) -> None:
    """Simulate delivering an order to the internal system.

    Behavior is driven by markers in the external ID so every scenario is
    reproducible: FAIL is rejected, DOWN is unavailable, SLOW never answers, and
    other IDs are rejected randomly at FAILURE_RATE and otherwise succeed after
    the simulated latency.
    """
    await asyncio.sleep(LATENCY_SECONDS)
    marker = external_id.upper()
    if "SLOW" in marker:
        await asyncio.sleep(3600)
    if "DOWN" in marker:
        raise InternalSystemUnavailable("Internal system is unavailable.")
    if "FAIL" in marker:
        raise InternalSystemRejected("Internal system rejected the order.")
    if random.random() < FAILURE_RATE:
        raise InternalSystemRejected("Internal system rejected the order.")

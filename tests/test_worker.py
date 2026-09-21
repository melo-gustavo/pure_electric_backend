import asyncio

import pytest
from sqlalchemy import select

from enums.order import OrderStatus
from integrations import internal_system
from models.order import Order
from workers import order as worker


@pytest.fixture(autouse=True)
def fast_internal_system(monkeypatch):
    """Remove simulated latency and backoff, and shorten the timeout."""
    monkeypatch.setattr(internal_system, "LATENCY_SECONDS", 0)
    monkeypatch.setattr(internal_system, "FAILURE_RATE", 0)
    monkeypatch.setattr(worker, "BACKOFF_BASE_SECONDS", 0)
    monkeypatch.setattr(worker, "INTERNAL_SYSTEM_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(worker, "MAX_ATTEMPTS", 3)


async def create_order(client, external_id: str) -> str:
    """Create an order through the API and return its ID."""
    response = await client.post(
        "/orders",
        json={"external_id": external_id, "customer": "Cliente", "amount": "10.00"},
    )
    return response.json()["id"]


def message_for(external_id: str) -> dict:
    """Build the queue payload the worker expects for an external ID."""
    return {"externalId": external_id, "customer": "Cliente", "amount": "10.00"}


async def load(session_factory, external_id: str) -> Order:
    """Reload an order from the database by external ID."""
    async with session_factory() as db:
        result = await db.execute(select(Order).where(Order.external_id == external_id))
        return result.scalar_one()


async def test_success_moves_order_to_processed(client, session_factory):
    """RECEIVED -> PROCESSED with processed_at stamped and no failure reason."""
    await create_order(client, "ORDER-OK")

    final = await worker.handle_order(session_factory, message_for("ORDER-OK"))

    order = await load(session_factory, "ORDER-OK")
    assert final == OrderStatus.PROCESSED
    assert order.status == OrderStatus.PROCESSED
    assert order.processed_at is not None
    assert order.failure_reason is None


async def test_rejection_moves_order_to_failed_without_retry(
    client, session_factory, monkeypatch
):
    """A rejection is permanent: FAILED with the reason, a single attempt."""
    calls = 0
    real_send = worker.send_order

    async def counting_send(*args):
        """Count calls and delegate to the real internal system."""
        nonlocal calls
        calls += 1
        await real_send(*args)

    monkeypatch.setattr(worker, "send_order", counting_send)
    await create_order(client, "ORDER-FAIL")

    final = await worker.handle_order(session_factory, message_for("ORDER-FAIL"))

    order = await load(session_factory, "ORDER-FAIL")
    assert final == OrderStatus.FAILED
    assert order.failure_reason == "Internal system rejected the order."
    assert order.processed_at is not None
    assert calls == 1


async def test_unavailable_is_retried_then_fails(client, session_factory, monkeypatch):
    """Unavailability is retried up to MAX_ATTEMPTS before FAILED."""
    calls = 0

    async def always_down(*args):
        """Fail every call as unavailable."""
        nonlocal calls
        calls += 1
        raise internal_system.InternalSystemUnavailable()

    monkeypatch.setattr(worker, "send_order", always_down)
    await create_order(client, "ORDER-DOWN")

    final = await worker.handle_order(session_factory, message_for("ORDER-DOWN"))

    order = await load(session_factory, "ORDER-DOWN")
    assert final == OrderStatus.FAILED
    assert calls == 3
    assert order.failure_reason is not None
    assert "unavailable after 3 attempts" in order.failure_reason


async def test_transient_failure_recovers_on_retry(
    client, session_factory, monkeypatch
):
    """An outage that clears before the last attempt still ends PROCESSED."""
    calls = 0

    async def flaky(*args):
        """Fail the first two calls, then succeed."""
        nonlocal calls
        calls += 1
        if calls < 3:
            raise internal_system.InternalSystemUnavailable()

    monkeypatch.setattr(worker, "send_order", flaky)
    await create_order(client, "ORDER-FLAKY")

    final = await worker.handle_order(session_factory, message_for("ORDER-FLAKY"))

    assert final == OrderStatus.PROCESSED
    assert calls == 3


async def test_slow_internal_system_times_out(client, session_factory):
    """A hanging internal system is cut by the timeout and ends FAILED."""
    await create_order(client, "ORDER-SLOW")

    final = await asyncio.wait_for(
        worker.handle_order(session_factory, message_for("ORDER-SLOW")), timeout=5
    )

    order = await load(session_factory, "ORDER-SLOW")
    assert final == OrderStatus.FAILED
    assert order.failure_reason is not None
    assert "unavailable" in order.failure_reason


async def test_unexpected_error_marks_failed(client, session_factory, monkeypatch):
    """Any unexpected exception ends FAILED instead of leaving PROCESSING."""

    async def boom(*args):
        """Raise an unexpected error."""
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "send_order", boom)
    await create_order(client, "ORDER-BOOM")

    final = await worker.handle_order(session_factory, message_for("ORDER-BOOM"))

    order = await load(session_factory, "ORDER-BOOM")
    assert final == OrderStatus.FAILED
    assert order.failure_reason == "boom"


async def test_duplicate_message_is_processed_once(
    client, session_factory, monkeypatch
):
    """Two deliveries of the same order call the internal system only once."""
    calls = 0

    async def counting_send(*args):
        """Count deliveries."""
        nonlocal calls
        calls += 1

    monkeypatch.setattr(worker, "send_order", counting_send)
    await create_order(client, "ORDER-DUP")

    first = await worker.handle_order(session_factory, message_for("ORDER-DUP"))
    second = await worker.handle_order(session_factory, message_for("ORDER-DUP"))

    assert first == OrderStatus.PROCESSED
    assert second is None
    assert calls == 1


async def test_concurrent_deliveries_claim_order_once(
    client, session_factory, monkeypatch
):
    """Simultaneous consumers: exactly one wins the claim."""
    calls = 0

    async def slow_ok(*args):
        """Succeed after yielding so both consumers overlap."""
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)

    monkeypatch.setattr(worker, "send_order", slow_ok)
    await create_order(client, "ORDER-RACE")

    results = await asyncio.gather(
        worker.handle_order(session_factory, message_for("ORDER-RACE")),
        worker.handle_order(session_factory, message_for("ORDER-RACE")),
    )

    assert sorted(r is None for r in results) == [False, True]
    assert calls == 1


async def test_unknown_order_is_skipped(session_factory):
    """A message for a missing order is skipped without error."""
    assert await worker.handle_order(session_factory, message_for("GHOST")) is None

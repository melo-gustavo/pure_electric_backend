import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from enums.order import OrderStatus
from models.order import Order
from repositories.order import OrderRepository
from schemas.order import OrderCreate


async def get_order(db, **filters) -> Order:
    """Return the matching order or None by the given column filters."""
    query = select(Order)
    for field, value in filters.items():
        query = query.where(getattr(Order, field) == value)
    result = await db.execute(query)
    return result.scalar_one_or_none()


CREATE_PAYLOAD = {
    "external_id": "ORDER-001",
    "customer": "Cliente Teste",
    "amount": "150.00",
}


async def test_create_order(client, session_factory):
    """Create an order and verify it is stored with correct defaults."""
    response = await client.post("/orders", json=CREATE_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["external_id"] == CREATE_PAYLOAD["external_id"]
    assert body["customer"] == CREATE_PAYLOAD["customer"]
    assert body["amount"] == CREATE_PAYLOAD["amount"]
    assert body["status"] == OrderStatus.RECEIVED.value
    assert body["failure_reason"] is None
    assert "created_at" in body
    assert body["processed_at"] is None
    assert uuid.UUID(body["id"])

    async with session_factory() as db:
        order = await get_order(db, external_id=CREATE_PAYLOAD["external_id"])
        assert order is not None
        assert order.status == OrderStatus.RECEIVED


async def test_create_order_duplicate_external_id(client):
    """Idempotent creation: returns existing order when external_id already exists."""
    await client.post("/orders", json=CREATE_PAYLOAD)

    duplicate = {**CREATE_PAYLOAD, "customer": "Outro Cliente"}
    response = await client.post("/orders", json=duplicate)

    assert response.status_code == 200
    body = response.json()
    assert body["external_id"] == CREATE_PAYLOAD["external_id"]
    assert body["customer"] == CREATE_PAYLOAD["customer"]  # original customer preserved
    assert body["status"] == OrderStatus.RECEIVED.value


async def test_create_order_invalid_payload(client):
    """Reject an invalid payload with 422."""
    payload = {"external_id": "", "customer": "", "amount": "-10"}
    response = await client.post("/orders", json=payload)

    assert response.status_code == 422


async def test_list_orders(client):
    """Return every created order."""
    await client.post("/orders", json=CREATE_PAYLOAD)
    await client.post(
        "/orders",
        json={
            **CREATE_PAYLOAD,
            "external_id": "ORDER-002",
            "customer": "Cliente 2",
        },
    )

    response = await client.get("/orders")

    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_get_order(client):
    """Return an order fetched by its ID."""
    created = await client.post("/orders", json=CREATE_PAYLOAD)

    response = await client.get(f"/orders/{created.json()['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created.json()["id"]


async def test_get_order_not_found(client):
    """Return 404 when the order does not exist."""
    response = await client.get(f"/orders/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_create_order_accepts_camel_case_payload(client):
    """Accept the camelCase payload from the challenge statement."""
    payload = {
        "externalId": "ORDER-CAMEL-001",
        "customer": "Cliente Exemplo",
        "amount": 150.00,
    }

    response = await client.post("/orders", json=payload)

    assert response.status_code == 201
    assert response.json()["external_id"] == payload["externalId"]


async def test_create_order_accepts_snake_case_payload(client):
    """Keep accepting snake_case, so both field spellings work."""
    response = await client.post(
        "/orders",
        json={**CREATE_PAYLOAD, "external_id": "ORDER-SNAKE-001"},
    )

    assert response.status_code == 201
    assert response.json()["external_id"] == "ORDER-SNAKE-001"


async def test_create_idempotent_recovers_from_integrity_error(
    session_factory, monkeypatch
):
    """Return the winning order when a concurrent INSERT raises IntegrityError."""
    async with session_factory() as db:
        db.add(
            Order(
                external_id="ORDER-WINNER",
                customer="Vencedor",
                amount=Decimal("150.00"),
            )
        )
        await db.commit()

        async def conflicting_create(_db, _data):
            """Simulate the INSERT losing the race to a concurrent request."""
            raise IntegrityError("INSERT INTO orders", {}, Exception("duplicate key"))

        async def lookup_winner(_db, _external_id):
            """Return the order the concurrent request managed to insert."""
            async with session_factory() as other:
                return await get_order(other, external_id="ORDER-WINNER")

        monkeypatch.setattr(OrderRepository, "create", conflicting_create)
        monkeypatch.setattr(OrderRepository, "get_by_external_id", lookup_winner)

        data = OrderCreate(
            externalId="ORDER-LOSER", customer="Cliente Teste", amount="150.00"
        )
        order, created = await OrderRepository.create_idempotent(db, data)

        assert created is False
        assert order.external_id == "ORDER-WINNER"
        assert order.customer == "Vencedor"


async def test_update_status_stamps_terminal_state(session_factory):
    """Stamp processed_at and failure_reason only on terminal states."""
    async with session_factory() as db:
        db.add(
            Order(
                external_id="ORDER-TERMINAL",
                customer="Cliente Teste",
                amount=Decimal("150.00"),
            )
        )
        await db.commit()

    async with session_factory() as db:
        await OrderRepository.update_status(
            db, "ORDER-TERMINAL", OrderStatus.PROCESSING
        )
        pending = await get_order(db, external_id="ORDER-TERMINAL")
        assert pending.status == OrderStatus.PROCESSING
        assert pending.processed_at is None
        assert pending.failure_reason is None

    async with session_factory() as db:
        await OrderRepository.update_status(
            db, "ORDER-TERMINAL", OrderStatus.FAILED, failure_reason="boom"
        )
        failed = await get_order(db, external_id="ORDER-TERMINAL")
        assert failed.status == OrderStatus.FAILED
        assert failed.failure_reason == "boom"
        assert failed.processed_at is not None

    async with session_factory() as db:
        await OrderRepository.update_status(db, "ORDER-TERMINAL", OrderStatus.PROCESSED)
        processed = await get_order(db, external_id="ORDER-TERMINAL")
        assert processed.status == OrderStatus.PROCESSED
        assert processed.failure_reason is None
        assert processed.processed_at is not None

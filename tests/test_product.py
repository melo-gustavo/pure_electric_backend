import uuid
from decimal import Decimal

from sqlalchemy import select

from models.product import Product


async def get_product(db, **filters) -> Product:
    """Return the matching product or None by the given column filters."""
    query = select(Product)
    for field, value in filters.items():
        query = query.where(getattr(Product, field) == value)
    result = await db.execute(query)
    return result.scalar_one_or_none()


CREATE_PAYLOAD = {
    "name": "Produto Teste",
    "description": "Descrição do produto",
    "amount": "99.90",
    "image": "https://example.com/image.jpg",
}


async def test_create_product(client, session_factory):
    """Create a product and verify it is stored correctly."""
    response = await client.post("/products", json=CREATE_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == CREATE_PAYLOAD["name"]
    assert body["description"] == CREATE_PAYLOAD["description"]
    assert body["amount"] == CREATE_PAYLOAD["amount"]
    assert body["image"] == CREATE_PAYLOAD["image"]
    assert "created_at" in body
    assert body["updated_at"] is None
    assert uuid.UUID(body["id"])

    async with session_factory() as db:
        product = await get_product(db, name=CREATE_PAYLOAD["name"])
        assert product is not None
        assert product.amount == Decimal(CREATE_PAYLOAD["amount"])


async def test_create_product_without_optional_fields(client):
    """Create a product providing only required fields."""
    payload = {"name": "Produto Simples", "amount": "10.00"}
    response = await client.post("/products", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["description"] is None
    assert body["image"] is None


async def test_create_product_invalid_payload(client):
    """Reject an invalid payload with 422."""
    payload = {"name": "", "amount": "-5"}
    response = await client.post("/products", json=payload)

    assert response.status_code == 422


async def test_list_products(client):
    """Return every created product."""
    await client.post("/products", json=CREATE_PAYLOAD)
    await client.post(
        "/products",
        json={
            **CREATE_PAYLOAD,
            "name": "Produto 2",
            "amount": "199.90",
        },
    )

    response = await client.get("/products")

    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_get_product(client):
    """Return a product fetched by its ID."""
    created = await client.post("/products", json=CREATE_PAYLOAD)

    response = await client.get(f"/products/{created.json()['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created.json()["id"]


async def test_get_product_not_found(client):
    """Return 404 when the product does not exist."""
    response = await client.get(f"/products/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_update_product(client):
    """Update product fields and return the updated record."""
    created = await client.post("/products", json=CREATE_PAYLOAD)

    response = await client.patch(
        f"/products/{created.json()['id']}",
        json={"name": "Produto Atualizado", "amount": "149.90"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Produto Atualizado"
    assert body["amount"] == "149.90"
    assert body["updated_at"] is not None


async def test_update_product_not_found(client):
    """Return 404 when updating an unknown product."""
    response = await client.patch(
        f"/products/{uuid.uuid4()}", json={"name": "Inexistente"}
    )

    assert response.status_code == 404


async def test_delete_product(client):
    """Delete a product and confirm it is gone afterwards."""
    created = await client.post("/products", json=CREATE_PAYLOAD)
    product_id = created.json()["id"]

    response = await client.delete(f"/products/{product_id}")

    assert response.status_code == 204
    assert (await client.get(f"/products/{product_id}")).status_code == 404


async def test_delete_product_not_found(client):
    """Return 404 when deleting an unknown product."""
    response = await client.delete(f"/products/{uuid.uuid4()}")

    assert response.status_code == 404

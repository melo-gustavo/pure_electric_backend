import uuid

from sqlalchemy import select

from models.user import User
from utils.security import SecurityUtils

CREATE_PAYLOAD = {
    "phone": "11999999999",
    "email": "maria@example.com",
    "password": "secret123",
    "full_name": "Maria da Silva",
    "cpf": "12345678900",
    "cnpj": "12345678000199",
}


async def get_user(db, **filters) -> User:
    """Return the matching user or None by the given column filters."""
    query = select(User)
    for field, value in filters.items():
        query = query.where(getattr(User, field) == value)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def test_create_user(client, session_factory):
    """Create a user with all fields and assert the stored password is hashed."""
    response = await client.post("/users", json=CREATE_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["phone"] == CREATE_PAYLOAD["phone"]
    assert body["email"] == CREATE_PAYLOAD["email"]
    assert body["full_name"] == CREATE_PAYLOAD["full_name"]
    assert body["cpf"] == CREATE_PAYLOAD["cpf"]
    assert body["cnpj"] == CREATE_PAYLOAD["cnpj"]
    assert "password" not in body
    assert uuid.UUID(body["id"])

    async with session_factory() as db:
        user = await get_user(db, phone=CREATE_PAYLOAD["phone"])
        assert user is not None
        assert user.password is not None
        assert user.password != CREATE_PAYLOAD["password"]
        assert SecurityUtils.verify_password(CREATE_PAYLOAD["password"], user.password)


async def test_create_user_without_optional_fields(client):
    """Create a user providing only required fields."""
    payload = {"phone": "11988887777", "full_name": "João Souza"}
    response = await client.post("/users", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] is None
    assert body["cpf"] is None
    assert body["cnpj"] is None


async def test_create_user_duplicate_phone(client):
    """Reject creation when the phone already exists."""
    await client.post("/users", json=CREATE_PAYLOAD)

    duplicate = {**CREATE_PAYLOAD, "email": "outro@example.com"}
    response = await client.post("/users", json=duplicate)

    assert response.status_code == 409
    assert "phone" in response.json()["detail"]


async def test_create_user_duplicate_email(client):
    """Reject creation when the email already exists."""
    await client.post("/users", json=CREATE_PAYLOAD)

    duplicate = {**CREATE_PAYLOAD, "phone": "11977776666"}
    response = await client.post("/users", json=duplicate)

    assert response.status_code == 409
    assert "email" in response.json()["detail"]


async def test_create_user_duplicate_cnpj(client):
    """Reject creation when the cnpj already exists."""
    await client.post("/users", json=CREATE_PAYLOAD)

    duplicate = {
        **CREATE_PAYLOAD,
        "phone": "11977776666",
        "email": None,
        "cpf": None,
    }
    response = await client.post("/users", json=duplicate)

    assert response.status_code == 409
    assert "cnpj" in response.json()["detail"]


async def test_create_user_duplicate_cpf(client):
    """Reject creation when the cpf already exists."""
    await client.post("/users", json=CREATE_PAYLOAD)

    duplicate = {
        **CREATE_PAYLOAD,
        "phone": "11977776666",
        "email": "outro@example.com",
        "cnpj": None,
    }
    response = await client.post("/users", json=duplicate)

    assert response.status_code == 409
    assert "cpf" in response.json()["detail"]


async def test_create_user_invalid_payload(client):
    """Reject an invalid payload with 422."""
    payload = {"phone": "11999999999", "email": "not-an-email", "full_name": "A"}
    response = await client.post("/users", json=payload)

    assert response.status_code == 422


async def test_list_users(client):
    """Return every created user."""
    await client.post("/users", json=CREATE_PAYLOAD)
    await client.post(
        "/users",
        json={
            **CREATE_PAYLOAD,
            "phone": "11988887777",
            "email": None,
            "cnpj": None,
            "cpf": None,
        },
    )

    response = await client.get("/users")

    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_get_user(client):
    """Return a user fetched by its ID."""
    created = await client.post("/users", json=CREATE_PAYLOAD)

    response = await client.get(f"/users/{created.json()['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created.json()["id"]


async def test_get_user_not_found(client):
    """Return 404 when the user does not exist."""
    response = await client.get(f"/users/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_update_user(client):
    """Update user fields and return the updated record."""
    created = await client.post("/users", json=CREATE_PAYLOAD)

    response = await client.patch(
        f"/users/{created.json()['id']}",
        json={"full_name": "Maria Oliveira", "phone": "11955554444"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Maria Oliveira"
    assert body["phone"] == "11955554444"


async def test_update_user_password(client, session_factory):
    """Update a password and assert it is stored hashed."""
    created = await client.post("/users", json=CREATE_PAYLOAD)

    response = await client.patch(
        f"/users/{created.json()['id']}", json={"password": "newsecret456"}
    )

    assert response.status_code == 200
    async with session_factory() as db:
        user = await get_user(db, id=uuid.UUID(created.json()["id"]))
        assert user.password is not None
        assert user.password != "newsecret456"
        assert SecurityUtils.verify_password("newsecret456", user.password)


async def test_update_user_conflict(client):
    """Reject an update that collides with another user's unique field."""
    first = await client.post("/users", json=CREATE_PAYLOAD)
    await client.post(
        "/users",
        json={
            **CREATE_PAYLOAD,
            "phone": "11988887777",
            "email": None,
            "cnpj": None,
            "cpf": None,
        },
    )

    response = await client.patch(
        f"/users/{first.json()['id']}", json={"phone": "11988887777"}
    )

    assert response.status_code == 409


async def test_update_user_not_found(client):
    """Return 404 when updating an unknown user."""
    response = await client.patch(
        f"/users/{uuid.uuid4()}", json={"full_name": "Ninguém"}
    )

    assert response.status_code == 404


async def test_delete_user(client):
    """Delete a user and confirm it is gone afterwards."""
    created = await client.post("/users", json=CREATE_PAYLOAD)
    user_id = created.json()["id"]

    response = await client.delete(f"/users/{user_id}")

    assert response.status_code == 204
    assert (await client.get(f"/users/{user_id}")).status_code == 404


async def test_delete_user_not_found(client):
    """Return 404 when deleting an unknown user."""
    response = await client.delete(f"/users/{uuid.uuid4()}")

    assert response.status_code == 404

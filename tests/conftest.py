import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from databases.postgres import get_session
from main import app
from models.base_model import Base


@pytest_asyncio.fixture
async def session_factory():
    """Provide an in-memory SQLite session factory with a clean schema."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield session_factory
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session_factory):
    """Provide an HTTP client whose session dependency points to SQLite."""

    async def override_get_session():
        """Yield a test session instead of the production one."""
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()

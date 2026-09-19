import os
from collections.abc import AsyncIterator

from dotenv import load_dotenv
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from utils.logger import get_logger

load_dotenv()
logger = get_logger("DATABASE")

DATABASE_USER = os.getenv("DATABASE_USER", "postgres")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD", "postgres")
DATABASE_NAME = os.getenv("DATABASE_NAME", "pure_electric")
DATABASE_HOST = os.getenv("DATABASE_HOST", "localhost")
DATABASE_PORT = os.getenv("DATABASE_PORT", "5432")

DATABASE_URL = (
    f"postgresql+asyncpg://{DATABASE_USER}:{DATABASE_PASSWORD}"
    f"@{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_NAME}"
)

engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _log_db_connection(_dbapi_connection, _connection_record) -> None:
    """Log a successful database connection."""
    logger.info(
        "Database connection established: %s@%s:%s/%s",
        DATABASE_USER,
        DATABASE_HOST,
        DATABASE_PORT,
        DATABASE_NAME,
    )


@event.listens_for(engine.sync_engine, "close")
def _log_db_disconnect(_dbapi_connection, _connection_record) -> None:
    """Log database disconnection."""
    logger.info("Database connection closed")


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async database session for the current request."""
    logger.debug("Opening database session")
    async with SessionLocal() as session:
        yield session
    logger.debug("Closing database session")

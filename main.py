from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from databases.postgres import SessionLocal
from routes.include_router import include_app_routers
from seeders.product_seeder import seed_products
from seeders.user_seeder import seed_users
from utils.logger import get_logger, setup_logging

setup_logging()
logger = get_logger("APP")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Seed initial users and products on startup before serving requests."""
    try:
        async with SessionLocal() as session:
            await seed_users(session)
            await seed_products(session)
    except Exception as exc:
        logger.warning("Seeding skipped: %s", exc)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Pure Electric API",
    version="1.0.0",
    description="""Pure Electric is a platform specializing in high-end
    electric scooters, offering a sophisticated shopping experience for
    those seeking sustainable urban mobility and a conscious lifestyle.""",
)

app.mount("/images", StaticFiles(directory="images"), name="images")

include_app_routers(app)

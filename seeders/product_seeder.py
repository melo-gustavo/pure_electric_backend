from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.product import Product
from utils.logger import get_logger

logger = get_logger("PRODUCT")

SEED_PRODUCTS: list[dict[str, Any]] = [
    {
        "name": "Escape",
        "image": "/images/escape.webp",
        "description": "Até 45 km de autonomia. Postura ideal de pilotagem.",
        "amount": Decimal("9900.00"),
    },
    {
        "name": "Escape Pro+ Suspension",
        "image": "/images/escape_pro_suspension.webp",
        "description": "Até 60 km de autonomia. Postura ideal de pilotagem.",
        "amount": Decimal("11900.00"),
    },
    {
        "name": "Escape Ultra Max Suspension",
        "image": "/images/escape_ultra_max_suspension.webp",
        "description": "Até 75 km de autonomia. Postura ideal de pilotagem.",
        "amount": Decimal("13900.00"),
    },
    {
        "name": "Advance",
        "image": "/images/advance.webp",
        "description": "Até 75 km de autonomia. Postura ideal de pilotagem.",
        "amount": Decimal("17900.00"),
    },
    {
        "name": "Flex",
        "image": "/images/flex.webp",
        "description": "O e-scooter mais compacto do mundo.",
        "amount": Decimal("17900.00"),
    },
    {
        "name": "Pure x McLaren MP4/4",
        "image": "/images/pure_x_mclaren_mp4.webp",
        "description": "Projetado com DNA das corridas.",
        "amount": Decimal("19900.00"),
    },
    {
        "name": "Pure x McLaren Flex",
        "image": "/images/pure_x_mclaren_flex.webp",
        "description": (
            "A engenharia das corridas de nível mundial encontra o e-scooter "
            "mais compacto do mundo."
        ),
        "amount": Decimal("22900.00"),
    },
]


async def seed_products(db: AsyncSession) -> None:
    """Create any missing seed products, leaving existing ones untouched."""
    created = 0
    for data in SEED_PRODUCTS:
        result = await db.execute(select(Product).where(Product.name == data["name"]))
        existing = result.scalar_one_or_none()
        if existing is not None:
            if existing.image is None and data.get("image"):
                existing.image = data["image"]
            continue

        db.add(Product(**data))
        created += 1

    await db.commit()
    logger.info("Seeded %d product(s)", created)

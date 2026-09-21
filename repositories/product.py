import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.product import Product
from schemas.product import ProductCreate, ProductUpdate


class ProductRepository:
    @staticmethod
    async def get_products(db: AsyncSession) -> list[Product]:
        """Return all products."""
        result = await db.execute(select(Product))
        return list(result.scalars().all())

    @staticmethod
    async def get_product_by_id(db: AsyncSession, product_id: uuid.UUID) -> Product:
        """Return a product by ID or raise 404 when not found."""
        result = await db.execute(select(Product).where(Product.id == product_id))
        product = result.scalar_one_or_none()
        if product is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Product not found."
            )
        return product

    @staticmethod
    async def create(db: AsyncSession, data: ProductCreate) -> Product:
        """Create a new product."""
        product = Product(
            name=data.name,
            description=data.description,
            amount=data.amount,
            image=data.image,
        )
        db.add(product)
        await db.commit()
        await db.refresh(product)
        return product

    @staticmethod
    async def update(
        db: AsyncSession, product_id: uuid.UUID, data: ProductUpdate
    ) -> Product:
        """Update a product."""
        product = await ProductRepository.get_product_by_id(db, product_id)

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(product, field, value)

        product.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(product)
        return product

    @staticmethod
    async def delete(db: AsyncSession, product_id: uuid.UUID) -> None:
        """Delete a product."""
        product = await ProductRepository.get_product_by_id(db, product_id)
        await db.delete(product)
        await db.commit()

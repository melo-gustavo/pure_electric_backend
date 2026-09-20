import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.order import Order
from schemas.order import OrderCreate


class OrderRepository:
    @staticmethod
    async def get_orders(db: AsyncSession) -> list[Order]:
        """Return all orders."""
        result = await db.execute(select(Order))
        return list(result.scalars().all())

    @staticmethod
    async def get_order_by_id(db: AsyncSession, order_id: uuid.UUID) -> Order:
        """Return an order by ID or raise 404 when not found."""
        result = await db.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
            )
        return order

    @staticmethod
    async def get_order_for_processing(
        db: AsyncSession, order_id: uuid.UUID
    ) -> Order | None:
        """Return an order by ID or None when not found."""
        result = await db.execute(select(Order).where(Order.id == order_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def create_or_get_order(
        db: AsyncSession, data: OrderCreate
    ) -> tuple[Order, bool]:
        """Create an order, returning the existing one for a repeated external ID."""
        existing = await db.execute(
            select(Order).where(Order.external_id == data.external_id)
        )
        found = existing.scalar_one_or_none()
        if found is not None:
            return found, False

        order = Order(**data.model_dump(exclude_unset=True))
        db.add(order)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            result = await db.execute(
                select(Order).where(Order.external_id == data.external_id)
            )
            found = result.scalar_one_or_none()
            if found is None:
                raise
            return found, False
        await db.refresh(order)
        return order, True

    @staticmethod
    async def save_state(db: AsyncSession, order: Order) -> Order:
        """Persist the current order state and return the refreshed instance."""
        await db.commit()
        await db.refresh(order)
        return order

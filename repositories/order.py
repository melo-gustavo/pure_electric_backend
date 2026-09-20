import json
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enums.order import OrderStatus
from models.order import Order
from queues.rabbitmq import publish
from schemas.order import OrderCreate
from utils.logger import get_logger

logger = get_logger(__name__)


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
    async def create(db: AsyncSession, data: OrderCreate) -> Order:
        """Create a new order."""
        logger.info(
            "Creating order",
            extra={"external_id": data.external_id, "customer": data.customer},
        )
        order = Order(
            external_id=data.external_id,
            customer=data.customer,
            amount=data.amount,
            status=OrderStatus.RECEIVED,
        )
        db.add(order)
        await db.commit()
        await db.refresh(order)
        logger.info(
            "Order created",
            extra={"order_id": str(order.id), "external_id": order.external_id},
        )
        return order

    @staticmethod
    async def create_and_publish(db: AsyncSession, data: OrderCreate) -> Order:
        """Create a new order and publish to processing queue."""
        order = await OrderRepository.create(db, data)

        message = {
            "id": str(order.id),
            "externalId": order.external_id,
            "customer": order.customer,
            "amount": str(order.amount),
            "status": order.status.value,
            "createdAt": order.created_at.isoformat(),
        }
        logger.info(
            "Publishing order to queue",
            extra={"order_id": str(order.id), "queue": "orders"},
        )
        await publish("orders", json.dumps(message))
        logger.info("Order published to queue", extra={"order_id": str(order.id)})

        return order

    @staticmethod
    async def find_by_external_id(db: AsyncSession, external_id: str) -> Order | None:
        """Return an order by external ID or None when not found."""
        result = await db.execute(select(Order).where(Order.external_id == external_id))

        result = result.scalar_one_or_none()

        if result is None:
            raise HTTPException(status_code=404, detail="Order not found.")

        return result

    @staticmethod
    async def update_status(db: AsyncSession, external_id: str, status: OrderStatus):
        """Update the status of an order by external ID."""
        order = await OrderRepository.find_by_external_id(db, external_id)
        if order:
            order.status = status
            await db.commit()

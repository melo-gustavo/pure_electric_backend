import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from enums.order import OrderStatus
from models.order import Order
from queues.rabbitmq import publish
from schemas.order import OrderCreate
from utils.logger import get_logger

logger = get_logger("ORDER")


class OrderRepository:
    @staticmethod
    async def get_orders(
        db: AsyncSession,
        statuses: list[OrderStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Order], int]:
        """Return one page of orders, newest first, plus the total that matches.

        Ordering by (created_at, id) keeps pages stable when timestamps tie.
        """
        conditions = [Order.status.in_(statuses)] if statuses else []
        total = await db.scalar(
            select(func.count()).select_from(Order).where(*conditions)
        )
        result = await db.execute(
            select(Order)
            .where(*conditions)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all()), total or 0

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
    async def get_by_external_id(db: AsyncSession, external_id: str) -> Order | None:
        """Return an order by external ID or None when not found."""
        result = await db.execute(select(Order).where(Order.external_id == external_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def create(db: AsyncSession, data: OrderCreate) -> Order:
        """Create a new order (assumes external_id does not exist)."""
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
    async def create_idempotent(
        db: AsyncSession, data: OrderCreate
    ) -> tuple[Order, bool]:
        """Create order idempotently: returns (existing_order, False)
        if external_id exists, else (new_order, True).
        Uses SELECT FOR UPDATE to serialize requests that already see the row, and
        falls back to the UNIQUE constraint when two requests insert the same
        external_id at the same time.
        """
        logger.info(
            "Creating order idempotently",
            extra={"external_id": data.external_id, "customer": data.customer},
        )

        result = await db.execute(
            select(Order).where(Order.external_id == data.external_id).with_for_update()
        )
        existing = result.scalar_one_or_none()

        if existing:
            logger.info(
                "Order already exists, returning existing",
                extra={
                    "order_id": str(existing.id),
                    "external_id": existing.external_id,
                    "status": existing.status.value,
                },
            )
            return existing, False

        try:
            order = await OrderRepository.create(db, data)
        except IntegrityError:
            await db.rollback()
            existing = await OrderRepository.get_by_external_id(db, data.external_id)
            if existing is None:
                raise
            logger.info(
                "Concurrent insert lost the race, returning the winning order",
                extra={
                    "order_id": str(existing.id),
                    "external_id": existing.external_id,
                    "status": existing.status.value,
                },
            )
            return existing, False

        return order, True

    @staticmethod
    async def create_and_publish(db: AsyncSession, data: OrderCreate) -> Order:
        """Create a new order idempotently and publish to processing queue
        if newly created."""
        order, created = await OrderRepository.create_idempotent(db, data)

        if created and order.status == OrderStatus.RECEIVED:
            await OrderRepository._publish_order(order)

        return order

    @staticmethod
    async def _publish_order(order: Order) -> None:
        """Publish order to processing queue."""
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

    @staticmethod
    async def claim_for_processing(db: AsyncSession, external_id: str) -> bool:
        """Atomically move an order from RECEIVED to PROCESSING.

        Returns False when the order does not exist or was already claimed, so
        only one consumer ever processes a given order.
        """
        result = await db.execute(
            update(Order)
            .where(
                Order.external_id == external_id, Order.status == OrderStatus.RECEIVED
            )
            .values(status=OrderStatus.PROCESSING)
            .returning(Order.id)
        )
        claimed = result.scalar_one_or_none()
        await db.commit()
        return claimed is not None

    @staticmethod
    async def reset_for_reprocessing(db: AsyncSession, order_id: uuid.UUID) -> Order:
        """Return a FAILED or stuck RECEIVED order to RECEIVED, or raise 409."""
        order = await OrderRepository.get_order_by_id(db, order_id)
        if order.status not in (OrderStatus.FAILED, OrderStatus.RECEIVED):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only FAILED or RECEIVED orders can be reprocessed.",
            )
        order.status = OrderStatus.RECEIVED
        order.failure_reason = None
        order.processed_at = None
        await db.commit()
        await db.refresh(order)
        return order

    @staticmethod
    async def update_status(
        db: AsyncSession,
        external_id: str,
        status: OrderStatus,
        failure_reason: str | None = None,
    ) -> None:
        """Update an order status, stamping processed_at and failure_reason on
        terminal states."""
        order = await OrderRepository.get_by_external_id(db, external_id)
        if order is None:
            return

        order.status = status

        if status in (OrderStatus.PROCESSED, OrderStatus.FAILED):
            order.processed_at = datetime.now(UTC)
            order.failure_reason = (
                failure_reason if status is OrderStatus.FAILED else None
            )

        await db.commit()

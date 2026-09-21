import asyncio
import json
import random

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from databases.postgres import SessionLocal
from enums.order import OrderStatus
from queues.rabbitmq import RABBITMQ_URL
from repositories.order import OrderRepository
from utils.logger import get_logger

logger = get_logger("ORDER")


QUEUE_NAME = "orders"
MOCK_REJECTION_REASON = "Internal system returned a processing failure."


async def process_order(message: AbstractIncomingMessage):
    """Consume an order message and drive the order to its final status."""
    async with message.process():
        data = json.loads(message.body)
        external_id = data["externalId"]
        logger.info("Processing order", extra={"external_id": external_id})

        async with SessionLocal() as db:
            repo = OrderRepository()

            order = await repo.get_by_external_id(db, external_id)
            if not order:
                logger.warning(
                    "Order not found, skipping", extra={"external_id": external_id}
                )
                return

            if order.status in (OrderStatus.PROCESSED, OrderStatus.FAILED):
                logger.info(
                    "Order already processed, skipping duplicate message",
                    extra={"external_id": external_id, "status": order.status.value},
                )
                return

            if order.status == OrderStatus.PROCESSING:
                logger.warning(
                    "Order already being processed, skipping duplicate message",
                    extra={"external_id": external_id},
                )
                return

            try:
                await repo.update_status(db, external_id, OrderStatus.PROCESSING)
                logger.info(
                    "Order status updated to PROCESSING",
                    extra={
                        "external_id": external_id,
                        "status": OrderStatus.PROCESSING.value,
                    },
                )

                success = await call_payment_mock()

                if success:
                    await repo.update_status(db, external_id, OrderStatus.PROCESSED)
                    logger.info(
                        "Order processing completed",
                        extra={
                            "external_id": external_id,
                            "status": OrderStatus.PROCESSED.value,
                        },
                    )
                else:
                    await repo.update_status(
                        db,
                        external_id,
                        OrderStatus.FAILED,
                        failure_reason=MOCK_REJECTION_REASON,
                    )
                    logger.warning(
                        "Order rejected by the internal system",
                        extra={
                            "external_id": external_id,
                            "status": OrderStatus.FAILED.value,
                            "failure_reason": MOCK_REJECTION_REASON,
                        },
                    )

            except Exception as e:
                logger.exception(
                    "Order processing failed",
                    extra={"external_id": external_id, "error": str(e)},
                )
                await repo.update_status(
                    db,
                    external_id,
                    OrderStatus.FAILED,
                    failure_reason=str(e)[:500],
                )


async def call_payment_mock() -> bool:
    """Simulate the internal system call, succeeding on roughly half the calls."""
    await asyncio.sleep(0.5)

    number = random.randint(1, 100)

    return number % 2 == 0


async def start_worker():
    """Connect to RabbitMQ and consume the orders queue until stopped."""
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()
    queue = await channel.declare_queue(QUEUE_NAME, durable=True)
    await queue.consume(process_order)
    logger.info(f"Worker listening on queue: {QUEUE_NAME}")
    await asyncio.Future()

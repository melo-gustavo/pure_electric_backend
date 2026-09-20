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

logger = get_logger(__name__)


QUEUE_NAME = "orders"


async def process_order(message: AbstractIncomingMessage):
    async with message.process():
        data = json.loads(message.body)
        external_id = data["externalId"]
        logger.info("Processing order", extra={"external_id": external_id})

        async with SessionLocal() as db:
            repo = OrderRepository()

            try:
                await repo.update_status(db, external_id, OrderStatus.PROCESSING)
                logger.info(
                    f"Order status updated to {OrderStatus.PROCESSING.value}",
                    extra={
                        "external_id": external_id,
                        "status": OrderStatus.PROCESSING.value,
                    },
                )

                success = await call_payment_mock()

                final_status = OrderStatus.PROCESSED if success else OrderStatus.FAILED
                await repo.update_status(db, external_id, final_status)
                logger.info(
                    f"Order processing completed with status: {final_status.value}",
                    extra={"external_id": external_id, "status": final_status.value},
                )

            except Exception as e:
                logger.exception(
                    f"""Order processing {OrderStatus.FAILED.value}
                    for external_id: {external_id}""",
                    extra={"external_id": external_id, "error": str(e)},
                )
                await repo.update_status(db, external_id, OrderStatus.FAILED)


async def call_payment_mock() -> bool:
    await asyncio.sleep(0.5)

    number = random.randint(1, 100)

    return number % 2 == 0


async def start_worker():
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()
    queue = await channel.declare_queue(QUEUE_NAME, durable=True)
    await queue.consume(process_order)
    logger.info(f"Worker listening on queue: {QUEUE_NAME}")
    await asyncio.Future()

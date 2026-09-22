import asyncio
import json
import os
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from databases.postgres import SessionLocal
from enums.order import OrderStatus
from integrations.internal_system import (
    InternalSystemRejected,
    InternalSystemUnavailable,
    send_order,
)
from queues.rabbitmq import RABBITMQ_URL
from repositories.order import OrderRepository
from utils.logger import get_logger
from utils.metrics import orders_processed_total

logger = get_logger("ORDER")


QUEUE_NAME = "orders"
PREFETCH_COUNT = int(os.getenv("WORKER_PREFETCH", "5"))
INTERNAL_SYSTEM_TIMEOUT_SECONDS = float(
    os.getenv("INTERNAL_SYSTEM_TIMEOUT_SECONDS", "10")
)
MAX_ATTEMPTS = int(os.getenv("INTERNAL_SYSTEM_MAX_ATTEMPTS", "3"))
BACKOFF_BASE_SECONDS = float(os.getenv("INTERNAL_SYSTEM_BACKOFF_SECONDS", "1"))


async def deliver_with_retry(data: dict[str, Any]) -> None:
    """Send the order to the internal system, retrying transient failures.

    Timeouts and unavailability are retried with exponential backoff up to
    MAX_ATTEMPTS; a rejection is permanent and propagates immediately.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            await asyncio.wait_for(
                send_order(data["externalId"], data["customer"], data["amount"]),
                timeout=INTERNAL_SYSTEM_TIMEOUT_SECONDS,
            )
            return
        except (TimeoutError, InternalSystemUnavailable) as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            delay = BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
            logger.warning(
                "Internal system call failed, retrying",
                extra={
                    "external_id": data["externalId"],
                    "attempt": attempt,
                    "retry_in_seconds": delay,
                    "error": type(exc).__name__,
                },
            )
            await asyncio.sleep(delay)


async def handle_order(
    session_factory: async_sessionmaker[AsyncSession], data: dict[str, Any]
) -> OrderStatus | None:
    """Drive one order RECEIVED -> PROCESSING -> PROCESSED/FAILED.

    Returns the final status, or None when the message was skipped because the
    order is missing or was already claimed by another delivery.
    """
    external_id = data["externalId"]
    logger.info("Processing order", extra={"external_id": external_id})

    async with session_factory() as db:
        if not await OrderRepository.claim_for_processing(db, external_id):
            logger.info(
                "Order not claimable (missing or already handled), skipping",
                extra={"external_id": external_id},
            )
            return None

        try:
            await deliver_with_retry(data)
        except InternalSystemRejected as exc:
            final, reason = OrderStatus.FAILED, str(exc)
        except (TimeoutError, InternalSystemUnavailable) as exc:
            final = OrderStatus.FAILED
            reason = (
                f"Internal system unavailable after {MAX_ATTEMPTS} attempts "
                f"({type(exc).__name__})."
            )
        except Exception as exc:
            logger.exception(
                "Order processing failed", extra={"external_id": external_id}
            )
            final, reason = OrderStatus.FAILED, str(exc)[:500]
        else:
            final, reason = OrderStatus.PROCESSED, None

        await OrderRepository.update_status(db, external_id, final, reason)
        orders_processed_total.labels(status=final.value).inc()
        logger.info(
            "Order processing finished",
            extra={
                "external_id": external_id,
                "status": final.value,
                "failure_reason": reason,
            },
        )
        return final


async def process_order(message: AbstractIncomingMessage):
    """Consume an order message and hand it to handle_order."""
    async with message.process():
        await handle_order(SessionLocal, json.loads(message.body))


async def start_worker():
    """Connect to RabbitMQ and consume the orders queue until stopped."""
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=PREFETCH_COUNT)
    queue = await channel.declare_queue(QUEUE_NAME, durable=True)
    await queue.consume(process_order)
    logger.info(f"Worker listening on queue: {QUEUE_NAME}")
    await asyncio.Future()

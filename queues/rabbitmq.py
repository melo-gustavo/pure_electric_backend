import os

import aio_pika

RABBITMQ_URL = os.getenv("RABBITMQ_URL")


async def get_channel():
    """Open a new robust connection and return a channel on it."""
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    return await connection.channel()


async def publish(queue_name: str, message: str):
    """Declare a durable queue and publish a persistent message to it."""
    channel = await get_channel()
    await channel.declare_queue(queue_name, durable=True)
    await channel.default_exchange.publish(
        aio_pika.Message(
            body=message.encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        ),
        routing_key=queue_name,
    )

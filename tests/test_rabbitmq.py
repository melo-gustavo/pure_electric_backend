import json
from unittest.mock import AsyncMock, patch

import pytest

from queues.rabbitmq import publish


class TestRabbitMQ:
    @pytest.mark.asyncio
    async def test_publish_calls_aio_pika_with_correct_params(self):
        """Test that publish declares queue and sends persistent message."""
        test_message = {"order_id": "123", "status": "PROCESSING"}
        queue_name = "test_orders"

        with patch("queues.rabbitmq.aio_pika.connect_robust") as mock_connect:
            mock_connection = AsyncMock()
            mock_channel = AsyncMock()
            mock_queue = AsyncMock()

            mock_connect.return_value = mock_connection
            mock_connection.channel.return_value = mock_channel
            mock_channel.declare_queue.return_value = mock_queue

            await publish(queue_name, json.dumps(test_message))

            mock_connect.assert_called_once()
            mock_connection.channel.assert_called_once()
            mock_channel.declare_queue.assert_called_once_with(queue_name, durable=True)
            mock_channel.default_exchange.publish.assert_called_once()

            # Verify the published message
            call_args = mock_channel.default_exchange.publish.call_args
            published_message = call_args[0][0]
            routing_key = call_args[1]["routing_key"]

            assert routing_key == queue_name
            assert published_message.delivery_mode.name == "PERSISTENT"
            assert json.loads(published_message.body.decode()) == test_message

    @pytest.mark.asyncio
    async def test_publish_handles_connection_error(self):
        """Test that publish propagates connection errors."""
        with patch("queues.rabbitmq.aio_pika.connect_robust") as mock_connect:
            mock_connect.side_effect = Exception("Connection failed")

            with pytest.raises(Exception, match="Connection failed"):
                await publish("test_queue", '{"test": "data"}')

import json
import logging
from datetime import UTC, datetime
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractConnection

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class RabbitMQClient:
    """RabbitMQ 连接与发布管理器"""

    def __init__(self) -> None:
        self._connection: AbstractConnection | None = None
        self._channel: AbstractChannel | None = None

    async def initialize(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            return

        settings = get_settings()
        self._connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        self._channel = await self._connection.channel(publisher_confirms=True)
        await self._channel.set_qos(prefetch_count=10)
        await self._channel.declare_queue(settings.rabbitmq_documents_queue, durable=True)

        logger.info(
            "RabbitMQ connected: %s:%s, queue=%s",
            settings.rabbitmq_host,
            settings.rabbitmq_port,
            settings.rabbitmq_documents_queue,
        )

    async def close(self) -> None:
        if self._channel is not None and not self._channel.is_closed:
            await self._channel.close()
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
        self._channel = None
        self._connection = None
        logger.info("RabbitMQ connection closed")

    async def ping(self) -> bool:
        return bool(
            self._connection is not None
            and not self._connection.is_closed
            and self._channel is not None
            and not self._channel.is_closed
        )

    async def publish_json(self, queue_name: str, payload: dict[str, Any]) -> None:
        if self._channel is None or self._channel.is_closed:
            raise RuntimeError("RabbitMQ channel not initialized")

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        message = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            timestamp=datetime.now(UTC),
        )
        await self._channel.default_exchange.publish(message, routing_key=queue_name)


_rabbitmq_client = RabbitMQClient()


async def init_rabbitmq() -> None:
    await _rabbitmq_client.initialize()


async def close_rabbitmq() -> None:
    await _rabbitmq_client.close()


async def ping_rabbitmq() -> bool:
    return await _rabbitmq_client.ping()


def get_rabbitmq_client() -> RabbitMQClient:
    return _rabbitmq_client

import json
import logging
from datetime import UTC, datetime
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractConnection

from app.core.config import get_settings

logger = logging.getLogger(__name__)


async def declare_documents_topology(channel: AbstractChannel) -> dict[str, Any]:
    """声明文档队列拓扑：主队列 + 分档延迟重试队列 + 死信队列。

    重试队列带 x-message-ttl，到期后经默认交换机死信回主队列；
    主队列消费失败的业务消息由 worker 主动发布到 DLQ（先发布后 ack，不依赖 broker 死信）。
    """
    settings = get_settings()
    main_queue = settings.rabbitmq_documents_queue

    topology: dict[str, Any] = {"main": main_queue, "retry": [], "dlq": settings.documents_dlq_queue}

    await channel.declare_queue(main_queue, durable=True)

    for delay in settings.document_worker_retry_delays:
        retry_queue = settings.documents_retry_queue(delay)
        await channel.declare_queue(
            retry_queue,
            durable=True,
            arguments={
                "x-message-ttl": delay * 1000,
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": main_queue,
            },
        )
        topology["retry"].append({"queue": retry_queue, "delaySeconds": delay})

    await channel.declare_queue(settings.documents_dlq_queue, durable=True)
    return topology


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
        topology = await declare_documents_topology(self._channel)

        logger.info(
            "RabbitMQ connected: %s:%s, queue=%s, retryTiers=%s, dlq=%s",
            settings.rabbitmq_host,
            settings.rabbitmq_port,
            settings.rabbitmq_documents_queue,
            [item["delaySeconds"] for item in topology["retry"]],
            settings.documents_dlq_queue,
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

    async def open_channel(self) -> AbstractChannel:
        """在现有连接上开新通道，用于 DLQ 检视等操作，避免占用发布通道"""
        if self._connection is None or self._connection.is_closed:
            raise RuntimeError("RabbitMQ connection not initialized")
        return await self._connection.channel()

    async def publish_json(
        self,
        queue_name: str,
        payload: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        await self.publish_raw(queue_name, body, headers=headers, content_type="application/json")

    async def publish_raw(
        self,
        queue_name: str,
        body: bytes,
        headers: dict[str, Any] | None = None,
        content_type: str = "application/octet-stream",
    ) -> None:
        if self._channel is None or self._channel.is_closed:
            raise RuntimeError("RabbitMQ channel not initialized")

        message = aio_pika.Message(
            body=body,
            content_type=content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            timestamp=datetime.now(UTC),
            headers=headers or {},
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

import json
import logging
from typing import Any

import redis.asyncio as redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis 客户端管理器"""

    def __init__(self) -> None:
        self._client: redis.Redis | None = None

    async def initialize(self) -> None:
        if self._client is not None:
            return

        settings = get_settings()
        self._client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            health_check_interval=30,
        )
        await self._client.ping()
        logger.info("Redis connected: %s:%s/%s", settings.redis_host, settings.redis_port, settings.redis_db)

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None
        logger.info("Redis connection closed")

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            pong = await self._client.ping()
            return bool(pong)
        except Exception:
            return False

    async def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int | None = None) -> None:
        if self._client is None:
            raise RuntimeError("Redis client not initialized")
        payload = json.dumps(value, ensure_ascii=False)
        if ttl_seconds is None:
            await self._client.set(key, payload)
        else:
            await self._client.set(key, payload, ex=ttl_seconds)

    async def get_json(self, key: str) -> dict[str, Any] | None:
        if self._client is None:
            raise RuntimeError("Redis client not initialized")
        payload = await self._client.get(key)
        if not payload:
            return None
        return json.loads(payload)


_redis_client = RedisClient()


async def init_redis() -> None:
    await _redis_client.initialize()


async def close_redis() -> None:
    await _redis_client.close()


async def ping_redis() -> bool:
    return await _redis_client.ping()


def get_redis_client() -> RedisClient:
    return _redis_client

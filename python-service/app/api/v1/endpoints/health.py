from fastapi import APIRouter, Request

from app.core.database import ping_database
from app.core.rabbitmq import ping_rabbitmq
from app.core.redis_client import ping_redis
from app.core.response import success

router = APIRouter(tags=["health"])


@router.get("/health")
async def get_health(request: Request) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id", "")
    if not trace_id:
        trace_id = "health-check"

    postgres_ok, redis_ok, rabbitmq_ok = await ping_database(), await ping_redis(), await ping_rabbitmq()
    service_status = {
        "postgres": "ok" if postgres_ok else "down",
        "redis": "ok" if redis_ok else "down",
        "rabbitmq": "ok" if rabbitmq_ok else "down",
    }
    status = "ok" if postgres_ok and redis_ok and rabbitmq_ok else "degraded"

    return success({"status": status, "services": service_status}, trace_id)

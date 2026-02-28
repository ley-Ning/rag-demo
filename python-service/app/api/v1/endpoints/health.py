from fastapi import APIRouter, Request

from app.core.response import success

router = APIRouter(tags=["health"])


@router.get("/health")
def get_health(request: Request) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id", "")
    if not trace_id:
        trace_id = "health-check"
    return success({"status": "ok"}, trace_id)

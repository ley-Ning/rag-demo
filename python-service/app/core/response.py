from typing import Any


def success(data: Any, trace_id: str, message: str = "ok") -> dict[str, Any]:
    return {
        "code": 0,
        "message": message,
        "data": data,
        "traceId": trace_id,
    }


def fail(trace_id: str, message: str, code: int = 1, data: Any = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "data": data,
        "traceId": trace_id,
    }

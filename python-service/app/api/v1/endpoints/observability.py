from collections import defaultdict
import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request

from app.core.database import get_db_conn
from app.core.response import success

router = APIRouter(prefix="/observability", tags=["observability"])


def _parse_json_list(value: Any) -> list[dict[str, Any]]:
    """兼容 asyncpg 对 jsonb 的不同解码行为"""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except Exception:
            return []
    return []


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


@router.get("/consumption-logs")
async def get_consumption_logs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    model_id: str | None = Query(default=None, alias="modelId"),
    status: str | None = Query(default=None, pattern="^(success|failed)$"),
    keyword: str | None = Query(default=None, max_length=200),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    """查询 Prompt Token 消耗和 MCP skill 调用日志"""
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    conditions: list[str] = []
    args: list[Any] = []

    if model_id:
        args.append(model_id)
        conditions.append(f"model_id = ${len(args)}")
    if status:
        args.append(status)
        conditions.append(f"status = ${len(args)}")
    if keyword:
        args.append(f"%{keyword.strip()}%")
        conditions.append(f"question ILIKE ${len(args)}")

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    count_sql = f"""
        SELECT COUNT(1)
        FROM retrieval_logs
        {where_clause}
    """
    total_count = await conn.fetchval(count_sql, *args)

    args_with_limit = [*args, limit]
    retrieval_sql = f"""
        SELECT
            id,
            trace_id,
            session_id,
            question,
            model_id,
            top_k,
            threshold,
            latency_ms,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            mcp_call_count,
            status,
            error_message,
            results,
            created_at
        FROM retrieval_logs
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ${len(args_with_limit)}
    """
    retrieval_rows = await conn.fetch(
        retrieval_sql,
        *args_with_limit,
    )

    if not retrieval_rows:
        return success({"items": [], "total": int(total_count or 0)}, trace_id)

    retrieval_ids = [int(row["id"]) for row in retrieval_rows]
    skill_rows = await conn.fetch(
        """
        SELECT
            retrieval_log_id,
            skill_name,
            status,
            latency_ms,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            input_summary,
            output_summary,
            error_message,
            created_at
        FROM mcp_skill_logs
        WHERE retrieval_log_id = ANY($1::bigint[])
        ORDER BY created_at ASC
        """,
        retrieval_ids,
    )

    skill_map: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in skill_rows:
        retrieval_log_id = int(row["retrieval_log_id"])
        skill_map[retrieval_log_id].append(
            {
                "skillName": row["skill_name"],
                "status": row["status"],
                "latencyMs": row["latency_ms"] or 0,
                "promptTokens": row["prompt_tokens"] or 0,
                "completionTokens": row["completion_tokens"] or 0,
                "totalTokens": row["total_tokens"] or 0,
                "inputSummary": row["input_summary"] or "",
                "outputSummary": row["output_summary"] or "",
                "errorMessage": row["error_message"],
                "createdAt": row["created_at"].isoformat(),
            }
        )

    items = []
    for row in retrieval_rows:
        retrieval_id = int(row["id"])
        skill_calls = skill_map.get(retrieval_id, [])
        results = _parse_json_list(row["results"])
        mcp_call_count = row["mcp_call_count"] if row["mcp_call_count"] is not None else len(skill_calls)

        items.append(
            {
                "id": retrieval_id,
                "traceId": row["trace_id"],
                "sessionId": row["session_id"],
                "question": row["question"],
                "modelId": row["model_id"] or "",
                "topK": row["top_k"],
                "threshold": row["threshold"],
                "latencyMs": row["latency_ms"] or 0,
                "promptTokens": row["prompt_tokens"] or 0,
                "completionTokens": row["completion_tokens"] or 0,
                "totalTokens": row["total_tokens"] or 0,
                "mcpCallCount": mcp_call_count,
                "status": row["status"] or "success",
                "errorMessage": row["error_message"],
                "references": results,
                "skillCalls": skill_calls,
                "createdAt": row["created_at"].isoformat(),
            }
        )

    return success({"items": items, "total": int(total_count or 0)}, trace_id)


@router.get("/tool-runs")
async def get_tool_runs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    tool_name: str | None = Query(default=None, alias="toolName"),
    status: str | None = Query(default=None, pattern="^(success|failed)$"),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    conditions: list[str] = []
    args: list[Any] = []
    if tool_name:
        args.append(tool_name.strip())
        conditions.append(f"tool_name = ${len(args)}")
    if status:
        args.append(status)
        conditions.append(f"status = ${len(args)}")

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    args_with_limit = [*args, limit]
    rows = await conn.fetch(
        f"""
        SELECT
            id,
            retrieval_log_id,
            trace_id,
            session_id,
            tool_name,
            source,
            status,
            latency_ms,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            input_summary,
            output_summary,
            output_payload,
            error_message,
            created_at
        FROM tool_runs
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ${len(args_with_limit)}
        """,
        *args_with_limit,
    )
    items = [
        {
            "id": int(row["id"]),
            "retrievalLogId": int(row["retrieval_log_id"]) if row["retrieval_log_id"] else None,
            "traceId": row["trace_id"],
            "sessionId": row["session_id"],
            "toolName": row["tool_name"],
            "source": row["source"],
            "status": row["status"],
            "latencyMs": row["latency_ms"] or 0,
            "promptTokens": row["prompt_tokens"] or 0,
            "completionTokens": row["completion_tokens"] or 0,
            "totalTokens": row["total_tokens"] or 0,
            "inputSummary": row["input_summary"] or "",
            "outputSummary": row["output_summary"] or "",
            "outputPayload": _parse_json_object(row["output_payload"]),
            "errorMessage": row["error_message"],
            "createdAt": row["created_at"].isoformat(),
        }
        for row in rows
    ]
    return success({"items": items, "total": len(items)}, trace_id)


@router.get("/deep-think-runs")
async def get_deep_think_runs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    stage: str | None = Query(default=None),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    args: list[Any] = []
    where_clause = ""
    if stage:
        args.append(stage.strip())
        where_clause = f"WHERE stage = ${len(args)}"

    args_with_limit = [*args, limit]
    rows = await conn.fetch(
        f"""
        SELECT
            id,
            retrieval_log_id,
            trace_id,
            session_id,
            stage,
            status,
            latency_ms,
            input_summary,
            output_summary,
            payload,
            error_message,
            created_at
        FROM deep_think_runs
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ${len(args_with_limit)}
        """,
        *args_with_limit,
    )
    items = [
        {
            "id": int(row["id"]),
            "retrievalLogId": int(row["retrieval_log_id"]) if row["retrieval_log_id"] else None,
            "traceId": row["trace_id"],
            "sessionId": row["session_id"],
            "stage": row["stage"],
            "status": row["status"],
            "latencyMs": row["latency_ms"] or 0,
            "inputSummary": row["input_summary"] or "",
            "outputSummary": row["output_summary"] or "",
            "payload": _parse_json_object(row["payload"]),
            "errorMessage": row["error_message"],
            "createdAt": row["created_at"].isoformat(),
        }
        for row in rows
    ]
    return success({"items": items, "total": len(items)}, trace_id)

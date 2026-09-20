import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.database import get_db_conn
from app.core.response import success
from app.domain.memory import get_memory_service

router = APIRouter(prefix="/memory", tags=["memory"])
logger = logging.getLogger(__name__)
settings = get_settings()


class CreateMemoryRequest(BaseModel):
    scope: str = Field(pattern="^(global|user)$")
    content: str = Field(min_length=1, max_length=200)
    scopeKey: str = Field(default="default", max_length=64)
    importance: int = Field(default=3, ge=1, le=5)


class UpdateMemoryRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=200)
    importance: int | None = Field(default=None, ge=1, le=5)
    enabled: bool | None = None


@router.get("")
async def list_memory(
    request: Request,
    scope: str | None = None,
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    """列出记忆条目（全局 + 用户长期），scope 可选过滤"""
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    if scope is not None and scope not in ("global", "user"):
        raise HTTPException(status_code=400, detail="scope 仅支持 global/user")
    entries = await get_memory_service().list_entries(conn, scope=scope)
    return success(
        {"items": [entry.to_dict() for entry in entries], "total": len(entries)},
        trace_id,
    )


@router.post("")
async def create_memory(
    payload: CreateMemoryRequest,
    request: Request,
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        entry = await get_memory_service().add_entry(
            conn,
            scope=payload.scope,
            content=payload.content,
            scope_key=payload.scopeKey,
            importance=payload.importance,
            source="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("[%s] Memory entry created: scope=%s id=%s", trace_id, entry.scope, entry.id)
    return success(entry.to_dict(), trace_id)


@router.patch("/{entry_id}")
async def update_memory(
    payload: UpdateMemoryRequest,
    request: Request,
    entry_id: int = Path(ge=1),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        entry = await get_memory_service().update_entry(
            conn,
            entry_id,
            content=payload.content,
            importance=payload.importance,
            enabled=payload.enabled,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success(entry.to_dict(), trace_id)


@router.delete("/{entry_id}")
async def delete_memory(
    request: Request,
    entry_id: int = Path(ge=1),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        await get_memory_service().delete_entry(conn, entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success({"deleted": True, "id": entry_id}, trace_id)

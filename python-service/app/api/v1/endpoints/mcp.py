from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from app.core.database import get_db_conn
from app.core.response import success
from app.domain.mcp.gateway import get_mcp_gateway
from app.domain.mcp.registry import (
    create_mcp_server,
    ensure_builtin_tools,
    list_mcp_servers,
    list_mcp_tools,
    set_mcp_tool_enabled,
    update_mcp_server,
)

router = APIRouter(prefix="/mcp", tags=["mcp"])


class CreateMcpServerRequest(BaseModel):
    serverKey: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9._:-]+$")
    name: str = Field(min_length=2, max_length=80)
    endpoint: str = Field(min_length=8, max_length=260)
    sourceType: str = Field(default="http", max_length=20)
    authType: str = Field(default="none", max_length=20)
    authConfig: dict[str, object] = Field(default_factory=dict)
    timeoutMs: int = Field(default=12000, ge=1000, le=120000)


class UpdateMcpServerRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    endpoint: str | None = Field(default=None, min_length=8, max_length=260)
    enabled: bool | None = None
    authType: str | None = Field(default=None, max_length=20)
    authConfig: dict[str, object] | None = None
    timeoutMs: int | None = Field(default=None, ge=1000, le=120000)


class UpdateMcpToolStatusRequest(BaseModel):
    enabled: bool


def _server_to_dict(server) -> dict[str, object]:
    return {
        "serverKey": server.server_key,
        "name": server.name,
        "sourceType": server.source_type,
        "endpoint": server.endpoint,
        "authType": server.auth_type,
        "authConfig": server.auth_config,
        "enabled": server.enabled,
        "timeoutMs": server.timeout_ms,
    }


def _tool_to_dict(tool) -> dict[str, object]:
    return {
        "toolName": tool.tool_name,
        "displayName": tool.display_name,
        "description": tool.description,
        "source": tool.source,
        "serverKey": tool.server_key,
        "toolSchema": tool.tool_schema,
        "enabled": tool.enabled,
    }


@router.get("/servers")
async def get_mcp_servers(request: Request, conn=Depends(get_db_conn)) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    servers = await list_mcp_servers(conn)
    return success({"items": [_server_to_dict(item) for item in servers]}, trace_id)


@router.post("/servers")
async def add_mcp_server(
    payload: CreateMcpServerRequest,
    request: Request,
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        created = await create_mcp_server(conn, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建 MCP Server 失败: {exc}") from exc

    return success(_server_to_dict(created), trace_id)


@router.patch("/servers/{server_key}")
async def patch_mcp_server(
    payload: UpdateMcpServerRequest,
    request: Request,
    server_key: str = Path(min_length=2, max_length=64),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        updated = await update_mcp_server(
            conn,
            server_key,
            {key: value for key, value in payload.model_dump().items() if value is not None},
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success(_server_to_dict(updated), trace_id)


@router.get("/tools")
async def get_mcp_tools(request: Request, conn=Depends(get_db_conn)) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    await ensure_builtin_tools(conn)
    tools = await list_mcp_tools(conn)
    return success({"items": [_tool_to_dict(item) for item in tools]}, trace_id)


@router.patch("/tools/{tool_name}/status")
async def patch_mcp_tool_status(
    payload: UpdateMcpToolStatusRequest,
    request: Request,
    tool_name: str = Path(min_length=2, max_length=128),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        updated = await set_mcp_tool_enabled(conn, tool_name, payload.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success(_tool_to_dict(updated), trace_id)


@router.post("/servers/{server_key}/sync-tools")
async def sync_mcp_server_tools(
    request: Request,
    server_key: str = Path(min_length=2, max_length=64),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    gateway = get_mcp_gateway()
    try:
        synced = await gateway.discover_external_tools(conn, server_key=server_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"同步 tools 失败: {exc}") from exc
    return success(
        {
            "serverKey": server_key,
            "syncedCount": len(synced),
            "items": synced,
        },
        trace_id,
    )

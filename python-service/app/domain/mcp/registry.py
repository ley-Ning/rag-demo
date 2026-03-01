import json
import re
from dataclasses import dataclass
from typing import Any

import asyncpg

TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9._:-]{2,128}$")
SERVER_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9._:-]{2,64}$")


@dataclass(frozen=True)
class McpServerInfo:
    server_key: str
    name: str
    source_type: str
    endpoint: str
    auth_type: str
    auth_config: dict[str, Any]
    enabled: bool
    timeout_ms: int


@dataclass(frozen=True)
class McpToolInfo:
    tool_name: str
    display_name: str
    description: str
    source: str
    server_key: str | None
    tool_schema: dict[str, Any]
    enabled: bool


BUILTIN_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "tool_name": "mcp.web.fetch",
        "display_name": "网页抓取",
        "description": "抓取网页正文并返回摘要片段",
        "source": "builtin",
        "server_key": None,
        "tool_schema": {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string", "description": "http/https 链接"},
                "maxChars": {"type": "integer", "description": "最大保留字符数"},
            },
        },
    },
    {
        "tool_name": "mcp.deep_think.pipeline",
        "display_name": "深度思考",
        "description": "plan/execute/reflect/verify 四阶段编排",
        "source": "builtin",
        "server_key": None,
        "tool_schema": {
            "type": "object",
            "required": ["question"],
            "properties": {
                "question": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
)


def _parse_json_dict(value: Any) -> dict[str, Any]:
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


def _to_server_info(row: asyncpg.Record) -> McpServerInfo:
    return McpServerInfo(
        server_key=row["server_key"],
        name=row["name"],
        source_type=row["source_type"],
        endpoint=row["endpoint"],
        auth_type=row["auth_type"],
        auth_config=_parse_json_dict(row["auth_config"]),
        enabled=bool(row["enabled"]),
        timeout_ms=int(row["timeout_ms"] or 12000),
    )


def _to_tool_info(row: asyncpg.Record) -> McpToolInfo:
    return McpToolInfo(
        tool_name=row["tool_name"],
        display_name=row["display_name"],
        description=row["description"] or "",
        source=row["source"],
        server_key=row["server_key"],
        tool_schema=_parse_json_dict(row["tool_schema"]),
        enabled=bool(row["enabled"]),
    )


async def ensure_builtin_tools(conn: asyncpg.Connection) -> None:
    for tool in BUILTIN_TOOLS:
        await conn.execute(
            """
            INSERT INTO mcp_tools (tool_name, display_name, description, source, server_key, tool_schema, enabled)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, TRUE)
            ON CONFLICT (tool_name) DO UPDATE
            SET
              display_name = EXCLUDED.display_name,
              description = EXCLUDED.description,
              source = EXCLUDED.source,
              tool_schema = EXCLUDED.tool_schema,
              updated_at = NOW()
            """,
            tool["tool_name"],
            tool["display_name"],
            tool["description"],
            tool["source"],
            tool["server_key"],
            json.dumps(tool["tool_schema"], ensure_ascii=False),
        )


async def list_mcp_servers(conn: asyncpg.Connection) -> list[McpServerInfo]:
    rows = await conn.fetch(
        """
        SELECT
            server_key,
            name,
            source_type,
            endpoint,
            auth_type,
            auth_config,
            enabled,
            timeout_ms
        FROM mcp_servers
        ORDER BY created_at DESC
        """
    )
    return [_to_server_info(row) for row in rows]


async def create_mcp_server(conn: asyncpg.Connection, payload: dict[str, Any]) -> McpServerInfo:
    server_key = str(payload.get("serverKey", "")).strip()
    if not SERVER_KEY_PATTERN.match(server_key):
        raise ValueError("serverKey 仅允许字母/数字/._:-，长度 2-64")

    name = str(payload.get("name", "")).strip()
    if len(name) < 2 or len(name) > 80:
        raise ValueError("name 长度需在 2-80 之间")

    endpoint = str(payload.get("endpoint", "")).strip()
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError("endpoint 必须以 http:// 或 https:// 开头")

    source_type = str(payload.get("sourceType", "http")).strip().lower() or "http"
    auth_type = str(payload.get("authType", "none")).strip().lower() or "none"
    timeout_ms = int(payload.get("timeoutMs", 12000) or 12000)
    timeout_ms = max(1000, min(timeout_ms, 120000))
    auth_config = payload.get("authConfig")
    if not isinstance(auth_config, dict):
        auth_config = {}

    row = await conn.fetchrow(
        """
        INSERT INTO mcp_servers (
            server_key, name, source_type, endpoint, auth_type, auth_config, enabled, timeout_ms
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, TRUE, $7)
        RETURNING
            server_key,
            name,
            source_type,
            endpoint,
            auth_type,
            auth_config,
            enabled,
            timeout_ms
        """,
        server_key,
        name,
        source_type,
        endpoint,
        auth_type,
        json.dumps(auth_config, ensure_ascii=False),
        timeout_ms,
    )
    if not row:
        raise RuntimeError("创建 MCP Server 失败")
    return _to_server_info(row)


async def update_mcp_server(
    conn: asyncpg.Connection,
    server_key: str,
    payload: dict[str, Any],
) -> McpServerInfo:
    if not SERVER_KEY_PATTERN.match(server_key):
        raise ValueError("serverKey 不合法")

    updates: list[str] = []
    args: list[Any] = []

    if "name" in payload:
        name = str(payload.get("name", "")).strip()
        if len(name) < 2 or len(name) > 80:
            raise ValueError("name 长度需在 2-80 之间")
        args.append(name)
        updates.append(f"name = ${len(args)}")

    if "endpoint" in payload:
        endpoint = str(payload.get("endpoint", "")).strip()
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("endpoint 必须以 http:// 或 https:// 开头")
        args.append(endpoint)
        updates.append(f"endpoint = ${len(args)}")

    if "enabled" in payload:
        enabled = bool(payload.get("enabled"))
        args.append(enabled)
        updates.append(f"enabled = ${len(args)}")

    if "timeoutMs" in payload:
        timeout_ms = int(payload.get("timeoutMs", 12000) or 12000)
        timeout_ms = max(1000, min(timeout_ms, 120000))
        args.append(timeout_ms)
        updates.append(f"timeout_ms = ${len(args)}")

    if "authType" in payload:
        auth_type = str(payload.get("authType", "none")).strip().lower() or "none"
        args.append(auth_type)
        updates.append(f"auth_type = ${len(args)}")

    if "authConfig" in payload:
        auth_config = payload.get("authConfig")
        if not isinstance(auth_config, dict):
            auth_config = {}
        args.append(json.dumps(auth_config, ensure_ascii=False))
        updates.append(f"auth_config = ${len(args)}::jsonb")

    if not updates:
        raise ValueError("未提供可更新字段")

    args.append(server_key)
    row = await conn.fetchrow(
        f"""
        UPDATE mcp_servers
        SET
            {", ".join(updates)},
            updated_at = NOW()
        WHERE server_key = ${len(args)}
        RETURNING
            server_key,
            name,
            source_type,
            endpoint,
            auth_type,
            auth_config,
            enabled,
            timeout_ms
        """,
        *args,
    )
    if not row:
        raise KeyError("MCP Server 不存在")
    return _to_server_info(row)


async def list_mcp_tools(
    conn: asyncpg.Connection,
    *,
    enabled_only: bool = False,
) -> list[McpToolInfo]:
    if enabled_only:
        rows = await conn.fetch(
            """
            SELECT
                tool_name,
                display_name,
                description,
                source,
                server_key,
                tool_schema,
                enabled
            FROM mcp_tools
            WHERE enabled = TRUE
            ORDER BY source ASC, tool_name ASC
            """
        )
    else:
        rows = await conn.fetch(
            """
            SELECT
                tool_name,
                display_name,
                description,
                source,
                server_key,
                tool_schema,
                enabled
            FROM mcp_tools
            ORDER BY source ASC, tool_name ASC
            """
        )
    return [_to_tool_info(row) for row in rows]


async def get_mcp_tool(conn: asyncpg.Connection, tool_name: str) -> McpToolInfo | None:
    row = await conn.fetchrow(
        """
        SELECT
            tool_name,
            display_name,
            description,
            source,
            server_key,
            tool_schema,
            enabled
        FROM mcp_tools
        WHERE tool_name = $1
        """,
        tool_name,
    )
    if not row:
        return None
    return _to_tool_info(row)


async def set_mcp_tool_enabled(
    conn: asyncpg.Connection,
    tool_name: str,
    enabled: bool,
) -> McpToolInfo:
    if not TOOL_NAME_PATTERN.match(tool_name):
        raise ValueError("toolName 不合法")

    row = await conn.fetchrow(
        """
        UPDATE mcp_tools
        SET enabled = $2, updated_at = NOW()
        WHERE tool_name = $1
        RETURNING
            tool_name,
            display_name,
            description,
            source,
            server_key,
            tool_schema,
            enabled
        """,
        tool_name,
        enabled,
    )
    if not row:
        raise KeyError("MCP Tool 不存在")
    return _to_tool_info(row)


async def get_mcp_server(conn: asyncpg.Connection, server_key: str) -> McpServerInfo | None:
    row = await conn.fetchrow(
        """
        SELECT
            server_key,
            name,
            source_type,
            endpoint,
            auth_type,
            auth_config,
            enabled,
            timeout_ms
        FROM mcp_servers
        WHERE server_key = $1
        """,
        server_key,
    )
    if not row:
        return None
    return _to_server_info(row)


async def list_external_tools_by_server(
    conn: asyncpg.Connection,
    server_key: str,
) -> list[McpToolInfo]:
    rows = await conn.fetch(
        """
        SELECT
            tool_name,
            display_name,
            description,
            source,
            server_key,
            tool_schema,
            enabled
        FROM mcp_tools
        WHERE source = 'external'
          AND server_key = $1
        ORDER BY tool_name ASC
        """,
        server_key,
    )
    return [_to_tool_info(row) for row in rows]


async def set_external_tools_enabled_by_server(
    conn: asyncpg.Connection,
    server_key: str,
    *,
    enabled: bool,
    keep_tool_names: set[str] | None = None,
) -> int:
    keep_tool_names = keep_tool_names or set()
    if keep_tool_names:
        result = await conn.execute(
            """
            UPDATE mcp_tools
            SET enabled = $2, updated_at = NOW()
            WHERE source = 'external'
              AND server_key = $1
              AND NOT (tool_name = ANY($3::text[]))
            """,
            server_key,
            enabled,
            list(keep_tool_names),
        )
    else:
        result = await conn.execute(
            """
            UPDATE mcp_tools
            SET enabled = $2, updated_at = NOW()
            WHERE source = 'external'
              AND server_key = $1
            """,
            server_key,
            enabled,
        )
    try:
        return int(result.split()[-1])
    except Exception:
        return 0


async def upsert_external_tool(
    conn: asyncpg.Connection,
    *,
    tool_name: str,
    display_name: str,
    description: str,
    server_key: str,
    tool_schema: dict[str, Any] | None = None,
) -> McpToolInfo:
    if not TOOL_NAME_PATTERN.match(tool_name):
        raise ValueError("toolName 不合法")
    row = await conn.fetchrow(
        """
        INSERT INTO mcp_tools (tool_name, display_name, description, source, server_key, tool_schema, enabled)
        VALUES ($1, $2, $3, 'external', $4, $5::jsonb, TRUE)
        ON CONFLICT (tool_name) DO UPDATE
        SET
            display_name = EXCLUDED.display_name,
            description = EXCLUDED.description,
            source = EXCLUDED.source,
            server_key = EXCLUDED.server_key,
            tool_schema = EXCLUDED.tool_schema,
            updated_at = NOW()
        RETURNING
            tool_name,
            display_name,
            description,
            source,
            server_key,
            tool_schema,
            enabled
        """,
        tool_name,
        display_name,
        description,
        server_key,
        json.dumps(tool_schema or {}, ensure_ascii=False),
    )
    if not row:
        raise RuntimeError("外部工具写入失败")
    return _to_tool_info(row)

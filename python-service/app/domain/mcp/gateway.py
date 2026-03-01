import asyncio
import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.core.config import get_settings
from app.domain.mcp.registry import (
    get_mcp_server,
    get_mcp_tool,
    list_external_tools_by_server,
    set_external_tools_enabled_by_server,
    upsert_external_tool,
)
from app.domain.tools.builtin_web_fetch import fetch_and_extract_webpage

settings = get_settings()


@dataclass
class ToolInvokeResult:
    tool_name: str
    source: str
    status: str
    latency_ms: int
    input_summary: str
    output_summary: str
    output_payload: dict[str, Any]
    error_message: str | None = None


def _invoke_external_sync(
    endpoint: str,
    payload: dict[str, Any],
    *,
    timeout_sec: float,
    auth_type: str,
    auth_config: dict[str, Any],
) -> dict[str, Any]:
    headers = {"content-type": "application/json"}
    if auth_type == "bearer":
        token = str(auth_config.get("token", "")).strip()
        if token:
            headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as response:
        content = response.read()
        body = json.loads(content.decode("utf-8", errors="ignore"))
    if not isinstance(body, dict):
        raise ValueError("MCP 外部服务响应格式错误")
    return body


def _extract_discovered_tools(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: Any = payload.get("tools")
    if candidates is None and isinstance(payload.get("data"), dict):
        candidates = payload["data"].get("tools")
    if not isinstance(candidates, list):
        return []
    parsed: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("toolName", item.get("name", ""))
        tool_name = str(raw_name).strip()
        if not tool_name:
            continue
        display_name = str(item.get("displayName", item.get("title", tool_name))).strip() or tool_name
        description = str(item.get("description", "")).strip()
        schema = item.get("toolSchema", item.get("schema", {}))
        if not isinstance(schema, dict):
            schema = {}
        parsed.append(
            {
                "toolName": tool_name,
                "displayName": display_name,
                "description": description,
                "toolSchema": schema,
            }
        )
    return parsed


class McpGateway:
    async def invoke(
        self,
        conn: asyncpg.Connection,
        *,
        tool_name: str,
        args: dict[str, Any],
        trace_id: str,
    ) -> ToolInvokeResult:
        tool = await get_mcp_tool(conn, tool_name)
        if tool is None:
            raise KeyError(f"工具不存在: {tool_name}")
        if not tool.enabled:
            raise RuntimeError(f"工具未启用: {tool_name}")

        if tool.source == "builtin":
            return await self._invoke_builtin(tool_name=tool_name, args=args)
        return await self._invoke_external(conn, tool_name=tool_name, args=args, trace_id=trace_id)

    async def _invoke_builtin(self, *, tool_name: str, args: dict[str, Any]) -> ToolInvokeResult:
        start = time.monotonic()
        if tool_name != "mcp.web.fetch":
            raise RuntimeError(f"暂不支持的内置工具: {tool_name}")

        url = str(args.get("url", "")).strip()
        if not url:
            raise ValueError("url 不能为空")

        max_chars = int(args.get("maxChars", settings.mcp_web_max_content_chars) or settings.mcp_web_max_content_chars)
        payload = await fetch_and_extract_webpage(
            url,
            timeout_sec=settings.mcp_web_request_timeout_sec,
            max_chars=max_chars,
        )
        return ToolInvokeResult(
            tool_name=tool_name,
            source="builtin",
            status="success",
            latency_ms=int((time.monotonic() - start) * 1000),
            input_summary=f"url={url}",
            output_summary=f"title={payload.get('title', '')[:80]},chars={payload.get('capturedChars', 0)}",
            output_payload=payload,
        )

    async def _invoke_external(
        self,
        conn: asyncpg.Connection,
        *,
        tool_name: str,
        args: dict[str, Any],
        trace_id: str,
    ) -> ToolInvokeResult:
        start = time.monotonic()
        tool = await get_mcp_tool(conn, tool_name)
        if tool is None or not tool.server_key:
            raise RuntimeError(f"外部工具缺少 server_key: {tool_name}")
        server = await get_mcp_server(conn, tool.server_key)
        if server is None:
            raise RuntimeError(f"MCP Server 不存在: {tool.server_key}")
        if not server.enabled:
            raise RuntimeError(f"MCP Server 未启用: {tool.server_key}")

        payload = {
            "toolName": tool_name,
            "args": args,
            "traceId": trace_id,
        }
        timeout_sec = max(1.0, min(float(server.timeout_ms) / 1000.0, 120.0))
        body = await asyncio.to_thread(
            _invoke_external_sync,
            server.endpoint,
            payload,
            timeout_sec=timeout_sec,
            auth_type=server.auth_type,
            auth_config=server.auth_config,
        )
        status = str(body.get("status", "success"))
        data = body.get("data")
        if not isinstance(data, dict):
            data = {"raw": data}
        error_message = body.get("errorMessage")
        if error_message is not None:
            error_message = str(error_message)

        return ToolInvokeResult(
            tool_name=tool_name,
            source="external",
            status="failed" if status == "failed" else "success",
            latency_ms=int((time.monotonic() - start) * 1000),
            input_summary=f"server={tool.server_key},args={len(args)}",
            output_summary=f"fields={len(data)}",
            output_payload=data,
            error_message=error_message,
        )

    async def discover_external_tools(
        self,
        conn: asyncpg.Connection,
        *,
        server_key: str,
    ) -> list[dict[str, Any]]:
        server = await get_mcp_server(conn, server_key)
        if server is None:
            raise KeyError(f"MCP Server 不存在: {server_key}")
        if not server.enabled:
            raise RuntimeError(f"MCP Server 未启用: {server_key}")

        timeout_sec = max(1.0, min(float(server.timeout_ms) / 1000.0, 120.0))
        body = await asyncio.to_thread(
            _invoke_external_sync,
            server.endpoint,
            {"op": "list_tools"},
            timeout_sec=timeout_sec,
            auth_type=server.auth_type,
            auth_config=server.auth_config,
        )
        discovered = _extract_discovered_tools(body)
        if not discovered:
            raise RuntimeError("外部 MCP Server 未返回可用 tools")

        synced_names: set[str] = set()
        synced_items: list[dict[str, Any]] = []
        for item in discovered:
            tool_name = str(item["toolName"])
            synced = await upsert_external_tool(
                conn,
                tool_name=tool_name,
                display_name=str(item["displayName"]),
                description=str(item["description"]),
                server_key=server_key,
                tool_schema=item["toolSchema"],
            )
            synced_names.add(tool_name)
            synced_items.append(
                {
                    "toolName": synced.tool_name,
                    "displayName": synced.display_name,
                    "description": synced.description,
                    "source": synced.source,
                    "serverKey": synced.server_key,
                    "enabled": synced.enabled,
                }
            )

        await set_external_tools_enabled_by_server(
            conn,
            server_key,
            enabled=False,
            keep_tool_names=synced_names,
        )

        current = await list_external_tools_by_server(conn, server_key)
        current_map = {item.tool_name: item.enabled for item in current}
        for item in synced_items:
            item["enabled"] = bool(current_map.get(item["toolName"], True))

        return synced_items


_gateway: McpGateway | None = None


def get_mcp_gateway() -> McpGateway:
    global _gateway
    if _gateway is None:
        _gateway = McpGateway()
    return _gateway

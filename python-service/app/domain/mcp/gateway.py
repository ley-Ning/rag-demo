import asyncio
import json
import time
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
from app.domain.mcp.standard_client import call_remote_tool, list_remote_tools
from app.domain.tools.builtin_sandbox import execute_python_code
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

        if tool_name == "mcp.web.fetch":
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

        if tool_name == "mcp.sandbox.execute":
            if not settings.sandbox_enabled:
                raise RuntimeError("代码沙盒功能未启用（SANDBOX_ENABLED=false）")
            code = str(args.get("code", ""))
            timeout_sec = int(args.get("timeoutSec", 0) or 0) or None
            payload = await execute_python_code(code, timeout_sec=timeout_sec)
            status = "success" if payload.get("exitCode") == 0 else "failed"
            return ToolInvokeResult(
                tool_name=tool_name,
                source="builtin",
                status=status,
                latency_ms=int((time.monotonic() - start) * 1000),
                input_summary=f"code_chars={len(code)},timeout={payload.get('execTimeoutSec')}s",
                output_summary=(
                    f"exit={payload.get('exitCode')},stdout_chars={len(payload.get('stdout', ''))}"
                ),
                output_payload=payload,
                error_message=(
                    "沙盒内代码执行失败"
                    if status == "failed"
                    else None
                ),
            )

        raise RuntimeError(f"暂不支持的内置工具: {tool_name}")

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

        timeout_sec = max(3.0, min(float(server.timeout_ms) / 1000.0, 120.0))
        result = await call_remote_tool(
            server.endpoint,
            tool_name=tool_name,
            args=args,
            auth_type=server.auth_type,
            auth_config=server.auth_config,
            timeout_sec=timeout_sec,
        )
        payload = result["payload"]
        is_error = bool(result["isError"])
        error_message = None
        if is_error:
            error_message = str(payload.get("text", "") or "工具返回错误")[:500]

        return ToolInvokeResult(
            tool_name=tool_name,
            source="external",
            status="failed" if is_error else "success",
            latency_ms=int((time.monotonic() - start) * 1000),
            input_summary=f"server={tool.server_key},args={len(args)}",
            output_summary=f"fields={len(payload)}",
            output_payload=payload,
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

        timeout_sec = max(3.0, min(float(server.timeout_ms) / 1000.0, 120.0))
        discovered = await list_remote_tools(
            server.endpoint,
            auth_type=server.auth_type,
            auth_config=server.auth_config,
            timeout_sec=timeout_sec,
        )
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

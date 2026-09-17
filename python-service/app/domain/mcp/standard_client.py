"""标准 MCP 协议客户端（Model Context Protocol，JSON-RPC over Streamable HTTP）。

供外部 MCP Server 轨道使用：
- list_remote_tools: initialize 握手 + tools/list，返回标准工具定义
- call_remote_tool: tools/call，解析 content 块与 structuredContent

每次调用独立建立会话（握手→调用→关闭），无连接复用，适合当前低频管理/调用场景。
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _build_http_client(
    *,
    auth_type: str,
    auth_config: dict[str, Any],
    timeout_sec: float,
) -> httpx.AsyncClient:
    headers = {"Accept": "application/json, text/event-stream"}
    if auth_type == "bearer":
        token = str(auth_config.get("token", "")).strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "apikey":
        header_name = str(auth_config.get("header", "x-api-key")).strip() or "x-api-key"
        api_key = str(auth_config.get("key", "")).strip()
        if api_key:
            headers[header_name] = api_key
    return httpx.AsyncClient(headers=headers, timeout=timeout_sec)


async def list_remote_tools(
    endpoint: str,
    *,
    auth_type: str = "none",
    auth_config: dict[str, Any] | None = None,
    timeout_sec: float = 20.0,
) -> list[dict[str, Any]]:
    """连接标准 MCP Server，返回 [{toolName, displayName, description, toolSchema}]"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    http_client = _build_http_client(
        auth_type=auth_type,
        auth_config=auth_config or {},
        timeout_sec=timeout_sec,
    )
    try:
        async with streamable_http_client(endpoint, http_client=http_client) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                tools: list[dict[str, Any]] = []
                for tool in listing.tools or []:
                    name = str(tool.name or "").strip()
                    if not name:
                        continue
                    schema = getattr(tool, "inputSchema", None)
                    if not isinstance(schema, dict):
                        schema = {}
                    tools.append(
                        {
                            "toolName": name,
                            "displayName": str(tool.title or name),
                            "description": str(tool.description or ""),
                            "toolSchema": schema,
                        }
                    )
                return tools
    finally:
        await http_client.aclose()


def _parse_tool_result(result: Any) -> dict[str, Any]:
    """把 MCP call_tool 结果（content 块 + structuredContent）转为普通 dict"""
    content_texts: list[str] = []
    for block in getattr(result, "content", None) or []:
        block_type = str(getattr(block, "type", ""))
        if block_type == "text":
            text = str(getattr(block, "text", "")).strip()
            if text:
                content_texts.append(text)

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        payload = dict(structured)
    elif content_texts:
        joined = "\n".join(content_texts)
        try:
            import json

            parsed = json.loads(joined)
            payload = parsed if isinstance(parsed, dict) else {"text": joined}
        except Exception:
            payload = {"text": joined}
    else:
        payload = {}

    if content_texts and "text" not in payload:
        payload["text"] = "\n".join(content_texts)[:20000]
    return payload


async def call_remote_tool(
    endpoint: str,
    *,
    tool_name: str,
    args: dict[str, Any],
    auth_type: str = "none",
    auth_config: dict[str, Any] | None = None,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    """调用标准 MCP Server 的工具，返回 {"payload": ..., "isError": ...}"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    http_client = _build_http_client(
        auth_type=auth_type,
        auth_config=auth_config or {},
        timeout_sec=timeout_sec,
    )
    try:
        async with streamable_http_client(endpoint, http_client=http_client) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, args)
                return {
                    "payload": _parse_tool_result(result),
                    "isError": bool(getattr(result, "isError", False)),
                }
    finally:
        await http_client.aclose()

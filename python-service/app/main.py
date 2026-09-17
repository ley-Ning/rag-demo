import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import close_database, init_database
from app.core.rabbitmq import close_rabbitmq, init_rabbitmq
from app.core.redis_client import close_redis, init_redis
from app.core.response import fail
from app.workers.document_worker import start_document_worker, stop_document_worker

settings = get_settings()
logger = logging.getLogger(__name__)


class McpTokenAuthMiddleware:
    """MCP Server 可选 Bearer Token 鉴权（MCP_SERVER_TOKEN 为空则放行，仅限内网开发）"""

    def __init__(self, app):  # noqa: ANN001 - Starlette ASGI app
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] == "http" and settings.mcp_server_token:
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            if headers.get("authorization") != f"Bearer {settings.mcp_server_token}":
                await send(
                    {
                        "type": "http.response.start",
                        "status":  401,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b'{"detail":"invalid or missing MCP token"}',
                    }
                )
                return
        await self.app(scope, receive, send)


# 自暴露 MCP Server 应用（模块级构建，lifespan 与挂载共用同一实例）
_mcp_expose_app = None
if settings.mcp_server_enabled:
    from app.domain.mcp.server_expose import build_mcp_server_app, get_mcp_session_lifespan

    _mcp_expose_app = build_mcp_server_app()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Starting dependencies: PostgreSQL, Redis, RabbitMQ")
    await init_database()
    await init_redis()
    await init_rabbitmq()
    await start_document_worker()
    # 子应用 lifespan（MCP 会话管理器）不会被 Mount 自动驱动，这里手动启动
    mcp_session = get_mcp_session_lifespan() if _mcp_expose_app is not None else None
    if mcp_session is not None:
        await mcp_session.__aenter__()
        logger.info("MCP server exposed at /mcp (standard Streamable HTTP)")
    logger.info("All dependencies initialized")
    try:
        yield
    finally:
        logger.info("Closing dependencies")
        if mcp_session is not None:
            await mcp_session.__aexit__(None, None, None)
        await stop_document_worker()
        await close_rabbitmq()
        await close_redis()
        await close_database()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if _mcp_expose_app is not None:
    app.mount("/mcp", McpTokenAuthMiddleware(_mcp_expose_app))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    trace_id = request.headers.get("x-trace-id", "validation-error")
    return JSONResponse(
        status_code=422,
        content=fail(trace_id=trace_id, message="参数校验失败", code=422, data=exc.errors()),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    trace_id = request.headers.get("x-trace-id", "http-error")
    return JSONResponse(
        status_code=exc.status_code,
        content=fail(trace_id=trace_id, message=str(exc.detail), code=exc.status_code),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _ = exc
    trace_id = request.headers.get("x-trace-id", "server-error")
    return JSONResponse(
        status_code=500,
        content=fail(trace_id=trace_id, message="服务内部错误", code=500),
    )


app.include_router(api_router)

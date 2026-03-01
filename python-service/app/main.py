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


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Starting dependencies: PostgreSQL, Redis, RabbitMQ")
    await init_database()
    await init_redis()
    await init_rabbitmq()
    await start_document_worker()
    logger.info("All dependencies initialized")
    try:
        yield
    finally:
        logger.info("Closing dependencies")
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

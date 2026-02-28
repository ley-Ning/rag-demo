from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.response import fail

settings = get_settings()
app = FastAPI(title=settings.app_name)

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

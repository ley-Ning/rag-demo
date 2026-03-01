import logging
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field

from app.core.response import success
from app.domain.embedding import get_embedding_service
from app.domain.models_registry import (
    create_model,
    delete_model,
    get_model as get_model_info,
    list_models,
    update_model,
    update_model_status,
)

router = APIRouter(prefix="/models", tags=["models"])
logger = logging.getLogger(__name__)


class ModelUpsertRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    provider: str = Field(min_length=2, max_length=40)
    capabilities: list[str] = Field(min_length=1, max_length=3)
    status: str = Field(pattern="^(online|offline)$")
    maxTokens: int = Field(ge=256, le=10000000)
    baseUrl: str = Field(default="", max_length=260)
    apiKey: str = Field(default="", max_length=260)


class CreateModelRequest(ModelUpsertRequest):
    id: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9._:-]+$")


class UpdateStatusRequest(BaseModel):
    status: str = Field(pattern="^(online|offline)$")


@router.get("")
def get_models(request: Request) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    return success({"items": list_models()}, trace_id)


@router.get("/{model_id}")
def get_model_detail(
    request: Request,
    model_id: str = Path(min_length=2, max_length=64),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        model = get_model_info(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="模型不存在") from exc

    return success(
        {
            "id": model.model_id,
            "name": model.name,
            "provider": model.provider,
            "capabilities": list(model.capabilities),
            "status": model.status,
            "maxTokens": model.max_tokens,
            "baseUrl": model.base_url,
            "apiKey": model.api_key,
        },
        trace_id,
    )


@router.post("")
def add_model(payload: CreateModelRequest, request: Request) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        created = create_model(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success(created, trace_id)


@router.put("/{model_id}")
def edit_model(
    payload: ModelUpsertRequest,
    request: Request,
    model_id: str = Path(min_length=2, max_length=64),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        updated = update_model(model_id, payload.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="模型不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success(updated, trace_id)


@router.patch("/{model_id}/status")
def edit_model_status(
    payload: UpdateStatusRequest,
    request: Request,
    model_id: str = Path(min_length=2, max_length=64),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        updated = update_model_status(model_id, payload.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="模型不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success(updated, trace_id)


@router.delete("/{model_id}")
def remove_model(
    request: Request,
    model_id: str = Path(min_length=2, max_length=64),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    try:
        removed = delete_model(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="模型不存在") from exc
    return success({"removed": removed}, trace_id)


class TestModelResponse(BaseModel):
    success: bool
    capability: str
    latency_ms: int
    message: str
    detail: str | None = None


@router.post("/{model_id}/test")
async def test_model_connection(
    request: Request,
    model_id: str = Path(min_length=2, max_length=64),
) -> dict[str, object]:
    """测试模型连接是否正常"""
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    try:
        model = get_model_info(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="模型不存在") from exc

    # 检查是否有 Base URL 和 API Key
    if not model.base_url or not model.api_key:
        return success(
            TestModelResponse(
                success=False,
                capability="unknown",
                latency_ms=0,
                message="模型未配置 Base URL 或 API Key",
                detail="请先在模型配置中填写 Base URL 和 API Key",
            ).model_dump(),
            trace_id,
        )

    # 优先测试 chat 能力
    if "chat" in model.capabilities:
        return await _test_chat_model(model, trace_id)

    # 其次测试 embedding 能力
    if "embedding" in model.capabilities:
        return await _test_embedding_model(model, trace_id)

    # 暂不支持的能力类型
    return success(
        TestModelResponse(
            success=False,
            capability="unknown",
            latency_ms=0,
            message="暂不支持测试此能力类型",
            detail=f"当前支持测试的能力: chat, embedding",
        ).model_dump(),
        trace_id,
    )


async def _test_chat_model(model, trace_id: str) -> dict[str, object]:
    """测试 Chat 模型连接"""
    from openai import AsyncAzureOpenAI

    from app.core.config import get_settings

    settings = get_settings()
    start_time = time.monotonic()

    try:
        client = AsyncAzureOpenAI(
            api_key=model.api_key,
            azure_endpoint=model.base_url,
            api_version=settings.azure_openai_api_version,
        )

        # 发送一个简单的测试请求
        response = await client.chat.completions.create(
            model=model.model_id,
            messages=[
                {"role": "user", "content": "Hello, this is a connection test. Please reply with 'OK'."},
            ],
            max_tokens=10,
            temperature=0,
        )

        latency_ms = int((time.monotonic() - start_time) * 1000)
        reply = response.choices[0].message.content if response.choices else ""

        logger.info(
            "[%s] Chat model test success: model=%s, latency=%sms, reply=%s",
            trace_id,
            model.model_id,
            latency_ms,
            reply[:50] if reply else "",
        )

        return success(
            TestModelResponse(
                success=True,
                capability="chat",
                latency_ms=latency_ms,
                message="模型连接正常",
                detail=f"模型响应: {reply[:100]}" if reply else None,
            ).model_dump(),
            trace_id,
        )

    except Exception as exc:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        error_msg = str(exc)
        logger.error(
            "[%s] Chat model test failed: model=%s, latency=%sms, error=%s",
            trace_id,
            model.model_id,
            latency_ms,
            error_msg,
        )

        return success(
            TestModelResponse(
                success=False,
                capability="chat",
                latency_ms=latency_ms,
                message="模型连接失败",
                detail=error_msg[:500],
            ).model_dump(),
            trace_id,
        )


async def _test_embedding_model(model, trace_id: str) -> dict[str, object]:
    """测试 Embedding 模型连接"""
    start_time = time.monotonic()

    try:
        from app.domain.models_registry import _registry

        embedding_service = get_embedding_service()
        embedding, usage = await embedding_service.embed_single_with_usage(
            "Hello, this is a connection test.",
            model.model_id,
            _registry,
        )

        latency_ms = int((time.monotonic() - start_time) * 1000)

        logger.info(
            "[%s] Embedding model test success: model=%s, latency=%sms, dimension=%d",
            trace_id,
            model.model_id,
            latency_ms,
            len(embedding),
        )

        return success(
            TestModelResponse(
                success=True,
                capability="embedding",
                latency_ms=latency_ms,
                message="模型连接正常",
                detail=f"向量维度: {len(embedding)}, tokens: {usage.total_tokens}",
            ).model_dump(),
            trace_id,
        )

    except Exception as exc:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        error_msg = str(exc)
        logger.error(
            "[%s] Embedding model test failed: model=%s, latency=%sms, error=%s",
            trace_id,
            model.model_id,
            latency_ms,
            error_msg,
        )

        return success(
            TestModelResponse(
                success=False,
                capability="embedding",
                latency_ms=latency_ms,
                message="模型连接失败",
                detail=error_msg[:500],
            ).model_dump(),
            trace_id,
        )

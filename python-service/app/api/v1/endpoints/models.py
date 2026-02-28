from uuid import uuid4

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field

from app.core.response import success
from app.domain.models_registry import (
    create_model,
    delete_model,
    list_models,
    update_model,
    update_model_status,
)

router = APIRouter(prefix="/models", tags=["models"])


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

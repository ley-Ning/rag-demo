from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.response import success
from app.domain.models_registry import model_supports

router = APIRouter(prefix="/chat", tags=["chat"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    modelId: str = Field(min_length=1)
    sessionId: str | None = None


@router.post("/ask")
def ask_question(payload: AskRequest, request: Request) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    if not model_supports(payload.modelId, "chat"):
        raise HTTPException(status_code=400, detail="当前模型不可用于聊天")

    answer = f"收到问题：{payload.question}。这是初始版本回显，后续将接入 RAG 检索。"
    references = [
        {
            "documentId": "demo-doc-001",
            "documentName": "产品手册示例.pdf",
            "chunkId": "chunk-1",
            "score": 0.88,
        }
    ]

    return success(
        {
            "answer": answer,
            "sessionId": payload.sessionId or "session-demo-001",
            "references": references,
        },
        trace_id,
    )

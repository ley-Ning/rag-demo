import re
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel, Field

from app.core.response import success

router = APIRouter(prefix="/documents", tags=["documents"])


class SplitPreviewRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    chunkSize: int = Field(default=400, ge=100, le=2000)
    overlap: int = Field(default=50, ge=0, le=500)


def _split_text(text: str, chunk_size: int, overlap: int) -> list[dict[str, object]]:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return []

    chunks: list[dict[str, object]] = []
    start = 0
    idx = 1
    step = max(chunk_size - overlap, 1)

    while start < len(clean):
        end = min(start + chunk_size, len(clean))
        content = clean[start:end]
        chunks.append(
            {
                "chunkId": f"preview-{idx}",
                "start": start,
                "end": end,
                "length": len(content),
                "content": content,
            }
        )
        idx += 1
        start += step

    return chunks


@router.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    strategy: str = Form("default"),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    task_id = f"task-{uuid4()}"
    _ = await file.read()

    return success(
        {
            "taskId": task_id,
            "fileName": file.filename,
            "strategy": strategy,
            "status": "queued",
        },
        trace_id,
    )


@router.post("/split-preview")
def split_preview(payload: SplitPreviewRequest, request: Request) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    chunks = _split_text(payload.content, payload.chunkSize, payload.overlap)
    return success({"items": chunks, "total": len(chunks)}, trace_id)

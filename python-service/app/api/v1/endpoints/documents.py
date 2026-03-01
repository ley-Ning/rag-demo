import json
import logging
import mimetypes
import re
from pathlib import Path as FsPath
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.database import get_db_conn
from app.core.rabbitmq import get_rabbitmq_client
from app.core.redis_client import get_redis_client
from app.core.response import success

router = APIRouter(prefix="/documents", tags=["documents"])
settings = get_settings()
logger = logging.getLogger(__name__)


class SplitPreviewRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    chunkSize: int = Field(default=400, ge=100, le=2000)
    overlap: int = Field(default=50, ge=0, le=500)
    strategy: str = Field(default="fixed", max_length=32)


class ImportFromToolRunRequest(BaseModel):
    toolRunId: int = Field(ge=1)
    title: str = Field(default="网页抓取导入", min_length=2, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=20)
    strategy: str = Field(default="parent_child", max_length=32)


SUPPORTED_SPLIT_STRATEGIES = {"fixed", "sentence", "paragraph", "parent_child", "pageindex"}
SPLIT_STRATEGY_ALIAS = {
    "default": "fixed",
    "fixed": "fixed",
    "sentence": "sentence",
    "paragraph": "paragraph",
    "parent-child": "parent_child",
    "parent_child": "parent_child",
    "parentchild": "parent_child",
    "pageindex": "pageindex",
    "page-index": "pageindex",
    "page_index": "pageindex",
}

MARKDOWN_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")
CHAPTER_HEADING_PATTERN = re.compile(
    r"^第[一二三四五六七八九十百千万零两0-9]+[章节部分篇卷][\s:：、.．-]*(.+)?$",
)
NUMBERED_HEADING_PATTERN = re.compile(r"^(\d+(?:\.\d+){0,4})[\s、.．:：\)]*(.+)$")
PAGE_NO_PATTERN = re.compile(r"第\s*(\d+)\s*页|page\s*(\d+)", flags=re.IGNORECASE)


def _parse_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _sanitize_file_name(file_name: str) -> str:
    raw = FsPath(file_name).name.strip() or "unnamed"
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", raw)
    return safe[:180] or "unnamed"


async def _save_upload_file(file: UploadFile, document_id: str, file_name: str) -> str:
    uploads_dir = settings.documents_upload_path
    uploads_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_file_name(file_name)
    target_path = uploads_dir / f"{document_id}-{safe_name}"

    with target_path.open("wb") as output:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)

    try:
        file.file.seek(0)
    except Exception:
        pass
    return str(target_path)


def _save_text_file(document_id: str, file_name: str, content: str) -> str:
    uploads_dir = settings.documents_upload_path
    uploads_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_file_name(file_name)
    target_path = uploads_dir / f"{document_id}-{safe_name}"
    target_path.write_text(content, encoding="utf-8")
    return str(target_path)


def _to_document_item(row: Any) -> dict[str, Any]:
    metadata = _parse_metadata(row["metadata"])
    file_size_value = metadata.get("fileSizeBytes")
    try:
        file_size = int(file_size_value) if file_size_value is not None else 0
    except Exception:
        file_size = 0

    return {
        "documentId": str(row["document_id"]),
        "fileName": row["file_name"] or "",
        "source": row["source"] or "",
        "status": row["status"] or "queued",
        "taskId": metadata.get("taskId"),
        "strategy": metadata.get("strategy"),
        "fileSizeBytes": file_size,
        "traceId": metadata.get("traceId"),
        "createdAt": row["created_at"].isoformat(),
        "updatedAt": row["updated_at"].isoformat(),
    }


def _normalize_strategy(strategy: str) -> str:
    normalized = strategy.strip().lower()
    mapped = SPLIT_STRATEGY_ALIAS.get(normalized)
    if not mapped:
        raise HTTPException(
            status_code=400,
            detail=(
                "不支持的切分策略: "
                f"{strategy}，可选: {', '.join(sorted(SUPPORTED_SPLIT_STRATEGIES))}"
            ),
        )
    return mapped


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_text_fixed(text: str, chunk_size: int, overlap: int) -> list[dict[str, object]]:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return []

    chunks: list[dict[str, object]] = []
    start = 0
    idx = 1
    safe_overlap = min(overlap, max(chunk_size - 1, 0))
    step = max(chunk_size - safe_overlap, 1)

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


def _split_long_unit(unit: str, chunk_size: int) -> list[str]:
    if len(unit) <= chunk_size:
        return [unit]
    return [unit[idx : idx + chunk_size] for idx in range(0, len(unit), chunk_size)]


def _merge_units(units: list[str], chunk_size: int) -> list[str]:
    merged: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
            continue
        candidate = f"{current} {unit}"
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            merged.append(current)
            current = unit
    if current:
        merged.append(current)
    return merged


def _split_text_sentence(text: str, chunk_size: int) -> list[str]:
    clean = _normalize_text(text)
    if not clean:
        return []
    units = [
        sentence.strip()
        for sentence in re.split(r"(?<=[。！？!?；;])\s*", clean)
        if sentence.strip()
    ]
    if not units:
        return _split_long_unit(clean, chunk_size)
    flattened: list[str] = []
    for unit in units:
        flattened.extend(_split_long_unit(unit, chunk_size))
    return _merge_units(flattened, chunk_size)


def _split_text_paragraph(text: str, chunk_size: int) -> list[str]:
    raw = text.replace("\r\n", "\n").strip()
    if not raw:
        return []
    units = [
        _normalize_text(paragraph)
        for paragraph in re.split(r"\n{2,}", raw)
        if _normalize_text(paragraph)
    ]
    if len(units) <= 1:
        return _split_text_sentence(text, chunk_size)
    flattened: list[str] = []
    for unit in units:
        flattened.extend(_split_long_unit(unit, chunk_size))
    return _merge_units(flattened, chunk_size)


def _extract_page_no(line: str) -> int | None:
    match = PAGE_NO_PATTERN.search(line)
    if not match:
        return None
    value = match.group(1) or match.group(2) or ""
    if not value:
        return None
    try:
        page_no = int(value)
    except Exception:
        return None
    if page_no <= 0:
        return None
    return page_no


def _detect_heading(line: str) -> tuple[int, str] | None:
    stripped = line.strip()
    if not stripped:
        return None

    markdown_match = MARKDOWN_HEADING_PATTERN.match(stripped)
    if markdown_match:
        level = min(max(len(markdown_match.group(1)), 1), 4)
        title = _normalize_text(markdown_match.group(2))
        return (level, title) if title else None

    chapter_match = CHAPTER_HEADING_PATTERN.match(stripped)
    if chapter_match:
        return (1, _normalize_text(stripped))

    numbered_match = NUMBERED_HEADING_PATTERN.match(stripped)
    if numbered_match:
        number = numbered_match.group(1)
        title_text = _normalize_text(numbered_match.group(2))
        if not title_text:
            return None
        if not re.search(r"[A-Za-z\u4e00-\u9fff]", title_text):
            return None
        level = min(number.count(".") + 1, 4)
        return (level, f"{number} {title_text}")

    return None


def _build_pageindex_sections(text: str) -> list[dict[str, Any]]:
    raw = text.replace("\r\n", "\n").strip()
    if not raw:
        return []

    sections: list[dict[str, Any]] = []
    lines = raw.split("\n")
    level_counters = [0, 0, 0, 0]
    heading_stack: list[str] = []
    last_seen_page = 1

    current: dict[str, Any] = {
        "title": "文档正文",
        "level": 1,
        "nodeId": "node-1",
        "nodePath": "文档正文",
        "pageStart": None,
        "pageEnd": None,
        "contentLines": [],
    }

    def flush_current() -> None:
        content = _normalize_text("\n".join(current["contentLines"]))
        if not content:
            return
        sections.append(
            {
                "title": current["title"],
                "level": current["level"],
                "nodeId": current["nodeId"],
                "nodePath": current["nodePath"],
                "pageStart": current["pageStart"],
                "pageEnd": current["pageEnd"],
                "content": content,
            }
        )

    for line in lines:
        stripped = line.strip()
        page_no = _extract_page_no(stripped)
        if page_no is not None:
            last_seen_page = page_no
            if current["pageStart"] is None:
                current["pageStart"] = page_no
            current["pageEnd"] = page_no

        heading = _detect_heading(stripped)
        if heading:
            flush_current()

            level, title = heading
            bounded_level = min(max(level, 1), 4)
            if bounded_level > len(heading_stack) + 1:
                bounded_level = len(heading_stack) + 1
            if bounded_level <= len(heading_stack):
                heading_stack = heading_stack[: bounded_level - 1]
            heading_stack.append(title)

            level_counters[bounded_level - 1] += 1
            for idx in range(bounded_level, len(level_counters)):
                level_counters[idx] = 0
            node_seq = [str(value) for value in level_counters[:bounded_level] if value > 0]
            node_id = f"node-{'-'.join(node_seq)}"

            current = {
                "title": title,
                "level": bounded_level,
                "nodeId": node_id,
                "nodePath": " > ".join(heading_stack),
                "pageStart": page_no if page_no is not None else last_seen_page,
                "pageEnd": page_no if page_no is not None else last_seen_page,
                "contentLines": [],
            }
            continue

        if stripped or current["contentLines"]:
            current["contentLines"].append(line)

    flush_current()

    if not sections:
        return []

    current_page = 1
    for section in sections:
        page_start = section.get("pageStart")
        page_end = section.get("pageEnd")
        if isinstance(page_start, int) and page_start > 0:
            current_page = page_start
        else:
            section["pageStart"] = current_page
        if isinstance(page_end, int) and page_end > 0:
            current_page = max(current_page, page_end)
        else:
            section["pageEnd"] = section["pageStart"]

    return sections


def _split_text_pageindex(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[dict[str, object]]:
    clean = _normalize_text(text)
    if not clean:
        return []

    sections = _build_pageindex_sections(text)
    if not sections:
        return _split_text_parent_child(text, chunk_size, overlap)

    chunks: list[dict[str, object]] = []
    chunk_index = 1
    search_cursor = 0

    for section in sections:
        section_content = str(section.get("content", "")).strip()
        if not section_content:
            continue

        section_start = clean.find(section_content, search_cursor)
        if section_start < 0:
            section_start = clean.find(section_content)
        if section_start < 0:
            section_start = search_cursor
        section_end = min(section_start + len(section_content), len(clean))
        search_cursor = section_end

        section_units = _split_text_paragraph(section_content, chunk_size)
        if not section_units:
            section_units = _split_text_sentence(section_content, chunk_size)
        if not section_units:
            section_units = _split_long_unit(section_content, chunk_size)

        section_chunks = _build_chunks_from_units(section_content, section_units, overlap)
        for section_chunk in section_chunks:
            local_start = int(section_chunk["start"])
            local_end = int(section_chunk["end"])
            global_start = min(section_start + local_start, len(clean))
            global_end = min(section_start + local_end, len(clean))
            content = clean[global_start:global_end]
            if not content:
                continue

            page_start = section.get("pageStart")
            page_end = section.get("pageEnd")
            level = section.get("level")
            section_title = str(section.get("title") or "文档正文")
            node_path = str(section.get("nodePath") or section_title)

            chunks.append(
                {
                    "chunkId": f"preview-{chunk_index}",
                    "start": global_start,
                    "end": global_end,
                    "length": len(content),
                    "content": content,
                    "nodeId": str(section.get("nodeId") or f"node-{chunk_index}"),
                    "nodePath": node_path,
                    "level": int(level) if isinstance(level, int) else 1,
                    "pageStart": int(page_start) if isinstance(page_start, int) else 1,
                    "pageEnd": int(page_end) if isinstance(page_end, int) else 1,
                    "charStart": global_start,
                    "charEnd": global_end,
                    "sectionTitle": section_title,
                }
            )
            chunk_index += 1

    return chunks


def _split_text_parent_child(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[dict[str, object]]:
    clean = _normalize_text(text)
    if not clean:
        return []

    # 父块更大，子块保留 chunk_size，便于后续做父召回 + 子精排
    parent_chunk_size = min(max(chunk_size * 3, chunk_size), 4000)
    parent_units = _split_text_paragraph(text, parent_chunk_size)
    if not parent_units:
        parent_units = _split_text_sentence(text, parent_chunk_size)

    parent_chunks = _build_chunks_from_units(clean, parent_units, overlap=0)
    if not parent_chunks:
        return []

    child_chunks: list[dict[str, object]] = []
    child_index = 1

    for parent_index, parent in enumerate(parent_chunks, start=1):
        parent_start = int(parent["start"])
        parent_end = int(parent["end"])
        parent_content = clean[parent_start:parent_end]
        parent_chunk_id = f"parent-{parent_index}"

        child_units = _split_text_sentence(parent_content, chunk_size)
        if not child_units:
            child_units = _split_long_unit(parent_content, chunk_size)
        local_children = _build_chunks_from_units(parent_content, child_units, overlap)

        for child in local_children:
            local_start = int(child["start"])
            local_end = int(child["end"])
            absolute_start = parent_start + local_start
            absolute_end = parent_start + local_end
            content = clean[absolute_start:absolute_end]
            child_chunks.append(
                {
                    "chunkId": f"preview-{child_index}",
                    "start": absolute_start,
                    "end": absolute_end,
                    "length": len(content),
                    "content": content,
                    "parentChunkId": parent_chunk_id,
                    "parentStart": parent_start,
                    "parentEnd": parent_end,
                    "parentLength": parent_end - parent_start,
                }
            )
            child_index += 1

    return child_chunks


def _build_chunks_from_units(
    text: str,
    chunk_units: list[str],
    overlap: int,
) -> list[dict[str, object]]:
    clean = _normalize_text(text)
    if not clean or not chunk_units:
        return []

    safe_overlap = min(overlap, 500)
    chunks: list[dict[str, object]] = []
    cursor = 0

    for idx, unit in enumerate(chunk_units, start=1):
        start = clean.find(unit, cursor)
        if start < 0:
            start = cursor
        end = min(start + len(unit), len(clean))
        content_start = max(start - safe_overlap, 0) if idx > 1 else start
        content = clean[content_start:end]

        chunks.append(
            {
                "chunkId": f"preview-{idx}",
                "start": content_start,
                "end": end,
                "length": len(content),
                "content": content,
            }
        )
        cursor = end

    return chunks


def _split_text(
    text: str,
    chunk_size: int,
    overlap: int,
    strategy: str,
) -> list[dict[str, object]]:
    normalized_strategy = _normalize_strategy(strategy)
    if normalized_strategy == "fixed":
        return _split_text_fixed(text, chunk_size, overlap)
    if normalized_strategy == "sentence":
        units = _split_text_sentence(text, chunk_size)
        return _build_chunks_from_units(text, units, overlap)
    if normalized_strategy == "pageindex":
        return _split_text_pageindex(text, chunk_size, overlap)
    if normalized_strategy == "parent_child":
        return _split_text_parent_child(text, chunk_size, overlap)
    units = _split_text_paragraph(text, chunk_size)
    return _build_chunks_from_units(text, units, overlap)


@router.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    strategy: str = Form("fixed"),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    task_id = f"task-{uuid4()}"
    document_id = str(uuid4())
    normalized_strategy = _normalize_strategy(strategy)
    file_name = file.filename or "unnamed"
    file_size = 0
    try:
        file.file.seek(0, 2)
        file_size = int(file.file.tell())
        file.file.seek(0)
    except Exception:
        file_size = 0

    storage_path = ""
    try:
        storage_path = await _save_upload_file(file, document_id, file_name)
    except Exception as exc:
        logger.exception("[%s] Save upload file failed: %s", trace_id, exc)
        raise HTTPException(status_code=500, detail="文件保存失败，请稍后重试") from exc

    metadata = {
        "taskId": task_id,
        "strategy": normalized_strategy,
        "fileSizeBytes": file_size,
        "traceId": trace_id,
        "storagePath": storage_path,
    }

    await conn.execute(
        """
        INSERT INTO documents (id, file_name, source, status, metadata)
        VALUES ($1::uuid, $2, $3, $4, $5::jsonb)
        """,
        document_id,
        file_name,
        "upload",
        "queued",
        json.dumps(metadata, ensure_ascii=False),
    )

    queue_payload = {
        "taskId": task_id,
        "documentId": document_id,
        "fileName": file_name,
        "strategy": normalized_strategy,
        "fileSizeBytes": file_size,
        "traceId": trace_id,
        "storagePath": storage_path,
    }
    await get_rabbitmq_client().publish_json(settings.rabbitmq_documents_queue, queue_payload)

    await get_redis_client().set_json(
        f"{settings.redis_key_prefix}:task:{task_id}",
        {
            "taskId": task_id,
            "documentId": document_id,
            "status": "queued",
            "traceId": trace_id,
        },
        ttl_seconds=3600,
    )

    return success(
        {
            "taskId": task_id,
            "documentId": document_id,
            "fileName": file_name,
            "fileSizeBytes": file_size,
            "strategy": normalized_strategy,
            "status": "queued",
        },
        trace_id,
    )


@router.get("")
async def list_documents(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None, max_length=32),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    normalized_status = status.strip() if status else ""

    conditions = ["deleted_at IS NULL"]
    args: list[Any] = []
    if normalized_status:
        args.append(normalized_status)
        conditions.append(f"status = ${len(args)}")

    where_clause = "WHERE " + " AND ".join(conditions)
    count_sql = f"""
        SELECT COUNT(1)
        FROM documents
        {where_clause}
    """
    total_count = await conn.fetchval(count_sql, *args)

    args_with_limit = [*args, limit]
    list_sql = f"""
        SELECT
            id::text AS document_id,
            file_name,
            source,
            status,
            metadata,
            created_at,
            updated_at
        FROM documents
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ${len(args_with_limit)}
    """
    rows = await conn.fetch(list_sql, *args_with_limit)
    items = [_to_document_item(row) for row in rows]

    return success(
        {
            "items": items,
            "total": int(total_count or 0),
        },
        trace_id,
    )


@router.get("/{document_id}/status")
async def get_document_status(
    request: Request,
    document_id: str = Path(min_length=8, max_length=64),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    row = await conn.fetchrow(
        """
        SELECT
            id::text AS document_id,
            file_name,
            source,
            status,
            metadata,
            created_at,
            updated_at
        FROM documents
        WHERE id::text = $1
          AND deleted_at IS NULL
        LIMIT 1
        """,
        document_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")

    item = _to_document_item(row)
    return success(
        {
            "documentId": item["documentId"],
            "fileName": item["fileName"],
            "status": item["status"],
            "taskId": item["taskId"],
            "strategy": item["strategy"],
            "updatedAt": item["updatedAt"],
            "createdAt": item["createdAt"],
        },
        trace_id,
    )


@router.get("/{document_id}")
async def get_document_detail(
    request: Request,
    document_id: str = Path(min_length=8, max_length=64),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    row = await conn.fetchrow(
        """
        SELECT
            id::text AS document_id,
            file_name,
            source,
            status,
            metadata,
            created_at,
            updated_at
        FROM documents
        WHERE id::text = $1
          AND deleted_at IS NULL
        LIMIT 1
        """,
        document_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")

    return success(_to_document_item(row), trace_id)


@router.get("/{document_id}/file")
async def preview_document_file(
    request: Request,
    document_id: str = Path(min_length=8, max_length=64),
    conn=Depends(get_db_conn),
):
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    row = await conn.fetchrow(
        """
        SELECT
            id::text AS document_id,
            file_name,
            metadata
        FROM documents
        WHERE id::text = $1
          AND deleted_at IS NULL
        LIMIT 1
        """,
        document_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="文档不存在")

    metadata = _parse_metadata(row["metadata"])
    storage_path_raw = str(metadata.get("storagePath") or "").strip()
    if not storage_path_raw:
        raise HTTPException(status_code=404, detail="文档原文件不存在")

    try:
        storage_path = FsPath(storage_path_raw).expanduser().resolve()
        uploads_root = settings.documents_upload_path.expanduser().resolve()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="文档文件路径非法") from exc

    if uploads_root not in storage_path.parents and storage_path != uploads_root:
        raise HTTPException(status_code=400, detail="文档文件路径非法")
    if not storage_path.exists() or not storage_path.is_file():
        raise HTTPException(status_code=404, detail="文档原文件不存在")

    file_name = str(row["file_name"] or storage_path.name or "document")
    media_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    encoded_name = quote(file_name)
    headers = {
        "x-trace-id": trace_id,
        "content-disposition": f"inline; filename*=UTF-8''{encoded_name}",
    }
    return FileResponse(path=storage_path, media_type=media_type, headers=headers)


@router.post("/import-from-tool-run")
async def import_document_from_tool_run(
    payload: ImportFromToolRunRequest,
    request: Request,
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    normalized_strategy = _normalize_strategy(payload.strategy)

    run_row = await conn.fetchrow(
        """
        SELECT
            id,
            trace_id,
            tool_name,
            status,
            output_payload
        FROM tool_runs
        WHERE id = $1
        LIMIT 1
        """,
        payload.toolRunId,
    )
    if not run_row:
        raise HTTPException(status_code=404, detail="toolRun 不存在")
    if run_row["status"] != "success":
        raise HTTPException(status_code=400, detail="toolRun 不是成功状态，不能导入")

    output_payload = _parse_metadata(run_row["output_payload"])
    excerpt = str(output_payload.get("excerpt", "")).strip()
    title = str(output_payload.get("title", payload.title)).strip()
    source_url = str(output_payload.get("url", "")).strip()
    if not excerpt:
        raise HTTPException(status_code=400, detail="toolRun 没有可导入正文内容")

    document_id = str(uuid4())
    task_id = f"task-{uuid4()}"
    tags = [item.strip() for item in payload.tags if item and item.strip()][:20]
    markdown = "\n".join(
        [
            f"# {title or payload.title}",
            "",
            f"- 来源 URL: {source_url or '未知'}",
            f"- 来源 ToolRun: {payload.toolRunId}",
            f"- 来源 Trace: {run_row['trace_id']}",
            f"- 标签: {', '.join(tags) if tags else '无'}",
            "",
            "## 正文",
            excerpt,
            "",
        ]
    )

    file_name = _sanitize_file_name(f"{title or 'tool-import'}.md")
    storage_path = _save_text_file(document_id, file_name, markdown)
    file_size = len(markdown.encode("utf-8"))
    metadata = {
        "taskId": task_id,
        "strategy": normalized_strategy,
        "fileSizeBytes": file_size,
        "traceId": trace_id,
        "storagePath": storage_path,
        "sourceToolRunId": payload.toolRunId,
        "sourceUrl": source_url,
        "tags": tags,
    }

    await conn.execute(
        """
        INSERT INTO documents (id, file_name, source, status, metadata)
        VALUES ($1::uuid, $2, $3, $4, $5::jsonb)
        """,
        document_id,
        file_name,
        "tool_run_import",
        "queued",
        json.dumps(metadata, ensure_ascii=False),
    )

    queue_payload = {
        "taskId": task_id,
        "documentId": document_id,
        "fileName": file_name,
        "strategy": normalized_strategy,
        "fileSizeBytes": file_size,
        "traceId": trace_id,
        "storagePath": storage_path,
    }
    await get_rabbitmq_client().publish_json(settings.rabbitmq_documents_queue, queue_payload)
    await get_redis_client().set_json(
        f"{settings.redis_key_prefix}:task:{task_id}",
        {
            "taskId": task_id,
            "documentId": document_id,
            "status": "queued",
            "traceId": trace_id,
            "sourceToolRunId": payload.toolRunId,
        },
        ttl_seconds=3600,
    )

    return success(
        {
            "taskId": task_id,
            "documentId": document_id,
            "fileName": file_name,
            "fileSizeBytes": file_size,
            "strategy": normalized_strategy,
            "status": "queued",
            "sourceToolRunId": payload.toolRunId,
        },
        trace_id,
    )


@router.post("/split-preview")
def split_preview(payload: SplitPreviewRequest, request: Request) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    normalized_strategy = _normalize_strategy(payload.strategy)
    chunks = _split_text(
        payload.content,
        payload.chunkSize,
        payload.overlap,
        normalized_strategy,
    )
    return success(
        {
            "items": chunks,
            "total": len(chunks),
            "strategy": normalized_strategy,
        },
        trace_id,
    )


@router.delete("/{document_id}")
async def delete_document(
    request: Request,
    document_id: str = Path(min_length=8, max_length=64),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    """软删除文档"""
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    row = await conn.fetchrow(
        """
        UPDATE documents
        SET deleted_at = NOW(), updated_at = NOW()
        WHERE id::text = $1 AND deleted_at IS NULL
        RETURNING id::text AS document_id, file_name
        """,
        document_id,
    )

    if not row:
        raise HTTPException(status_code=404, detail="文档不存在或已被删除")

    logger.info(
        "[%s] Document deleted: document_id=%s, file_name=%s",
        trace_id,
        row["document_id"],
        row["file_name"],
    )

    return success(
        {
            "deleted": True,
            "documentId": row["document_id"],
            "fileName": row["file_name"],
        },
        trace_id,
    )


@router.get("/{document_id}/chunks")
async def get_document_chunks(
    request: Request,
    document_id: str = Path(min_length=8, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    """获取文档的分块列表"""
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    # 检查文档是否存在
    doc_row = await conn.fetchrow(
        """
        SELECT id::text AS document_id, file_name, status
        FROM documents
        WHERE id::text = $1 AND deleted_at IS NULL
        """,
        document_id,
    )

    if not doc_row:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 获取分块总数
    total_count = await conn.fetchval(
        """
        SELECT COUNT(1)
        FROM document_chunks
        WHERE document_id = $1
        """,
        doc_row["document_id"],
    )

    # 获取分块列表
    rows = await conn.fetch(
        """
        SELECT
            id::text AS chunk_id,
            chunk_index,
            content,
            token_count,
            metadata,
            created_at
        FROM document_chunks
        WHERE document_id = $1
        ORDER BY chunk_index ASC
        LIMIT $2 OFFSET $3
        """,
        doc_row["document_id"],
        limit,
        offset,
    )

    chunks: list[dict[str, Any]] = []
    for row in rows:
        metadata = _parse_metadata(row["metadata"])
        node_id = metadata.get("nodeId") or metadata.get("node_id")
        node_path = metadata.get("nodePath") or metadata.get("node_path")
        level = metadata.get("level")
        page_start = metadata.get("pageStart") or metadata.get("page_start")
        page_end = metadata.get("pageEnd") or metadata.get("page_end")
        char_start = metadata.get("charStart") or metadata.get("char_start")
        char_end = metadata.get("charEnd") or metadata.get("char_end")
        section_title = metadata.get("sectionTitle") or metadata.get("section_title")

        try:
            level = int(level) if level is not None else None
        except Exception:
            level = None
        try:
            page_start = int(page_start) if page_start is not None else None
        except Exception:
            page_start = None
        try:
            page_end = int(page_end) if page_end is not None else None
        except Exception:
            page_end = None
        try:
            char_start = int(char_start) if char_start is not None else None
        except Exception:
            char_start = None
        try:
            char_end = int(char_end) if char_end is not None else None
        except Exception:
            char_end = None

        chunks.append(
            {
                "chunkId": row["chunk_id"],
                "chunkIndex": row["chunk_index"],
                "content": row["content"],
                "tokenCount": row["token_count"] or 0,
                "length": len(row["content"]) if row["content"] else 0,
                "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
                "nodeId": node_id,
                "nodePath": node_path,
                "level": level,
                "pageStart": page_start,
                "pageEnd": page_end,
                "charStart": char_start,
                "charEnd": char_end,
                "sectionTitle": section_title,
            }
        )

    return success(
        {
            "documentId": doc_row["document_id"],
            "fileName": doc_row["file_name"],
            "status": doc_row["status"],
            "chunks": chunks,
            "total": int(total_count or 0),
        },
        trace_id,
    )

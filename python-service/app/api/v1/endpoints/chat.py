import json
import logging
import time
from uuid import uuid4

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.database import db_conn_context, get_db_conn, get_optional_db_conn
from app.core.response import success
from app.domain.models_registry import _registry, model_supports
from app.domain.rag_service import RAGExecutionError, SkillCallLog, get_rag_service
from app.domain.tools.orchestrator import DeepThinkRunRecord, ToolRunRecord, get_tool_orchestrator

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)
settings = get_settings()


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    modelId: str = Field(min_length=1)
    sessionId: str | None = None
    embeddingModelId: str | None = None
    documentIds: list[str] | None = None
    useRag: bool = False
    enableTools: bool | None = None
    enableDeepThink: bool | None = None
    maxToolSteps: int | None = Field(default=None, ge=1, le=12)


# ============== 聊天历史存储函数 ==============

async def _save_chat_message(
    conn: asyncpg.Connection,
    session_id: str,
    role: str,
    content: str,
    references: list[dict[str, object]] | None = None,
) -> None:
    """保存单条聊天消息"""
    try:
        await conn.execute(
            """
            INSERT INTO chat_messages (session_id, role, content, "references")
            VALUES ($1, $2, $3, $4::jsonb)
            """,
            session_id,
            role,
            content,
            json.dumps(references or [], ensure_ascii=False),
        )
    except Exception:
        logger.exception("Failed to save chat message, session=%s", session_id)


async def _ensure_session(
    conn: asyncpg.Connection,
    session_id: str,
    model_id: str,
    use_rag: bool,
    title: str | None = None,
) -> None:
    """确保会话存在，不存在则创建"""
    try:
        await conn.execute(
            """
            INSERT INTO chat_sessions (session_id, model_id, use_rag, title)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (session_id) DO UPDATE SET updated_at = NOW()
            """,
            session_id,
            model_id,
            use_rag,
            title or "新对话",
        )
    except Exception:
        logger.exception("Failed to ensure session, session=%s", session_id)


async def _write_retrieval_log(
    conn: asyncpg.Connection,
    *,
    trace_id: str,
    session_id: str | None,
    question: str,
    model_id: str,
    latency_ms: int,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    mcp_call_count: int,
    status: str,
    error_message: str | None,
    references: list[dict[str, object]],
) -> int | None:
    """写 retrieval 日志并返回主键，失败仅记录日志不打断主流程"""
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO retrieval_logs (
                trace_id,
                session_id,
                question,
                model_id,
                top_k,
                threshold,
                latency_ms,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                mcp_call_count,
                status,
                error_message,
                results
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12, $13, $14::jsonb
            )
            RETURNING id
            """,
            trace_id,
            session_id,
            question,
            model_id,
            settings.rag_top_k,
            settings.rag_min_score,
            latency_ms,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            mcp_call_count,
            status,
            error_message,
            json.dumps(references, ensure_ascii=False),
        )
        return int(row["id"]) if row else None
    except Exception:
        logger.exception("Failed to write retrieval_logs, trace_id=%s", trace_id)
        return None


async def _write_skill_logs(
    conn: asyncpg.Connection,
    *,
    retrieval_log_id: int | None,
    trace_id: str,
    session_id: str | None,
    skill_calls: list[SkillCallLog],
) -> None:
    """写 skill 调用日志，失败不打断主流程"""
    if retrieval_log_id is None or not skill_calls:
        return

    try:
        await conn.executemany(
            """
            INSERT INTO mcp_skill_logs (
                retrieval_log_id,
                trace_id,
                session_id,
                skill_name,
                status,
                latency_ms,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                input_summary,
                output_summary,
                error_message
            )
            VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11, $12
            )
            """,
            [
                (
                    retrieval_log_id,
                    trace_id,
                    session_id,
                    call.skill_name,
                    call.status,
                    call.latency_ms,
                    call.prompt_tokens,
                    call.completion_tokens,
                    call.total_tokens,
                    call.input_summary,
                    call.output_summary,
                    call.error_message,
                )
                for call in skill_calls
            ],
        )
    except Exception:
        logger.exception("Failed to write mcp_skill_logs, trace_id=%s", trace_id)


def _tool_run_to_dict(item: ToolRunRecord) -> dict[str, object]:
    return {
        "toolName": item.tool_name,
        "source": item.source,
        "status": item.status,
        "latencyMs": item.latency_ms,
        "promptTokens": item.prompt_tokens,
        "completionTokens": item.completion_tokens,
        "totalTokens": item.total_tokens,
        "inputSummary": item.input_summary,
        "outputSummary": item.output_summary,
        "outputPayload": item.output_payload,
        "errorMessage": item.error_message,
    }


def _deep_think_run_to_dict(item: DeepThinkRunRecord) -> dict[str, object]:
    return {
        "stage": item.stage,
        "status": item.status,
        "latencyMs": item.latency_ms,
        "inputSummary": item.input_summary,
        "outputSummary": item.output_summary,
        "payload": item.payload,
        "errorMessage": item.error_message,
    }


def _to_skill_call_from_tool_run(item: ToolRunRecord) -> SkillCallLog:
    return SkillCallLog(
        skill_name=item.tool_name,
        status=item.status,
        latency_ms=item.latency_ms,
        prompt_tokens=item.prompt_tokens,
        completion_tokens=item.completion_tokens,
        total_tokens=item.total_tokens,
        input_summary=item.input_summary,
        output_summary=item.output_summary,
        error_message=item.error_message,
    )


def _to_skill_call_from_deep_think(item: DeepThinkRunRecord) -> SkillCallLog:
    return SkillCallLog(
        skill_name=f"mcp.deep_think.{item.stage}",
        status=item.status,
        latency_ms=item.latency_ms,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        input_summary=item.input_summary,
        output_summary=item.output_summary,
        error_message=item.error_message,
    )


async def _write_tool_runs(
    conn: asyncpg.Connection,
    *,
    retrieval_log_id: int | None,
    trace_id: str,
    session_id: str | None,
    tool_runs: list[ToolRunRecord],
) -> None:
    if not tool_runs:
        return
    try:
        await conn.executemany(
            """
            INSERT INTO tool_runs (
                retrieval_log_id,
                trace_id,
                session_id,
                tool_name,
                source,
                status,
                latency_ms,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                input_summary,
                output_summary,
                output_payload,
                error_message
            )
            VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11, $12, $13::jsonb, $14
            )
            """,
            [
                (
                    retrieval_log_id,
                    trace_id,
                    session_id,
                    item.tool_name,
                    item.source,
                    item.status,
                    item.latency_ms,
                    item.prompt_tokens,
                    item.completion_tokens,
                    item.total_tokens,
                    item.input_summary,
                    item.output_summary,
                    json.dumps(item.output_payload, ensure_ascii=False),
                    item.error_message,
                )
                for item in tool_runs
            ],
        )
    except Exception:
        logger.exception("Failed to write tool_runs, trace_id=%s", trace_id)


async def _write_deep_think_runs(
    conn: asyncpg.Connection,
    *,
    retrieval_log_id: int | None,
    trace_id: str,
    session_id: str | None,
    deep_think_runs: list[DeepThinkRunRecord],
) -> None:
    if not deep_think_runs:
        return
    try:
        await conn.executemany(
            """
            INSERT INTO deep_think_runs (
                retrieval_log_id,
                trace_id,
                session_id,
                stage,
                status,
                latency_ms,
                input_summary,
                output_summary,
                payload,
                error_message
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10
            )
            """,
            [
                (
                    retrieval_log_id,
                    trace_id,
                    session_id,
                    item.stage,
                    item.status,
                    item.latency_ms,
                    item.input_summary,
                    item.output_summary,
                    json.dumps(item.payload, ensure_ascii=False),
                    item.error_message,
                )
                for item in deep_think_runs
            ],
        )
    except Exception:
        logger.exception("Failed to write deep_think_runs, trace_id=%s", trace_id)


def _sse_event(event: str, data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _chunk_text(text: str, size: int = 24) -> list[str]:
    if not text:
        return []
    return [text[index : index + size] for index in range(0, len(text), size)]


def _parse_references(value: object) -> list[dict[str, object]]:
    """兼容 asyncpg 对 jsonb 的不同解码行为"""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except Exception:
            return []
    return []


@router.post("/ask")
async def ask_question(
    payload: AskRequest,
    request: Request,
    conn: asyncpg.Connection | None = Depends(get_optional_db_conn),
) -> dict[str, object]:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    start_time = time.monotonic()
    enable_tools = payload.enableTools if payload.enableTools is not None else settings.mcp_auto_call
    enable_deep_think = (
        payload.enableDeepThink
        if payload.enableDeepThink is not None
        else settings.deep_think_enabled
    )
    max_tool_steps = payload.maxToolSteps or settings.mcp_max_steps

    if not model_supports(payload.modelId, "chat"):
        raise HTTPException(status_code=400, detail="当前模型不可用于聊天")

    if payload.useRag and conn is None:
        raise HTTPException(status_code=503, detail="数据库未就绪，暂时无法使用 RAG 检索")

    embedding_model_id = payload.embeddingModelId or "text-embedding-3-large"
    if payload.useRag and not model_supports(embedding_model_id, "embedding"):
        raise HTTPException(status_code=400, detail=f"Embedding 模型不可用: {embedding_model_id}")

    rewritten_question = payload.question
    orchestration_skill_calls: list[SkillCallLog] = []
    tool_runs: list[ToolRunRecord] = []
    deep_think_runs: list[DeepThinkRunRecord] = []
    deep_think_summary: str | None = None

    try:
        if conn is not None and settings.mcp_enabled and (enable_tools or enable_deep_think):
            orchestrator = get_tool_orchestrator()
            orchestration = await orchestrator.orchestrate(
                conn,
                question=payload.question,
                trace_id=trace_id,
                enable_tools=enable_tools,
                enable_deep_think=enable_deep_think,
                max_tool_steps=max_tool_steps,
            )
            rewritten_question = orchestration.rewritten_question
            tool_runs = orchestration.tool_runs
            deep_think_runs = orchestration.deep_think_runs
            deep_think_summary = orchestration.deep_think_summary
            orchestration_skill_calls = [
                SkillCallLog(
                    skill_name=item.skill_name,
                    status=item.status,
                    latency_ms=item.latency_ms,
                    prompt_tokens=item.prompt_tokens,
                    completion_tokens=item.completion_tokens,
                    total_tokens=item.total_tokens,
                    input_summary=item.input_summary,
                    output_summary=item.output_summary,
                    error_message=item.error_message,
                )
                for item in orchestration.skill_calls
            ]
            orchestration_skill_calls.extend(
                [_to_skill_call_from_deep_think(item) for item in deep_think_runs]
            )

        rag_service = get_rag_service()
        if payload.useRag:
            result = await rag_service.ask(
                question=rewritten_question,
                model_id=payload.modelId,
                registry=_registry,
                conn=conn,
                embedding_model_id=embedding_model_id,
                session_id=payload.sessionId,
                document_ids=payload.documentIds,
            )
        else:
            result = await rag_service.chat_only(
                question=rewritten_question,
                model_id=payload.modelId,
                registry=_registry,
                session_id=payload.sessionId,
            )
        merged_skill_calls = [*orchestration_skill_calls, *result.skill_calls]

        latency_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            "[%s] Chat query completed in %sms, mode=%s, references: %d",
            trace_id,
            latency_ms,
            "rag" if payload.useRag else "chat-only",
            len(result.references),
        )

        if conn is not None:
            retrieval_log_id = await _write_retrieval_log(
                conn,
                trace_id=trace_id,
                session_id=result.session_id,
                question=payload.question,
                model_id=result.model_id,
                latency_ms=latency_ms,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                mcp_call_count=len(merged_skill_calls),
                status="success",
                error_message=None,
                references=result.references,
            )
            await _write_skill_logs(
                conn,
                retrieval_log_id=retrieval_log_id,
                trace_id=trace_id,
                session_id=result.session_id,
                skill_calls=merged_skill_calls,
            )
            await _write_tool_runs(
                conn,
                retrieval_log_id=retrieval_log_id,
                trace_id=trace_id,
                session_id=result.session_id,
                tool_runs=tool_runs,
            )
            await _write_deep_think_runs(
                conn,
                retrieval_log_id=retrieval_log_id,
                trace_id=trace_id,
                session_id=result.session_id,
                deep_think_runs=deep_think_runs,
            )

        return success(
            {
                "answer": result.answer,
                "sessionId": result.session_id,
                "references": result.references,
                "toolRuns": [_tool_run_to_dict(item) for item in tool_runs],
                "deepThinkSummary": deep_think_summary,
                "deepThinkRuns": [_deep_think_run_to_dict(item) for item in deep_think_runs],
            },
            trace_id,
        )

    except KeyError as exc:
        logger.error("[%s] Model not found: %s", trace_id, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except RAGExecutionError as exc:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        logger.exception("[%s] Chat execution failed: %s", trace_id, exc)
        if conn is not None:
            retrieval_log_id = await _write_retrieval_log(
                conn,
                trace_id=trace_id,
                session_id=exc.session_id,
                question=payload.question,
                model_id=exc.model_id,
                latency_ms=latency_ms,
                prompt_tokens=exc.prompt_tokens,
                completion_tokens=exc.completion_tokens,
                total_tokens=exc.total_tokens,
                mcp_call_count=len(orchestration_skill_calls) + len(exc.skill_calls),
                status="failed",
                error_message=str(exc),
                references=[],
            )
            merged_error_skill_calls = [*orchestration_skill_calls, *exc.skill_calls]
            await _write_skill_logs(
                conn,
                retrieval_log_id=retrieval_log_id,
                trace_id=trace_id,
                session_id=exc.session_id,
                skill_calls=merged_error_skill_calls,
            )
            await _write_tool_runs(
                conn,
                retrieval_log_id=retrieval_log_id,
                trace_id=trace_id,
                session_id=exc.session_id,
                tool_runs=tool_runs,
            )
            await _write_deep_think_runs(
                conn,
                retrieval_log_id=retrieval_log_id,
                trace_id=trace_id,
                session_id=exc.session_id,
                deep_think_runs=deep_think_runs,
            )
        raise HTTPException(status_code=500, detail="问答服务暂时不可用，请稍后重试") from exc

    except Exception as exc:
        logger.exception("[%s] Chat query failed: %s", trace_id, exc)
        latency_ms = int((time.monotonic() - start_time) * 1000)
        if conn is not None:
            retrieval_log_id = await _write_retrieval_log(
                conn,
                trace_id=trace_id,
                session_id=payload.sessionId,
                question=payload.question,
                model_id=payload.modelId,
                latency_ms=latency_ms,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                mcp_call_count=len(orchestration_skill_calls),
                status="failed",
                error_message=str(exc),
                references=[],
            )
            await _write_skill_logs(
                conn,
                retrieval_log_id=retrieval_log_id,
                trace_id=trace_id,
                session_id=payload.sessionId,
                skill_calls=orchestration_skill_calls,
            )
            await _write_tool_runs(
                conn,
                retrieval_log_id=retrieval_log_id,
                trace_id=trace_id,
                session_id=payload.sessionId,
                tool_runs=tool_runs,
            )
            await _write_deep_think_runs(
                conn,
                retrieval_log_id=retrieval_log_id,
                trace_id=trace_id,
                session_id=payload.sessionId,
                deep_think_runs=deep_think_runs,
            )
        raise HTTPException(status_code=500, detail="问答服务暂时不可用，请稍后重试") from exc


@router.post("/ask-stream")
async def ask_question_stream(
    payload: AskRequest,
    request: Request,
) -> StreamingResponse:
    trace_id = request.headers.get("x-trace-id") or str(uuid4())
    enable_tools = payload.enableTools if payload.enableTools is not None else settings.mcp_auto_call
    enable_deep_think = (
        payload.enableDeepThink
        if payload.enableDeepThink is not None
        else settings.deep_think_enabled
    )
    max_tool_steps = payload.maxToolSteps or settings.mcp_max_steps

    if not model_supports(payload.modelId, "chat"):
        raise HTTPException(status_code=400, detail="当前模型不可用于聊天")

    session_id = payload.sessionId or f"session-{uuid4().hex[:8]}"
    use_rag = payload.useRag

    async def event_generator():
        rag_service = get_rag_service()
        full_answer = ""
        references: list[dict[str, object]] = []
        title = payload.question[:30] + ("..." if len(payload.question) > 30 else "")
        start_time = time.monotonic()
        model_id = payload.modelId
        skill_calls: list[SkillCallLog] = []
        orchestration_skill_calls: list[SkillCallLog] = []
        tool_runs: list[ToolRunRecord] = []
        deep_think_runs: list[DeepThinkRunRecord] = []
        deep_think_summary: str | None = None
        rewritten_question = payload.question
        usage_stats = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        llm_start_time: float | None = None
        log_written = False

        async def persist_observability_logs(
            conn: asyncpg.Connection,
            *,
            session_id_for_log: str | None,
            model_id_for_log: str,
            prompt_tokens: int,
            completion_tokens: int,
            total_tokens: int,
            status: str,
            error_message: str | None,
            references_for_log: list[dict[str, object]],
            skill_calls_for_log: list[SkillCallLog],
            tool_runs_for_log: list[ToolRunRecord],
            deep_think_runs_for_log: list[DeepThinkRunRecord],
        ) -> None:
            nonlocal log_written
            retrieval_log_id = await _write_retrieval_log(
                conn,
                trace_id=trace_id,
                session_id=session_id_for_log,
                question=payload.question,
                model_id=model_id_for_log,
                latency_ms=int((time.monotonic() - start_time) * 1000),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                mcp_call_count=len(skill_calls_for_log),
                status=status,
                error_message=error_message,
                references=references_for_log,
            )
            await _write_skill_logs(
                conn,
                retrieval_log_id=retrieval_log_id,
                trace_id=trace_id,
                session_id=session_id_for_log,
                skill_calls=skill_calls_for_log,
            )
            await _write_tool_runs(
                conn,
                retrieval_log_id=retrieval_log_id,
                trace_id=trace_id,
                session_id=session_id_for_log,
                tool_runs=tool_runs_for_log,
            )
            await _write_deep_think_runs(
                conn,
                retrieval_log_id=retrieval_log_id,
                trace_id=trace_id,
                session_id=session_id_for_log,
                deep_think_runs=deep_think_runs_for_log,
            )
            log_written = True

        try:
            if use_rag:
                embedding_model_id = payload.embeddingModelId or "text-embedding-3-large"
                if not model_supports(embedding_model_id, "embedding"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Embedding 模型不可用: {embedding_model_id}",
                    )

                try:
                    async with db_conn_context() as rag_conn:
                        if settings.mcp_enabled and (enable_tools or enable_deep_think):
                            orchestrator = get_tool_orchestrator()
                            orchestration = await orchestrator.orchestrate(
                                rag_conn,
                                question=payload.question,
                                trace_id=trace_id,
                                enable_tools=enable_tools,
                                enable_deep_think=enable_deep_think,
                                max_tool_steps=max_tool_steps,
                            )
                            rewritten_question = orchestration.rewritten_question
                            tool_runs = orchestration.tool_runs
                            deep_think_runs = orchestration.deep_think_runs
                            deep_think_summary = orchestration.deep_think_summary
                            orchestration_skill_calls = [
                                SkillCallLog(
                                    skill_name=item.skill_name,
                                    status=item.status,
                                    latency_ms=item.latency_ms,
                                    prompt_tokens=item.prompt_tokens,
                                    completion_tokens=item.completion_tokens,
                                    total_tokens=item.total_tokens,
                                    input_summary=item.input_summary,
                                    output_summary=item.output_summary,
                                    error_message=item.error_message,
                                )
                                for item in orchestration.skill_calls
                            ]
                            orchestration_skill_calls.extend(
                                [_to_skill_call_from_deep_think(item) for item in deep_think_runs]
                            )
                        result = await rag_service.ask(
                            question=rewritten_question,
                            model_id=payload.modelId,
                            registry=_registry,
                            conn=rag_conn,
                            embedding_model_id=embedding_model_id,
                            session_id=session_id,
                            document_ids=payload.documentIds,
                        )
                        model_id = result.model_id
                        skill_calls = [*orchestration_skill_calls, *result.skill_calls]
                        full_answer = result.answer
                        references = result.references

                        await _ensure_session(rag_conn, session_id, payload.modelId, use_rag, title)
                        await _save_chat_message(rag_conn, session_id, "user", payload.question)
                        await _save_chat_message(
                            rag_conn,
                            session_id,
                            "assistant",
                            full_answer,
                            references,
                        )
                        await persist_observability_logs(
                            rag_conn,
                            session_id_for_log=result.session_id,
                            model_id_for_log=result.model_id,
                            prompt_tokens=result.prompt_tokens,
                            completion_tokens=result.completion_tokens,
                            total_tokens=result.total_tokens,
                            status="success",
                            error_message=None,
                            references_for_log=references,
                            skill_calls_for_log=skill_calls,
                            tool_runs_for_log=tool_runs,
                            deep_think_runs_for_log=deep_think_runs,
                        )
                except RuntimeError as exc:
                    if isinstance(exc, RAGExecutionError):
                        raise
                    raise HTTPException(
                        status_code=503,
                        detail="数据库未就绪，暂时无法使用 RAG 检索",
                    ) from exc

                for piece in _chunk_text(result.answer):
                    yield _sse_event("chunk", {"text": piece})
                yield _sse_event(
                    "done",
                    {
                        "sessionId": result.session_id,
                        "references": references,
                        "mode": "rag",
                        "toolRuns": [_tool_run_to_dict(item) for item in tool_runs],
                        "deepThinkSummary": deep_think_summary,
                        "deepThinkRuns": [_deep_think_run_to_dict(item) for item in deep_think_runs],
                    },
                )
            else:
                if settings.mcp_enabled and (enable_tools or enable_deep_think):
                    try:
                        async with db_conn_context() as tool_conn:
                            orchestrator = get_tool_orchestrator()
                            orchestration = await orchestrator.orchestrate(
                                tool_conn,
                                question=payload.question,
                                trace_id=trace_id,
                                enable_tools=enable_tools,
                                enable_deep_think=enable_deep_think,
                                max_tool_steps=max_tool_steps,
                            )
                            rewritten_question = orchestration.rewritten_question
                            tool_runs = orchestration.tool_runs
                            deep_think_runs = orchestration.deep_think_runs
                            deep_think_summary = orchestration.deep_think_summary
                            orchestration_skill_calls = [
                                SkillCallLog(
                                    skill_name=item.skill_name,
                                    status=item.status,
                                    latency_ms=item.latency_ms,
                                    prompt_tokens=item.prompt_tokens,
                                    completion_tokens=item.completion_tokens,
                                    total_tokens=item.total_tokens,
                                    input_summary=item.input_summary,
                                    output_summary=item.output_summary,
                                    error_message=item.error_message,
                                )
                                for item in orchestration.skill_calls
                            ]
                            orchestration_skill_calls.extend(
                                [_to_skill_call_from_deep_think(item) for item in deep_think_runs]
                            )
                    except Exception as exc:
                        logger.warning(
                            "[%s] Tool orchestration skipped in stream chat-only: %s",
                            trace_id,
                            exc,
                        )
                llm_start_time = time.monotonic()
                async for piece in rag_service.chat_only_stream(
                    question=rewritten_question,
                    model_id=payload.modelId,
                    registry=_registry,
                    usage_sink=usage_stats,
                ):
                    full_answer += piece
                    yield _sse_event("chunk", {"text": piece})

                llm_latency_ms = int((time.monotonic() - llm_start_time) * 1000)
                skill_calls = [
                    *orchestration_skill_calls,
                    SkillCallLog(
                        skill_name="mcp.llm.generate",
                        status="success",
                        latency_ms=llm_latency_ms,
                        prompt_tokens=usage_stats["prompt_tokens"],
                        completion_tokens=usage_stats["completion_tokens"],
                        total_tokens=usage_stats["total_tokens"],
                        input_summary=f"model={payload.modelId},mode=chat-only",
                        output_summary=f"answer_chars={len(full_answer)}",
                    )
                ]
                yield _sse_event(
                    "done",
                    {
                        "sessionId": session_id,
                        "references": [],
                        "mode": "chat-only",
                        "toolRuns": [_tool_run_to_dict(item) for item in tool_runs],
                        "deepThinkSummary": deep_think_summary,
                        "deepThinkRuns": [_deep_think_run_to_dict(item) for item in deep_think_runs],
                    },
                )

                try:
                    async with db_conn_context() as history_conn:
                        await _ensure_session(history_conn, session_id, payload.modelId, use_rag, title)
                        await _save_chat_message(history_conn, session_id, "user", payload.question)
                        await _save_chat_message(
                            history_conn,
                            session_id,
                            "assistant",
                            full_answer,
                            references,
                        )
                        await persist_observability_logs(
                            history_conn,
                            session_id_for_log=session_id,
                            model_id_for_log=payload.modelId,
                            prompt_tokens=usage_stats["prompt_tokens"],
                            completion_tokens=usage_stats["completion_tokens"],
                            total_tokens=usage_stats["total_tokens"],
                            status="success",
                            error_message=None,
                            references_for_log=[],
                            skill_calls_for_log=skill_calls,
                            tool_runs_for_log=tool_runs,
                            deep_think_runs_for_log=deep_think_runs,
                        )
                except Exception:
                    logger.warning(
                        "[%s] Chat stream history/observability skipped: db unavailable, session=%s",
                        trace_id,
                        session_id,
                    )

        except RAGExecutionError as exc:
            logger.exception("[%s] Chat stream execution failed: %s", trace_id, exc)
            if not log_written:
                try:
                    async with db_conn_context() as log_conn:
                        merged_error_calls = [*orchestration_skill_calls, *exc.skill_calls]
                        await persist_observability_logs(
                            log_conn,
                            session_id_for_log=exc.session_id,
                            model_id_for_log=exc.model_id,
                            prompt_tokens=exc.prompt_tokens,
                            completion_tokens=exc.completion_tokens,
                            total_tokens=exc.total_tokens,
                            status="failed",
                            error_message=str(exc),
                            references_for_log=[],
                            skill_calls_for_log=merged_error_calls,
                            tool_runs_for_log=tool_runs,
                            deep_think_runs_for_log=deep_think_runs,
                        )
                except Exception:
                    logger.warning(
                        "[%s] Chat stream failed log skipped: db unavailable, session=%s",
                        trace_id,
                        exc.session_id,
                    )
            yield _sse_event(
                "error",
                {
                    "message": "问答服务暂时不可用，请稍后重试",
                    "traceId": trace_id,
                    "code": 500,
                },
            )
        except HTTPException as exc:
            if not log_written:
                error_skill_calls = skill_calls if skill_calls else orchestration_skill_calls
                if not use_rag:
                    has_llm_skill = any(item.skill_name == "mcp.llm.generate" for item in error_skill_calls)
                    if not has_llm_skill:
                        error_latency_ms = int(
                            (time.monotonic() - (llm_start_time or start_time)) * 1000
                        )
                        error_skill_calls = [
                            *error_skill_calls,
                            SkillCallLog(
                                skill_name="mcp.llm.generate",
                                status="failed",
                                latency_ms=error_latency_ms,
                                prompt_tokens=usage_stats["prompt_tokens"],
                                completion_tokens=usage_stats["completion_tokens"],
                                total_tokens=usage_stats["total_tokens"],
                                input_summary=f"model={payload.modelId},mode=chat-only",
                                output_summary="",
                                error_message=str(exc.detail),
                            )
                        ]
                try:
                    async with db_conn_context() as log_conn:
                        await persist_observability_logs(
                            log_conn,
                            session_id_for_log=session_id,
                            model_id_for_log=model_id,
                            prompt_tokens=usage_stats["prompt_tokens"],
                            completion_tokens=usage_stats["completion_tokens"],
                            total_tokens=usage_stats["total_tokens"],
                            status="failed",
                            error_message=str(exc.detail),
                            references_for_log=[],
                            skill_calls_for_log=error_skill_calls,
                            tool_runs_for_log=tool_runs,
                            deep_think_runs_for_log=deep_think_runs,
                        )
                except Exception:
                    logger.warning(
                        "[%s] Chat stream failed log skipped: db unavailable, session=%s",
                        trace_id,
                        session_id,
                    )
            yield _sse_event(
                "error",
                {
                    "message": str(exc.detail),
                    "traceId": trace_id,
                    "code": exc.status_code,
                },
            )
        except Exception as exc:
            logger.exception("[%s] Chat stream failed: %s", trace_id, exc)
            if not log_written:
                error_skill_calls = skill_calls if skill_calls else orchestration_skill_calls
                if not use_rag:
                    has_llm_skill = any(item.skill_name == "mcp.llm.generate" for item in error_skill_calls)
                    if not has_llm_skill:
                        error_latency_ms = int(
                            (time.monotonic() - (llm_start_time or start_time)) * 1000
                        )
                        error_skill_calls = [
                            *error_skill_calls,
                            SkillCallLog(
                                skill_name="mcp.llm.generate",
                                status="failed",
                                latency_ms=error_latency_ms,
                                prompt_tokens=usage_stats["prompt_tokens"],
                                completion_tokens=usage_stats["completion_tokens"],
                                total_tokens=usage_stats["total_tokens"],
                                input_summary=f"model={payload.modelId},mode=chat-only",
                                output_summary="",
                                error_message=str(exc),
                            )
                        ]
                try:
                    async with db_conn_context() as log_conn:
                        await persist_observability_logs(
                            log_conn,
                            session_id_for_log=session_id,
                            model_id_for_log=model_id,
                            prompt_tokens=usage_stats["prompt_tokens"],
                            completion_tokens=usage_stats["completion_tokens"],
                            total_tokens=usage_stats["total_tokens"],
                            status="failed",
                            error_message=str(exc),
                            references_for_log=[],
                            skill_calls_for_log=error_skill_calls,
                            tool_runs_for_log=tool_runs,
                            deep_think_runs_for_log=deep_think_runs,
                        )
                except Exception:
                    logger.warning(
                        "[%s] Chat stream failed log skipped: db unavailable, session=%s",
                        trace_id,
                        session_id,
                    )
            yield _sse_event(
                "error",
                {
                    "message": "问答服务暂时不可用，请稍后重试",
                    "traceId": trace_id,
                    "code": 500,
                },
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============== 聊天历史接口 ==============

@router.get("/sessions")
async def list_sessions(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    """获取聊天会话列表"""
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    rows = await conn.fetch(
        """
        SELECT
            session_id,
            model_id,
            title,
            use_rag,
            created_at,
            updated_at
        FROM chat_sessions
        ORDER BY updated_at DESC
        LIMIT $1 OFFSET $2
        """,
        limit,
        offset,
    )

    count_row = await conn.fetchrow("SELECT COUNT(*) AS total FROM chat_sessions")
    total = count_row["total"] if count_row else 0

    items = [
        {
            "sessionId": row["session_id"],
            "modelId": row["model_id"],
            "title": row["title"],
            "useRag": row["use_rag"],
            "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
            "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]

    return success({"items": items, "total": total}, trace_id)


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    request: Request,
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    """获取会话的消息历史"""
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    # 检查会话是否存在
    session_row = await conn.fetchrow(
        "SELECT session_id FROM chat_sessions WHERE session_id = $1",
        session_id,
    )
    if not session_row:
        raise HTTPException(status_code=404, detail="会话不存在")

    rows = await conn.fetch(
        """
        SELECT
            id,
            role,
            content,
            "references",
            created_at
        FROM chat_messages
        WHERE session_id = $1
        ORDER BY created_at ASC
        """,
        session_id,
    )

    messages = [
        {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "references": _parse_references(row["references"]),
            "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]

    return success({"sessionId": session_id, "messages": messages}, trace_id)


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
    conn=Depends(get_db_conn),
) -> dict[str, object]:
    """删除会话及其消息"""
    trace_id = request.headers.get("x-trace-id") or str(uuid4())

    result = await conn.execute(
        "DELETE FROM chat_sessions WHERE session_id = $1",
        session_id,
    )
    deleted = int(result.split()[-1]) if result else 0
    if deleted == 0:
        raise HTTPException(status_code=404, detail="会话不存在")

    return success({"deleted": True, "sessionId": session_id}, trace_id)

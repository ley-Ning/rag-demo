import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aio_pika
import asyncpg
from aio_pika.abc import AbstractIncomingMessage
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    OpenAIError,
    RateLimitError,
)

from app.api.v1.endpoints.documents import _normalize_strategy, _split_text
from app.core.config import get_settings
from app.core.database import db_conn_context
from app.core.rabbitmq import declare_documents_topology
from app.core.redis_client import get_redis_client
from app.domain.document_extract import extract_text
from app.domain.embedding import get_embedding_service
from app.domain.models_registry import _registry
from app.domain.vector_store import get_vector_store

logger = logging.getLogger(__name__)

DLQ_REASON_NON_RETRYABLE = "non-retryable"
DLQ_REASON_RETRY_EXHAUSTED = "retry-exhausted"
DLQ_REASON_INVALID_PAYLOAD = "invalid-payload"


class NonRetryableTaskError(Exception):
    """业务性失败：重试也不会成功（文件缺失/格式不支持/配置缺失等），应直接进死信队列"""


def _is_retryable_openai_error(exc: OpenAIError) -> bool:
    """网络/限流/服务端错误可重试；认证、配置、参数类错误重试无意义"""
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code >= 500


class DocumentWorker:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._connection: aio_pika.RobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._queue: aio_pika.abc.AbstractQueue | None = None
        self._consumer_tag: str | None = None

    async def start(self) -> None:
        if not self._settings.document_worker_enabled:
            logger.info("Document worker disabled by config")
            return
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="document-worker")
        logger.info("Document worker task started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._queue is not None and self._consumer_tag:
            try:
                await self._queue.cancel(self._consumer_tag)
            except Exception:
                logger.exception("Failed to cancel document worker consumer")

        if self._task is not None:
            try:
                await self._task
            except Exception:
                logger.exception("Document worker task exited with error")
            self._task = None

        await self._close_consumer()
        logger.info("Document worker stopped")

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._connect_and_consume()
                await self._stop_event.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Document worker loop failed, retrying in 3s")
                await asyncio.sleep(3)
            finally:
                await self._close_consumer()

    async def _connect_and_consume(self) -> None:
        self._connection = await aio_pika.connect_robust(self._settings.rabbitmq_url)
        # publisher_confirms=True：重试/死信发布得到 broker 确认后才 ack 原消息
        self._channel = await self._connection.channel(publisher_confirms=True)
        await self._channel.set_qos(prefetch_count=max(self._settings.document_worker_prefetch, 1))
        topology = await declare_documents_topology(self._channel)
        self._queue = await self._channel.get_queue(self._settings.rabbitmq_documents_queue)
        self._consumer_tag = await self._queue.consume(self._on_message, no_ack=False)
        logger.info(
            "Document worker consuming queue=%s prefetch=%s retryTiers=%s dlq=%s",
            self._settings.rabbitmq_documents_queue,
            self._settings.document_worker_prefetch,
            [item["delaySeconds"] for item in topology["retry"]],
            self._settings.documents_dlq_queue,
        )

    async def _close_consumer(self) -> None:
        if self._channel is not None and not self._channel.is_closed:
            await self._channel.close()
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
        self._channel = None
        self._connection = None
        self._queue = None
        self._consumer_tag = None

    @staticmethod
    def _read_retry_count(headers: dict[str, Any] | None) -> int:
        if not headers:
            return 0
        value = headers.get("x-retry-count", 0)
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return 0

    async def _publish_disposition(
        self,
        *,
        routing_key: str,
        body: bytes,
        headers: dict[str, Any],
        content_type: str = "application/json",
    ) -> None:
        """发布重试/死信消息。发布失败时抛出，由调用方决定不 ack 原消息。"""
        if self._channel is None or self._channel.is_closed:
            raise RuntimeError("Document worker channel not available")
        message = aio_pika.Message(
            body=body,
            content_type=content_type,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            timestamp=datetime.now(UTC),
            headers=headers,
        )
        await self._channel.default_exchange.publish(message, routing_key=routing_key)

    @staticmethod
    def _build_failure_headers(
        *,
        original_headers: dict[str, Any] | None,
        error: BaseException | None,
        retry_count: int,
        now_iso: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, Any] = {"x-retry-count": retry_count}
        first_failed = (original_headers or {}).get("x-first-failed-at")
        headers["x-first-failed-at"] = (
            first_failed if isinstance(first_failed, str) and first_failed else now_iso
        )
        if error is not None:
            headers["x-error-class"] = type(error).__name__
            headers["x-error-message"] = str(error)[:500]
        if extra:
            headers.update(extra)
        return headers

    async def _publish_retry(
        self,
        payload: dict[str, Any],
        raw_body: bytes,
        original_headers: dict[str, Any] | None,
        next_retry_count: int,
        delay_seconds: int,
        error: Exception,
    ) -> None:
        now_iso = datetime.now(UTC).isoformat()
        headers = self._build_failure_headers(
            original_headers=original_headers,
            error=error,
            retry_count=next_retry_count,
            now_iso=now_iso,
            extra={"x-next-retry-delay-sec": delay_seconds},
        )
        await self._publish_disposition(
            routing_key=self._settings.documents_retry_queue(delay_seconds),
            body=raw_body,
            headers=headers,
        )
        logger.warning(
            "Document task scheduled for retry %s/%s in %ss: documentId=%s error=%s",
            next_retry_count,
            len(self._settings.document_worker_retry_delays),
            delay_seconds,
            payload.get("documentId"),
            str(error)[:200],
        )

    async def _publish_dlq(
        self,
        payload: dict[str, Any] | None,
        raw_body: bytes,
        original_headers: dict[str, Any] | None,
        retry_count: int,
        reason: str,
        error: BaseException | None,
    ) -> None:
        now_iso = datetime.now(UTC).isoformat()
        headers = self._build_failure_headers(
            original_headers=original_headers,
            error=error,
            retry_count=retry_count,
            now_iso=now_iso,
            extra={"x-dlq-reason": reason, "x-dlq-entered-at": now_iso},
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else raw_body
        await self._publish_disposition(
            routing_key=self._settings.documents_dlq_queue,
            body=body,
            headers=headers,
            content_type="application/json" if payload is not None else "application/octet-stream",
        )
        logger.error(
            "Document task moved to DLQ: reason=%s retryCount=%s documentId=%s error=%s",
            reason,
            retry_count,
            payload.get("documentId") if payload else "unknown",
            str(error)[:200] if error else "n/a",
        )

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        try:
            payload = json.loads(message.body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("queue payload 必须是 JSON 对象")
        except Exception as exc:
            logger.error("Invalid queue message, moving to DLQ: %s", exc)
            await self._publish_dlq(
                payload=None,
                raw_body=message.body,
                original_headers=message.headers,
                retry_count=self._read_retry_count(message.headers),
                reason=DLQ_REASON_INVALID_PAYLOAD,
                error=exc,
            )
            await message.ack()
            return

        retry_count = self._read_retry_count(message.headers)
        try:
            await self._process_task(payload)
            await message.ack()
            return
        except NonRetryableTaskError as exc:
            disposition = ("dlq", DLQ_REASON_NON_RETRYABLE, exc)
        except Exception as exc:
            delays = self._settings.document_worker_retry_delays
            if retry_count < len(delays):
                disposition = ("retry", delays[retry_count], exc)
            else:
                disposition = ("dlq", DLQ_REASON_RETRY_EXHAUSTED, exc)

        # 处置消息：发布成功才 ack 原消息；发布失败则断开连接让消息重投，绝不丢
        try:
            if disposition[0] == "retry":
                await self._publish_retry(
                    payload,
                    message.body,
                    message.headers,
                    retry_count + 1,
                    disposition[1],
                    disposition[2],
                )
                await self._mark_document_retrying(payload, retry_count + 1, disposition[1], disposition[2])
            else:
                await self._publish_dlq(
                    payload,
                    message.body,
                    message.headers,
                    retry_count,
                    disposition[1],
                    disposition[2],
                )
                await self._mark_document_failed(payload, retry_count, disposition[1], disposition[2])
        except Exception:
            logger.exception(
                "Failed to dispose failed message, closing connection for redelivery: documentId=%s",
                payload.get("documentId"),
            )
            if self._connection is not None and not self._connection.is_closed:
                try:
                    await self._connection.close()
                except Exception:
                    logger.exception("Failed to close worker connection after disposition error")
            return

        await message.ack()

    def _resolve_embedding_model_id(self) -> str:
        preferred = self._settings.document_worker_embedding_model_id.strip()
        if preferred and _registry.model_supports(preferred, "embedding"):
            return preferred

        for item in _registry.list_models():
            model_id = str(item.get("id", "")).strip()
            status = str(item.get("status", "")).strip().lower()
            caps = item.get("capabilities", [])
            if status == "online" and isinstance(caps, list) and "embedding" in caps:
                return model_id

        raise RuntimeError("没有可用的 embedding 模型（需在线且包含 embedding 能力）")

    @staticmethod
    def _read_text_file(path: str, file_name: str) -> str:
        if not path:
            raise RuntimeError("任务缺少 storagePath，无法读取上传文件")

        file_path = Path(path)
        if not file_path.exists():
            raise RuntimeError(f"上传文件不存在: {file_path}")

        ext = file_path.suffix.lower() or Path(file_name).suffix.lower()
        return extract_text(file_path, ext)

    async def _set_task_cache(
        self,
        task_id: str,
        *,
        document_id: str,
        trace_id: str,
        status: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not task_id:
            return
        payload: dict[str, Any] = {
            "taskId": task_id,
            "documentId": document_id,
            "status": status,
            "traceId": trace_id,
        }
        if extra:
            payload.update(extra)
        try:
            await get_redis_client().set_json(
                f"{self._settings.redis_key_prefix}:task:{task_id}",
                payload,
                ttl_seconds=3600,
            )
        except Exception:
            # Redis 只是状态缓存，DB 才是事实来源；缓存失败不阻断任务
            logger.warning("Failed to refresh task cache in Redis: taskId=%s status=%s", task_id, status)

    async def _update_document_status(
        self,
        conn: asyncpg.Connection,
        document_id: str,
        *,
        status: str,
        metadata_patch: dict[str, Any],
    ) -> None:
        await conn.execute(
            """
            UPDATE documents
            SET status = $2,
                metadata = COALESCE(metadata, '{}'::jsonb) || $3::jsonb,
                updated_at = NOW()
            WHERE id::text = $1
              AND deleted_at IS NULL
            """,
            document_id,
            status,
            json.dumps(metadata_patch, ensure_ascii=False),
        )

    async def _cleanup_document_chunks(self, document_id: str) -> None:
        try:
            async with db_conn_context() as conn:
                await get_vector_store().delete_document_chunks(conn, document_id)
        except Exception:
            logger.warning(
                "Failed to cleanup partial chunks (best-effort): documentId=%s", document_id
            )

    async def _mark_document_retrying(
        self,
        payload: dict[str, Any],
        next_retry_count: int,
        delay_seconds: int,
        error: Exception,
    ) -> None:
        document_id = str(payload.get("documentId", "")).strip()
        if not document_id:
            return
        await self._cleanup_document_chunks(document_id)
        try:
            async with db_conn_context() as conn:
                await self._update_document_status(
                    conn,
                    document_id,
                    status="retrying",
                    metadata_patch={
                        "retryCount": next_retry_count,
                        "nextRetryDelaySec": delay_seconds,
                        "workerError": str(error)[:500],
                        "lastFailedAt": datetime.now(UTC).isoformat(),
                    },
                )
        except Exception:
            logger.warning(
                "Failed to mark document as retrying (will recover on next attempt): documentId=%s",
                document_id,
            )

        await self._set_task_cache(
            str(payload.get("taskId", "")).strip(),
            document_id=document_id,
            trace_id=str(payload.get("traceId", "")).strip() or "worker-trace",
            status="retrying",
            extra={"retryCount": next_retry_count, "nextRetryDelaySec": delay_seconds},
        )

    async def _mark_document_failed(
        self,
        payload: dict[str, Any],
        retry_count: int,
        dlq_reason: str,
        error: BaseException,
    ) -> None:
        document_id = str(payload.get("documentId", "")).strip()
        if not document_id:
            return
        try:
            async with db_conn_context() as conn:
                await self._update_document_status(
                    conn,
                    document_id,
                    status="failed",
                    metadata_patch={
                        "retryCount": retry_count,
                        "dlqReason": dlq_reason,
                        "finalError": str(error)[:500],
                        "lastFailedAt": datetime.now(UTC).isoformat(),
                    },
                )
        except Exception:
            logger.warning("Failed to persist document failed status: documentId=%s", document_id)

        await self._set_task_cache(
            str(payload.get("taskId", "")).strip(),
            document_id=document_id,
            trace_id=str(payload.get("traceId", "")).strip() or "worker-trace",
            status="failed",
            extra={"error": str(error)[:500], "dlqReason": dlq_reason},
        )

    @staticmethod
    def _build_chunk_metadata(
        *,
        base: dict[str, Any],
        chunk: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = {
            **base,
            "chunkId": chunk.get("chunkId"),
            "start": chunk.get("start"),
            "end": chunk.get("end"),
            "length": chunk.get("length"),
        }
        if chunk.get("parentChunkId"):
            metadata["parentChunkId"] = chunk.get("parentChunkId")
            metadata["parentStart"] = chunk.get("parentStart")
            metadata["parentEnd"] = chunk.get("parentEnd")
            metadata["parentLength"] = chunk.get("parentLength")

        for key in (
            "nodeId",
            "nodePath",
            "level",
            "pageStart",
            "pageEnd",
            "charStart",
            "charEnd",
            "sectionTitle",
        ):
            value = chunk.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            metadata[key] = value

        return metadata

    async def _process_task(self, payload: dict[str, Any]) -> None:
        task_id = str(payload.get("taskId", "")).strip()
        document_id = str(payload.get("documentId", "")).strip()
        file_name = str(payload.get("fileName", "")).strip() or "unnamed"
        trace_id = str(payload.get("traceId", "")).strip() or "worker-trace"
        storage_path = str(payload.get("storagePath", "")).strip()

        if not document_id:
            raise NonRetryableTaskError("消息缺少 documentId，无法关联文档记录")

        strategy_raw = str(payload.get("strategy", "fixed"))
        try:
            strategy = _normalize_strategy(strategy_raw)
        except Exception:
            strategy = "fixed"

        await self._set_task_cache(
            task_id,
            document_id=document_id,
            trace_id=trace_id,
            status="processing",
            extra={"fileName": file_name, "strategy": strategy},
        )

        async with db_conn_context() as conn:
            await self._update_document_status(
                conn,
                document_id,
                status="processing",
                metadata_patch={
                    "workerStartedAt": datetime.now(UTC).isoformat(),
                    "strategy": strategy,
                    "storagePath": storage_path,
                },
            )

        # 业务性失败（文件/格式/模型配置问题）：重试无意义，直接进死信
        try:
            text = self._read_text_file(storage_path, file_name)
        except Exception as exc:
            raise NonRetryableTaskError(str(exc)) from exc

        chunks = _split_text(
            text,
            chunk_size=max(self._settings.document_worker_chunk_size, 100),
            overlap=max(self._settings.document_worker_overlap, 0),
            strategy=strategy,
        )
        if not chunks:
            raise NonRetryableTaskError("文档切分后无有效分块")

        try:
            embedding_model_id = self._resolve_embedding_model_id()
        except Exception as exc:
            raise NonRetryableTaskError(str(exc)) from exc

        embedding_service = get_embedding_service()
        vector_store = get_vector_store()

        total_prompt_tokens = 0
        total_embedding_tokens = 0
        inserted_chunks = 0

        base_chunk_meta = {
            "file_name": file_name,
            "strategy": strategy,
            "taskId": task_id,
            "traceId": trace_id,
            "storagePath": storage_path,
        }

        # embedding/DB 等基础设施工况导致的异常原样抛出，由 _on_message 分类为可重试；
        # 但 openai 的认证/配置/参数类错误属于部署问题，重试无意义，直接判不可重试
        async with db_conn_context() as conn:
            await vector_store.delete_document_chunks(conn, document_id)
            for chunk_index, chunk in enumerate(chunks, start=1):
                chunk_content = str(chunk.get("content", "")).strip()
                if not chunk_content:
                    continue

                try:
                    embedding, usage = await embedding_service.embed_single_with_usage(
                        chunk_content,
                        embedding_model_id,
                        _registry,
                    )
                except OpenAIError as exc:
                    if not _is_retryable_openai_error(exc):
                        raise NonRetryableTaskError(
                            f"embedding 调用失败（配置/鉴权类错误）: {exc}"
                        ) from exc
                    raise
                total_prompt_tokens += usage.prompt_tokens
                total_embedding_tokens += usage.total_tokens

                chunk_metadata = self._build_chunk_metadata(base=base_chunk_meta, chunk=chunk)
                await vector_store.insert_chunk(
                    conn,
                    document_id=document_id,
                    chunk_index=chunk_index,
                    content=chunk_content,
                    embedding=embedding,
                    metadata=chunk_metadata,
                    embedding_model=embedding_model_id,
                )
                inserted_chunks += 1
            await self._update_document_status(
                conn,
                document_id,
                status="completed",
                metadata_patch={
                    "embeddingModelId": embedding_model_id,
                    "chunkCount": inserted_chunks,
                    "promptTokens": total_prompt_tokens,
                    "embeddingTokens": total_embedding_tokens,
                },
            )

        await self._set_task_cache(
            task_id,
            document_id=document_id,
            trace_id=trace_id,
            status="completed",
            extra={"chunkCount": inserted_chunks, "embeddingModelId": embedding_model_id},
        )
        logger.info(
            "[%s] Document worker completed: document_id=%s chunks=%s strategy=%s",
            trace_id,
            document_id,
            inserted_chunks,
            strategy,
        )


_document_worker = DocumentWorker()


async def start_document_worker() -> None:
    await _document_worker.start()


async def stop_document_worker() -> None:
    await _document_worker.stop()

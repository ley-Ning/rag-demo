import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aio_pika
import asyncpg
from aio_pika.abc import AbstractIncomingMessage

from app.api.v1.endpoints.documents import _normalize_strategy, _split_text
from app.core.config import get_settings
from app.core.database import db_conn_context
from app.core.redis_client import get_redis_client
from app.domain.embedding import get_embedding_service
from app.domain.models_registry import _registry
from app.domain.vector_store import get_vector_store

logger = logging.getLogger(__name__)


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
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=max(self._settings.document_worker_prefetch, 1))
        self._queue = await self._channel.declare_queue(
            self._settings.rabbitmq_documents_queue,
            durable=True,
        )
        self._consumer_tag = await self._queue.consume(self._on_message, no_ack=False)
        logger.info(
            "Document worker consuming queue=%s prefetch=%s",
            self._settings.rabbitmq_documents_queue,
            self._settings.document_worker_prefetch,
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

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            try:
                payload = json.loads(message.body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("queue payload 必须是 JSON 对象")
            except Exception as exc:
                logger.error("Invalid queue message, dropped: %s", exc)
                return

            await self._process_task(payload)

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
        if ext not in {".txt", ".md", ".markdown", ".text", ".log", ".csv", ".json"}:
            raise RuntimeError(f"暂不支持的文件类型: {ext or 'unknown'}，当前仅支持 txt/md/csv/json")

        raw = file_path.read_bytes()
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return raw.decode(encoding)
            except Exception:
                continue
        raise RuntimeError("文件解码失败，请确保是 UTF-8 或 GB18030 文本文件")

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
        await get_redis_client().set_json(
            f"{self._settings.redis_key_prefix}:task:{task_id}",
            payload,
            ttl_seconds=3600,
        )

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
            logger.error("Document worker dropped message: documentId missing")
            return

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

        try:
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

            text = self._read_text_file(storage_path, file_name)
            chunks = _split_text(
                text,
                chunk_size=max(self._settings.document_worker_chunk_size, 100),
                overlap=max(self._settings.document_worker_overlap, 0),
                strategy=strategy,
            )
            if not chunks:
                raise RuntimeError("文档切分后无有效分块")

            embedding_model_id = self._resolve_embedding_model_id()
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

            async with db_conn_context() as conn:
                await vector_store.delete_document_chunks(conn, document_id)
                for chunk_index, chunk in enumerate(chunks, start=1):
                    chunk_content = str(chunk.get("content", "")).strip()
                    if not chunk_content:
                        continue

                    embedding, usage = await embedding_service.embed_single_with_usage(
                        chunk_content,
                        embedding_model_id,
                        _registry,
                    )
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
        except Exception as exc:
            logger.exception(
                "[%s] Document worker failed: document_id=%s error=%s",
                trace_id,
                document_id,
                exc,
            )
            try:
                async with db_conn_context() as conn:
                    await get_vector_store().delete_document_chunks(conn, document_id)
                    await self._update_document_status(
                        conn,
                        document_id,
                        status="failed",
                        metadata_patch={"workerError": str(exc)[:500]},
                    )
            except Exception:
                logger.exception("[%s] Failed to persist worker error status", trace_id)

            await self._set_task_cache(
                task_id,
                document_id=document_id,
                trace_id=trace_id,
                status="failed",
                extra={"error": str(exc)[:500]},
            )


_document_worker = DocumentWorker()


async def start_document_worker() -> None:
    await _document_worker.start()


async def stop_document_worker() -> None:
    await _document_worker.stop()

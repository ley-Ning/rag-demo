import logging
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg
import json

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """向量检索结果"""

    chunk_id: str
    document_id: str
    chunk_index: int
    content: str
    score: float
    metadata: dict[str, Any]
    parent_chunk_id: str | None = None
    is_expanded: bool = False


class VectorStore:
    """向量存储服务"""

    def __init__(self) -> None:
        settings = get_settings()
        self._vector_dimension = max(int(settings.vector_dimension), 1)

    @staticmethod
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

    @staticmethod
    def _extract_parent_chunk_id(metadata: dict[str, Any]) -> str | None:
        """从 metadata 提取父块 ID，兼容多种命名"""
        if not metadata:
            return None

        for key in ("parentChunkId", "parent_chunk_id", "parentId", "parent_id"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        parent_start = metadata.get("parentStart", metadata.get("parent_start"))
        parent_end = metadata.get("parentEnd", metadata.get("parent_end"))
        if parent_start is not None and parent_end is not None:
            return f"range:{parent_start}:{parent_end}"

        return None

    @staticmethod
    def _to_search_result(row: asyncpg.Record) -> SearchResult:
        metadata = VectorStore._parse_metadata(row["metadata"])
        return SearchResult(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            chunk_index=int(row["chunk_index"]),
            content=row["content"],
            score=float(row["score"]),
            metadata=metadata,
            parent_chunk_id=VectorStore._extract_parent_chunk_id(metadata),
            is_expanded=False,
        )

    async def _expand_with_neighbor_children(
        self,
        conn: asyncpg.Connection,
        *,
        base_results: list[SearchResult],
        top_k: int,
        child_expand_window: int = 1,
    ) -> list[SearchResult]:
        """
        父召回子精排：
        1) 先按 parent_chunk_id 聚合父块分
        2) 再把父块下相邻子块补回来，构造更完整上下文
        """
        grouped_by_parent: dict[str, list[SearchResult]] = {}
        for result in base_results:
            if not result.parent_chunk_id:
                continue
            grouped_by_parent.setdefault(result.parent_chunk_id, []).append(result)

        if not grouped_by_parent:
            return []

        parent_rank = sorted(
            grouped_by_parent.items(),
            key=lambda item: max(chunk.score for chunk in item[1]),
            reverse=True,
        )

        selected_parent_items = parent_rank[:top_k]
        selected_keys = {key for key, _ in selected_parent_items}
        selected_hits = [
            hit for hit in base_results if hit.parent_chunk_id and hit.parent_chunk_id in selected_keys
        ]

        # 若父块命中太少，直接退回原始结果
        if len(selected_hits) < 2:
            return []

        merged: dict[str, SearchResult] = {hit.chunk_id: hit for hit in selected_hits}
        child_expand_window = max(0, min(child_expand_window, 3))

        for hit in selected_hits:
            if child_expand_window <= 0:
                continue

            start_idx = max(hit.chunk_index - child_expand_window, 1)
            end_idx = hit.chunk_index + child_expand_window

            rows = await conn.fetch(
                """
                SELECT
                    dc.id::text AS chunk_id,
                    dc.document_id::text AS document_id,
                    dc.chunk_index,
                    dc.content,
                    dc.metadata
                FROM document_chunks dc
                JOIN documents d ON dc.document_id = d.id
                WHERE d.deleted_at IS NULL
                  AND dc.document_id = $1::uuid
                  AND dc.chunk_index BETWEEN $2 AND $3
                ORDER BY dc.chunk_index ASC
                """,
                hit.document_id,
                start_idx,
                end_idx,
            )

            for row in rows:
                chunk_id = row["chunk_id"]
                if chunk_id in merged:
                    continue

                metadata = self._parse_metadata(row["metadata"])
                parent_chunk_id = self._extract_parent_chunk_id(metadata) or hit.parent_chunk_id
                if parent_chunk_id != hit.parent_chunk_id:
                    continue

                distance = abs(int(row["chunk_index"]) - hit.chunk_index)
                expanded_score = max(hit.score - 0.03 * distance, 0.0)

                merged[chunk_id] = SearchResult(
                    chunk_id=chunk_id,
                    document_id=row["document_id"],
                    chunk_index=int(row["chunk_index"]),
                    content=row["content"],
                    score=expanded_score,
                    metadata=metadata,
                    parent_chunk_id=parent_chunk_id,
                    is_expanded=True,
                )

        parent_order = {parent_id: index for index, (parent_id, _) in enumerate(selected_parent_items)}

        sorted_results = sorted(
            merged.values(),
            key=lambda item: (
                parent_order.get(item.parent_chunk_id or "", 10_000),
                1 if item.is_expanded else 0,
                -item.score,
                item.chunk_index,
            ),
        )
        return sorted_results[:top_k]

    async def insert_chunk(
        self,
        conn: asyncpg.Connection,
        document_id: str,
        chunk_index: int,
        content: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
        embedding_model: str | None = None,
    ) -> str:
        """
        插入文档分块和向量

        Args:
            conn: 数据库连接
            document_id: 文档 ID
            chunk_index: 分块索引
            content: 分块内容
            embedding: 向量
            metadata: 元数据
            embedding_model: embedding 模型名称

        Returns:
            chunk_id
        """
        chunk_id = uuid.uuid4()
        document_uuid = uuid.UUID(document_id)
        metadata_payload = json.dumps(metadata or {}, ensure_ascii=False)
        normalized_embedding = self._normalize_embedding(embedding)

        async with conn.transaction():
            # 确保文档存在
            await conn.execute(
                """
                INSERT INTO documents (id, file_name, source, status, metadata)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT (id) DO NOTHING
                """,
                document_uuid,
                f"auto-{document_id}",
                "generated",
                "processing",
                "{}",
            )

            # 插入分块
            await conn.execute(
                """
                INSERT INTO document_chunks (id, document_id, chunk_index, content, metadata)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                chunk_id,
                document_uuid,
                chunk_index,
                content,
                metadata_payload,
            )

            # 插入向量
            await conn.execute(
                """
                INSERT INTO chunk_embeddings (chunk_id, embedding, model_id)
                VALUES ($1, $2, $3)
                """,
                chunk_id,
                normalized_embedding,
                embedding_model or "unknown",
            )

        chunk_id_str = str(chunk_id)
        logger.debug("Inserted chunk %s for document %s", chunk_id_str, document_id)
        return chunk_id_str

    async def similarity_search(
        self,
        conn: asyncpg.Connection,
        query_embedding: list[float],
        top_k: int = 5,
        min_score: float = 0.5,
        document_ids: list[str] | None = None,
        use_parent_child_rerank: bool = True,
        candidate_multiplier: int = 6,
        child_expand_window: int = 1,
    ) -> list[SearchResult]:
        """
        向量相似度检索

        Args:
            conn: 数据库连接
            query_embedding: 查询向量
            top_k: 返回结果数量
            min_score: 最小相似度阈值 (cosine similarity)
            document_ids: 可选，限定检索的文档 ID 列表
            use_parent_child_rerank: 是否启用父召回子精排
            candidate_multiplier: 候选召回倍率（用于重排）
            child_expand_window: 子块扩展窗口（左右各扩几个）

        Returns:
            检索结果列表
        """
        safe_top_k = max(1, min(top_k, 50))
        safe_multiplier = max(1, min(candidate_multiplier, 20))
        candidate_k = min(max(safe_top_k * safe_multiplier, safe_top_k), 200)
        normalized_query_embedding = self._normalize_embedding(query_embedding)

        # 构建动态 SQL
        if document_ids:
            # 将 document_ids 转为 UUID 列表
            doc_uuids = [uuid.UUID(doc_id) for doc_id in document_ids]
            rows = await conn.fetch(
                """
                SELECT
                    dc.id::text AS chunk_id,
                    dc.document_id::text AS document_id,
                    dc.chunk_index,
                    dc.content,
                    dc.metadata,
                    1 - (ce.embedding <=> $1::vector) as score
                FROM chunk_embeddings ce
                JOIN document_chunks dc ON ce.chunk_id = dc.id
                JOIN documents d ON dc.document_id = d.id
                WHERE 1 - (ce.embedding <=> $1::vector) >= $2
                    AND d.deleted_at IS NULL
                    AND dc.document_id = ANY($4::uuid[])
                ORDER BY ce.embedding <=> $1::vector
                LIMIT $3
                """,
                normalized_query_embedding,
                min_score,
                candidate_k,
                doc_uuids,
            )

            if not rows:
                logger.info(
                    "No hits above threshold for scoped docs, fallback to best-effort retrieval: docs=%s, min_score=%s",
                    len(doc_uuids),
                    min_score,
                )
                rows = await conn.fetch(
                    """
                    SELECT
                        dc.id::text AS chunk_id,
                        dc.document_id::text AS document_id,
                        dc.chunk_index,
                        dc.content,
                        dc.metadata,
                        1 - (ce.embedding <=> $1::vector) as score
                    FROM chunk_embeddings ce
                    JOIN document_chunks dc ON ce.chunk_id = dc.id
                    JOIN documents d ON dc.document_id = d.id
                    WHERE d.deleted_at IS NULL
                      AND dc.document_id = ANY($3::uuid[])
                    ORDER BY ce.embedding <=> $1::vector
                    LIMIT $2
                    """,
                    normalized_query_embedding,
                    candidate_k,
                    doc_uuids,
                )
        else:
            rows = await conn.fetch(
                """
                SELECT
                    dc.id::text AS chunk_id,
                    dc.document_id::text AS document_id,
                    dc.chunk_index,
                    dc.content,
                    dc.metadata,
                    1 - (ce.embedding <=> $1::vector) as score
                FROM chunk_embeddings ce
                JOIN document_chunks dc ON ce.chunk_id = dc.id
                JOIN documents d ON dc.document_id = d.id
                WHERE 1 - (ce.embedding <=> $1::vector) >= $2
                  AND d.deleted_at IS NULL
                ORDER BY ce.embedding <=> $1::vector
                LIMIT $3
                """,
                normalized_query_embedding,
                min_score,
                candidate_k,
            )

        results = [self._to_search_result(row) for row in rows]

        if not results:
            return []

        if use_parent_child_rerank:
            reranked = await self._expand_with_neighbor_children(
                conn,
                base_results=results,
                top_k=safe_top_k,
                child_expand_window=child_expand_window,
            )
            if reranked:
                logger.debug(
                    "Parent-child rerank applied: base_hits=%s, final_hits=%s",
                    len(results),
                    len(reranked),
                )
                return reranked

        plain_results = results[:safe_top_k]
        logger.debug(
            "Found %s results with score >= %s, returned=%s",
            len(results),
            min_score,
            len(plain_results),
        )
        return plain_results

    def _normalize_embedding(self, embedding: list[float]) -> list[float]:
        if len(embedding) == self._vector_dimension:
            return embedding
        if len(embedding) > self._vector_dimension:
            return embedding[: self._vector_dimension]
        return [*embedding, *([0.0] * (self._vector_dimension - len(embedding)))]

    async def delete_document_chunks(
        self,
        conn: asyncpg.Connection,
        document_id: str,
    ) -> int:
        """
        删除文档的所有分块

        Args:
            conn: 数据库连接
            document_id: 文档 ID

        Returns:
            删除的分块数量
        """
        result = await conn.execute(
            """
            DELETE FROM document_chunks
            WHERE document_id = $1::uuid
            """,
            document_id,
        )
        deleted_count = int(result.split()[-1]) if result else 0
        logger.debug("Deleted %s chunks for document %s", deleted_count, document_id)
        return deleted_count


# 全局实例
_vector_store = VectorStore()


def get_vector_store() -> VectorStore:
    """获取向量存储实例"""
    return _vector_store

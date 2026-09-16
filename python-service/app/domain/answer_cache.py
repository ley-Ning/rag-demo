"""问答答案缓存：Redis 精确命中 + pgvector 语义命中。

设计要点：
- 精确命中走 Redis（sha256(问题 + 模型 + 检索参数 + 文档过滤)），零额外计算。
- 语义命中走 answer_cache 表的问题向量近邻检索（cosine 相似度 >= 阈值）。
- 知识库版本（kb_version）变更后旧缓存自然失联：版本号参与键与查询条件。
- 所有操作 best-effort：缓存故障只降级性能，绝不允许打断问答主链路。
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg

from app.core.config import get_settings
from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

KB_VERSION_KEY = "kb:version"
KB_VERSION_DEFAULT = 1


@dataclass
class CachedAnswer:
    """缓存命中的答案"""

    answer: str
    references: list[dict[str, Any]] = field(default_factory=list)
    kind: str = "exact"  # exact | semantic
    similarity: float | None = None
    cached_at: str = ""


class AnswerCacheService:
    def __init__(self) -> None:
        self._settings = get_settings()

    # ---------- 知识库版本 ----------

    async def get_kb_version(self) -> int:
        try:
            client = get_redis_client()
            version = await client.get_int(f"{self._settings.redis_key_prefix}:{KB_VERSION_KEY}")
            return version or KB_VERSION_DEFAULT
        except Exception:
            return KB_VERSION_DEFAULT

    async def bump_kb_version(self) -> int:
        """知识库内容变更（上传/删除/导入）后调用，使旧缓存失联"""
        try:
            client = get_redis_client()
            version = await client.incr_int(f"{self._settings.redis_key_prefix}:{KB_VERSION_KEY}")
            logger.info("Answer cache kb_version bumped to %s", version)
            return version or KB_VERSION_DEFAULT
        except Exception:
            logger.warning("Failed to bump kb_version, cache keeps previous version")
            return await self.get_kb_version()

    # ---------- 缓存键 ----------

    def build_cache_key(
        self,
        *,
        question: str,
        model_id: str,
        embedding_model_id: str,
        document_ids: list[str] | None,
        kb_version: int,
    ) -> str:
        doc_scope = ",".join(sorted(document_ids)) if document_ids else "*"
        raw = "|".join(
            [
                question.strip(),
                model_id,
                embedding_model_id,
                doc_scope,
                str(self._settings.rag_top_k),
                str(self._settings.rag_min_score),
                str(kb_version),
            ]
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return f"{self._settings.redis_key_prefix}:answer:{digest}"

    # ---------- 查询 ----------

    async def lookup_exact(self, cache_key: str) -> CachedAnswer | None:
        if not self._settings.rag_answer_cache_enabled:
            return None
        try:
            payload = await get_redis_client().get_json(cache_key)
        except Exception:
            logger.warning("Answer cache exact lookup failed (redis), degrade to miss")
            return None
        if not isinstance(payload, dict) or not payload.get("answer"):
            return None
        references = payload.get("references")
        return CachedAnswer(
            answer=str(payload["answer"]),
            references=references if isinstance(references, list) else [],
            kind="exact",
            similarity=1.0,
            cached_at=str(payload.get("cachedAt", "")),
        )

    async def lookup_semantic(
        self,
        conn: asyncpg.Connection,
        *,
        question_embedding: list[float],
        model_id: str,
        embedding_model_id: str,
        document_ids: list[str] | None,
        kb_version: int,
    ) -> CachedAnswer | None:
        if not self._settings.rag_semantic_cache_enabled:
            return None
        # 文档过滤作用域不同则语义命中不可信，直接跳过
        if document_ids:
            return None
        try:
            row = await conn.fetchrow(
                """
                SELECT cache_key, question, answer, references_json,
                       1 - (question_embedding <=> $1::vector) AS similarity
                FROM answer_cache
                WHERE model_id = $2
                  AND embedding_model_id = $3
                  AND kb_version = $4
                  AND 1 - (question_embedding <=> $1::vector) >= $5
                ORDER BY question_embedding <=> $1::vector
                LIMIT 1
                """,
                question_embedding,
                model_id,
                embedding_model_id,
                kb_version,
                self._settings.rag_semantic_cache_threshold,
            )
        except Exception:
            logger.warning("Answer cache semantic lookup failed (postgres), degrade to miss")
            return None
        if row is None:
            return None

        references = row["references_json"]
        if isinstance(references, str):
            try:
                references = json.loads(references)
            except Exception:
                references = []
        try:
            await conn.execute(
                """
                UPDATE answer_cache
                SET hit_count = hit_count + 1, updated_at = NOW()
                WHERE cache_key = $1
                """,
                row["cache_key"],
            )
        except Exception:
            logger.debug("Semantic cache hit_count update skipped")

        similarity = float(row["similarity"] or 0.0)
        logger.info(
            "Answer cache semantic hit: similarity=%.4f question=%s",
            similarity,
            str(row["question"])[:50],
        )
        return CachedAnswer(
            answer=str(row["answer"]),
            references=references if isinstance(references, list) else [],
            kind="semantic",
            similarity=similarity,
            cached_at="",
        )

    # ---------- 存储 ----------

    async def store(
        self,
        conn: asyncpg.Connection,
        *,
        cache_key: str,
        question: str,
        question_embedding: list[float],
        answer: str,
        references: list[dict[str, Any]],
        model_id: str,
        embedding_model_id: str,
        document_ids: list[str] | None,
        kb_version: int,
    ) -> None:
        if not self._settings.rag_answer_cache_enabled or not answer.strip():
            return
        cached_at = datetime.now(UTC).isoformat()
        payload = {
            "answer": answer,
            "references": references,
            "modelId": model_id,
            "cachedAt": cached_at,
        }
        try:
            await get_redis_client().set_json(
                cache_key,
                payload,
                ttl_seconds=self._settings.rag_answer_cache_ttl_sec,
            )
        except Exception:
            logger.warning("Answer cache store to redis failed, skip")

        if document_ids:
            # 文档过滤作用域只做精确命中，不进语义索引
            return
        try:
            await conn.execute(
                """
                INSERT INTO answer_cache (
                    cache_key, question, question_embedding, answer, references_json,
                    model_id, embedding_model_id, kb_version
                )
                VALUES ($1, $2, $3::vector, $4, $5::jsonb, $6, $7, $8)
                ON CONFLICT (cache_key) DO UPDATE SET
                    answer = EXCLUDED.answer,
                    references_json = EXCLUDED.references_json,
                    kb_version = EXCLUDED.kb_version,
                    updated_at = NOW()
                """,
                cache_key,
                question,
                question_embedding,
                answer,
                json.dumps(references, ensure_ascii=False),
                model_id,
                embedding_model_id,
                kb_version,
            )
            await self._prune_stale_entries(conn, kb_version)
        except Exception:
            logger.warning("Answer cache store to postgres failed, skip")

    async def _prune_stale_entries(self, conn: asyncpg.Connection, current_version: int) -> None:
        """顺手清理：过期版本行 + 超量时淘汰最旧行"""
        try:
            await conn.execute(
                "DELETE FROM answer_cache WHERE kb_version < $1", current_version
            )
            max_entries = self._settings.rag_answer_cache_max_entries
            if max_entries > 0:
                await conn.execute(
                    """
                    DELETE FROM answer_cache
                    WHERE id IN (
                        SELECT id FROM answer_cache
                        ORDER BY updated_at DESC
                        OFFSET $1
                    )
                    """,
                    max_entries,
                )
        except Exception:
            logger.debug("Answer cache prune skipped")


_answer_cache_service = AnswerCacheService()


def get_answer_cache_service() -> AnswerCacheService:
    return _answer_cache_service

import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import asyncpg
from openai import AsyncAzureOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, wait_fixed

from app.core.config import get_settings
from app.domain.answer_cache import CachedAnswer, get_answer_cache_service
from app.domain.embedding import EmbeddingUsage, get_embedding_service
from app.domain.models_registry import ModelInfo, ModelRegistry
from app.domain.reranker import get_reranker_service
from app.domain.vector_store import SearchResult, get_vector_store

logger = logging.getLogger(__name__)

RAG_SYSTEM_PROMPT = """你是一个智能助手，根据提供的上下文信息回答用户问题。
请遵循以下规则：
1. 仅使用提供的上下文信息回答问题
2. 如果上下文信息不足以回答问题，请诚实告知
3. 回答要准确、简洁、有帮助
4. 在回答中引用相关的来源编号 [1], [2] 等

上下文信息：
{context}
"""

CHAT_SYSTEM_PROMPT = """你是一个专业、可靠的 AI 助手。
请直接回答用户问题，表达清晰，避免编造信息。
"""

QUESTION_REWRITE_SYSTEM_PROMPT = """你是对话问题改写助手。请把用户的追问改写成不依赖上下文、可独立理解的问题。
规则：
1. 展开追问里的指代（如"它/这个/第二点"），保留追问的全部意图
2. 只输出改写后的问题本身，不要回答，不要任何解释
3. 如果追问本身已经是独立问题，原样输出
"""


def _trim_history(
    history: list[dict[str, Any]] | None,
    max_messages: int,
    max_chars: int,
) -> list[dict[str, Any]]:
    """裁剪对话历史：保留最近 N 条，并从头裁掉超字符预算的部分"""
    if not history:
        return []
    trimmed: list[dict[str, Any]] = []
    for item in reversed(history):
        if len(trimmed) >= max_messages:
            break
        role = str(item.get("role", ""))
        content = str(item.get("content", "") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        trimmed.append({"role": role, "content": content})

    trimmed.reverse()
    total_chars = sum(len(item["content"]) for item in trimmed)
    while trimmed and total_chars > max_chars:
        removed = trimmed.pop(0)
        total_chars -= len(removed["content"])
    return trimmed


@dataclass
class SkillCallLog:
    """单次 MCP skill 调用记录"""

    skill_name: str
    status: str
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    input_summary: str = ""
    output_summary: str = ""
    error_message: str | None = None


@dataclass
class LlmGenerationUsage:
    """LLM 生成调用 token 统计"""

    answer: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class RAGResponse:
    """RAG 问答响应"""

    answer: str
    references: list[dict[str, Any]]
    session_id: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    skill_calls: list[SkillCallLog]
    cache_hit: bool = False
    cache_kind: str = "none"  # none | exact | semantic
    cache_similarity: float | None = None


class RAGExecutionError(RuntimeError):
    """RAG 执行异常，保留可观测上下文"""

    def __init__(
        self,
        message: str,
        session_id: str,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        skill_calls: list[SkillCallLog],
    ) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.model_id = model_id
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.skill_calls = skill_calls


class RAGService:
    """RAG 检索增强生成服务"""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: AsyncAzureOpenAI | None = None

    def _get_chat_client(self, model: ModelInfo) -> AsyncAzureOpenAI:
        """获取 Chat 客户端，优先读模型配置，其次读系统默认配置"""
        if model.base_url and model.api_key:
            return AsyncAzureOpenAI(
                api_key=model.api_key,
                azure_endpoint=model.base_url,
                api_version=self._settings.azure_openai_api_version,
            )

        if self._client is None:
            self._client = AsyncAzureOpenAI(
                api_key=self._settings.azure_openai_api_key,
                azure_endpoint=self._settings.azure_openai_endpoint,
                api_version=self._settings.azure_openai_api_version,
            )
        return self._client

    async def ask(
        self,
        question: str,
        model_id: str,
        registry: ModelRegistry,
        conn: asyncpg.Connection,
        embedding_model_id: str = "text-embedding-3-large",
        session_id: str | None = None,
        document_ids: list[str] | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> RAGResponse:
        """RAG 问答主流程，附带 token 和 skill 调用明细"""
        resolved_session_id = session_id or f"session-{hash(question) % 1000000:06d}"
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        skill_calls: list[SkillCallLog] = []

        query_embedding: list[float]
        embedding_usage: EmbeddingUsage
        search_results: list[SearchResult]

        embedding_service = get_embedding_service()
        vector_store = get_vector_store()

        # 多轮记忆：带历史的提问依赖会话上下文，不查也不写答案缓存
        trimmed_history = _trim_history(
            history,
            self._settings.chat_history_max_messages,
            self._settings.chat_history_max_chars,
        )
        has_history = bool(trimmed_history)

        # 0) 答案缓存：精确命中（Redis），命中则跳过 embedding/检索/生成
        answer_cache = get_answer_cache_service()
        kb_version = await answer_cache.get_kb_version()
        cache_key = answer_cache.build_cache_key(
            question=question,
            model_id=model_id,
            embedding_model_id=embedding_model_id,
            document_ids=document_ids,
            kb_version=kb_version,
        )
        if self._settings.rag_answer_cache_enabled and not has_history:
            cache_start = time.monotonic()
            cached_exact = await answer_cache.lookup_exact(cache_key)
            if cached_exact is not None:
                skill_calls.append(
                    SkillCallLog(
                        skill_name="mcp.answer.cache",
                        status="success",
                        latency_ms=int((time.monotonic() - cache_start) * 1000),
                        input_summary="kind=exact",
                        output_summary=f"answer_chars={len(cached_exact.answer)}",
                    )
                )
                return self._cached_response(
                    cached_exact, resolved_session_id, model_id, skill_calls
                )

        # 0.5) 追问改写：带历史时把追问展开成独立问题，检索质量才有保障
        retrieval_question = question
        if has_history and self._settings.chat_question_rewrite_enabled:
            rewrite_start = time.monotonic()
            try:
                retrieval_question = await self._rewrite_question(
                    question=question,
                    history=trimmed_history,
                    model_id=model_id,
                    registry=registry,
                )
                skill_calls.append(
                    SkillCallLog(
                        skill_name="mcp.chat.rewrite",
                        status="success",
                        latency_ms=int((time.monotonic() - rewrite_start) * 1000),
                        input_summary=f"chars={len(question)}",
                        output_summary=f"rewritten_chars={len(retrieval_question)}",
                    )
                )
            except Exception as exc:
                retrieval_question = question
                skill_calls.append(
                    SkillCallLog(
                        skill_name="mcp.chat.rewrite",
                        status="failed",
                        latency_ms=int((time.monotonic() - rewrite_start) * 1000),
                        input_summary=f"chars={len(question)}",
                        output_summary="",
                        error_message=str(exc),
                    )
                )

        # 1) MCP skill: embedding
        embedding_start = time.monotonic()
        try:
            query_embedding, embedding_usage = await embedding_service.embed_single_with_usage(
                retrieval_question,
                embedding_model_id,
                registry,
            )
            embedding_latency_ms = int((time.monotonic() - embedding_start) * 1000)
            prompt_tokens += embedding_usage.prompt_tokens
            total_tokens += embedding_usage.total_tokens
            skill_calls.append(
                SkillCallLog(
                    skill_name="mcp.embedding.generate",
                    status="success",
                    latency_ms=embedding_latency_ms,
                    prompt_tokens=embedding_usage.prompt_tokens,
                    completion_tokens=0,
                    total_tokens=embedding_usage.total_tokens,
                    input_summary=f"chars={len(question)},model={embedding_model_id}",
                    output_summary=f"dimension={len(query_embedding)}",
                )
            )
        except Exception as exc:
            embedding_latency_ms = int((time.monotonic() - embedding_start) * 1000)
            skill_calls.append(
                SkillCallLog(
                    skill_name="mcp.embedding.generate",
                    status="failed",
                    latency_ms=embedding_latency_ms,
                    input_summary=f"chars={len(question)},model={embedding_model_id}",
                    output_summary="",
                    error_message=str(exc),
                )
            )
            raise RAGExecutionError(
                "Embedding 调用失败",
                resolved_session_id,
                model_id,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                skill_calls,
            ) from exc

        # 1.5) 答案缓存：语义命中（问题向量近邻），命中则跳过检索/生成
        if self._settings.rag_semantic_cache_enabled and not has_history:
            cache_start = time.monotonic()
            cached_semantic = await answer_cache.lookup_semantic(
                conn,
                question_embedding=query_embedding,
                model_id=model_id,
                embedding_model_id=embedding_model_id,
                document_ids=document_ids,
                kb_version=kb_version,
            )
            if cached_semantic is not None:
                skill_calls.append(
                    SkillCallLog(
                        skill_name="mcp.answer.cache",
                        status="success",
                        latency_ms=int((time.monotonic() - cache_start) * 1000),
                        input_summary=f"kind=semantic,similarity={cached_semantic.similarity}",
                        output_summary=f"answer_chars={len(cached_semantic.answer)}",
                    )
                )
                return self._cached_response(
                    cached_semantic, resolved_session_id, model_id, skill_calls
                )

        # 2) MCP skill: vector search
        # 交叉重排启用时先多召回候选，重排后截断回 top_k
        rerank_model = None
        if self._settings.rag_cross_rerank_enabled:
            rerank_model = get_reranker_service().resolve_model(registry)
        search_top_k = self._settings.rag_top_k
        if rerank_model is not None:
            search_top_k = min(
                self._settings.rag_top_k * max(1, self._settings.rag_rerank_candidate_multiplier),
                50,
            )

        search_start = time.monotonic()
        try:
            search_results = await vector_store.similarity_search(
                conn,
                query_embedding,
                top_k=search_top_k,
                min_score=self._settings.rag_min_score,
                document_ids=document_ids,
                use_parent_child_rerank=self._settings.rag_parent_child_rerank,
                candidate_multiplier=self._settings.rag_parent_candidate_multiplier,
                child_expand_window=self._settings.rag_parent_child_expand_window,
            )
            search_latency_ms = int((time.monotonic() - search_start) * 1000)
            doc_filter_summary = f",docs={len(document_ids)}" if document_ids else ""
            mode_summary = ",mode=parent-child" if self._settings.rag_parent_child_rerank else ",mode=flat"
            rerank_summary = (
                f",rerank_candidates={search_top_k}" if rerank_model is not None else ""
            )
            skill_calls.append(
                SkillCallLog(
                    skill_name="mcp.vector.search",
                    status="success",
                    latency_ms=search_latency_ms,
                    input_summary=(
                        f"top_k={search_top_k},final={self._settings.rag_top_k}"
                        f",min_score={self._settings.rag_min_score}"
                        f"{doc_filter_summary}{mode_summary}{rerank_summary}"
                    ),
                    output_summary=f"hits={len(search_results)}",
                )
            )
        except Exception as exc:
            search_latency_ms = int((time.monotonic() - search_start) * 1000)
            doc_filter_summary = f",docs={len(document_ids)}" if document_ids else ""
            mode_summary = ",mode=parent-child" if self._settings.rag_parent_child_rerank else ",mode=flat"
            skill_calls.append(
                SkillCallLog(
                    skill_name="mcp.vector.search",
                    status="failed",
                    latency_ms=search_latency_ms,
                    input_summary=(
                        f"top_k={self._settings.rag_top_k},min_score={self._settings.rag_min_score}"
                        f"{doc_filter_summary}{mode_summary}"
                    ),
                    output_summary="",
                    error_message=str(exc),
                )
            )
            raise RAGExecutionError(
                "向量检索失败",
                resolved_session_id,
                model_id,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                skill_calls,
            ) from exc

        # 2.5) MCP skill: cross rerank（交叉重排；失败降级为向量序截断）
        if rerank_model is not None and len(search_results) > 1:
            rerank_start = time.monotonic()
            vector_order = [result.chunk_id for result in search_results]
            try:
                ranked = await get_reranker_service().rerank(
                    retrieval_question,
                    [result.content for result in search_results],
                    top_n=self._settings.rag_top_k,
                    model=rerank_model,
                )
                reranked_results = [search_results[index] for index, _score in ranked]
                # 重排结果不足 top_k 时用剩余候选按向量序补齐
                if len(reranked_results) < self._settings.rag_top_k:
                    used = {index for index, _score in ranked}
                    leftovers = [result for idx, result in enumerate(search_results) if idx not in used]
                    reranked_results.extend(leftovers)
                search_results = reranked_results[: self._settings.rag_top_k]
                new_order = [result.chunk_id for result in search_results]
                order_changed = new_order != vector_order[: len(new_order)]
                skill_calls.append(
                    SkillCallLog(
                        skill_name="mcp.rerank.rank",
                        status="success",
                        latency_ms=int((time.monotonic() - rerank_start) * 1000),
                        input_summary=(
                            f"candidates={len(vector_order)},model={rerank_model.model_id},"
                            f"top_k={self._settings.rag_top_k}"
                        ),
                        output_summary=(
                            f"final={len(search_results)},order_changed={order_changed},"
                            f"top1={str(ranked[0][1])[:6] if ranked else 'n/a'}"
                        ),
                    )
                )
            except Exception as exc:
                search_results = search_results[: self._settings.rag_top_k]
                skill_calls.append(
                    SkillCallLog(
                        skill_name="mcp.rerank.rank",
                        status="failed",
                        latency_ms=int((time.monotonic() - rerank_start) * 1000),
                        input_summary=f"candidates={len(vector_order)},model={rerank_model.model_id}",
                        output_summary="",
                        error_message=f"重排失败已降级向量序: {str(exc)[:200]}",
                    )
                )
        else:
            search_results = search_results[: self._settings.rag_top_k]

        # 3) 构建上下文
        context = self._build_context(search_results)

        # 4) MCP skill: LLM generation
        generation_start = time.monotonic()
        try:
            generation = await self._generate_answer(
                question=question,
                context=context,
                model_id=model_id,
                registry=registry,
                history=trimmed_history,
            )
            generation_latency_ms = int((time.monotonic() - generation_start) * 1000)
            prompt_tokens += generation.prompt_tokens
            completion_tokens += generation.completion_tokens
            total_tokens += generation.total_tokens
            skill_calls.append(
                SkillCallLog(
                    skill_name="mcp.llm.generate",
                    status="success",
                    latency_ms=generation_latency_ms,
                    prompt_tokens=generation.prompt_tokens,
                    completion_tokens=generation.completion_tokens,
                    total_tokens=generation.total_tokens,
                    input_summary=f"model={model_id},context_chars={len(context)}",
                    output_summary=f"answer_chars={len(generation.answer)}",
                )
            )
        except Exception as exc:
            generation_latency_ms = int((time.monotonic() - generation_start) * 1000)
            skill_calls.append(
                SkillCallLog(
                    skill_name="mcp.llm.generate",
                    status="failed",
                    latency_ms=generation_latency_ms,
                    input_summary=f"model={model_id},context_chars={len(context)}",
                    output_summary="",
                    error_message=str(exc),
                )
            )
            raise RAGExecutionError(
                "LLM 生成失败",
                resolved_session_id,
                model_id,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                skill_calls,
            ) from exc

        references = self._build_references(search_results)

        # 5) 写入答案缓存（best-effort，失败不打断主链路；带历史的答案依赖会话上下文，不缓存）
        if not has_history:
            await answer_cache.store(
                conn,
                cache_key=cache_key,
                question=question,
                question_embedding=query_embedding,
                answer=generation.answer,
                references=references,
                model_id=model_id,
                embedding_model_id=embedding_model_id,
                document_ids=document_ids,
                kb_version=kb_version,
            )

        return RAGResponse(
            answer=generation.answer,
            references=references,
            session_id=resolved_session_id,
            model_id=model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            skill_calls=skill_calls,
        )

    @staticmethod
    def _cached_response(
        cached: CachedAnswer,
        session_id: str,
        model_id: str,
        skill_calls: list[SkillCallLog],
    ) -> RAGResponse:
        return RAGResponse(
            answer=cached.answer,
            references=cached.references,
            session_id=session_id,
            model_id=model_id,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            skill_calls=skill_calls,
            cache_hit=True,
            cache_kind=cached.kind,
            cache_similarity=cached.similarity,
        )

    async def chat_only(
        self,
        question: str,
        model_id: str,
        registry: ModelRegistry,
        session_id: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> RAGResponse:
        """普通聊天（不走 embedding/向量检索）"""
        resolved_session_id = session_id or f"session-{hash(question) % 1000000:06d}"
        trimmed_history = _trim_history(
            history,
            self._settings.chat_history_max_messages,
            self._settings.chat_history_max_chars,
        )
        generation_start = time.monotonic()
        try:
            generation = await self._generate_plain_answer(
                question=question,
                model_id=model_id,
                registry=registry,
                history=trimmed_history,
            )
        except Exception as exc:
            raise RAGExecutionError(
                "LLM 生成失败",
                resolved_session_id,
                model_id,
                0,
                0,
                0,
                [
                    SkillCallLog(
                        skill_name="mcp.llm.generate",
                        status="failed",
                        latency_ms=int((time.monotonic() - generation_start) * 1000),
                        input_summary=f"model={model_id},mode=chat-only",
                        output_summary="",
                        error_message=str(exc),
                    )
                ],
            ) from exc

        return RAGResponse(
            answer=generation.answer,
            references=[],
            session_id=resolved_session_id,
            model_id=model_id,
            prompt_tokens=generation.prompt_tokens,
            completion_tokens=generation.completion_tokens,
            total_tokens=generation.total_tokens,
            skill_calls=[
                SkillCallLog(
                    skill_name="mcp.llm.generate",
                    status="success",
                    latency_ms=int((time.monotonic() - generation_start) * 1000),
                    prompt_tokens=generation.prompt_tokens,
                    completion_tokens=generation.completion_tokens,
                    total_tokens=generation.total_tokens,
                    input_summary=f"model={model_id},mode=chat-only",
                    output_summary=f"answer_chars={len(generation.answer)}",
                )
            ],
        )

    async def chat_only_stream(
        self,
        question: str,
        model_id: str,
        registry: ModelRegistry,
        usage_sink: dict[str, int] | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """普通聊天流式输出（不走 embedding/向量检索）"""
        model = registry.get_model(model_id)
        client = self._get_chat_client(model)
        deployment_name = model_id
        trimmed_history = _trim_history(
            history,
            self._settings.chat_history_max_messages,
            self._settings.chat_history_max_chars,
        )

        request_payload = {
            "model": deployment_name,
            "messages": [
                {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                *trimmed_history,
                {"role": "user", "content": question},
            ],
            "temperature": 0.7,
            "max_tokens": 1024,
            "stream": True,
        }
        try:
            stream = await client.chat.completions.create(
                **request_payload,
                stream_options={"include_usage": True},
            )
        except Exception:
            # 兼容部分模型/网关不支持 include_usage 的情况
            stream = await client.chat.completions.create(**request_payload)

        usage_stats = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage:
                usage_stats["prompt_tokens"] = (usage.prompt_tokens or 0)
                usage_stats["completion_tokens"] = (usage.completion_tokens or 0)
                usage_stats["total_tokens"] = (usage.total_tokens or 0)

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta

        if usage_sink is not None:
            usage_sink.update(usage_stats)

    @staticmethod
    def _build_messages(
        system_prompt: str,
        history: list[dict[str, Any]] | None,
        question: str,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system_prompt},
            *(history or []),
            {"role": "user", "content": question},
        ]

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_fixed(2),
        reraise=True,
    )
    async def _rewrite_question(
        self,
        question: str,
        history: list[dict[str, Any]],
        model_id: str,
        registry: ModelRegistry,
    ) -> str:
        """把追问改写成独立问题（用于检索），失败由调用方回退原问题"""
        model = registry.get_model(model_id)
        client = self._get_chat_client(model)

        response = await client.chat.completions.create(
            model=model_id,
            messages=self._build_messages(QUESTION_REWRITE_SYSTEM_PROMPT, history, question),
            temperature=0.0,
            max_tokens=256,
        )
        rewritten = (response.choices[0].message.content or "").strip()
        return rewritten or question

    def _build_context(self, results: list[SearchResult]) -> str:
        """构建上下文字符串"""
        if not results:
            return "暂无相关上下文信息。"

        context_parts = []
        for index, result in enumerate(results, start=1):
            context_parts.append(f"[{index}] {result.content}")
        return "\n\n".join(context_parts)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _generate_answer(
        self,
        question: str,
        context: str,
        model_id: str,
        registry: ModelRegistry,
        history: list[dict[str, Any]] | None = None,
    ) -> LlmGenerationUsage:
        """调用 LLM 生成回答，并返回 token 使用统计"""
        model = registry.get_model(model_id)
        client = self._get_chat_client(model)
        deployment_name = model_id
        system_prompt = RAG_SYSTEM_PROMPT.format(context=context)

        response = await client.chat.completions.create(
            model=deployment_name,
            messages=[
                {"role": "system", "content": system_prompt},
                *(history or []),
                {"role": "user", "content": question},
            ],
            temperature=0.7,
            max_tokens=1024,
        )

        answer = response.choices[0].message.content or ""
        prompt_tokens = (response.usage.prompt_tokens if response.usage else 0) or 0
        completion_tokens = (response.usage.completion_tokens if response.usage else 0) or 0
        total_tokens = (response.usage.total_tokens if response.usage else 0) or 0

        logger.debug(
            "Generated answer chars=%s, prompt_tokens=%s, completion_tokens=%s, total_tokens=%s",
            len(answer),
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )
        return LlmGenerationUsage(
            answer=answer,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _generate_plain_answer(
        self,
        question: str,
        model_id: str,
        registry: ModelRegistry,
        history: list[dict[str, Any]] | None = None,
    ) -> LlmGenerationUsage:
        """普通聊天模式（不带检索上下文）"""
        model = registry.get_model(model_id)
        client = self._get_chat_client(model)
        deployment_name = model_id

        response = await client.chat.completions.create(
            model=deployment_name,
            messages=[
                {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                *(history or []),
                {"role": "user", "content": question},
            ],
            temperature=0.7,
            max_tokens=1024,
        )

        answer = response.choices[0].message.content or ""
        prompt_tokens = (response.usage.prompt_tokens if response.usage else 0) or 0
        completion_tokens = (response.usage.completion_tokens if response.usage else 0) or 0
        total_tokens = (response.usage.total_tokens if response.usage else 0) or 0

        return LlmGenerationUsage(
            answer=answer,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    def _build_references(self, results: list[SearchResult]) -> list[dict[str, Any]]:
        """构建引用列表"""
        references = []
        for result in results:
            references.append(
                {
                    "documentId": result.document_id,
                    "documentName": result.metadata.get("file_name", result.document_id),
                    "chunkId": result.chunk_id,
                    "score": round(result.score, 4),
                    "parentChunkId": result.parent_chunk_id,
                    "isExpanded": result.is_expanded,
                }
            )
        return references


_rag_service = RAGService()


def get_rag_service() -> RAGService:
    """获取 RAG 服务实例"""
    return _rag_service

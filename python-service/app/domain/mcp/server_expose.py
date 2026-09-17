"""把 rag-demo 能力暴露为标准 MCP Server（Streamable HTTP）。

挂载在 /mcp，外部 MCP 客户端（Claude Desktop、Cursor、其他 Agent）可直接接入：
- knowledge_search: 向量检索知识库分块
- ask_knowledge_base: RAG 问答（带答案缓存）
- list_documents: 文档列表
- upload_text_document: 文本内容入库（走处理队列）

可选 Bearer Token 鉴权（MCP_SERVER_TOKEN 为空则不校验，仅限内网开发环境）。
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.core.database import db_conn_context
from app.core.rabbitmq import get_rabbitmq_client
from app.domain.answer_cache import get_answer_cache_service
from app.domain.embedding import get_embedding_service
from app.domain.models_registry import _registry
from app.domain.rag_service import get_rag_service
from app.domain.vector_store import get_vector_store

logger = logging.getLogger(__name__)


async def _tool_knowledge_search(query: str, top_k: int = 5) -> dict[str, Any]:
    """在知识库中做向量检索，返回最相关的文档分块"""
    top_k = max(1, min(int(top_k), 20))
    settings = get_settings()
    embedding_service = get_embedding_service()
    vector_store = get_vector_store()

    model_id = settings.document_worker_embedding_model_id
    embeddings, _usage = await embedding_service.embed_texts([query], model_id, _registry)

    async with db_conn_context() as conn:
        results = await vector_store.similarity_search(
            conn,
            embeddings[0] if embeddings else [],
            top_k=top_k,
            min_score=settings.rag_min_score,
            use_parent_child_rerank=settings.rag_parent_child_rerank,
            candidate_multiplier=settings.rag_parent_candidate_multiplier,
            child_expand_window=settings.rag_parent_child_expand_window,
        )
        items = [
            {
                "documentId": result.document_id,
                "fileName": result.metadata.get("file_name", ""),
                "chunkIndex": result.chunk_index,
                "content": result.content,
                "score": round(result.score, 4),
            }
            for result in results
        ]
    return {"query": query, "items": items, "total": len(items)}


async def _tool_ask_knowledge_base(question: str) -> dict[str, Any]:
    """对知识库提问，返回答案与引用来源（走完整 RAG 链路，命中答案缓存时秒回）"""
    rag_service = get_rag_service()
    model_id = get_settings().document_worker_embedding_model_id
    # 以 embedding 模型名兜底解析一个可用 chat 模型
    chat_model_id = "gpt-4.1-mini"
    for item in _registry.list_models():
        caps = item.get("capabilities", [])
        if item.get("status") == "online" and isinstance(caps, list) and "chat" in caps:
            chat_model_id = str(item.get("id", chat_model_id))
            break

    async with db_conn_context() as conn:
        result = await rag_service.ask(
            question=question,
            model_id=chat_model_id,
            registry=_registry,
            conn=conn,
            embedding_model_id=model_id,
        )
    return {
        "answer": result.answer,
        "references": result.references,
        "modelId": result.model_id,
        "cacheHit": result.cache_hit,
        "cacheKind": result.cache_kind,
    }


async def _tool_list_documents(limit: int = 20) -> dict[str, Any]:
    """列出知识库文档（含处理状态）"""
    limit = max(1, min(int(limit), 100))
    async with db_conn_context() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text AS document_id, file_name, status, metadata, created_at
            FROM documents
            WHERE deleted_at IS NULL
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
        items = []
        for row in rows:
            metadata = row["metadata"]
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            items.append(
                {
                    "documentId": row["document_id"],
                    "fileName": row["file_name"],
                    "status": row["status"],
                    "chunkCount": (metadata or {}).get("chunkCount"),
                    "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
                }
            )
    return {"items": items, "total": len(items)}


async def _tool_upload_text_document(
    file_name: str,
    content: str,
    strategy: str = "fixed",
) -> dict[str, Any]:
    """把文本内容作为文档入库（进入切分/向量化处理队列）"""
    from app.api.v1.endpoints.documents import _normalize_strategy, _save_text_file

    settings = get_settings()
    if not content.strip():
        raise ValueError("content 不能为空")

    strategy = _normalize_strategy(strategy)
    document_id = str(uuid.uuid4())
    task_id = f"task-{uuid.uuid4()}"
    trace_id = f"mcp-upload-{uuid.uuid4().hex[:8]}"
    storage_path = _save_text_file(document_id, file_name, content)

    async with db_conn_context() as conn:
        await conn.execute(
            """
            INSERT INTO documents (id, file_name, source, status, metadata)
            VALUES ($1::uuid, $2, $3, $4, $5::jsonb)
            """,
            document_id,
            file_name,
            "mcp-upload",
            "queued",
            json.dumps(
                {
                    "taskId": task_id,
                    "strategy": strategy,
                    "fileSizeBytes": len(content.encode("utf-8")),
                    "traceId": trace_id,
                    "storagePath": storage_path,
                    "uploadedVia": "mcp",
                },
                ensure_ascii=False,
            ),
        )

    await get_rabbitmq_client().publish_json(
        settings.rabbitmq_documents_queue,
        {
            "taskId": task_id,
            "documentId": document_id,
            "fileName": file_name,
            "strategy": strategy,
            "fileSizeBytes": len(content.encode("utf-8")),
            "traceId": trace_id,
            "storagePath": storage_path,
        },
    )
    await get_answer_cache_service().bump_kb_version()
    return {
        "documentId": document_id,
        "taskId": task_id,
        "fileName": file_name,
        "status": "queued",
        "uploadedAt": datetime.now(UTC).isoformat(),
    }


_mcp_server_instance = None
_mcp_server_app = None


def get_or_build_mcp_server():
    """构建（并缓存）MCP Server 实例与 ASGI 应用；streamable_http_app 只能调用一次"""
    global _mcp_server_instance, _mcp_server_app
    if _mcp_server_instance is not None:
        return _mcp_server_instance

    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="rag-demo",
        title="玄武智库知识库",
        description="企业知识库 RAG 服务：向量检索、知识问答、文档管理",
        instructions=(
            "这是玄武智库 RAG 知识库。检索相关内容用 knowledge_search；"
            "直接要答案用 ask_knowledge_base；管理文档用 list_documents / upload_text_document。"
        ),
    )

    server.add_tool(_tool_knowledge_search, name="knowledge_search")
    server.add_tool(_tool_ask_knowledge_base, name="ask_knowledge_base")
    server.add_tool(_tool_list_documents, name="list_documents")
    server.add_tool(_tool_upload_text_document, name="upload_text_document")

    # 内层路由设为 "/"，挂载到主应用 /mcp 后完整端点即为 /mcp
    _mcp_server_app = server.streamable_http_app(streamable_http_path="/")
    _mcp_server_instance = server
    return server


def build_mcp_server_app():
    get_or_build_mcp_server()
    return _mcp_server_app


def get_mcp_session_lifespan():
    """MCP 会话管理器的生命周期上下文（主应用 lifespan 手动驱动）"""
    server = get_or_build_mcp_server()
    return server.session_manager.run()

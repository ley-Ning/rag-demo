-- RAG Demo: PostgreSQL + pgvector 初始化脚本
-- 说明：这个脚本只在容器首次初始化数据目录时执行一次。

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_name TEXT NOT NULL,
    source TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index > 0),
    content TEXT NOT NULL,
    token_count INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id UUID PRIMARY KEY REFERENCES document_chunks(id) ON DELETE CASCADE,
    embedding VECTOR(1536) NOT NULL,
    model_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS retrieval_logs (
    id BIGSERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL,
    session_id TEXT,
    question TEXT NOT NULL,
    model_id TEXT,
    top_k INTEGER NOT NULL,
    threshold DOUBLE PRECISION NOT NULL,
    latency_ms INTEGER,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    mcp_call_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'success',
    error_message TEXT,
    results JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mcp_skill_logs (
    id BIGSERIAL PRIMARY KEY,
    retrieval_log_id BIGINT NOT NULL REFERENCES retrieval_logs(id) ON DELETE CASCADE,
    trace_id TEXT NOT NULL,
    session_id TEXT,
    skill_name TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms INTEGER,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    input_summary TEXT,
    output_summary TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_chunks_document_chunk ON document_chunks(document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_retrieval_logs_trace_id ON retrieval_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_logs_created_at ON retrieval_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_retrieval_logs_model_id ON retrieval_logs(model_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_logs_status ON retrieval_logs(status);
CREATE INDEX IF NOT EXISTS idx_mcp_skill_logs_retrieval_id ON mcp_skill_logs(retrieval_log_id);
CREATE INDEX IF NOT EXISTS idx_mcp_skill_logs_trace_id ON mcp_skill_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_mcp_skill_logs_created_at ON mcp_skill_logs(created_at DESC);

-- 向量检索索引：先用 HNSW，后续可按数据规模切换 IVF。
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_hnsw
    ON chunk_embeddings
    USING hnsw (embedding vector_cosine_ops);

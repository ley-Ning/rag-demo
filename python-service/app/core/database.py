import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg
from pgvector.asyncpg import register_vector

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class DatabasePool:
    """PostgreSQL 连接池管理器"""

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        """初始化连接池"""
        if self._pool is not None:
            return

        settings = get_settings()
        self._pool = await asyncpg.create_pool(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_database,
            user=settings.postgres_user,
            password=settings.postgres_password,
            min_size=settings.postgres_min_pool_size,
            max_size=settings.postgres_max_pool_size,
            init=self._init_connection,
        )
        logger.info(
            "Database pool initialized: %s:%s/%s",
            settings.postgres_host,
            settings.postgres_port,
            settings.postgres_database,
        )
        await self._ensure_observability_schema()

    async def _init_connection(self, conn: asyncpg.Connection) -> None:
        """初始化数据库连接，注册 pgvector 类型"""
        await register_vector(conn)

    async def _ensure_observability_schema(self) -> None:
        """运行时兜底：补齐可观测字段与表，兼容旧库"""
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")

        create_retrieval_table_sql = """
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
        )
        """
        alter_statements = [
            "ALTER TABLE retrieval_logs ADD COLUMN IF NOT EXISTS model_id TEXT",
            "ALTER TABLE retrieval_logs ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE retrieval_logs ADD COLUMN IF NOT EXISTS completion_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE retrieval_logs ADD COLUMN IF NOT EXISTS total_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE retrieval_logs ADD COLUMN IF NOT EXISTS mcp_call_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE retrieval_logs ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'success'",
            "ALTER TABLE retrieval_logs ADD COLUMN IF NOT EXISTS error_message TEXT",
        ]

        create_table_sql = """
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
        )
        """
        create_mcp_servers_sql = """
        CREATE TABLE IF NOT EXISTS mcp_servers (
            id BIGSERIAL PRIMARY KEY,
            server_key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'http',
            endpoint TEXT NOT NULL,
            auth_type TEXT NOT NULL DEFAULT 'none',
            auth_config JSONB NOT NULL DEFAULT '{}'::jsonb,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            timeout_ms INTEGER NOT NULL DEFAULT 12000,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
        create_mcp_tools_sql = """
        CREATE TABLE IF NOT EXISTS mcp_tools (
            id BIGSERIAL PRIMARY KEY,
            tool_name TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            server_key TEXT,
            tool_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
        create_tool_runs_sql = """
        CREATE TABLE IF NOT EXISTS tool_runs (
            id BIGSERIAL PRIMARY KEY,
            retrieval_log_id BIGINT REFERENCES retrieval_logs(id) ON DELETE SET NULL,
            trace_id TEXT NOT NULL,
            session_id TEXT,
            tool_name TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            input_summary TEXT NOT NULL DEFAULT '',
            output_summary TEXT NOT NULL DEFAULT '',
            output_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
        create_deep_think_runs_sql = """
        CREATE TABLE IF NOT EXISTS deep_think_runs (
            id BIGSERIAL PRIMARY KEY,
            retrieval_log_id BIGINT REFERENCES retrieval_logs(id) ON DELETE SET NULL,
            trace_id TEXT NOT NULL,
            session_id TEXT,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            input_summary TEXT NOT NULL DEFAULT '',
            output_summary TEXT NOT NULL DEFAULT '',
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
        index_statements = [
            "CREATE INDEX IF NOT EXISTS idx_retrieval_logs_trace_id ON retrieval_logs(trace_id)",
            "CREATE INDEX IF NOT EXISTS idx_retrieval_logs_created_at ON retrieval_logs(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_retrieval_logs_model_id ON retrieval_logs(model_id)",
            "CREATE INDEX IF NOT EXISTS idx_retrieval_logs_status ON retrieval_logs(status)",
            "CREATE INDEX IF NOT EXISTS idx_mcp_skill_logs_retrieval_id ON mcp_skill_logs(retrieval_log_id)",
            "CREATE INDEX IF NOT EXISTS idx_mcp_skill_logs_trace_id ON mcp_skill_logs(trace_id)",
            "CREATE INDEX IF NOT EXISTS idx_mcp_skill_logs_created_at ON mcp_skill_logs(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_mcp_servers_server_key ON mcp_servers(server_key)",
            "CREATE INDEX IF NOT EXISTS idx_mcp_servers_enabled ON mcp_servers(enabled)",
            "CREATE INDEX IF NOT EXISTS idx_mcp_tools_tool_name ON mcp_tools(tool_name)",
            "CREATE INDEX IF NOT EXISTS idx_mcp_tools_source ON mcp_tools(source)",
            "CREATE INDEX IF NOT EXISTS idx_tool_runs_trace_id ON tool_runs(trace_id)",
            "CREATE INDEX IF NOT EXISTS idx_tool_runs_created_at ON tool_runs(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_tool_runs_tool_name ON tool_runs(tool_name)",
            "CREATE INDEX IF NOT EXISTS idx_deep_think_runs_trace_id ON deep_think_runs(trace_id)",
            "CREATE INDEX IF NOT EXISTS idx_deep_think_runs_created_at ON deep_think_runs(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_deep_think_runs_stage ON deep_think_runs(stage)",
        ]

        # 聊天会话表
        create_chat_sessions_sql = """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id BIGSERIAL PRIMARY KEY,
            session_id TEXT UNIQUE NOT NULL,
            model_id TEXT NOT NULL,
            title TEXT,
            use_rag BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """

        # 聊天消息表
        create_chat_messages_sql = """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id BIGSERIAL PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            "references" JSONB DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """

        chat_index_statements = [
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_session_id ON chat_sessions(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_created_at ON chat_sessions(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id ON chat_messages(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at ON chat_messages(created_at)",
        ]

        async with self._pool.acquire() as conn:
            await conn.execute(create_retrieval_table_sql)
            for sql in alter_statements:
                await conn.execute(sql)
            await conn.execute(create_table_sql)
            await conn.execute(create_mcp_servers_sql)
            await conn.execute(create_mcp_tools_sql)
            await conn.execute(create_tool_runs_sql)
            await conn.execute(create_deep_think_runs_sql)
            for sql in index_statements:
                await conn.execute(sql)
            # 聊天历史表
            await conn.execute(create_chat_sessions_sql)
            await conn.execute(create_chat_messages_sql)
            for sql in chat_index_statements:
                await conn.execute(sql)

    async def close(self) -> None:
        """关闭连接池"""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("Database pool closed")

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[asyncpg.Connection, None]:
        """获取数据库连接的上下文管理器"""
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        async with self._pool.acquire() as conn:
            yield conn


# 全局数据库池实例
_db_pool = DatabasePool()


async def init_database() -> None:
    """初始化数据库连接池"""
    await _db_pool.initialize()


async def close_database() -> None:
    """关闭数据库连接池"""
    await _db_pool.close()


async def ping_database() -> bool:
    """数据库健康检查"""
    try:
        async with _db_pool.get_connection() as conn:
            value = await conn.fetchval("SELECT 1")
            return value == 1
    except Exception:
        return False


@asynccontextmanager
async def db_conn_context() -> AsyncGenerator[asyncpg.Connection, None]:
    """上下文管理器方式获取连接（给手动 async with 场景使用）"""
    async with _db_pool.get_connection() as conn:
        yield conn


async def get_db_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """FastAPI 依赖注入：获取数据库连接"""
    async with _db_pool.get_connection() as conn:
        yield conn


async def get_optional_db_conn() -> AsyncGenerator[asyncpg.Connection | None, None]:
    """
    FastAPI 依赖注入：可选数据库连接

    说明：
    - 普通聊天场景不一定依赖向量检索，因此允许在 DB 不可用时返回 None。
    """
    try:
        async with _db_pool.get_connection() as conn:
            yield conn
    except Exception:
        logger.warning("Optional DB connection unavailable, fallback to non-RAG mode")
        yield None

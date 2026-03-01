from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "RAG Python Service"
    app_env: str = "dev"
    app_port: int = 8090
    cors_origins: str = "http://localhost:8081"
    model_registry_file: str = "data/models_registry.json"
    documents_upload_dir: str = "data/uploads"

    # PostgreSQL 配置
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_database: str = "rag_demo"
    postgres_user: str = "rag_user"
    postgres_password: str = "rag_pass"
    postgres_min_pool_size: int = 2
    postgres_max_pool_size: int = 10

    # Redis 配置
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    redis_key_prefix: str = "rag_demo"

    # RabbitMQ 配置
    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "rag"
    rabbitmq_password: str = "rag_pass"
    rabbitmq_vhost: str = "/"
    rabbitmq_documents_queue: str = "documents.upload"

    # Azure OpenAI 默认配置
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-02-15-preview"

    # RAG 配置
    rag_top_k: int = 5
    rag_min_score: float = 0.5
    rag_parent_child_rerank: bool = True
    rag_parent_candidate_multiplier: int = 6
    rag_parent_child_expand_window: int = 1
    vector_dimension: int = 1536

    # MCP / Tool Orchestration 配置
    mcp_enabled: bool = True
    mcp_auto_call: bool = True
    mcp_max_steps: int = 6
    mcp_http_timeout_ms: int = 12000
    mcp_web_allow_all_domains: bool = True
    mcp_web_max_content_chars: int = 12000
    mcp_web_request_timeout_sec: int = 12

    # 深度思考编排
    deep_think_enabled: bool = True
    deep_think_max_iterations: int = 3

    # 文档 Worker 配置
    document_worker_enabled: bool = True
    document_worker_prefetch: int = 2
    document_worker_chunk_size: int = 400
    document_worker_overlap: int = 50
    document_worker_embedding_model_id: str = "text-embedding-3-large"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def model_registry_path(self) -> Path:
        path = Path(self.model_registry_file)
        if path.is_absolute():
            return path
        return self.project_root / path

    @property
    def documents_upload_path(self) -> Path:
        path = Path(self.documents_upload_dir)
        if path.is_absolute():
            return path
        return self.project_root / path

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_database}"
        )

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def rabbitmq_url(self) -> str:
        return (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}/{self.rabbitmq_vhost}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

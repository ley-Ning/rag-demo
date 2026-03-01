# RAG Python Service (FastAPI)

## 1. 安装与启动

先在项目根目录启动中间件：

```bash
docker compose up -d
```

再启动 Python 服务：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8090
```

## 2. 可用接口

- `GET /api/v1/health`
- `GET /api/v1/models`
- `POST /api/v1/models`
- `PUT /api/v1/models/{model_id}`
- `PATCH /api/v1/models/{model_id}/status`
- `DELETE /api/v1/models/{model_id}`
- `POST /api/v1/chat/ask`
- `POST /api/v1/chat/ask-stream`
- `POST /api/v1/documents/upload`
- `POST /api/v1/documents/split-preview`
- `POST /api/v1/documents/import-from-tool-run`
- `GET /api/v1/mcp/servers`
- `POST /api/v1/mcp/servers`
- `PATCH /api/v1/mcp/servers/{server_key}`
- `POST /api/v1/mcp/servers/{server_key}/sync-tools`
- `GET /api/v1/mcp/tools`
- `PATCH /api/v1/mcp/tools/{tool_name}/status`
- `GET /api/v1/observability/consumption-logs`
- `GET /api/v1/observability/tool-runs`
- `GET /api/v1/observability/deep-think-runs`

## 3. 响应结构

统一返回：`code/message/data/traceId`

## 4. MCP 外部工具同步说明

- `POST /api/v1/mcp/servers/{server_key}/sync-tools` 会向外部 endpoint 发：

```json
{"op":"list_tools"}
```

- 外部返回示例（两种字段都支持）：

```json
{
  "tools": [
    {"toolName":"mcp.demo.search","displayName":"Demo Search","description":"...", "toolSchema":{"type":"object"}},
    {"name":"mcp.demo.fetch","title":"Demo Fetch","description":"...", "schema":{"type":"object"}}
  ]
}
```

## 5. 环境变量（可选）

```bash
APP_NAME=RAG Python Service
APP_ENV=dev
APP_PORT=8090
CORS_ORIGINS=http://localhost:8081
MODEL_REGISTRY_FILE=data/models_registry.json
DOCUMENTS_UPLOAD_DIR=data/uploads

# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DATABASE=rag_demo
POSTGRES_USER=rag_user
POSTGRES_PASSWORD=rag_pass

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
REDIS_KEY_PREFIX=rag_demo

# RabbitMQ
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=rag
RABBITMQ_PASSWORD=rag_pass
RABBITMQ_VHOST=/
RABBITMQ_DOCUMENTS_QUEUE=documents.upload

# RAG 检索
RAG_TOP_K=5
RAG_MIN_SCORE=0.5
RAG_PARENT_CHILD_RERANK=true
RAG_PARENT_CANDIDATE_MULTIPLIER=6
RAG_PARENT_CHILD_EXPAND_WINDOW=1
VECTOR_DIMENSION=1536

# MCP / 插件编排
MCP_ENABLED=true
MCP_AUTO_CALL=true
MCP_MAX_STEPS=6
MCP_HTTP_TIMEOUT_MS=12000
MCP_WEB_ALLOW_ALL_DOMAINS=true
MCP_WEB_MAX_CONTENT_CHARS=12000
MCP_WEB_REQUEST_TIMEOUT_SEC=12
DEEP_THINK_ENABLED=true
DEEP_THINK_MAX_ITERATIONS=3

# 文档 Worker
DOCUMENT_WORKER_ENABLED=true
DOCUMENT_WORKER_PREFETCH=2
DOCUMENT_WORKER_CHUNK_SIZE=400
DOCUMENT_WORKER_OVERLAP=50
DOCUMENT_WORKER_EMBEDDING_MODEL_ID=text-embedding-3-large
```

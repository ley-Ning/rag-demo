# RAG Python Service (FastAPI)

## 1. 安装与启动

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
- `POST /api/v1/documents/upload`
- `POST /api/v1/documents/split-preview`

## 3. 响应结构

统一返回：`code/message/data/traceId`

## 4. 环境变量（可选）

```bash
APP_NAME=RAG Python Service
APP_ENV=dev
APP_PORT=8090
CORS_ORIGINS=http://localhost:8081
MODEL_REGISTRY_FILE=data/models_registry.json
```

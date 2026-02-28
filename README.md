# rag_demo

主公，这是一套 Next.js + FastAPI 的 RAG 初始工程。

## 目录

- `frontend/`：Next.js + React + TS + antd
- `python-service/`：FastAPI AI 服务骨架
- `docs/`：规划、实现记录、阶段计划

## 快速启动

### 1) 启动 Python 服务

```bash
cd python-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8090
```

### 2) 启动前端

```bash
cd frontend
npm install
echo 'RAG_PYTHON_URL=http://127.0.0.1:8000' > .env.local
npm run dev
```

打开：`http://localhost:8081`

## 首版能力

- 聊天页：可选 chat 模型并提问
- 文档页：上传入队 + 切割实时预览
- 模型页：模型能力和状态展示
- 设置页：系统设置占位
# rag-demo

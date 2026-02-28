# RAG Frontend (Next.js + TypeScript + Ant Design)

## 1. 启动方式

```bash
npm install
npm run dev
```

默认地址：`http://localhost:8081`

## 2. 环境变量

在 `frontend/.env.local` 写：

```bash
RAG_PYTHON_URL=http://127.0.0.1:8090
```

## 3. 当前页面

- `/chat`：聊天问答（仅允许选择 chat 能力模型）
- `/documents`：文档上传 + 切割实时预览
- `/models`：模型能力列表
- `/settings`：系统设置

## 4. API 代理

前端请求走 Next.js API Route，再转发到 Python：

- `GET /api/v1/models`
- `POST /api/v1/chat/ask`
- `POST /api/v1/documents/upload`
- `POST /api/v1/documents/split-preview`

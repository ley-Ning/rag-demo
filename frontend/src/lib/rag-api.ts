import {
  AskRequest,
  AskResult,
  ChatSessionsResult,
  ChatSessionMessages,
  ChunkPreview,
  ConsumptionLogsQuery,
  ConsumptionLogsResult,
  CreateModelRequest,
  DeepThinkRunItem,
  DocumentListResult,
  DocumentStatusResult,
  McpServerItem,
  McpSyncResult,
  McpToolItem,
  ModelItem,
  SplitPreviewRequest,
  SplitStrategy,
  ToolRunItem,
  UpdateModelRequest,
  UploadResult,
} from "@/types/rag";
import { httpDelete, httpGet, httpPatch, httpPost, httpPostForm, httpPut } from "@/lib/http";
import { postSseJson } from "@/lib/sse";

interface AskStreamDonePayload {
  sessionId: string;
  references: AskResult["references"];
  mode: "rag" | "chat-only";
  toolRuns?: ToolRunItem[];
  deepThinkSummary?: string | null;
  deepThinkRuns?: DeepThinkRunItem[];
}

export async function fetchModels(): Promise<ModelItem[]> {
  const res = await httpGet<{ items: ModelItem[] }>("/api/v1/models");
  return res.data.items;
}

export async function fetchModelById(modelId: string): Promise<ModelItem> {
  const res = await httpGet<ModelItem>(`/api/v1/models/${encodeURIComponent(modelId)}`);
  return res.data;
}

export async function createModel(payload: CreateModelRequest): Promise<ModelItem> {
  const res = await httpPost<ModelItem, CreateModelRequest>("/api/v1/models", payload);
  return res.data;
}

export async function updateModel(
  modelId: string,
  payload: UpdateModelRequest,
): Promise<ModelItem> {
  const res = await httpPut<ModelItem, UpdateModelRequest>(
    `/api/v1/models/${encodeURIComponent(modelId)}`,
    payload,
  );
  return res.data;
}

export async function updateModelStatus(
  modelId: string,
  status: "online" | "offline",
): Promise<ModelItem> {
  const res = await httpPatch<ModelItem, { status: "online" | "offline" }>(
    `/api/v1/models/${encodeURIComponent(modelId)}/status`,
    { status },
  );
  return res.data;
}

export async function deleteModel(modelId: string): Promise<{ removed: ModelItem }> {
  const res = await httpDelete<{ removed: ModelItem }>(
    `/api/v1/models/${encodeURIComponent(modelId)}`,
  );
  return res.data;
}

export interface TestModelResult {
  success: boolean;
  capability: string;
  latency_ms: number;
  message: string;
  detail?: string;
}

export async function testModel(modelId: string): Promise<TestModelResult> {
  const res = await httpPost<TestModelResult, void>(
    `/api/v1/models/${encodeURIComponent(modelId)}/test`,
    undefined as unknown as void,
  );
  return res.data;
}

export async function askQuestion(payload: AskRequest): Promise<AskResult> {
  const res = await httpPost<AskResult, AskRequest>("/api/v1/chat/ask", payload);
  return res.data;
}

export async function askQuestionStream(
  payload: AskRequest,
  handlers: {
    onChunk: (text: string) => void;
    onDone: (result: AskStreamDonePayload) => void;
  },
): Promise<void> {
  return postSseJson<AskRequest, AskStreamDonePayload>(
    "/api/v1/chat/ask-stream",
    payload,
    handlers,
  );
}

export async function splitPreview(
  payload: SplitPreviewRequest,
): Promise<{ items: ChunkPreview[]; total: number }> {
  const res = await httpPost<{ items: ChunkPreview[]; total: number }, SplitPreviewRequest>(
    "/api/v1/documents/split-preview",
    payload,
  );
  return res.data;
}

export async function uploadDocument(
  file: File,
  strategy: SplitStrategy = "fixed",
): Promise<UploadResult> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("strategy", strategy);

  const res = await httpPostForm<UploadResult>("/api/v1/documents/upload", formData);
  return res.data;
}

export async function fetchDocuments(
  status?: string,
  limit = 50,
): Promise<DocumentListResult> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (status) {
    params.set("status", status);
  }
  const res = await httpGet<DocumentListResult>(`/api/v1/documents?${params.toString()}`);
  return res.data;
}

export async function deleteDocument(documentId: string): Promise<{ deleted: boolean; documentId: string; fileName: string }> {
  const res = await httpDelete<{ deleted: boolean; documentId: string; fileName: string }>(
    `/api/v1/documents/${encodeURIComponent(documentId)}`,
  );
  return res.data;
}

export interface DocumentChunksResult {
  documentId: string;
  fileName: string;
  status: string;
  chunks: Array<{
    chunkId: string;
    chunkIndex: number;
    content: string;
    tokenCount: number;
    length: number;
    createdAt: string | null;
    nodeId?: string | null;
    nodePath?: string | null;
    level?: number | null;
    pageStart?: number | null;
    pageEnd?: number | null;
    charStart?: number | null;
    charEnd?: number | null;
    sectionTitle?: string | null;
  }>;
  total: number;
}

export async function fetchDocumentChunks(
  documentId: string,
  limit = 50,
  offset = 0,
): Promise<DocumentChunksResult> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  const res = await httpGet<DocumentChunksResult>(
    `/api/v1/documents/${encodeURIComponent(documentId)}/chunks?${params.toString()}`,
  );
  return res.data;
}

export async function fetchDocumentStatus(
  documentId: string,
): Promise<DocumentStatusResult> {
  const res = await httpGet<DocumentStatusResult>(
    `/api/v1/documents/${encodeURIComponent(documentId)}/status`,
  );
  return res.data;
}

export async function importDocumentFromToolRun(payload: {
  toolRunId: number;
  title: string;
  tags?: string[];
  strategy?: SplitStrategy;
}): Promise<UploadResult & { sourceToolRunId: number }> {
  const res = await httpPost<UploadResult & { sourceToolRunId: number }, typeof payload>(
    "/api/v1/documents/import-from-tool-run",
    payload,
  );
  return res.data;
}

export async function fetchConsumptionLogs(limit = 50): Promise<ConsumptionLogsResult> {
  const query = new URLSearchParams({ limit: String(limit) });
  const res = await httpGet<ConsumptionLogsResult>(
    `/api/v1/observability/consumption-logs?${query.toString()}`,
  );
  return res.data;
}

export async function fetchConsumptionLogsByQuery(
  payload: ConsumptionLogsQuery,
): Promise<ConsumptionLogsResult> {
  const query = new URLSearchParams();
  query.set("limit", String(payload.limit ?? 50));
  if (payload.modelId && payload.modelId.trim()) {
    query.set("modelId", payload.modelId.trim());
  }
  if (payload.status && payload.status !== "all") {
    query.set("status", payload.status);
  }
  if (payload.keyword && payload.keyword.trim()) {
    query.set("keyword", payload.keyword.trim());
  }
  const res = await httpGet<ConsumptionLogsResult>(
    `/api/v1/observability/consumption-logs?${query.toString()}`,
  );
  return res.data;
}

export async function fetchToolRuns(limit = 100): Promise<{ items: ToolRunItem[]; total: number }> {
  const query = new URLSearchParams({ limit: String(limit) });
  const res = await httpGet<{ items: ToolRunItem[]; total: number }>(
    `/api/v1/observability/tool-runs?${query.toString()}`,
  );
  return res.data;
}

export async function fetchDeepThinkRuns(
  limit = 100,
): Promise<{ items: DeepThinkRunItem[]; total: number }> {
  const query = new URLSearchParams({ limit: String(limit) });
  const res = await httpGet<{ items: DeepThinkRunItem[]; total: number }>(
    `/api/v1/observability/deep-think-runs?${query.toString()}`,
  );
  return res.data;
}

export async function fetchMcpServers(): Promise<McpServerItem[]> {
  const res = await httpGet<{ items: McpServerItem[] }>("/api/v1/mcp/servers");
  return res.data.items;
}

export async function createMcpServer(payload: {
  serverKey: string;
  name: string;
  endpoint: string;
  sourceType?: string;
  authType?: string;
  authConfig?: Record<string, unknown>;
  timeoutMs?: number;
}): Promise<McpServerItem> {
  const res = await httpPost<McpServerItem, typeof payload>("/api/v1/mcp/servers", payload);
  return res.data;
}

export async function updateMcpServer(
  serverKey: string,
  payload: {
    name?: string;
    endpoint?: string;
    enabled?: boolean;
    authType?: string;
    authConfig?: Record<string, unknown>;
    timeoutMs?: number;
  },
): Promise<McpServerItem> {
  const res = await httpPatch<McpServerItem, typeof payload>(
    `/api/v1/mcp/servers/${encodeURIComponent(serverKey)}`,
    payload,
  );
  return res.data;
}

export async function syncMcpServerTools(serverKey: string): Promise<McpSyncResult> {
  const res = await httpPost<McpSyncResult, void>(
    `/api/v1/mcp/servers/${encodeURIComponent(serverKey)}/sync-tools`,
    undefined as unknown as void,
  );
  return res.data;
}

export async function fetchMcpTools(): Promise<McpToolItem[]> {
  const res = await httpGet<{ items: McpToolItem[] }>("/api/v1/mcp/tools");
  return res.data.items;
}

export async function updateMcpToolStatus(toolName: string, enabled: boolean): Promise<McpToolItem> {
  const res = await httpPatch<McpToolItem, { enabled: boolean }>(
    `/api/v1/mcp/tools/${encodeURIComponent(toolName)}/status`,
    { enabled },
  );
  return res.data;
}

// ============== 聊天历史 API ==============

export async function fetchChatSessions(limit = 20, offset = 0): Promise<ChatSessionsResult> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  const res = await httpGet<ChatSessionsResult>(`/api/v1/chat/sessions?${params.toString()}`);
  return res.data;
}

export async function fetchSessionMessages(sessionId: string): Promise<ChatSessionMessages> {
  const res = await httpGet<ChatSessionMessages>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
  );
  return res.data;
}

export async function deleteChatSession(sessionId: string): Promise<{ deleted: boolean }> {
  const res = await httpDelete<{ deleted: boolean }>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`,
  );
  return res.data;
}

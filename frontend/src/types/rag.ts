export type ModelCapability = "chat" | "embedding" | "rerank";
export type ModelStatus = "online" | "offline";
export type SplitStrategy =
  | "fixed"
  | "sentence"
  | "paragraph"
  | "parent_child"
  | "pageindex";

export interface ApiResponse<T> {
  code: number;
  message: string;
  data: T;
  traceId: string;
}

export interface ModelItem {
  id: string;
  name: string;
  provider: string;
  capabilities: ModelCapability[];
  status: ModelStatus;
  maxTokens: number;
  baseUrl: string;
  apiKey: string;
}

export interface CreateModelRequest {
  id: string;
  name: string;
  provider: string;
  capabilities: ModelCapability[];
  status: ModelStatus;
  maxTokens: number;
  baseUrl: string;
  apiKey: string;
}

export interface UpdateModelRequest {
  name: string;
  provider: string;
  capabilities: ModelCapability[];
  status: ModelStatus;
  maxTokens: number;
  baseUrl: string;
  apiKey: string;
}

export interface AskRequest {
  question: string;
  modelId: string;
  sessionId?: string;
  embeddingModelId?: string;
  documentIds?: string[]; // 可选：限定检索的文档范围
  useRag?: boolean;
  enableTools?: boolean;
  enableDeepThink?: boolean;
  maxToolSteps?: number;
}

export interface ToolRunItem {
  toolName: string;
  source: "builtin" | "external" | string;
  status: "success" | "failed";
  latencyMs: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  inputSummary: string;
  outputSummary: string;
  outputPayload: Record<string, unknown>;
  errorMessage?: string | null;
}

export interface DeepThinkRunItem {
  stage: string;
  status: "success" | "failed";
  latencyMs: number;
  inputSummary: string;
  outputSummary: string;
  payload: Record<string, unknown>;
  errorMessage?: string | null;
}

export interface AskResult {
  answer: string;
  sessionId: string;
  references: Array<{
    documentId: string;
    documentName: string;
    chunkId: string;
    score: number;
  }>;
  toolRuns?: ToolRunItem[];
  deepThinkSummary?: string | null;
  deepThinkRuns?: DeepThinkRunItem[];
}

export interface SplitPreviewRequest {
  content: string;
  chunkSize: number;
  overlap: number;
  strategy?: SplitStrategy;
}

export interface ChunkPreview {
  chunkId: string;
  start: number;
  end: number;
  length: number;
  content: string;
  parentChunkId?: string;
  parentStart?: number;
  parentEnd?: number;
  parentLength?: number;
  nodeId?: string;
  nodePath?: string;
  level?: number;
  pageStart?: number;
  pageEnd?: number;
  charStart?: number;
  charEnd?: number;
  sectionTitle?: string;
}

export interface UploadResult {
  taskId: string;
  documentId: string;
  fileName: string;
  fileSizeBytes: number;
  strategy: SplitStrategy;
  status: string;
}

export interface DocumentItem {
  documentId: string;
  fileName: string;
  source: string;
  status: string;
  taskId?: string | null;
  strategy?: SplitStrategy | null;
  fileSizeBytes: number;
  traceId?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface DocumentListResult {
  items: DocumentItem[];
  total: number;
}

export interface DocumentStatusResult {
  documentId: string;
  fileName: string;
  status: string;
  taskId?: string | null;
  strategy?: SplitStrategy | null;
  createdAt: string;
  updatedAt: string;
}

export interface SkillCallLogItem {
  skillName: string;
  status: "success" | "failed";
  latencyMs: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  inputSummary: string;
  outputSummary: string;
  errorMessage?: string | null;
  createdAt: string;
}

export interface ConsumptionLogItem {
  id: number;
  traceId: string;
  sessionId?: string | null;
  question: string;
  modelId: string;
  topK: number;
  threshold: number;
  latencyMs: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  mcpCallCount: number;
  status: "success" | "failed";
  errorMessage?: string | null;
  references: AskResult["references"];
  skillCalls: SkillCallLogItem[];
  createdAt: string;
}

export interface ConsumptionLogsResult {
  items: ConsumptionLogItem[];
  total: number;
}

export interface ConsumptionLogsQuery {
  limit?: number;
  modelId?: string;
  status?: "all" | "success" | "failed";
  keyword?: string;
}

export interface McpServerItem {
  serverKey: string;
  name: string;
  sourceType: string;
  endpoint: string;
  authType: string;
  authConfig: Record<string, unknown>;
  enabled: boolean;
  timeoutMs: number;
}

export interface McpToolItem {
  toolName: string;
  displayName: string;
  description: string;
  source: "builtin" | "external" | string;
  serverKey?: string | null;
  toolSchema: Record<string, unknown>;
  enabled: boolean;
}

export interface McpSyncResult {
  serverKey: string;
  syncedCount: number;
  items: McpToolItem[];
}

// ============== 聊天历史相关 ==============

export interface ChatSession {
  sessionId: string;
  modelId: string;
  title: string;
  useRag: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  references: AskResult["references"];
  createdAt: string;
}

export interface ChatSessionMessages {
  sessionId: string;
  messages: ChatMessage[];
}

export interface ChatSessionsResult {
  items: ChatSession[];
  total: number;
}

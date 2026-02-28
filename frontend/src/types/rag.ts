export type ModelCapability = "chat" | "embedding" | "rerank";
export type ModelStatus = "online" | "offline";

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
}

export interface SplitPreviewRequest {
  content: string;
  chunkSize: number;
  overlap: number;
}

export interface ChunkPreview {
  chunkId: string;
  start: number;
  end: number;
  length: number;
  content: string;
}

export interface UploadResult {
  taskId: string;
  fileName: string;
  strategy: string;
  status: string;
}

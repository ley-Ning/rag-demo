import {
  AskRequest,
  AskResult,
  ChunkPreview,
  CreateModelRequest,
  ModelItem,
  SplitPreviewRequest,
  UpdateModelRequest,
  UploadResult,
} from "@/types/rag";
import { httpDelete, httpGet, httpPatch, httpPost, httpPostForm, httpPut } from "@/lib/http";

export async function fetchModels(): Promise<ModelItem[]> {
  const res = await httpGet<{ items: ModelItem[] }>("/api/v1/models");
  return res.data.items;
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

export async function askQuestion(payload: AskRequest): Promise<AskResult> {
  const res = await httpPost<AskResult, AskRequest>("/api/v1/chat/ask", payload);
  return res.data;
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
  strategy = "default",
): Promise<UploadResult> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("strategy", strategy);

  const res = await httpPostForm<UploadResult>("/api/v1/documents/upload", formData);
  return res.data;
}

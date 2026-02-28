import { ApiResponse } from "@/types/rag";

const JSON_HEADERS = {
  "content-type": "application/json",
};

function createTraceId() {
  return `trace-${crypto.randomUUID()}`;
}

async function parseEnvelope<T>(response: Response): Promise<ApiResponse<T>> {
  const payload = (await response.json()) as ApiResponse<T>;
  if (!response.ok || payload.code !== 0) {
    throw new Error(payload.message || "请求失败");
  }
  return payload;
}

export async function httpGet<T>(url: string): Promise<ApiResponse<T>> {
  const response = await fetch(url, {
    headers: {
      "x-trace-id": createTraceId(),
    },
    cache: "no-store",
  });

  return parseEnvelope<T>(response);
}

export async function httpPost<T, B = unknown>(
  url: string,
  body: B,
): Promise<ApiResponse<T>> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      ...JSON_HEADERS,
      "x-trace-id": createTraceId(),
    },
    body: JSON.stringify(body),
  });

  return parseEnvelope<T>(response);
}

export async function httpPut<T, B = unknown>(
  url: string,
  body: B,
): Promise<ApiResponse<T>> {
  const response = await fetch(url, {
    method: "PUT",
    headers: {
      ...JSON_HEADERS,
      "x-trace-id": createTraceId(),
    },
    body: JSON.stringify(body),
  });

  return parseEnvelope<T>(response);
}

export async function httpPatch<T, B = unknown>(
  url: string,
  body: B,
): Promise<ApiResponse<T>> {
  const response = await fetch(url, {
    method: "PATCH",
    headers: {
      ...JSON_HEADERS,
      "x-trace-id": createTraceId(),
    },
    body: JSON.stringify(body),
  });

  return parseEnvelope<T>(response);
}

export async function httpDelete<T>(url: string): Promise<ApiResponse<T>> {
  const response = await fetch(url, {
    method: "DELETE",
    headers: {
      "x-trace-id": createTraceId(),
    },
  });

  return parseEnvelope<T>(response);
}

export async function httpPostForm<T>(
  url: string,
  formData: FormData,
): Promise<ApiResponse<T>> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "x-trace-id": createTraceId(),
    },
    body: formData,
  });

  return parseEnvelope<T>(response);
}

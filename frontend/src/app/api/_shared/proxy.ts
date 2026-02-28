import { NextRequest, NextResponse } from "next/server";

const PYTHON_BASE_URL = process.env.RAG_PYTHON_URL ?? "http://127.0.0.1:8090";

function traceIdFromRequest(request: NextRequest) {
  return request.headers.get("x-trace-id") ?? `trace-${crypto.randomUUID()}`;
}

async function parsePayload(response: Response) {
  try {
    return await response.json();
  } catch {
    return {
      code: response.status,
      message: "后端返回了非 JSON 内容",
      data: null,
      traceId: "proxy-parse-error",
    };
  }
}

export async function proxyJson(
  request: NextRequest,
  path: string,
  method = "GET",
): Promise<NextResponse> {
  const traceId = traceIdFromRequest(request);
  const hasJsonBody = method === "POST" || method === "PUT" || method === "PATCH";
  const body = hasJsonBody ? await request.text() : undefined;

  const upstream = await fetch(`${PYTHON_BASE_URL}${path}`, {
    method,
    headers: {
      ...(hasJsonBody ? { "content-type": "application/json" } : {}),
      "x-trace-id": traceId,
    },
    body,
    cache: "no-store",
  });

  const payload = await parsePayload(upstream);
  return NextResponse.json(payload, { status: upstream.status });
}

export async function proxyFormData(
  request: NextRequest,
  path: string,
): Promise<NextResponse> {
  const traceId = traceIdFromRequest(request);
  const formData = await request.formData();

  const upstream = await fetch(`${PYTHON_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "x-trace-id": traceId,
    },
    body: formData,
    cache: "no-store",
  });

  const payload = await parsePayload(upstream);
  return NextResponse.json(payload, { status: upstream.status });
}

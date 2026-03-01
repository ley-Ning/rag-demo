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
  const query = request.nextUrl.search ?? "";
  const upstreamUrl = `${PYTHON_BASE_URL}${path}${query}`;

  const upstream = await fetch(upstreamUrl, {
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

export async function proxySse(
  request: NextRequest,
  path: string,
  method = "POST",
): Promise<Response> {
  const traceId = traceIdFromRequest(request);
  const body = await request.text();
  const query = request.nextUrl.search ?? "";
  const upstreamUrl = `${PYTHON_BASE_URL}${path}${query}`;

  const upstream = await fetch(upstreamUrl, {
    method,
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
      "x-trace-id": traceId,
    },
    body,
    cache: "no-store",
  });

  if (!upstream.ok || !upstream.body) {
    const payload = await parsePayload(upstream);
    return NextResponse.json(payload, { status: upstream.status });
  }

  return new NextResponse(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-trace-id": traceId,
    },
  });
}

export async function proxyBinary(
  request: NextRequest,
  path: string,
  method = "GET",
): Promise<Response> {
  const traceId = traceIdFromRequest(request);
  const query = request.nextUrl.search ?? "";
  const upstreamUrl = `${PYTHON_BASE_URL}${path}${query}`;

  const upstream = await fetch(upstreamUrl, {
    method,
    headers: {
      "x-trace-id": traceId,
    },
    cache: "no-store",
  });

  if (!upstream.ok) {
    const payload = await parsePayload(upstream);
    return NextResponse.json(payload, { status: upstream.status });
  }

  const headers = new Headers();
  const contentType = upstream.headers.get("content-type");
  const contentDisposition = upstream.headers.get("content-disposition");
  const contentLength = upstream.headers.get("content-length");

  if (contentType) {
    headers.set("content-type", contentType);
  }
  if (contentDisposition) {
    headers.set("content-disposition", contentDisposition);
  }
  if (contentLength) {
    headers.set("content-length", contentLength);
  }
  headers.set("cache-control", "no-store");
  headers.set("x-trace-id", traceId);

  return new Response(upstream.body, {
    status: upstream.status,
    headers,
  });
}

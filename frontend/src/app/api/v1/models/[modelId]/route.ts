import { NextRequest } from "next/server";

import { proxyJson } from "@/app/api/_shared/proxy";

type RouteContext = {
  params: Promise<{ modelId: string }>;
};

export async function GET(request: NextRequest, context: RouteContext) {
  const { modelId } = await context.params;
  return proxyJson(request, `/api/v1/models/${encodeURIComponent(modelId)}`, "GET");
}

export async function PUT(request: NextRequest, context: RouteContext) {
  const { modelId } = await context.params;
  return proxyJson(request, `/api/v1/models/${encodeURIComponent(modelId)}`, "PUT");
}

export async function DELETE(request: NextRequest, context: RouteContext) {
  const { modelId } = await context.params;
  return proxyJson(request, `/api/v1/models/${encodeURIComponent(modelId)}`, "DELETE");
}

import { NextRequest } from "next/server";

import { proxyJson } from "@/app/api/_shared/proxy";

interface Params {
  params: Promise<{ toolName: string }>;
}

export async function PATCH(request: NextRequest, { params }: Params) {
  const { toolName } = await params;
  return proxyJson(
    request,
    `/api/v1/mcp/tools/${encodeURIComponent(toolName)}/status`,
    "PATCH",
  );
}


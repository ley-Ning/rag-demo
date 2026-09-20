import { NextRequest } from "next/server";

import { proxyJson } from "@/app/api/_shared/proxy";

type RouteContext = {
  params: Promise<{ toolName: string }>;
};

export async function POST(request: NextRequest, context: RouteContext) {
  const { toolName } = await context.params;
  return proxyJson(
    request,
    `/api/v1/mcp/tools/${encodeURIComponent(toolName)}/invoke`,
    "POST",
  );
}

import { NextRequest } from "next/server";

import { proxyJson } from "@/app/api/_shared/proxy";

type RouteContext = {
  params: Promise<{ modelId: string }>;
};

export async function PATCH(request: NextRequest, context: RouteContext) {
  const { modelId } = await context.params;
  return proxyJson(
    request,
    `/api/v1/models/${encodeURIComponent(modelId)}/status`,
    "PATCH",
  );
}

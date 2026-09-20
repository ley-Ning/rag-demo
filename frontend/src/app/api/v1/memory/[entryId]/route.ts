import { NextRequest } from "next/server";

import { proxyJson } from "@/app/api/_shared/proxy";

type RouteContext = {
  params: Promise<{ entryId: string }>;
};

export async function PATCH(request: NextRequest, context: RouteContext) {
  const { entryId } = await context.params;
  return proxyJson(request, `/api/v1/memory/${encodeURIComponent(entryId)}`, "PATCH");
}

export async function DELETE(request: NextRequest, context: RouteContext) {
  const { entryId } = await context.params;
  return proxyJson(request, `/api/v1/memory/${encodeURIComponent(entryId)}`, "DELETE");
}

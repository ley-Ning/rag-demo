import { NextRequest } from "next/server";

import { proxyJson } from "@/app/api/_shared/proxy";

type RouteContext = {
  params: Promise<{ documentId: string }>;
};

export async function DELETE(request: NextRequest, context: RouteContext) {
  const { documentId } = await context.params;
  return proxyJson(request, `/api/v1/documents/${encodeURIComponent(documentId)}`, "DELETE");
}

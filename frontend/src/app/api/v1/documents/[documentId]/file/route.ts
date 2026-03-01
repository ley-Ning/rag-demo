import { NextRequest } from "next/server";

import { proxyBinary } from "@/app/api/_shared/proxy";

type RouteContext = {
  params: Promise<{ documentId: string }>;
};

export async function GET(request: NextRequest, context: RouteContext) {
  const { documentId } = await context.params;
  return proxyBinary(request, `/api/v1/documents/${encodeURIComponent(documentId)}/file`, "GET");
}

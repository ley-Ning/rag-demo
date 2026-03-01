import { NextRequest } from "next/server";

import { proxyJson } from "@/app/api/_shared/proxy";

interface Params {
  params: Promise<{ serverKey: string }>;
}

export async function POST(request: NextRequest, { params }: Params) {
  const { serverKey } = await params;
  return proxyJson(
    request,
    `/api/v1/mcp/servers/${encodeURIComponent(serverKey)}/sync-tools`,
    "POST",
  );
}


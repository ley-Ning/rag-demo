import { NextRequest } from "next/server";

import { proxyJson } from "@/app/api/_shared/proxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/api/v1/documents/split-preview", "POST");
}

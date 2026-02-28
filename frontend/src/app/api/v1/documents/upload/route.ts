import { NextRequest } from "next/server";

import { proxyFormData } from "@/app/api/_shared/proxy";

export async function POST(request: NextRequest) {
  return proxyFormData(request, "/api/v1/documents/upload");
}

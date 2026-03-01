import { NextRequest } from "next/server";
import { proxyJson } from "@/app/api/_shared/proxy";

export async function GET(request: NextRequest) {
  return proxyJson(request, "/api/v1/observability/consumption-logs", "GET");
}

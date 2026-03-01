import { NextRequest } from "next/server";

import { proxySse } from "@/app/api/_shared/proxy";

export async function POST(request: NextRequest) {
  return proxySse(request, "/api/v1/chat/ask-stream", "POST");
}

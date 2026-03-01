import { NextRequest } from "next/server";
import { proxyJson } from "@/app/api/_shared/proxy";

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
) {
  const { sessionId } = await params;
  return proxyJson(request, `/api/v1/chat/sessions/${sessionId}`, "DELETE");
}

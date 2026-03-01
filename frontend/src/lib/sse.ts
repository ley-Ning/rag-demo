type SseHandlers<TDone = Record<string, unknown>> = {
  onChunk: (text: string) => void;
  onDone: (data: TDone) => void;
};

function parseSseBlock(block: string): { event: string; data: string } | null {
  const trimmed = block.trim();
  if (!trimmed) {
    return null;
  }

  let event = "message";
  const dataLines: string[] = [];

  for (const line of trimmed.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }

  return {
    event,
    data: dataLines.join("\n"),
  };
}

export async function postSseJson<TPayload, TDone = Record<string, unknown>>(
  url: string,
  payload: TPayload,
  handlers: SseHandlers<TDone>,
): Promise<void> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
      "x-trace-id": `trace-${crypto.randomUUID()}`,
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    try {
      const errorPayload = (await response.json()) as { message?: string };
      throw new Error(errorPayload.message || "流式请求失败");
    } catch {
      throw new Error("流式请求失败");
    }
  }

  if (!response.body) {
    throw new Error("浏览器不支持流式响应");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });

    while (true) {
      const boundary = buffer.indexOf("\n\n");
      if (boundary < 0) {
        break;
      }

      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const parsed = parseSseBlock(block);
      if (!parsed) {
        continue;
      }

      const eventData = parsed.data ? JSON.parse(parsed.data) : {};
      if (parsed.event === "chunk") {
        handlers.onChunk(String((eventData as { text?: string }).text || ""));
      } else if (parsed.event === "done") {
        handlers.onDone(eventData as TDone);
        return;
      } else if (parsed.event === "error") {
        const message = String(
          (eventData as { message?: string }).message || "流式问答失败",
        );
        throw new Error(message);
      }
    }
  }

  throw new Error("流式响应中断，请重试");
}

"""本地 Mock Azure OpenAI 服务，用于无真实密钥环境下联调与测试。

用法：
    .venv/bin/python scripts/mock_azure_openai.py            # 默认 127.0.0.1:8091
    .venv/bin/python scripts/mock_azure_openai.py 9000       # 自定义端口

实现 Azure OpenAI 风格接口：
    POST /openai/deployments/{deployment}/embeddings?api-version=...
    POST /openai/deployments/{deployment}/chat/completions?api-version=...

行为：
- embeddings：确定性词袋向量（拉丁词 + CJK 单字），L2 归一化。
  相同字符集合的不同说法会得到相近向量，可用于语义缓存测试。
- chat/completions：回答固定为 "【模拟回答】…[1]"，支持 stream=true 的 SSE。
  通过环境变量 MOCK_LLM_DELAY_MS 注入延迟可模拟慢模型，对比缓存收益。
"""

import asyncio
import hashlib
import math
import os
import re
import sys
from typing import Any

import uvicorn
from fastapi import FastAPI, Header
from fastapi.responses import StreamingResponse

DIM = 1536
LATIN_RE = re.compile(r"[a-z0-9]+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
ENV_LLM_DELAY_MS = int(os.environ.get("MOCK_LLM_DELAY_MS", "0") or 0)

app = FastAPI(title="Mock Azure OpenAI")


def embed_text(text: str) -> list[float]:
    lowered = text.lower()
    tokens: list[str] = LATIN_RE.findall(lowered)
    tokens.extend(CJK_RE.findall(lowered))
    vector = [0.0] * DIM
    for token in tokens:
        index = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % DIM
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 8) for value in vector]


def token_usage(prompt_tokens: int, completion_tokens: int) -> dict[str, int]:
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/openai/deployments/{deployment}/embeddings")
async def embeddings(deployment: str, body: dict[str, Any]) -> dict[str, Any]:
    inputs = body.get("input") or []
    if isinstance(inputs, str):
        inputs = [inputs]
    data = [
        {"object": "embedding", "index": i, "embedding": embed_text(str(text))}
        for i, text in enumerate(inputs)
    ]
    total_chars = sum(len(str(text)) for text in inputs)
    return {
        "object": "list",
        "data": data,
        "model": deployment,
        "usage": token_usage(total_chars, 0),
    }


@app.post("/openai/deployments/{deployment}/chat/completions")
async def chat_completions(
    deployment: str,
    body: dict[str, Any],
    x_mock_llm_delay_ms: int = Header(default=0),
) -> Any:
    messages = body.get("messages") or []
    system_content = ""
    for message in messages:
        if message.get("role") == "system":
            system_content = str(message.get("content", ""))
            break
    user_question = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            user_question = str(message.get("content", ""))[:120]
            break

    # 追问改写请求（RAG 多轮记忆链路）：透传原问题，便于验证改写调用已发生
    if "改写" in system_content:
        rewrite_answer = user_question.strip() or "（空问题）"
        prompt_tokens = sum(len(str(m.get("content", ""))) for m in messages)
        return {
            "id": "mock-chatcmpl-rewrite",
            "object": "chat.completion",
            "model": deployment,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": rewrite_answer},
                    "finish_reason": "stop",
                }
            ],
            "usage": token_usage(prompt_tokens, len(rewrite_answer)),
        }

    # 回显对话历史摘要，验证多轮记忆确实送达模型
    history_messages = [m for m in messages if m.get("role") in ("user", "assistant")][:-1]
    history_note = ""
    if history_messages:
        last_user = next(
            (
                str(m.get("content", ""))[:24]
                for m in reversed(history_messages)
                if m.get("role") == "user"
            ),
            "",
        )
        last_assistant = next(
            (
                str(m.get("content", ""))[:24]
                for m in reversed(history_messages)
                if m.get("role") == "assistant"
            ),
            "",
        )
        history_note = (
            f"（已带入{len(history_messages)}条历史｜近user:{last_user}｜近assistant:{last_assistant}）"
        )

    answer = (
        f"【模拟回答】关于「{user_question}」的要点：知识库检索命中相关上下文，综合整理如下 [1]。{history_note}"
    )

    prompt_tokens = sum(len(str(m.get("content", ""))) for m in messages)
    usage = token_usage(prompt_tokens, len(answer))

    delay_ms = max(x_mock_llm_delay_ms, ENV_LLM_DELAY_MS)
    if delay_ms > 0:
        await asyncio.sleep(min(delay_ms, 30000) / 1000)

    if body.get("stream"):
        async def stream_chunks():
            chunk_size = 16
            for i in range(0, len(answer), chunk_size):
                yield (
                    'data: {"choices": [{"index": 0, "delta": {"content": '
                    + _json_str(answer[i : i + chunk_size])
                    + "}}]}\n\n"
                )
            yield (
                'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], '
                '"usage": ' + str(usage).replace("'", '"') + "}\n\n"
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_chunks(), media_type="text/event-stream")

    return {
        "id": "mock-chatcmpl",
        "object": "chat.completion",
        "model": deployment,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


def _json_str(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8091
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()

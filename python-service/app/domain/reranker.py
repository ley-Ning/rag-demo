"""交叉重排服务：用 bge-reranker 类模型对候选分块按 query 精排。

协议采用 Cohere/Jina 风格的 /rerank HTTP 接口（TEI、vLLM 等常见部署形态均兼容）：
    POST {base_url}/rerank
    {"model": ..., "query": ..., "documents": [...], "top_n": n}
    -> {"results": [{"index": 0, "relevance_score": 0.87}, ...]}

无在线 rerank 模型或调用失败时由调用方降级（保持向量序），绝不阻断检索主链路。
"""

import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.domain.models_registry import ModelInfo, ModelRegistry

logger = logging.getLogger(__name__)


class RerankerService:
    def __init__(self) -> None:
        self._settings = get_settings()

    def resolve_model(self, registry: ModelRegistry) -> ModelInfo | None:
        """取第一个在线、具备 rerank 能力且配置了 baseUrl 的模型"""
        for item in registry.list_models():
            caps = item.get("capabilities", [])
            model_id = str(item.get("id", "")).strip()
            base_url = str(item.get("baseUrl", "") or "").strip()
            status = str(item.get("status", "")).strip().lower()
            if status == "online" and isinstance(caps, list) and "rerank" in caps and base_url and model_id:
                return registry.get_model(model_id)
        return None

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int | None = None,
        model: ModelInfo,
    ) -> list[tuple[int, float]]:
        """返回 [(候选下标, 相关性分)]，按分数降序。

        调用失败抛异常，由上层降级为原序。
        """
        if not documents:
            return []
        timeout_sec = max(2, min(self._settings.rag_rerank_timeout_sec, 60))
        endpoint = f"{model.base_url.rstrip('/')}/rerank"
        headers = {"Content-Type": "application/json"}
        api_key = str(getattr(model, "api_key", "") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict[str, Any] = {
            "model": model.model_id,
            "query": query,
            "documents": documents,
        }
        if top_n:
            payload["top_n"] = top_n

        async with httpx.AsyncClient(timeout=timeout_sec, headers=headers) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            body = response.json()

        raw_results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(raw_results, list):
            raise ValueError("rerank 响应缺少 results 数组")

        ranked: list[tuple[int, float]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item["index"])
                score = float(item.get("relevance_score", item.get("score", 0.0)))
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= index < len(documents):
                ranked.append((index, score))
        ranked.sort(key=lambda pair: pair[1], reverse=True)
        return ranked


_reranker_service = RerankerService()


def get_reranker_service() -> RerankerService:
    return _reranker_service

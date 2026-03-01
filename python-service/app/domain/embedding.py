import logging
from dataclasses import dataclass
from typing import Any

from openai import AsyncAzureOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.domain.models_registry import ModelInfo, ModelRegistry

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingUsage:
    """Embedding 调用 token 统计"""

    prompt_tokens: int
    total_tokens: int


class EmbeddingService:
    """Embedding 服务，封装 Azure OpenAI Embedding API"""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: AsyncAzureOpenAI | None = None

    def _get_client(self, model: ModelInfo | None = None) -> AsyncAzureOpenAI:
        """获取 Azure OpenAI 客户端"""
        if model and model.base_url and model.api_key:
            # 使用模型注册表中的配置
            return AsyncAzureOpenAI(
                api_key=model.api_key,
                azure_endpoint=model.base_url,
                api_version=self._settings.azure_openai_api_version,
            )

        # 使用默认配置
        if self._client is None:
            self._client = AsyncAzureOpenAI(
                api_key=self._settings.azure_openai_api_key,
                azure_endpoint=self._settings.azure_openai_endpoint,
                api_version=self._settings.azure_openai_api_version,
            )
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def embed_texts(
        self,
        texts: list[str],
        model_id: str,
        registry: ModelRegistry,
    ) -> tuple[list[list[float]], EmbeddingUsage]:
        """
        将文本列表转换为向量

        Args:
            texts: 文本列表
            model_id: embedding 模型 ID
            registry: 模型注册表

        Returns:
            (向量列表, token 使用量)
        """
        if not texts:
            return [], EmbeddingUsage(prompt_tokens=0, total_tokens=0)

        # 获取模型配置
        model = registry.get_model(model_id)
        client = self._get_client(model)

        # 获取 Azure 部署名称（使用 model_id 作为部署名称）
        deployment_name = model_id

        logger.debug(f"Generating embeddings for {len(texts)} texts using {model_id}")

        response = await client.embeddings.create(input=texts, model=deployment_name)

        embeddings = [item.embedding for item in response.data]
        usage = EmbeddingUsage(
            prompt_tokens=(response.usage.prompt_tokens if response.usage else 0) or 0,
            total_tokens=(response.usage.total_tokens if response.usage else 0) or 0,
        )
        logger.debug(
            "Generated %s embeddings, dimension=%s, prompt_tokens=%s, total_tokens=%s",
            len(embeddings),
            len(embeddings[0]) if embeddings else 0,
            usage.prompt_tokens,
            usage.total_tokens,
        )

        return embeddings, usage

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def embed_single(
        self,
        text: str,
        model_id: str,
        registry: ModelRegistry,
    ) -> list[float]:
        """
        将单个文本转换为向量

        Args:
            text: 文本内容
            model_id: embedding 模型 ID
            registry: 模型注册表

        Returns:
            向量
        """
        embeddings, _ = await self.embed_texts([text], model_id, registry)
        return embeddings[0] if embeddings else []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def embed_single_with_usage(
        self,
        text: str,
        model_id: str,
        registry: ModelRegistry,
    ) -> tuple[list[float], EmbeddingUsage]:
        """将单个文本转换为向量，并返回 token 使用统计"""
        embeddings, usage = await self.embed_texts([text], model_id, registry)
        return (embeddings[0] if embeddings else []), usage


# 全局实例
_embedding_service = EmbeddingService()


def get_embedding_service() -> EmbeddingService:
    """获取 Embedding 服务实例"""
    return _embedding_service

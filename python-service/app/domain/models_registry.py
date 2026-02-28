import json
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from app.core.config import get_settings

ALLOWED_CAPABILITIES = {"chat", "embedding", "rerank"}
ALLOWED_STATUS = {"online", "offline"}
MODEL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._:-]{2,64}$")


@dataclass(frozen=True)
class ModelInfo:
    model_id: str
    name: str
    provider: str
    capabilities: tuple[str, ...]
    status: str
    max_tokens: int
    base_url: str
    api_key: str


DEFAULT_MODELS: tuple[dict[str, object], ...] = (
    {
        "id": "gpt-4.1-mini",
        "name": "GPT-4.1 Mini",
        "provider": "openai",
        "capabilities": ["chat"],
        "status": "online",
        "maxTokens": 128000,
        "baseUrl": "",
        "apiKey": "",
    },
    {
        "id": "text-embedding-3-large",
        "name": "Text Embedding 3 Large",
        "provider": "openai",
        "capabilities": ["embedding"],
        "status": "online",
        "maxTokens": 8192,
        "baseUrl": "",
        "apiKey": "",
    },
    {
        "id": "bge-reranker-v2-m3",
        "name": "BGE Reranker V2 M3",
        "provider": "bge",
        "capabilities": ["rerank"],
        "status": "online",
        "maxTokens": 4096,
        "baseUrl": "",
        "apiKey": "",
    },
)


class ModelRegistry:
    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._lock = Lock()
        self._models: dict[str, ModelInfo] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            if self._file_path.exists():
                try:
                    payload = json.loads(self._file_path.read_text(encoding="utf-8"))
                    if not isinstance(payload, list):
                        raise ValueError("models file must be a list")
                    items = [self._normalize(raw) for raw in payload]
                    self._models = {item.model_id: item for item in items}
                    return
                except Exception:
                    self._models = {}

            items = [self._normalize(raw) for raw in DEFAULT_MODELS]
            self._models = {item.model_id: item for item in items}
            self._persist_unlocked()

    def _persist_unlocked(self) -> None:
        serialized = [
            self._to_dict(item)
            for item in sorted(
                self._models.values(),
                key=lambda model: (model.provider.lower(), model.name.lower()),
            )
        ]
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(
            json.dumps(serialized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _normalize(self, raw: dict[str, object]) -> ModelInfo:
        model_id = str(raw.get("id", "")).strip()
        if not MODEL_ID_PATTERN.match(model_id):
            raise ValueError("模型 ID 仅允许字母、数字、.-_:，长度 2-64")

        name = str(raw.get("name", "")).strip()
        if len(name) < 2 or len(name) > 80:
            raise ValueError("模型名称长度需在 2-80 之间")

        provider = str(raw.get("provider", "")).strip()
        if len(provider) < 2 or len(provider) > 40:
            raise ValueError("模型提供商长度需在 2-40 之间")

        raw_caps = raw.get("capabilities")
        if not isinstance(raw_caps, list) or len(raw_caps) == 0:
            raise ValueError("模型能力不能为空")
        caps = sorted({str(cap).strip().lower() for cap in raw_caps if str(cap).strip()})
        if len(caps) == 0:
            raise ValueError("模型能力不能为空")
        invalid_caps = [cap for cap in caps if cap not in ALLOWED_CAPABILITIES]
        if invalid_caps:
            raise ValueError(f"不支持的能力标签: {', '.join(invalid_caps)}")

        status = str(raw.get("status", "")).strip().lower()
        if status not in ALLOWED_STATUS:
            raise ValueError("模型状态仅支持 online/offline")

        try:
            max_tokens = int(raw.get("maxTokens", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("maxTokens 必须是整数") from exc
        if max_tokens < 256 or max_tokens > 10000000:
            raise ValueError("maxTokens 需在 256-10000000 之间")

        base_url = str(raw.get("baseUrl", "")).strip()
        if len(base_url) > 260:
            raise ValueError("baseUrl 长度不能超过 260")

        api_key = str(raw.get("apiKey", "")).strip()
        if len(api_key) > 260:
            raise ValueError("apiKey 长度不能超过 260")

        return ModelInfo(
            model_id=model_id,
            name=name,
            provider=provider,
            capabilities=tuple(caps),
            status=status,
            max_tokens=max_tokens,
            base_url=base_url,
            api_key=api_key,
        )

    @staticmethod
    def _to_dict(model: ModelInfo) -> dict[str, object]:
        return {
            "id": model.model_id,
            "name": model.name,
            "provider": model.provider,
            "capabilities": list(model.capabilities),
            "status": model.status,
            "maxTokens": model.max_tokens,
            "baseUrl": model.base_url,
            "apiKey": model.api_key,
        }

    def list_models(self) -> list[dict[str, object]]:
        with self._lock:
            return [
                self._to_dict(item)
                for item in sorted(
                    self._models.values(),
                    key=lambda model: (model.provider.lower(), model.name.lower()),
                )
            ]

    def create_model(self, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            model = self._normalize(payload)
            if model.model_id in self._models:
                raise ValueError("模型 ID 已存在，请更换后重试")
            self._models[model.model_id] = model
            self._persist_unlocked()
            return self._to_dict(model)

    def update_model(self, model_id: str, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            current = self._models.get(model_id)
            if current is None:
                raise KeyError("模型不存在")

            merged = self._to_dict(current)
            merged.update(payload)
            merged["id"] = model_id
            updated = self._normalize(merged)
            self._models[model_id] = updated
            self._persist_unlocked()
            return self._to_dict(updated)

    def update_model_status(self, model_id: str, status: str) -> dict[str, object]:
        return self.update_model(model_id, {"status": status})

    def delete_model(self, model_id: str) -> dict[str, object]:
        with self._lock:
            current = self._models.get(model_id)
            if current is None:
                raise KeyError("模型不存在")
            deleted = self._to_dict(current)
            del self._models[model_id]
            self._persist_unlocked()
            return deleted

    def model_supports(self, model_id: str, capability: str) -> bool:
        with self._lock:
            current = self._models.get(model_id)
            if current is None:
                return False
            return current.status == "online" and capability in current.capabilities


_registry = ModelRegistry(get_settings().model_registry_path)


def list_models() -> list[dict[str, object]]:
    return _registry.list_models()


def create_model(payload: dict[str, object]) -> dict[str, object]:
    return _registry.create_model(payload)


def update_model(model_id: str, payload: dict[str, object]) -> dict[str, object]:
    return _registry.update_model(model_id, payload)


def update_model_status(model_id: str, status: str) -> dict[str, object]:
    return _registry.update_model_status(model_id, status)


def delete_model(model_id: str) -> dict[str, object]:
    return _registry.delete_model(model_id)


def model_supports(model_id: str, capability: str) -> bool:
    return _registry.model_supports(model_id, capability)

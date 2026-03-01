from fastapi import APIRouter

from app.api.v1.endpoints import chat, documents, health, mcp, models, observability

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(models.router)
api_router.include_router(chat.router)
api_router.include_router(documents.router)
api_router.include_router(observability.router)
api_router.include_router(mcp.router)

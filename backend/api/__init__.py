"""HTTP API routers. Mount via ``register_routers(app)``."""

from __future__ import annotations

from fastapi import FastAPI

from backend.api.auth import router as auth_router
from backend.api.chat import router as chat_router
from backend.api.conversations import router as conversations_router
from backend.api.doc_workspace import router as doc_workspace_router
from backend.api.knowledge import router as knowledge_router
from backend.api.sales_workspace import router as sales_workspace_router
from backend.api.speech import router as speech_router
from backend.api.transcribe import router as transcribe_router
from backend.api.voice_clone import router as voice_clone_router


def register_routers(app: FastAPI) -> None:
    """挂载全部业务路由；路径与原先保持一致。"""
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(conversations_router)
    app.include_router(doc_workspace_router)
    app.include_router(sales_workspace_router)
    app.include_router(knowledge_router)
    app.include_router(speech_router)
    app.include_router(transcribe_router)
    app.include_router(voice_clone_router)

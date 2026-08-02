"""FastAPI 应用入口：生命周期、中间件、路由挂载。"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager, suppress

# psycopg 异步模式在 Windows 上不支持 ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api import register_routers
from backend.common.checkpoint import close_checkpointer, init_checkpointer
from backend.common.errors import http_public_detail, public_client_error
from backend.config import settings
from backend.db.database import close_db, init_db
from backend.graph import compile_graph


def _configure_logging() -> None:
    """应用日志保持 INFO；压低第三方预热噪声（HF 探测 404 / FAISS SIMD 回退等）。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
        force=True,
    )
    noisy = (
        "httpx",
        "httpcore",
        "urllib3",
        "milvus_lite",
        "milvus_lite.server_manager",
    )
    for name in noisy:
        logging.getLogger(name).setLevel(logging.ERROR)


_configure_logging()
logger = logging.getLogger(__name__)


def _warmup_indexing_stack() -> None:
    """预热 Embedding + search_kb Reranker + Milvus，避免首次上传/检索时长时间卡顿。"""
    from backend.config import settings
    from backend.indexing.embedding import embedding_service
    from backend.indexing.milvus_client import get_milvus_service

    logger.info(
        "Indexing stack provider: embedding=%s rerank=%s dim=%s",
        settings.embedding_provider_resolved,
        settings.rerank_provider_resolved,
        settings.embedding_dimensions,
    )
    embedding_service.warmup()

    if settings.rerank_enabled:
        try:
            from backend.tools.public.search_kb.reranker import rerank_service

            rerank_service.warmup()
        except Exception:
            logger.exception("Reranker warmup failed (will retry on first retrieve)")

    try:
        get_milvus_service().init_collection()
        logger.info("Milvus collection warmed up")
    except Exception:
        logger.exception("Milvus warmup failed (will retry on first index)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialized")

    milvus_uri = (settings.milvus_uri or "").strip()
    if milvus_uri.endswith(".db") or milvus_uri.startswith("./") or "://" not in milvus_uri:
        logger.warning(
            "Milvus URI looks like Lite file DB (%s). "
            "Production must use remote Standalone, e.g. http://host:19530",
            milvus_uri,
        )

    checkpointer = await init_checkpointer()
    compile_graph(checkpointer)
    logger.info("LangGraph compiled with Postgres checkpointer")

    from backend.config import apply_langsmith_environ

    if apply_langsmith_environ(settings):
        logger.info(
            "LangSmith tracing ON project=%s endpoint=%s",
            (settings.langsmith_project or "langgraph-demo").strip(),
            (settings.langsmith_endpoint or "default").strip() or "default",
        )
    else:
        logger.info(
            "LangSmith tracing OFF（设置 LANGSMITH_TRACING=true 且配置 LANGSMITH_API_KEY 后生效）"
        )

    if os.getenv("WARMUP_EMBEDDING", "1").strip().lower() not in ("0", "false", "no"):
        asyncio.create_task(asyncio.to_thread(_warmup_indexing_stack))
        logger.info("Embedding/Rerank/Milvus warmup started in background")

    gc_stop = asyncio.Event()
    gc_tasks: list[asyncio.Task] = []
    if int(getattr(settings, "workspace_gc_interval_sec", 0) or 0) > 0:
        from backend.services.workspace_gc import workspace_gc_loop

        gc_tasks.append(asyncio.create_task(workspace_gc_loop(gc_stop)))
        logger.info(
            "Workspace GC scheduled: interval=%ss ttl_days=%s",
            settings.workspace_gc_interval_sec,
            settings.workspace_ttl_days,
        )
    if int(getattr(settings, "sales_workspace_gc_interval_sec", 0) or 0) > 0:
        from backend.services.sales_workspace_gc import sales_workspace_gc_loop

        gc_tasks.append(asyncio.create_task(sales_workspace_gc_loop(gc_stop)))
        logger.info(
            "Sales workspace GC scheduled: interval=%ss ttl_days=%s",
            settings.sales_workspace_gc_interval_sec,
            settings.sales_workspace_ttl_days,
        )

    yield

    gc_stop.set()
    for t in gc_tasks:
        t.cancel()
        with suppress(asyncio.CancelledError):
            await t
    await close_checkpointer()
    await close_db()


app = FastAPI(title="LangGraph Chat API", lifespan=lifespan)

_cors_origins = [
    o.strip()
    for o in (settings.cors_origins or "").split(",")
    if o.strip()
] or ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    """HTTPException.detail 再过安全闸，避免误把异常原文塞进 detail。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": http_public_detail(
                exc.detail,
                fallback="请求失败，请稍后重试。",
            )
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    logger.info("Request validation failed: %s", exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": "请求参数无效，请检查后重试。"},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):
    """未捕获异常：日志全量，响应固定文案（无堆栈、无密钥）。"""
    logger.exception("Unhandled API error")
    return JSONResponse(
        status_code=500,
        content={
            "detail": public_client_error(
                exc, kind="chat", fallback="服务繁忙，请稍后重试。"
            )
        },
    )


register_routers(app)


def _run() -> None:
    """启动 API（单进程）。Windows 强制 SelectorEventLoop，兼容新旧 uvicorn。"""
    # 单 worker：SSE / ActiveRun 为进程内状态；此处不用 uvicorn workers 多进程
    config = uvicorn.Config(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        limit_concurrency=max(32, int(settings.uvicorn_limit_concurrency or 200)),
    )
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        # uvicorn 在 Win 上常强制 Proactor；psycopg 异步只认 Selector
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        except KeyboardInterrupt:
            # Ctrl+C：正常退出，避免打一整段 traceback
            pass
        finally:
            with suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
    else:
        try:
            asyncio.run(server.serve())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    _run()

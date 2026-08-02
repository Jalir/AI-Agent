"""LangGraph AsyncPostgresSaver checkpointer 与会话重置。

与业务表 conversations/messages 分离：本模块写入 checkpoints /
checkpoint_blobs / checkpoint_writes（由 setup() 自动建表）。
"""

from __future__ import annotations

import asyncio
import logging
import sys

# 被直接脚本/测试 import 时也保证 Windows 事件循环兼容 psycopg
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from backend.config import settings

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None
# 模块级导出；在 init_checkpointer() 之后才非 None
checkpointer: AsyncPostgresSaver | None = None


async def init_checkpointer() -> AsyncPostgresSaver:
    """打开 psycopg 连接池、建 checkpoint 表，返回可用的 checkpointer。"""
    global _pool, checkpointer
    if checkpointer is not None:
        return checkpointer

    pool_min = max(1, int(settings.postgres_checkpoint_pool_min or 1))
    configured_max = int(settings.postgres_checkpoint_pool_max or 0)
    if configured_max <= 0:
        # 默认对齐图并发，避免 checkpointer 池成为瓶颈
        pool_max = max(10, int(settings.max_concurrent_runs or 32))
    else:
        pool_max = configured_max
    pool_max = max(pool_min, pool_max)

    acquire_timeout = float(settings.postgres_checkpoint_pool_timeout_sec or 10.0)
    max_lifetime = float(settings.postgres_checkpoint_pool_max_lifetime_sec or 3600.0)
    max_idle = float(settings.postgres_checkpoint_pool_max_idle_sec or 600.0)

    _pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=pool_min,
        max_size=pool_max,
        open=False,
        # 池耗尽时尽快失败；定期回收连接避免僵死
        timeout=acquire_timeout if acquire_timeout > 0 else 30.0,
        max_lifetime=max_lifetime if max_lifetime > 0 else 0,
        max_idle=max_idle if max_idle > 0 else 0,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    await _pool.open()
    saver = AsyncPostgresSaver(_pool)
    await saver.setup()
    checkpointer = saver
    logger.info(
        "Postgres checkpointer initialized (pool %d-%d)",
        pool_min,
        pool_max,
    )
    return saver


async def close_checkpointer() -> None:
    """关闭连接池（应用关闭时调用）。"""
    global _pool, checkpointer
    checkpointer = None
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Postgres checkpointer closed")


def get_checkpointer() -> AsyncPostgresSaver:
    if checkpointer is None:
        raise RuntimeError(
            "Checkpointer not initialized; ensure init_checkpointer() ran in app lifespan"
        )
    return checkpointer


async def reset_thread(thread_id: str) -> None:
    """Clear a thread's checkpoint so it starts fresh."""
    if checkpointer is None:
        return
    try:
        await checkpointer.adelete_thread(thread_id)
    except Exception:
        logger.exception("Failed to reset checkpoint for thread %s", thread_id)


async def reset_all_threads() -> None:
    """Clear all checkpoints."""
    if _pool is None:
        return
    try:
        async with _pool.connection() as conn:
            await conn.execute("DELETE FROM checkpoint_writes")
            await conn.execute("DELETE FROM checkpoint_blobs")
            await conn.execute("DELETE FROM checkpoints")
    except Exception:
        logger.exception("Failed to reset all checkpoints")

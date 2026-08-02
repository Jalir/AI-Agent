"""文档工作区元数据：临时 RAG，与共享 knowledge_files 隔离。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.config import settings
from backend.db.database import get_pool

PARSE_PENDING = "pending"
PARSE_PARSING = "parsing"
PARSE_DONE = "done"
PARSE_FAILED = "failed"


def workspace_ttl_days() -> int:
    return max(1, int(settings.workspace_ttl_days or 10))


def _ttl_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=workspace_ttl_days())


def milvus_collection_for_workspace(workspace_id: int) -> str:
    """独立 collection 名（与 embeddings_collection 物理隔离）。"""
    return f"doc_ws_{int(workspace_id)}"


# Postgres advisory lock 命名空间（与业务表无关的固定常量）
_GC_LOCK_K1 = 710_001
_GC_LOCK_K2 = 1
_WS_LOCK_K1 = 710_002


async def init_workspace_tables() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS doc_workspaces (
                id                SERIAL PRIMARY KEY,
                user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title             VARCHAR(200) NOT NULL DEFAULT '文档工作区',
                thread_id         VARCHAR(255) NOT NULL UNIQUE,
                milvus_collection VARCHAR(128) NOT NULL DEFAULT '',
                status            VARCHAR(32) NOT NULL DEFAULT 'active',
                expires_at        TIMESTAMPTZ NOT NULL,
                purge_fail_count  INTEGER NOT NULL DEFAULT 0,
                created_at        TIMESTAMPTZ DEFAULT NOW(),
                updated_at        TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            ALTER TABLE doc_workspaces
            ADD COLUMN IF NOT EXISTS purge_fail_count INTEGER NOT NULL DEFAULT 0;
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_doc_workspaces_user
                ON doc_workspaces(user_id, updated_at DESC);
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_doc_workspaces_gc
                ON doc_workspaces(status, expires_at);
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS doc_workspace_files (
                id            SERIAL PRIMARY KEY,
                workspace_id  INTEGER NOT NULL
                    REFERENCES doc_workspaces(id) ON DELETE CASCADE,
                file_name     VARCHAR(500) NOT NULL,
                file_url      TEXT NOT NULL,
                object_key    VARCHAR(500) NOT NULL DEFAULT '',
                file_size     BIGINT NOT NULL DEFAULT 0,
                file_type     VARCHAR(100) NOT NULL DEFAULT '',
                parse_status  VARCHAR(32) NOT NULL DEFAULT 'pending',
                parse_error   TEXT NOT NULL DEFAULT '',
                char_count    INTEGER NOT NULL DEFAULT 0,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_doc_workspace_files_ws
                ON doc_workspace_files(workspace_id, created_at DESC);
            """
        )


def _row_workspace(r: Any) -> dict:
    return {
        "id": r["id"],
        "user_id": r["user_id"],
        "title": r["title"],
        "thread_id": r["thread_id"],
        "milvus_collection": r["milvus_collection"],
        "status": r["status"],
        "expires_at": r["expires_at"].isoformat() if r["expires_at"] else "",
        "purge_fail_count": int(r["purge_fail_count"] or 0)
        if "purge_fail_count" in r
        else 0,
        "created_at": r["created_at"].isoformat() if r["created_at"] else "",
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else "",
    }


def public_workspace_view(ws: dict | None) -> dict | None:
    """对外 DTO：隐藏 milvus_collection 等内部字段。"""
    if not ws:
        return None
    return {
        "id": ws["id"],
        "user_id": ws["user_id"],
        "title": ws["title"],
        "thread_id": ws["thread_id"],
        "status": ws["status"],
        "expires_at": ws.get("expires_at") or "",
        "created_at": ws.get("created_at") or "",
        "updated_at": ws.get("updated_at") or "",
    }


def _row_file(r: Any) -> dict:
    return {
        "id": r["id"],
        "workspace_id": r["workspace_id"],
        "file_name": r["file_name"],
        "file_url": r["file_url"],
        "object_key": r["object_key"],
        "file_size": r["file_size"],
        "file_type": r["file_type"],
        "parse_status": r["parse_status"],
        "parse_error": r["parse_error"],
        "char_count": r["char_count"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else "",
    }


async def create_workspace(
    *,
    user_id: int,
    thread_id: str,
    title: str = "文档工作区",
) -> dict:
    expires = _ttl_expires_at()
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO doc_workspaces (user_id, title, thread_id, expires_at)
                VALUES ($1, $2, $3, $4)
                RETURNING *
                """,
                user_id,
                (title or "文档工作区")[:200],
                thread_id,
                expires,
            )
            coll = milvus_collection_for_workspace(int(row["id"]))
            row = await conn.fetchrow(
                """
                UPDATE doc_workspaces
                SET milvus_collection = $2, updated_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                row["id"],
                coll,
            )
    return _row_workspace(row)


async def get_workspace(workspace_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM doc_workspaces WHERE id = $1",
            workspace_id,
        )
    return _row_workspace(row) if row else None


async def get_workspace_for_user(workspace_id: int, user_id: int) -> dict | None:
    """仅返回未过期的活跃工作区。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM doc_workspaces
            WHERE id = $1 AND user_id = $2
              AND status = 'active'
              AND expires_at > NOW()
            """,
            workspace_id,
            user_id,
        )
    return _row_workspace(row) if row else None


async def get_latest_workspace_for_user(user_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM doc_workspaces
            WHERE user_id = $1 AND status = 'active'
              AND expires_at > NOW()
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            user_id,
        )
    return _row_workspace(row) if row else None


async def list_workspace_ids_for_user(user_id: int) -> list[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM doc_workspaces WHERE user_id = $1 ORDER BY id ASC;",
            user_id,
        )
    return [int(r["id"]) for r in rows]


async def touch_workspace(workspace_id: int, *, renew_ttl: bool = True) -> None:
    """刷新活跃时间；默认滑动续期 expires_at。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if renew_ttl:
            await conn.execute(
                """
                UPDATE doc_workspaces
                SET updated_at = NOW(), expires_at = $2
                WHERE id = $1 AND status = 'active'
                """,
                workspace_id,
                _ttl_expires_at(),
            )
        else:
            await conn.execute(
                "UPDATE doc_workspaces SET updated_at = NOW() WHERE id = $1",
                workspace_id,
            )


async def list_workspaces_for_gc(*, limit: int = 20) -> list[dict]:
    """GC 候选：已过期仍 active，或已 closed 待硬删除。"""
    limit = max(1, min(int(limit or 20), 200))
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM doc_workspaces
            WHERE (status = 'active' AND expires_at <= NOW())
               OR status = 'closed'
            ORDER BY expires_at ASC NULLS FIRST, id ASC
            LIMIT $1
            """,
            limit,
        )
    return [_row_workspace(r) for r in rows]


async def delete_workspace_row(workspace_id: int) -> bool:
    """硬删除工作区行（CASCADE 清 files / chunks）。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM doc_workspaces WHERE id = $1",
            workspace_id,
        )
    parts = (result or "").split()
    return bool(parts and parts[-1].isdigit() and int(parts[-1]) > 0)


async def list_workspace_files(workspace_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM doc_workspace_files
            WHERE workspace_id = $1
            ORDER BY created_at DESC
            """,
            workspace_id,
        )
    return [_row_file(r) for r in rows]


async def insert_workspace_file(
    *,
    workspace_id: int,
    file_name: str,
    file_url: str,
    object_key: str,
    file_size: int,
    file_type: str,
    parse_status: str = PARSE_PENDING,
) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO doc_workspace_files
                (workspace_id, file_name, file_url, object_key, file_size,
                 file_type, parse_status)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            workspace_id,
            file_name,
            file_url,
            object_key,
            file_size,
            file_type,
            parse_status,
        )
    await touch_workspace(workspace_id)
    return _row_file(row)


async def get_workspace_file(file_id: int, workspace_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM doc_workspace_files
            WHERE id = $1 AND workspace_id = $2
            """,
            file_id,
            workspace_id,
        )
    return _row_file(row) if row else None


async def update_workspace_file_parse(
    file_id: int,
    *,
    parse_status: str,
    parse_error: str = "",
    char_count: int | None = None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if char_count is None:
            await conn.execute(
                """
                UPDATE doc_workspace_files
                SET parse_status = $2, parse_error = $3
                WHERE id = $1
                """,
                file_id,
                parse_status,
                parse_error or "",
            )
        else:
            await conn.execute(
                """
                UPDATE doc_workspace_files
                SET parse_status = $2, parse_error = $3, char_count = $4
                WHERE id = $1
                """,
                file_id,
                parse_status,
                parse_error or "",
                int(char_count),
            )


async def delete_workspace_file_row(file_id: int, workspace_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM doc_workspace_files
            WHERE id = $1 AND workspace_id = $2
            RETURNING *
            """,
            file_id,
            workspace_id,
        )
    if row:
        await touch_workspace(workspace_id)
        return _row_file(row)
    return None


async def mark_workspace_closed(workspace_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE doc_workspaces
            SET status = 'closed', updated_at = NOW()
            WHERE id = $1
            """,
            workspace_id,
        )


async def bump_purge_fail(workspace_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE doc_workspaces
            SET purge_fail_count = COALESCE(purge_fail_count, 0) + 1,
                updated_at = NOW()
            WHERE id = $1
            """,
            workspace_id,
        )


async def get_workspace_by_thread_for_user(
    thread_id: str,
    user_id: int,
) -> dict | None:
    """按 thread 反查工作区（含已过期但仍属本人的 active/closed，供强制注入）。"""
    tid = (thread_id or "").strip()
    if not tid:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM doc_workspaces
            WHERE thread_id = $1 AND user_id = $2
              AND status IN ('active', 'closed')
            """,
            tid,
            user_id,
        )
    return _row_workspace(row) if row else None


async def is_workspace_active(workspace_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 FROM doc_workspaces
            WHERE id = $1 AND status = 'active' AND expires_at > NOW()
            """,
            workspace_id,
        )
    return row is not None


@asynccontextmanager
async def workspace_advisory_lock(
    workspace_id: int,
    *,
    blocking: bool = False,
) -> AsyncIterator[bool]:
    """在同一连接上持有 workspace 互斥锁（兼容连接池）。

    yield True 表示拿到锁；非阻塞失败时 yield False（不持锁）。
    """
    pool = await get_pool()
    conn = await pool.acquire()
    locked = False
    try:
        if blocking:
            await conn.fetchval(
                "SELECT pg_advisory_lock($1, $2)",
                _WS_LOCK_K1,
                int(workspace_id),
            )
            locked = True
            yield True
        else:
            locked = bool(
                await conn.fetchval(
                    "SELECT pg_try_advisory_lock($1, $2)",
                    _WS_LOCK_K1,
                    int(workspace_id),
                )
            )
            yield locked
    finally:
        if locked:
            try:
                await conn.fetchval(
                    "SELECT pg_advisory_unlock($1, $2)",
                    _WS_LOCK_K1,
                    int(workspace_id),
                )
            except Exception:
                pass
        await pool.release(conn)


@asynccontextmanager
async def gc_advisory_lock() -> AsyncIterator[bool]:
    """GC 全局锁：多 worker 仅一个实例执行清理。"""
    pool = await get_pool()
    conn = await pool.acquire()
    locked = False
    try:
        locked = bool(
            await conn.fetchval(
                "SELECT pg_try_advisory_lock($1, $2)",
                _GC_LOCK_K1,
                _GC_LOCK_K2,
            )
        )
        yield locked
    finally:
        if locked:
            try:
                await conn.fetchval(
                    "SELECT pg_advisory_unlock($1, $2)",
                    _GC_LOCK_K1,
                    _GC_LOCK_K2,
                )
            except Exception:
                pass
        await pool.release(conn)

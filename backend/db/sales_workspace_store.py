"""销售分析工作区：Excel 结构化入库，与文档 RAG / 共享知识库隔离。"""

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

_GC_LOCK_K1 = 720_001
_GC_LOCK_K2 = 1
_SA_LOCK_K1 = 720_002


def sales_ttl_days() -> int:
    return max(1, int(getattr(settings, "sales_workspace_ttl_days", None) or settings.workspace_ttl_days or 10))


def _ttl_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=sales_ttl_days())


async def init_sales_workspace_tables() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sales_workspaces (
                id               SERIAL PRIMARY KEY,
                user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title            VARCHAR(200) NOT NULL DEFAULT '销售分析',
                thread_id        VARCHAR(255) NOT NULL UNIQUE,
                status           VARCHAR(32) NOT NULL DEFAULT 'active',
                expires_at       TIMESTAMPTZ NOT NULL,
                purge_fail_count INTEGER NOT NULL DEFAULT 0,
                created_at       TIMESTAMPTZ DEFAULT NOW(),
                updated_at       TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sales_workspaces_user
                ON sales_workspaces(user_id, updated_at DESC);
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sales_workspaces_gc
                ON sales_workspaces(status, expires_at);
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sales_workspace_files (
                id            SERIAL PRIMARY KEY,
                workspace_id  INTEGER NOT NULL
                    REFERENCES sales_workspaces(id) ON DELETE CASCADE,
                file_name     VARCHAR(500) NOT NULL,
                file_url      TEXT NOT NULL,
                object_key    VARCHAR(500) NOT NULL DEFAULT '',
                file_size     BIGINT NOT NULL DEFAULT 0,
                file_type     VARCHAR(100) NOT NULL DEFAULT '',
                parse_status  VARCHAR(32) NOT NULL DEFAULT 'pending',
                parse_error   TEXT NOT NULL DEFAULT '',
                sheet_count   INTEGER NOT NULL DEFAULT 0,
                row_count     INTEGER NOT NULL DEFAULT 0,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sales_workspace_files_ws
                ON sales_workspace_files(workspace_id, created_at DESC);
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sales_tables (
                id            SERIAL PRIMARY KEY,
                workspace_id  INTEGER NOT NULL
                    REFERENCES sales_workspaces(id) ON DELETE CASCADE,
                file_id       INTEGER NOT NULL
                    REFERENCES sales_workspace_files(id) ON DELETE CASCADE,
                sheet_name    VARCHAR(255) NOT NULL DEFAULT '',
                columns_json  JSONB NOT NULL DEFAULT '[]'::jsonb,
                row_count     INTEGER NOT NULL DEFAULT 0,
                warnings_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sales_tables_ws
                ON sales_tables(workspace_id, file_id);
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sales_table_rows (
                id         BIGSERIAL PRIMARY KEY,
                table_id   INTEGER NOT NULL
                    REFERENCES sales_tables(id) ON DELETE CASCADE,
                row_idx    INTEGER NOT NULL,
                data       JSONB NOT NULL DEFAULT '{}'::jsonb
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sales_table_rows_table
                ON sales_table_rows(table_id, row_idx);
            """
        )


def _row_workspace(r: Any) -> dict:
    return {
        "id": r["id"],
        "user_id": r["user_id"],
        "title": r["title"],
        "thread_id": r["thread_id"],
        "status": r["status"],
        "expires_at": r["expires_at"].isoformat() if r["expires_at"] else "",
        "purge_fail_count": int(r["purge_fail_count"] or 0),
        "created_at": r["created_at"].isoformat() if r["created_at"] else "",
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else "",
    }


def public_workspace_view(ws: dict | None) -> dict | None:
    if not ws:
        return None
    return {
        "id": ws["id"],
        "user_id": ws["user_id"],
        "title": ws["title"],
        "thread_id": ws["thread_id"],
        "status": ws["status"],
        "expires_at": ws.get("expires_at") or "",
    }


def _row_file(r: Any) -> dict:
    return {
        "id": r["id"],
        "workspace_id": r["workspace_id"],
        "file_name": r["file_name"],
        "file_url": r["file_url"],
        "object_key": r["object_key"],
        "file_size": int(r["file_size"] or 0),
        "file_type": r["file_type"] or "",
        "parse_status": r["parse_status"],
        "parse_error": r["parse_error"] or "",
        "sheet_count": int(r["sheet_count"] or 0),
        "row_count": int(r["row_count"] or 0),
        "created_at": r["created_at"].isoformat() if r["created_at"] else "",
    }


def _row_table(r: Any) -> dict:
    cols = r["columns_json"]
    if isinstance(cols, str):
        import json

        cols = json.loads(cols)
    warns = r["warnings_json"]
    if isinstance(warns, str):
        import json

        warns = json.loads(warns)
    return {
        "id": r["id"],
        "workspace_id": r["workspace_id"],
        "file_id": r["file_id"],
        "sheet_name": r["sheet_name"] or "",
        "columns": cols if isinstance(cols, list) else [],
        "row_count": int(r["row_count"] or 0),
        "warnings": warns if isinstance(warns, list) else [],
        "created_at": r["created_at"].isoformat() if r["created_at"] else "",
    }


async def create_workspace(*, user_id: int, thread_id: str, title: str = "销售分析") -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO sales_workspaces (user_id, title, thread_id, expires_at)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            user_id,
            title,
            thread_id,
            _ttl_expires_at(),
        )
    return _row_workspace(row)


async def get_workspace(workspace_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM sales_workspaces WHERE id = $1",
            workspace_id,
        )
    return _row_workspace(row) if row else None


async def get_workspace_for_user(workspace_id: int, user_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM sales_workspaces
            WHERE id = $1 AND user_id = $2 AND status = 'active'
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
            SELECT * FROM sales_workspaces
            WHERE user_id = $1 AND status = 'active' AND expires_at > NOW()
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
            "SELECT id FROM sales_workspaces WHERE user_id = $1 ORDER BY id ASC;",
            user_id,
        )
    return [int(r["id"]) for r in rows]


async def get_workspace_by_thread_for_user(thread_id: str, user_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM sales_workspaces
            WHERE thread_id = $1 AND user_id = $2
            """,
            thread_id,
            user_id,
        )
    return _row_workspace(row) if row else None


async def touch_workspace(workspace_id: int, *, renew_ttl: bool = True) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if renew_ttl:
            await conn.execute(
                """
                UPDATE sales_workspaces
                SET updated_at = NOW(), expires_at = $2
                WHERE id = $1
                """,
                workspace_id,
                _ttl_expires_at(),
            )
        else:
            await conn.execute(
                "UPDATE sales_workspaces SET updated_at = NOW() WHERE id = $1",
                workspace_id,
            )


async def mark_workspace_closed(workspace_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE sales_workspaces
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
            UPDATE sales_workspaces
            SET purge_fail_count = COALESCE(purge_fail_count, 0) + 1,
                updated_at = NOW()
            WHERE id = $1
            """,
            workspace_id,
        )


async def delete_workspace_row(workspace_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM sales_workspaces WHERE id = $1",
            workspace_id,
        )
    return result.endswith("1")


async def list_workspaces_for_gc(*, limit: int = 20) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM sales_workspaces
            WHERE status = 'closed'
               OR expires_at <= NOW()
            ORDER BY updated_at ASC
            LIMIT $1
            """,
            max(1, int(limit)),
        )
    return [_row_workspace(r) for r in rows]


async def list_workspace_files(workspace_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM sales_workspace_files
            WHERE workspace_id = $1
            ORDER BY created_at DESC
            """,
            workspace_id,
        )
    return [_row_file(r) for r in rows]


async def insert_workspace_file(
    workspace_id: int,
    *,
    file_name: str,
    file_url: str,
    object_key: str,
    file_size: int,
    file_type: str,
) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO sales_workspace_files
                (workspace_id, file_name, file_url, object_key, file_size,
                 file_type, parse_status)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            workspace_id,
            file_name,
            file_url,
            object_key,
            int(file_size),
            file_type,
            PARSE_PENDING,
        )
    await touch_workspace(workspace_id)
    return _row_file(row)


async def get_workspace_file(file_id: int, workspace_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM sales_workspace_files
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
    sheet_count: int = 0,
    row_count: int = 0,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE sales_workspace_files
            SET parse_status = $2,
                parse_error = $3,
                sheet_count = $4,
                row_count = $5
            WHERE id = $1
            """,
            file_id,
            parse_status,
            parse_error or "",
            int(sheet_count),
            int(row_count),
        )


async def delete_workspace_file_row(file_id: int, workspace_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM sales_workspace_files
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


async def delete_tables_for_file(file_id: int) -> None:
    """CASCADE 会清 rows；显式删 tables 即可。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM sales_tables WHERE file_id = $1",
            file_id,
        )


async def insert_table(
    *,
    workspace_id: int,
    file_id: int,
    sheet_name: str,
    columns: list[dict],
    warnings: list[str],
) -> int:
    import json

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO sales_tables
                (workspace_id, file_id, sheet_name, columns_json, warnings_json)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
            RETURNING id
            """,
            workspace_id,
            file_id,
            sheet_name,
            json.dumps(columns, ensure_ascii=False),
            json.dumps(warnings, ensure_ascii=False),
        )
    return int(row["id"])


async def insert_table_rows(table_id: int, rows: list[dict]) -> int:
    if not rows:
        return 0
    import json

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO sales_table_rows (table_id, row_idx, data)
            VALUES ($1, $2, $3::jsonb)
            """,
            [
                (table_id, int(r["row_idx"]), json.dumps(r["data"], ensure_ascii=False))
                for r in rows
            ],
        )
        await conn.execute(
            "UPDATE sales_tables SET row_count = $2 WHERE id = $1",
            table_id,
            len(rows),
        )
    return len(rows)


async def list_tables(workspace_id: int, *, file_id: int | None = None) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if file_id is not None:
            rows = await conn.fetch(
                """
                SELECT * FROM sales_tables
                WHERE workspace_id = $1 AND file_id = $2
                ORDER BY id ASC
                """,
                workspace_id,
                file_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM sales_tables
                WHERE workspace_id = $1
                ORDER BY id ASC
                """,
                workspace_id,
            )
    return [_row_table(r) for r in rows]


async def get_table_for_workspace(table_id: int, workspace_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM sales_tables
            WHERE id = $1 AND workspace_id = $2
            """,
            table_id,
            workspace_id,
        )
    return _row_table(row) if row else None


async def fetch_table_rows(
    table_id: int,
    *,
    offset: int = 0,
    limit: int = 200,
) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT row_idx, data FROM sales_table_rows
            WHERE table_id = $1
            ORDER BY row_idx ASC
            OFFSET $2 LIMIT $3
            """,
            table_id,
            max(0, int(offset)),
            max(1, int(limit)),
        )
    out: list[dict] = []
    for r in rows:
        data = r["data"]
        if isinstance(data, str):
            import json

            data = json.loads(data)
        out.append({"row_idx": int(r["row_idx"]), "data": data if isinstance(data, dict) else {}})
    return out


async def count_table_rows(table_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM sales_table_rows WHERE table_id = $1",
            table_id,
        )
    return int(n or 0)


@asynccontextmanager
async def sales_advisory_lock(
    workspace_id: int, *, blocking: bool = True
) -> AsyncIterator[bool]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if blocking:
            await conn.execute(
                "SELECT pg_advisory_lock($1, $2)",
                _SA_LOCK_K1,
                int(workspace_id),
            )
            locked = True
        else:
            locked = bool(
                await conn.fetchval(
                    "SELECT pg_try_advisory_lock($1, $2)",
                    _SA_LOCK_K1,
                    int(workspace_id),
                )
            )
        try:
            yield locked
        finally:
            if locked:
                await conn.execute(
                    "SELECT pg_advisory_unlock($1, $2)",
                    _SA_LOCK_K1,
                    int(workspace_id),
                )


@asynccontextmanager
async def gc_advisory_lock(*, blocking: bool = False) -> AsyncIterator[bool]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if blocking:
            await conn.execute(
                "SELECT pg_advisory_lock($1, $2)",
                _GC_LOCK_K1,
                _GC_LOCK_K2,
            )
            locked = True
        else:
            locked = bool(
                await conn.fetchval(
                    "SELECT pg_try_advisory_lock($1, $2)",
                    _GC_LOCK_K1,
                    _GC_LOCK_K2,
                )
            )
        try:
            yield locked
        finally:
            if locked:
                await conn.execute(
                    "SELECT pg_advisory_unlock($1, $2)",
                    _GC_LOCK_K1,
                    _GC_LOCK_K2,
                )

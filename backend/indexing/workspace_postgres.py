"""工作区 L1/L2 分块表 —— 与共享 document_chunks 物理隔离。"""

from __future__ import annotations

from backend.db.database import get_pool


async def init_workspace_document_chunks_table() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_document_chunks (
                id              SERIAL PRIMARY KEY,
                workspace_id    INTEGER NOT NULL
                    REFERENCES doc_workspaces(id) ON DELETE CASCADE,
                file_id         INTEGER NOT NULL DEFAULT 0,
                chunk_id        VARCHAR(256) NOT NULL,
                parent_chunk_id VARCHAR(256) NOT NULL DEFAULT '',
                root_chunk_id   VARCHAR(256) NOT NULL,
                chunk_level     SMALLINT NOT NULL CHECK (chunk_level IN (1, 2)),
                chunk_idx       INTEGER NOT NULL DEFAULT 0,
                filename        VARCHAR(500) NOT NULL,
                file_path       TEXT NOT NULL DEFAULT '',
                file_type       VARCHAR(50) NOT NULL DEFAULT '',
                page_number     INTEGER NOT NULL DEFAULT 0,
                text            TEXT NOT NULL,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (workspace_id, chunk_id)
            );
            """
        )
        await conn.execute(
            """
            ALTER TABLE workspace_document_chunks
            ADD COLUMN IF NOT EXISTS file_id INTEGER NOT NULL DEFAULT 0;
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_wdc_ws_parent
                ON workspace_document_chunks(workspace_id, parent_chunk_id);
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_wdc_ws_filename
                ON workspace_document_chunks(workspace_id, filename);
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_wdc_ws_file_id
                ON workspace_document_chunks(workspace_id, file_id);
            """
        )


async def insert_workspace_chunks(workspace_id: int, chunks: list[dict]) -> int:
    if not chunks:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO workspace_document_chunks
                (workspace_id, file_id, chunk_id, parent_chunk_id, root_chunk_id,
                 chunk_level, chunk_idx, filename, file_path, file_type, page_number, text)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (workspace_id, chunk_id) DO UPDATE SET
                file_id         = EXCLUDED.file_id,
                parent_chunk_id = EXCLUDED.parent_chunk_id,
                root_chunk_id   = EXCLUDED.root_chunk_id,
                chunk_level     = EXCLUDED.chunk_level,
                chunk_idx       = EXCLUDED.chunk_idx,
                filename        = EXCLUDED.filename,
                file_path       = EXCLUDED.file_path,
                file_type       = EXCLUDED.file_type,
                page_number     = EXCLUDED.page_number,
                text            = EXCLUDED.text;
            """,
            [
                (
                    int(workspace_id),
                    int(c.get("file_id") or 0),
                    c["chunk_id"],
                    c.get("parent_chunk_id", ""),
                    c["root_chunk_id"],
                    c["chunk_level"],
                    c.get("chunk_idx", 0),
                    c["filename"],
                    c.get("file_path", ""),
                    c.get("file_type", ""),
                    c.get("page_number", 0),
                    c["text"],
                )
                for c in chunks
            ],
        )
    return len(chunks)


async def query_workspace_l2_by_chunk_ids(
    workspace_id: int,
    chunk_ids: list[str],
) -> dict[str, dict]:
    ids = [c for c in dict.fromkeys(chunk_ids) if c]
    if not ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_id, parent_chunk_id, root_chunk_id, chunk_level, chunk_idx,
                   filename, file_path, file_type, page_number, text, created_at
            FROM workspace_document_chunks
            WHERE workspace_id = $1
              AND chunk_level = 2
              AND chunk_id = ANY($2::varchar[])
            """,
            int(workspace_id),
            ids,
        )
    return {str(r["chunk_id"]): dict(r) for r in rows}


async def delete_workspace_chunks_by_file_id(
    workspace_id: int,
    file_id: int,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM workspace_document_chunks
            WHERE workspace_id = $1 AND file_id = $2
            """,
            int(workspace_id),
            int(file_id),
        )
    parts = result.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


async def delete_workspace_chunks_by_filename(
    workspace_id: int,
    filename: str,
) -> int:
    """兼容旧数据；新索引请用 delete_workspace_chunks_by_file_id。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM workspace_document_chunks
            WHERE workspace_id = $1 AND filename = $2
            """,
            int(workspace_id),
            filename,
        )
    parts = result.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


async def delete_all_workspace_chunks(workspace_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM workspace_document_chunks WHERE workspace_id = $1",
            int(workspace_id),
        )
    parts = result.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0

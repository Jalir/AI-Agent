"""
PostgreSQL 存储服务 —— L1 / L2 文档块共表

将 document_loader 产出的 L1、L2 chunk 存入 Postgres 表 document_chunks。
L3 粒度太细，走 Milvus 向量检索；L1/L2 走 Postgres 做结构化查询和上下文补全。
"""
from backend.db.database import get_pool


async def init_document_chunks_table() -> None:
    """创建 document_chunks 表（L1 + L2 共表），幂等。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS document_chunks (
                id              SERIAL PRIMARY KEY,
                chunk_id        VARCHAR(256) UNIQUE NOT NULL,
                parent_chunk_id VARCHAR(256) NOT NULL DEFAULT '',
                root_chunk_id   VARCHAR(256) NOT NULL,
                chunk_level     SMALLINT NOT NULL CHECK (chunk_level IN (1, 2)),
                chunk_idx       INTEGER NOT NULL DEFAULT 0,
                filename        VARCHAR(500) NOT NULL,
                file_path       TEXT NOT NULL DEFAULT '',
                file_type       VARCHAR(50) NOT NULL DEFAULT '',
                page_number     INTEGER NOT NULL DEFAULT 0,
                text            TEXT NOT NULL,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        # 复合索引对齐真实查询（WHERE + ORDER BY）；旧单列索引保留兼容
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dc_root_chunk ON document_chunks(root_chunk_id);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dc_root_level_idx "
            "ON document_chunks(root_chunk_id, chunk_level, chunk_idx);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dc_filename ON document_chunks(filename);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dc_filename_level_idx "
            "ON document_chunks(filename, chunk_level, chunk_idx);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dc_parent_chunk ON document_chunks(parent_chunk_id);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dc_parent_level_idx "
            "ON document_chunks(parent_chunk_id, chunk_level, chunk_idx);"
        )

        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dc_text_trgm ON document_chunks USING GIN (text gin_trgm_ops);"
        )


async def insert_chunks(chunks: list[dict]) -> int:
    """批量插入 L1/L2 分块，chunk_id 冲突时覆盖更新（幂等）。返回插入行数。"""
    if not chunks:
        return 0

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO document_chunks
                (chunk_id, parent_chunk_id, root_chunk_id, chunk_level, chunk_idx,
                 filename, file_path, file_type, page_number, text)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (chunk_id) DO UPDATE SET
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


async def query_l1_by_filename(filename: str) -> list[dict]:
    """查询某个文件的所有 L1 顶层块。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_id, parent_chunk_id, root_chunk_id, chunk_level, chunk_idx,
                   filename, file_path, file_type, page_number, text, created_at
            FROM document_chunks
            WHERE filename = $1 AND chunk_level = 1
            ORDER BY chunk_idx;
            """,
            filename,
        )
        return [dict(r) for r in rows]


async def query_l2_by_parent(parent_chunk_id: str) -> list[dict]:
    """查询某个 L1 块下的所有 L2 子块。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_id, parent_chunk_id, root_chunk_id, chunk_level, chunk_idx,
                   filename, file_path, file_type, page_number, text, created_at
            FROM document_chunks
            WHERE parent_chunk_id = $1 AND chunk_level = 2
            ORDER BY chunk_idx;
            """,
            parent_chunk_id,
        )
        return [dict(r) for r in rows]


async def query_l2_by_chunk_ids(chunk_ids: list[str]) -> dict[str, dict]:
    """
    按 L2 的 chunk_id 批量查询（L3.parent_chunk_id → L2.chunk_id）。

    一次 SQL + ANY，返回 {chunk_id: row}，供 O(1) 回填。
    """
    ids = [c for c in dict.fromkeys(chunk_ids) if c]
    if not ids:
        return {}

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_id, parent_chunk_id, root_chunk_id, chunk_level, chunk_idx,
                   filename, file_path, file_type, page_number, text, created_at
            FROM document_chunks
            WHERE chunk_level = 2 AND chunk_id = ANY($1::varchar[])
            """,
            ids,
        )
        return {str(r["chunk_id"]): dict(r) for r in rows}


async def query_all_by_filename(filename: str) -> list[dict]:
    """查询某个文件的所有 L1+L2 块。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_id, parent_chunk_id, root_chunk_id, chunk_level, chunk_idx,
                   filename, file_path, file_type, page_number, text, created_at
            FROM document_chunks
            WHERE filename = $1
            ORDER BY chunk_idx;
            """,
            filename,
        )
        return [dict(r) for r in rows]


async def query_by_root_id(root_chunk_id: str) -> list[dict]:
    """查询某个 L1 根节点及其下所有 L2 子块（一棵完整的子树）。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_id, parent_chunk_id, root_chunk_id, chunk_level, chunk_idx,
                   filename, file_path, file_type, page_number, text, created_at
            FROM document_chunks
            WHERE root_chunk_id = $1
            ORDER BY chunk_level, chunk_idx;
            """,
            root_chunk_id,
        )
        return [dict(r) for r in rows]


async def search_by_text(keyword: str, limit: int = 20) -> list[dict]:
    """基于 pg_trgm 的模糊文本搜索，按相似度降序。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_id, parent_chunk_id, root_chunk_id, chunk_level, chunk_idx,
                   filename, file_path, file_type, page_number, text, created_at,
                   similarity(text, $1) AS sim
            FROM document_chunks
            WHERE text % $1
            ORDER BY sim DESC
            LIMIT $2;
            """,
            keyword,
            limit,
        )
        return [dict(r) for r in rows]


async def delete_by_filename(filename: str) -> int:
    """删除某个文件的所有 L1/L2 块，返回删除行数。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM document_chunks WHERE filename = $1;",
            filename,
        )
        parts = result.split()
        return int(parts[-1]) if parts and parts[-1].isdigit() else 0


async def count_chunks(filename: str | None = None) -> int:
    """统计块总数，可按文件过滤。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if filename:
            row = await conn.fetchrow(
                "SELECT COUNT(*) FROM document_chunks WHERE filename = $1;",
                filename,
            )
        else:
            row = await conn.fetchrow("SELECT COUNT(*) FROM document_chunks;")
        return row[0] if row else 0




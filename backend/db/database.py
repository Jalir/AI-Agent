import asyncpg
import json
from backend.config import settings


# 引入环境
from dotenv import load_dotenv
load_dotenv()


_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        pool_min = max(1, int(settings.postgres_pool_min or 2))
        pool_max = max(pool_min, int(settings.postgres_pool_max or 40))
        cmd_timeout = float(settings.postgres_command_timeout_sec or 30.0)
        acquire_timeout = float(settings.postgres_pool_acquire_timeout_sec or 10.0)
        max_inactive = float(settings.postgres_pool_max_inactive_sec or 300.0)
        _pool = await asyncpg.create_pool(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            min_size=pool_min,
            max_size=pool_max,
            command_timeout=cmd_timeout if cmd_timeout > 0 else None,
            # 池耗尽时尽快失败，避免请求无限挂起
            timeout=acquire_timeout if acquire_timeout > 0 else None,
            # 回收闲置连接，降低僵死连接与防火墙掐断风险
            max_inactive_connection_lifetime=(
                max_inactive if max_inactive > 0 else 0
            ),
        )
    return _pool


async def init_db() -> None:
    from backend.db.auth_store import ensure_admin_user, init_auth_tables
    from backend.db.rbac_store import init_rbac_tables, seed_rbac
    from backend.db.voice_clone_store import init_voice_clone_tables
    from backend.db.workspace_store import init_workspace_tables
    from backend.indexing.postgres_store import init_document_chunks_table
    from backend.indexing.workspace_postgres import init_workspace_document_chunks_table

    # 先建 users，conversations / knowledge_files 才能挂 FK
    await init_auth_tables()
    await init_rbac_tables()
    await seed_rbac()
    await init_voice_clone_tables()

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id          SERIAL PRIMARY KEY,
                thread_id   VARCHAR(255) UNIQUE NOT NULL,
                user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
                title       VARCHAR(500) DEFAULT '新对话',
                created_at  TIMESTAMPTZ DEFAULT NOW(),
                updated_at  TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_user
                ON conversations(user_id, updated_at DESC);
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id              SERIAL PRIMARY KEY,
                thread_id       VARCHAR(255) NOT NULL
                    REFERENCES conversations(thread_id) ON DELETE CASCADE,
                role            VARCHAR(50) NOT NULL
                    CHECK (role IN ('user', 'assistant', 'system', 'tool')),
                content         TEXT NOT NULL,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            ALTER TABLE messages
            ADD COLUMN IF NOT EXISTS token_total INTEGER NOT NULL DEFAULT 0;
        """)
        await conn.execute("""
            ALTER TABLE messages
            ADD COLUMN IF NOT EXISTS attachments JSONB NOT NULL DEFAULT '[]'::jsonb;
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_thread
                ON messages(thread_id, created_at);
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_thread_role
                ON messages(thread_id, role);
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_files (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
                file_name   VARCHAR(500) NOT NULL,
                file_url    TEXT NOT NULL,
                file_size   BIGINT NOT NULL DEFAULT 0,
                file_type   VARCHAR(100) NOT NULL DEFAULT '',
                object_key  VARCHAR(500) NOT NULL DEFAULT '',
                parse_status VARCHAR(32) NOT NULL DEFAULT 'done',
                parse_error  TEXT NOT NULL DEFAULT '',
                created_at  TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        # 兼容已有表：补齐解析状态 / 归属字段
        await conn.execute("""
            ALTER TABLE knowledge_files
            ADD COLUMN IF NOT EXISTS parse_status VARCHAR(32) NOT NULL DEFAULT 'done';
        """)
        await conn.execute("""
            ALTER TABLE knowledge_files
            ADD COLUMN IF NOT EXISTS parse_error TEXT NOT NULL DEFAULT '';
        """)
        await conn.execute("""
            ALTER TABLE knowledge_files
            ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_knowledge_files_user
                ON knowledge_files(user_id, created_at DESC);
        """)

    await ensure_admin_user()
    await init_document_chunks_table()
    await init_workspace_tables()
    await init_workspace_document_chunks_table()
    from backend.db.sales_workspace_store import init_sales_workspace_tables

    await init_sales_workspace_tables()


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# ---- conversation CRUD ----

class ConversationAccessError(PermissionError):
    """会话不属于当前用户或不存在。"""


async def get_conversation(thread_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, thread_id, user_id, title, created_at, updated_at "
            "FROM conversations WHERE thread_id = $1;",
            thread_id,
        )
        return dict(row) if row else None


async def assert_conversation_owner(thread_id: str, user_id: int) -> dict:
    """校验会话归属；不存在或非本人则抛 ConversationAccessError。"""
    row = await get_conversation(thread_id)
    if not row or row.get("user_id") != user_id:
        raise ConversationAccessError("conversation not found")
    return row


async def upsert_conversation(
    thread_id: str,
    user_id: int,
    title: str = "新对话",
) -> None:
    """创建或刷新会话；若 thread_id 已属他人则拒绝。"""
    await upsert_conversation_and_count_user_messages(thread_id, user_id, title=title)


async def upsert_conversation_and_count_user_messages(
    thread_id: str,
    user_id: int,
    title: str = "新对话",
) -> int:
    """单连接：upsert 会话并返回当前 user 消息数（插入本轮之前）。

    ON CONFLICT 时若归属他人则拒绝（DO UPDATE WHERE 不匹配 → 无 RETURNING）。
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO conversations (thread_id, user_id, title, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (thread_id) DO UPDATE SET
                    user_id = COALESCE(conversations.user_id, EXCLUDED.user_id),
                    updated_at = NOW()
                WHERE conversations.user_id IS NULL
                   OR conversations.user_id = EXCLUDED.user_id
                RETURNING user_id;
                """,
                thread_id,
                user_id,
                title,
            )
            if row is None or (
                row["user_id"] is not None and int(row["user_id"]) != int(user_id)
            ):
                raise ConversationAccessError("conversation not found")

            count_row = await conn.fetchrow(
                "SELECT COUNT(*)::int AS c FROM messages "
                "WHERE thread_id = $1 AND role = 'user';",
                thread_id,
            )
            return int(count_row["c"]) if count_row else 0


async def list_conversations(user_id: int, limit: int = 50, offset: int = 0):
    """主对话列表：排除文档工作区 / 销售分析 thread，避免与专用区混用。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, thread_id, user_id, title, created_at, updated_at
            FROM conversations
            WHERE user_id = $1
              AND thread_id NOT LIKE 'ws-%'
              AND thread_id NOT LIKE 'sa-%'
              AND NOT EXISTS (
                    SELECT 1 FROM doc_workspaces dw
                    WHERE dw.thread_id = conversations.thread_id
              )
              AND NOT EXISTS (
                    SELECT 1 FROM sales_workspaces sw
                    WHERE sw.thread_id = conversations.thread_id
              )
            ORDER BY updated_at DESC
            LIMIT $2 OFFSET $3;
            """,
            user_id,
            limit,
            offset,
        )
        return [dict(r) for r in rows]


async def list_thread_ids_for_user(user_id: int) -> list[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT thread_id FROM conversations WHERE user_id = $1;",
            user_id,
        )
        return [r["thread_id"] for r in rows]


async def list_message_attachment_keys_for_user(user_id: int) -> list[str]:
    """收集该用户全部会话消息里的附件 object_key（删用户前清 OSS）。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.attachments
            FROM messages m
            INNER JOIN conversations c ON c.thread_id = m.thread_id
            WHERE c.user_id = $1
              AND m.attachments IS NOT NULL
              AND m.attachments != '[]'::jsonb;
            """,
            user_id,
        )
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        atts = row.get("attachments")
        if not isinstance(atts, list):
            continue
        for att in atts:
            if not isinstance(att, dict):
                continue
            key = str(att.get("object_key") or "").strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


async def delete_all_conversations_for_user(user_id: int) -> list[str]:
    """删除该用户全部会话行（含工作区 thread；messages 靠 CASCADE）。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            DELETE FROM conversations
            WHERE user_id = $1
            RETURNING thread_id;
            """,
            user_id,
        )
        return [r["thread_id"] for r in rows]


async def detach_knowledge_files_owner(user_id: int) -> int:
    """共享知识库保留文件，仅解除上传者归属，避免删管理员时误删全员库。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE knowledge_files SET user_id = NULL WHERE user_id = $1;",
            user_id,
        )
    parts = (result or "").split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


async def delete_conversation(thread_id: str, user_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM conversations WHERE thread_id = $1 AND user_id = $2;",
            thread_id,
            user_id,
        )
        return result.split()[-1] != "0"


async def delete_all_conversations(user_id: int) -> list[str]:
    """删除该用户主对话会话（排除文档工作区 / 销售分析 thread）。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            DELETE FROM conversations
            WHERE user_id = $1
              AND thread_id NOT LIKE 'ws-%'
              AND thread_id NOT LIKE 'sa-%'
              AND NOT EXISTS (
                    SELECT 1 FROM doc_workspaces dw
                    WHERE dw.thread_id = conversations.thread_id
              )
              AND NOT EXISTS (
                    SELECT 1 FROM sales_workspaces sw
                    WHERE sw.thread_id = conversations.thread_id
              )
            RETURNING thread_id;
            """,
            user_id,
        )
        return [r["thread_id"] for r in rows]


# ---- message CRUD ----

async def insert_message(
    thread_id: str,
    role: str,
    content: str,
    *,
    token_total: int = 0,
    attachments: list | None = None,
) -> None:
    pool = await get_pool()
    total = max(0, int(token_total or 0))
    atts = attachments if isinstance(attachments, list) else []
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO messages (thread_id, role, content, token_total, attachments) "
            "VALUES ($1, $2, $3, $4, $5::jsonb);",
            thread_id,
            role,
            content,
            total,
            json.dumps(atts, ensure_ascii=False),
        )


def _serialize_message_row(row) -> dict:
    item = dict(row)
    atts = item.get("attachments")
    if isinstance(atts, str):
        try:
            atts = json.loads(atts)
        except json.JSONDecodeError:
            atts = []
    elif atts is None:
        atts = []
    item["attachments"] = atts if isinstance(atts, list) else []
    created = item.get("created_at")
    if created is not None and hasattr(created, "isoformat"):
        item["created_at"] = created.isoformat()
    return item


# 取最近 N 条再按时间正序返回（外层 ASC 保对话顺序）
_RECENT_MESSAGES_SQL = """
    SELECT role, content, token_total, attachments, created_at
    FROM (
        SELECT role, content, token_total, attachments, created_at
        FROM messages
        WHERE thread_id = $1
        ORDER BY created_at DESC
        LIMIT $2
    ) recent
    ORDER BY created_at ASC;
"""


async def get_messages(thread_id: str, limit: int = 200):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_RECENT_MESSAGES_SQL, thread_id, limit)
        return [_serialize_message_row(r) for r in rows]


async def get_messages_for_user(
    thread_id: str,
    user_id: int,
    limit: int = 200,
) -> list[dict]:
    """校验归属并拉取消息（同一连接，少一次 pool 往返）。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        owned = await conn.fetchrow(
            "SELECT 1 AS ok FROM conversations "
            "WHERE thread_id = $1 AND user_id = $2;",
            thread_id,
            user_id,
        )
        if not owned:
            raise ConversationAccessError("conversation not found")
        rows = await conn.fetch(_RECENT_MESSAGES_SQL, thread_id, limit)
        return [_serialize_message_row(r) for r in rows]


async def count_user_messages(thread_id: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*)::int AS c FROM messages "
            "WHERE thread_id = $1 AND role = 'user';",
            thread_id,
        )
        return int(row["c"]) if row else 0


async def clear_messages(thread_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM messages WHERE thread_id = $1;", thread_id)


async def update_conversation_title(thread_id: str, title: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE conversations SET title = $2, updated_at = NOW() WHERE thread_id = $1;",
            thread_id,
            title,
        )


# ---- knowledge files CRUD ----

_KB_SELECT_FIELDS = (
    "id, user_id, file_name, file_url, file_size, file_type, object_key, "
    "parse_status, parse_error, created_at"
)


def _serialize_kb_row(row) -> dict:
    data = dict(row)
    created = data.get("created_at")
    if created is not None and hasattr(created, "isoformat"):
        data["created_at"] = created.isoformat()
    return data


async def insert_knowledge_file(
    file_name: str,
    file_url: str,
    file_size: int,
    file_type: str,
    object_key: str,
    *,
    user_id: int,
    parse_status: str = "parsing",
) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO knowledge_files
                (user_id, file_name, file_url, file_size, file_type, object_key, parse_status)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING {_KB_SELECT_FIELDS};
            """,
            user_id,
            file_name,
            file_url,
            file_size,
            file_type,
            object_key,
            parse_status,
        )
        return _serialize_kb_row(row)


async def update_knowledge_file_parse_status(
    file_id: int,
    parse_status: str,
    parse_error: str = "",
) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE knowledge_files
            SET parse_status = $2, parse_error = $3
            WHERE id = $1
            RETURNING {_KB_SELECT_FIELDS};
            """,
            file_id,
            parse_status,
            parse_error or "",
        )
        return _serialize_kb_row(row) if row else None


async def list_knowledge_files(
    limit: int = 100,
    offset: int = 0,
    *,
    user_id: int | None = None,
):
    """列出知识库文件。user_id 为空时返回全部（共享知识库）。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id is None:
            rows = await conn.fetch(
                f"SELECT {_KB_SELECT_FIELDS} "
                "FROM knowledge_files "
                "ORDER BY created_at DESC LIMIT $1 OFFSET $2;",
                limit,
                offset,
            )
        else:
            rows = await conn.fetch(
                f"SELECT {_KB_SELECT_FIELDS} "
                "FROM knowledge_files WHERE user_id = $1 "
                "ORDER BY created_at DESC LIMIT $2 OFFSET $3;",
                user_id,
                limit,
                offset,
            )
        return [_serialize_kb_row(r) for r in rows]


async def get_knowledge_file(file_id: int, user_id: int | None = None) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id is None:
            row = await conn.fetchrow(
                f"SELECT {_KB_SELECT_FIELDS} FROM knowledge_files WHERE id = $1;",
                file_id,
            )
        else:
            row = await conn.fetchrow(
                f"SELECT {_KB_SELECT_FIELDS} FROM knowledge_files "
                "WHERE id = $1 AND user_id = $2;",
                file_id,
                user_id,
            )
        return _serialize_kb_row(row) if row else None


async def delete_knowledge_file(file_id: int, user_id: int | None = None) -> bool:
    """删除知识库记录。user_id 为空时按 id 删除（管理员）。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id is None:
            result = await conn.execute(
                "DELETE FROM knowledge_files WHERE id = $1;",
                file_id,
            )
        else:
            result = await conn.execute(
                "DELETE FROM knowledge_files WHERE id = $1 AND user_id = $2;",
                file_id,
                user_id,
            )
        return result.split()[-1] != "0"

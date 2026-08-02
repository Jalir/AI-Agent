"""声音克隆合成历史（按 user_id 隔离）。"""

from __future__ import annotations

from backend.db.database import get_pool

_SELECT = (
    "id, user_id, speak_text, model, speed, ref_file_name, "
    "audio_url, object_key, file_size, created_at"
)


def _serialize(row) -> dict:
    data = dict(row)
    created = data.get("created_at")
    if created is not None and hasattr(created, "isoformat"):
        data["created_at"] = created.isoformat()
    speed = data.get("speed")
    if speed is not None:
        data["speed"] = float(speed)
    return data


async def init_voice_clone_tables() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_clone_history (
                id            SERIAL PRIMARY KEY,
                user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                speak_text    TEXT NOT NULL,
                model         VARCHAR(128) NOT NULL DEFAULT '',
                speed         DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                ref_file_name VARCHAR(500) NOT NULL DEFAULT '',
                audio_url     TEXT NOT NULL,
                object_key    VARCHAR(500) NOT NULL DEFAULT '',
                file_size     BIGINT NOT NULL DEFAULT 0,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_voice_clone_history_user
                ON voice_clone_history(user_id, created_at DESC);
            """
        )


async def insert_history(
    *,
    user_id: int,
    speak_text: str,
    model: str,
    speed: float,
    ref_file_name: str,
    audio_url: str,
    object_key: str,
    file_size: int,
) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO voice_clone_history
                (user_id, speak_text, model, speed, ref_file_name,
                 audio_url, object_key, file_size)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING {_SELECT};
            """,
            user_id,
            speak_text,
            model,
            float(speed),
            ref_file_name or "",
            audio_url,
            object_key or "",
            int(file_size or 0),
        )
        return _serialize(row)


async def list_history(user_id: int, *, limit: int = 50, offset: int = 0) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_SELECT}
            FROM voice_clone_history
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3;
            """,
            user_id,
            max(1, min(int(limit), 100)),
            max(0, int(offset)),
        )
        return [_serialize(r) for r in rows]


async def get_history(item_id: int, user_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {_SELECT}
            FROM voice_clone_history
            WHERE id = $1 AND user_id = $2;
            """,
            item_id,
            user_id,
        )
        return _serialize(row) if row else None


async def delete_history(item_id: int, user_id: int) -> dict | None:
    """删除并返回被删行（便于清理 OSS）。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            DELETE FROM voice_clone_history
            WHERE id = $1 AND user_id = $2
            RETURNING {_SELECT};
            """,
            item_id,
            user_id,
        )
        return _serialize(row) if row else None


async def delete_all_history(user_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            DELETE FROM voice_clone_history
            WHERE user_id = $1
            RETURNING {_SELECT};
            """,
            user_id,
        )
        return [_serialize(r) for r in rows]

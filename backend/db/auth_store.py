"""用户与 refresh token 持久化。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from backend.common.security import hash_password, hash_token
from backend.config import settings
from backend.db.database import get_pool

# 鉴权热路径短缓存（进程内）；写用户时调用 invalidate
_USER_CACHE_TTL_SEC = 30.0
_user_cache: dict[int, tuple[float, dict]] = {}


def invalidate_user_cache(user_id: int | None = None) -> None:
    if user_id is None:
        _user_cache.clear()
        return
    _user_cache.pop(int(user_id), None)


def _serialize_user(row) -> dict:
    data = dict(row)
    for key in ("created_at", "updated_at"):
        val = data.get(key)
        if val is not None and hasattr(val, "isoformat"):
            data[key] = val.isoformat()
    return data


async def init_auth_tables() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id              SERIAL PRIMARY KEY,
                username        VARCHAR(64) UNIQUE NOT NULL,
                email           VARCHAR(255) UNIQUE,
                password_hash   TEXT NOT NULL,
                role            VARCHAR(20) NOT NULL DEFAULT 'user'
                    CHECK (role IN ('admin', 'user')),
                is_active       BOOLEAN NOT NULL DEFAULT TRUE,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                updated_at      TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id              SERIAL PRIMARY KEY,
                user_id         INTEGER NOT NULL
                    REFERENCES users(id) ON DELETE CASCADE,
                token_hash      VARCHAR(64) UNIQUE NOT NULL,
                expires_at      TIMESTAMPTZ NOT NULL,
                revoked_at      TIMESTAMPTZ,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user
                ON refresh_tokens(user_id);
        """)


async def ensure_admin_user() -> None:
    """若配置了 AUTH_ADMIN_PASSWORD 且库中无管理员，则创建初始管理员。"""
    password = (settings.auth_admin_password or "").strip()
    if not password:
        return

    username = (settings.auth_admin_username or "admin").strip() or "admin"
    email = (settings.auth_admin_email or "").strip() or None

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM users WHERE role = 'admin' LIMIT 1;"
        )
        if row:
            return
        existing = await conn.fetchrow(
            "SELECT id FROM users WHERE username = $1;", username
        )
        if existing:
            await conn.execute(
                "UPDATE users SET role = 'admin', password_hash = $2, "
                "is_active = TRUE, updated_at = NOW() WHERE id = $1;",
                existing["id"],
                hash_password(password),
            )
            return
        await conn.execute(
            """
            INSERT INTO users (username, email, password_hash, role)
            VALUES ($1, $2, $3, 'admin');
            """,
            username,
            email,
            hash_password(password),
        )


async def create_user(
    *,
    username: str,
    password: str,
    email: str | None = None,
    role: str = "user",
) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (username, email, password_hash, role)
            VALUES ($1, $2, $3, $4)
            RETURNING id, username, email, role, is_active, created_at, updated_at;
            """,
            username,
            email,
            hash_password(password),
            role,
        )
        return _serialize_user(row)


async def get_user_by_id(user_id: int) -> dict | None:
    uid = int(user_id)
    now = time.monotonic()
    cached = _user_cache.get(uid)
    if cached is not None:
        expires_at, data = cached
        if now < expires_at:
            return data
        _user_cache.pop(uid, None)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, email, password_hash, role, is_active, "
            "created_at, updated_at FROM users WHERE id = $1;",
            uid,
        )
        if not row:
            return None
        data = _serialize_user(row)
        _user_cache[uid] = (now + _USER_CACHE_TTL_SEC, data)
        return data


async def get_user_by_username(username: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, email, password_hash, role, is_active, "
            "created_at, updated_at FROM users WHERE username = $1;",
            username,
        )
        return _serialize_user(row) if row else None


async def list_users(limit: int = 100, offset: int = 0) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, username, email, role, is_active, created_at, updated_at "
            "FROM users ORDER BY id ASC LIMIT $1 OFFSET $2;",
            limit,
            offset,
        )
        return [_serialize_user(r) for r in rows]


async def count_admins() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE role = 'admin';"
            )
            or 0
        )


async def delete_user(user_id: int) -> bool:
    """硬删除用户行。

    调用方应先执行 ``purge_user_data`` 清理会话 / 工作区 / OSS 等；
    此处再吊销 token 并删用户，剩余 FK（refresh_tokens 等）靠 CASCADE。
    """
    uid = int(user_id)
    await revoke_all_refresh_tokens(uid)
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute("DELETE FROM users WHERE id = $1;", uid)
    invalidate_user_cache(uid)
    return str(status).endswith("DELETE 1")


async def lookup_users_for_recipient(
    query: str,
    *,
    allow_fuzzy: bool = False,
    limit: int = 5,
) -> list[dict]:
    """按用户名 / 邮箱解析收件人（仅返回公开字段）。

    - 默认：username / email 精确匹配（大小写不敏感）
    - allow_fuzzy=True（管理员）：额外允许 username 前缀模糊匹配
    """
    q = (query or "").strip()
    if not q:
        return []
    lim = max(1, min(int(limit), 20))
    pool = await get_pool()
    async with pool.acquire() as conn:
        if allow_fuzzy:
            rows = await conn.fetch(
                """
                SELECT id, username, email, role, is_active
                FROM users
                WHERE is_active = TRUE
                  AND (
                    LOWER(username) = LOWER($1)
                    OR (email IS NOT NULL AND LOWER(email) = LOWER($1))
                    OR username ILIKE $2
                  )
                ORDER BY
                  CASE WHEN LOWER(username) = LOWER($1) THEN 0
                       WHEN email IS NOT NULL AND LOWER(email) = LOWER($1) THEN 1
                       ELSE 2 END,
                  id ASC
                LIMIT $3;
                """,
                q,
                f"{q}%",
                lim,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, username, email, role, is_active
                FROM users
                WHERE is_active = TRUE
                  AND (
                    LOWER(username) = LOWER($1)
                    OR (email IS NOT NULL AND LOWER(email) = LOWER($1))
                  )
                ORDER BY id ASC
                LIMIT $2;
                """,
                q,
                lim,
            )
        return [
            {
                "id": int(r["id"]),
                "username": str(r["username"]),
                "email": r["email"],
                "role": str(r["role"]),
                "is_active": bool(r["is_active"]),
            }
            for r in rows
        ]


async def store_refresh_token(user_id: int, raw_token: str) -> None:
    expires = datetime.now(timezone.utc) + timedelta(
        days=max(1, int(settings.jwt_refresh_expire_days))
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
            VALUES ($1, $2, $3);
            """,
            user_id,
            hash_token(raw_token),
            expires,
        )


async def get_valid_refresh_token(raw_token: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, user_id, expires_at, revoked_at
            FROM refresh_tokens
            WHERE token_hash = $1;
            """,
            hash_token(raw_token),
        )
        if not row:
            return None
        if row["revoked_at"] is not None:
            return None
        expires = row["expires_at"]
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            return None
        return dict(row)


async def revoke_refresh_token(raw_token: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = NOW()
            WHERE token_hash = $1 AND revoked_at IS NULL;
            """,
            hash_token(raw_token),
        )


async def revoke_all_refresh_tokens(user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = NOW()
            WHERE user_id = $1 AND revoked_at IS NULL;
            """,
            user_id,
        )


async def rotate_refresh_token(old_raw: str, user_id: int, new_raw: str) -> None:
    """吊销旧 token 并写入新 token（同一事务）。"""
    expires = datetime.now(timezone.utc) + timedelta(
        days=max(1, int(settings.jwt_refresh_expire_days))
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE refresh_tokens
                SET revoked_at = NOW()
                WHERE token_hash = $1 AND revoked_at IS NULL;
                """,
                hash_token(old_raw),
            )
            await conn.execute(
                """
                INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
                VALUES ($1, $2, $3);
                """,
                user_id,
                hash_token(new_raw),
                expires,
            )

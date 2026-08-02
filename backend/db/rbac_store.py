"""RBAC：permissions 目录 + role_permissions 映射。"""

from __future__ import annotations

import logging
import time

from backend.common.permissions import (
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_CATALOG,
)
from backend.db.database import get_pool

logger = logging.getLogger(__name__)

_ROLE_PERM_CACHE_TTL_SEC = 30.0
_role_perm_cache: dict[str, tuple[float, frozenset[str]]] = {}


def invalidate_role_permission_cache(role: str | None = None) -> None:
    if role is None:
        _role_perm_cache.clear()
        return
    _role_perm_cache.pop(str(role).strip().lower(), None)


async def init_rbac_tables() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS permissions (
                code        VARCHAR(64) PRIMARY KEY,
                description TEXT NOT NULL DEFAULT '',
                created_at  TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS role_permissions (
                role            VARCHAR(20) NOT NULL
                    CHECK (role IN ('admin', 'user')),
                permission_code VARCHAR(64) NOT NULL
                    REFERENCES permissions(code) ON DELETE CASCADE,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (role, permission_code)
            );
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_role_permissions_role
                ON role_permissions(role);
        """)


async def seed_rbac() -> None:
    """幂等写入权限目录与默认角色映射。

    - 目录：upsert 描述
    - 角色映射：仅 INSERT … ON CONFLICT DO NOTHING（只增不删）
      人工撤销的权限不会被种子加回；新增默认码会在下次启动补上
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        for code, desc in PERMISSION_CATALOG.items():
            await conn.execute(
                """
                INSERT INTO permissions (code, description)
                VALUES ($1, $2)
                ON CONFLICT (code) DO UPDATE
                    SET description = EXCLUDED.description;
                """,
                code,
                desc,
            )

        for role, codes in DEFAULT_ROLE_PERMISSIONS.items():
            for code in sorted(codes):
                await conn.execute(
                    """
                    INSERT INTO role_permissions (role, permission_code)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING;
                    """,
                    role,
                    code,
                )

    invalidate_role_permission_cache()
    logger.info(
        "RBAC seeded: %d permission(s), default roles=%s",
        len(PERMISSION_CATALOG),
        sorted(DEFAULT_ROLE_PERMISSIONS),
    )


async def get_permissions_for_role(role: str) -> frozenset[str]:
    key = (role or "user").strip().lower() or "user"
    now = time.monotonic()
    cached = _role_perm_cache.get(key)
    if cached is not None:
        expires_at, data = cached
        if now < expires_at:
            return data
        _role_perm_cache.pop(key, None)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT permission_code FROM role_permissions WHERE role = $1;",
            key,
        )
    perms = frozenset(str(r["permission_code"]) for r in rows)
    _role_perm_cache[key] = (now + _ROLE_PERM_CACHE_TTL_SEC, perms)
    return perms


async def role_has_permission(role: str, code: str) -> bool:
    perms = await get_permissions_for_role(role)
    return code in perms


async def list_permission_catalog() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT code, description, created_at FROM permissions ORDER BY code ASC;"
        )
    out: list[dict] = []
    for r in rows:
        item = dict(r)
        created = item.get("created_at")
        if created is not None and hasattr(created, "isoformat"):
            item["created_at"] = created.isoformat()
        out.append(item)
    return out


async def list_role_permission_matrix() -> dict[str, list[str]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, permission_code FROM role_permissions ORDER BY role, permission_code;"
        )
    matrix: dict[str, list[str]] = {}
    for r in rows:
        role = str(r["role"])
        matrix.setdefault(role, []).append(str(r["permission_code"]))
    return matrix

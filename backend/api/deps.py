"""FastAPI 鉴权依赖。"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.common.security import decode_access_token
from backend.db.auth_store import get_user_by_id
from backend.db.rbac_store import get_permissions_for_role

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class AuthUser:
    id: int
    username: str
    email: str | None
    role: str
    is_active: bool
    permissions: frozenset[str] = frozenset()

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def has_permission(self, code: str) -> bool:
        return code in self.permissions


async def _to_auth_user(row: dict) -> AuthUser:
    role = str(row["role"])
    perms = await get_permissions_for_role(role)
    return AuthUser(
        id=int(row["id"]),
        username=str(row["username"]),
        email=row.get("email"),
        role=role,
        is_active=bool(row.get("is_active", True)),
        permissions=perms,
    )


def public_user(user: AuthUser | dict) -> dict:
    if isinstance(user, AuthUser):
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "is_active": user.is_active,
            "permissions": sorted(user.permissions),
        }
    # dict 路径（如 admin 列表）默认不查库灌权限，避免 N+1；需要时可另查
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user.get("email"),
        "role": user["role"],
        "is_active": user.get("is_active", True),
        "permissions": sorted(user["permissions"])
        if isinstance(user.get("permissions"), (set, frozenset, list, tuple))
        else [],
    }


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthUser:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(creds.credentials)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    row = await get_user_by_id(user_id)
    if not row or not row.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已禁用",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await _to_auth_user(row)


async def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


def require_permission(code: str):
    """依赖工厂：要求当前用户具备指定权限码。"""

    async def _checker(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if not user.has_permission(code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少权限：{code}",
            )
        return user

    return _checker

"""注册 / 登录 / 刷新 / 登出 / 当前用户 / 管理员用户管理。"""

from __future__ import annotations

import logging
import re

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from backend.api.deps import AuthUser, get_current_user, public_user, require_admin
from backend.common.security import (
    create_access_token,
    new_refresh_token,
    verify_password,
)
from backend.config import settings
from backend.db.auth_store import (
    count_admins,
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    get_valid_refresh_token,
    list_users,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
    store_refresh_token,
)
from backend.db.rbac_store import (
    get_permissions_for_role,
    list_permission_catalog,
    list_role_permission_matrix,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fff]{2,32}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)
    email: str | None = Field(None, max_length=255)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


def _normalize_username(raw: str) -> str:
    return (raw or "").strip()


def _normalize_email(raw: str | None) -> str | None:
    if raw is None:
        return None
    email = raw.strip().lower()
    return email or None


def _set_refresh_cookie(response: Response, token: str) -> None:
    samesite = (settings.auth_cookie_samesite or "lax").lower()
    if samesite not in ("lax", "strict", "none"):
        samesite = "lax"
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        secure=bool(settings.auth_cookie_secure) or samesite == "none",
        samesite=samesite,
        max_age=max(1, int(settings.jwt_refresh_expire_days)) * 86400,
        path="/api/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/api/auth",
    )


async def _public_user_with_perms(user: dict) -> dict:
    role = str(user.get("role") or "user")
    perms = await get_permissions_for_role(role)
    payload = {k: v for k, v in user.items() if k != "password_hash"}
    payload["permissions"] = perms
    return public_user(payload)


async def _token_response(user: dict, access_token: str) -> dict:
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": await _public_user_with_perms(user),
    }


async def _issue_tokens(response: Response, user: dict) -> dict:
    access = create_access_token(
        user_id=int(user["id"]),
        username=str(user["username"]),
        role=str(user["role"]),
    )
    refresh = new_refresh_token()
    await store_refresh_token(int(user["id"]), refresh)
    _set_refresh_cookie(response, refresh)
    return await _token_response(user, access)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, response: Response):
    username = _normalize_username(req.username)
    if not _USERNAME_RE.match(username):
        raise HTTPException(
            status_code=400,
            detail="用户名需为 2–32 位字母、数字、下划线或中文",
        )
    email = _normalize_email(req.email)
    if email and not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="邮箱格式无效")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")

    try:
        user = await create_user(
            username=username,
            password=req.password,
            email=email,
            role="user",
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="用户名或邮箱已被占用") from None

    logger.info("User registered: %s", username)
    return await _issue_tokens(response, user)


@router.post("/login")
async def login(req: LoginRequest, response: Response):
    username = _normalize_username(req.username)
    user = await get_user_by_username(username)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="账号已禁用")

    # 不把 password_hash 带回客户端
    safe = {k: v for k, v in user.items() if k != "password_hash"}
    logger.info("User login: %s", username)
    return await _issue_tokens(response, safe)


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    raw = request.cookies.get(settings.auth_cookie_name)
    if not raw:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")

    row = await get_valid_refresh_token(raw)
    if not row:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="未登录或登录已过期")

    user = await get_user_by_id(int(row["user_id"]))
    if not user or not user.get("is_active", True):
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")

    new_raw = new_refresh_token()
    await rotate_refresh_token(raw, int(user["id"]), new_raw)
    _set_refresh_cookie(response, new_raw)

    access = create_access_token(
        user_id=int(user["id"]),
        username=str(user["username"]),
        role=str(user["role"]),
    )
    safe = {k: v for k, v in user.items() if k != "password_hash"}
    return await _token_response(safe, access)


@router.post("/logout")
async def logout(request: Request, response: Response):
    raw = request.cookies.get(settings.auth_cookie_name)
    if raw:
        await revoke_refresh_token(raw)
    _clear_refresh_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(user: AuthUser = Depends(get_current_user)):
    return public_user(user)


@router.post("/logout-all")
async def logout_all(
    request: Request,
    response: Response,
    user: AuthUser = Depends(get_current_user),
):
    await revoke_all_refresh_tokens(user.id)
    _clear_refresh_cookie(response)
    return {"ok": True}


@router.get("/admin/users")
async def admin_list_users(
    limit: int = 100,
    offset: int = 0,
    _: AuthUser = Depends(require_admin),
):
    rows = await list_users(limit=min(limit, 200), offset=max(0, offset))
    result = []
    for r in rows:
        item = await _public_user_with_perms(r)
        item["created_at"] = r.get("created_at")
        item["updated_at"] = r.get("updated_at")
        result.append(item)
    return result


@router.delete("/admin/users/{user_id}")
async def admin_delete_user(
    user_id: int,
    admin: AuthUser = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")

    target = await get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")

    if str(target.get("role")) == "admin" and await count_admins() <= 1:
        raise HTTPException(status_code=400, detail="不能删除唯一的管理员账号")

    from backend.services.user_purge import purge_user_data

    try:
        purge_summary = await purge_user_data(user_id)
    except Exception:
        logger.exception(
            "Admin %s purge user data failed id=%s", admin.username, user_id
        )
        raise HTTPException(
            status_code=500,
            detail="清理用户关联数据失败，请稍后重试",
        ) from None

    ok = await delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="用户不存在")

    logger.info(
        "Admin %s deleted user id=%s username=%s purge=%s",
        admin.username,
        user_id,
        target.get("username"),
        purge_summary,
    )
    return {"ok": True, "purge": purge_summary}


@router.get("/admin/permissions")
async def admin_list_permissions(_: AuthUser = Depends(require_admin)):
    """权限目录 + 角色映射矩阵（只读；改映射目前走 DB / 后续管理接口）。"""
    return {
        "catalog": await list_permission_catalog(),
        "roles": await list_role_permission_matrix(),
    }

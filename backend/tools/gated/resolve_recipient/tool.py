"""解析收件人邮箱（只读，带权限）。"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from backend.db.auth_store import lookup_users_for_recipient

logger = logging.getLogger(__name__)

TOOL_NAME = "resolve_recipient"


def _payload(**kwargs: Any) -> str:
    return json.dumps(kwargs, ensure_ascii=False)


async def run_resolve_recipient(
    query: str,
    *,
    allow_fuzzy: bool = False,
) -> str:
    """按用户名/邮箱查询收件人，返回结构化 JSON 字符串供模型阅读。"""
    q = (query or "").strip()
    if not q:
        return _payload(
            status="invalid",
            message="查询词为空。请提供用户名或完整邮箱。",
            hint="ask_user_for_email",
        )

    try:
        rows = await lookup_users_for_recipient(
            q, allow_fuzzy=bool(allow_fuzzy), limit=5
        )
    except Exception:
        logger.exception("resolve_recipient DB lookup failed")
        return _payload(
            status="error",
            message="查询收件人失败，请稍后重试。",
        )

    if not rows:
        return _payload(
            status="not_found",
            query=q,
            message=f"未找到用户「{q}」。",
            hint="ask_user_for_email",
            guidance=(
                "请用自然语言向用户索要完整收件人邮箱地址；"
                "拿到真实邮箱后再调用 send_email，不要编造邮箱。"
            ),
        )

    if len(rows) > 1:
        candidates = [
            {
                "username": r["username"],
                "email": r["email"] or None,
                "has_email": bool(r.get("email")),
            }
            for r in rows
        ]
        return _payload(
            status="ambiguous",
            query=q,
            candidates=candidates,
            message=f"匹配到 {len(rows)} 个用户，请向用户确认是哪一位。",
            hint="ask_user_to_disambiguate",
        )

    user = rows[0]
    email = (user.get("email") or "").strip()
    if not email:
        return _payload(
            status="no_email",
            query=q,
            username=user["username"],
            message=f"用户「{user['username']}」未绑定邮箱。",
            hint="ask_user_for_email",
            guidance=(
                "请向用户索要该收件人的完整邮箱；"
                "确认后再调用 send_email，不要猜测或使用占位邮箱。"
            ),
        )

    return _payload(
        status="found",
        query=q,
        username=user["username"],
        email=email,
        message=f"已找到收件人 {user['username']} <{email}>。",
        guidance="请使用返回的 email 作为 send_email 的 to 参数。",
    )


@tool(TOOL_NAME)
async def resolve_recipient(query: str) -> str:
    """按用户名/邮箱查收件人真实邮箱。返回 JSON：found 用 email 发信；否则向用户索要，勿编造。

    Args:
        query: 用户名或完整邮箱
    """
    # 无 config 时仅精确匹配；tools_node 会按权限注入 allow_fuzzy
    return await run_resolve_recipient(query, allow_fuzzy=False)

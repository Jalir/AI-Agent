"""会话列表 / 消息 / 清空等 CRUD 路由（按用户隔离）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.api.deps import AuthUser, get_current_user
from backend.common.checkpoint import reset_thread
from backend.common.messages import normalize_stored_attachments
from backend.common.oss import resolve_attachment_url
from backend.db.database import (
    ConversationAccessError,
    assert_conversation_owner,
    clear_messages,
    delete_all_conversations,
    delete_conversation,
    get_messages_for_user,
    list_conversations,
    update_conversation_title,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])


@router.get("/api/conversations")
async def list_conversations_endpoint(
    limit: int = 50,
    offset: int = 0,
    user: AuthUser = Depends(get_current_user),
):
    rows = await list_conversations(user.id, limit, offset)
    return rows


@router.delete("/api/conversations")
async def delete_all_conversations_endpoint(
    user: AuthUser = Depends(get_current_user),
):
    """删除当前用户的全部会话与消息。"""
    thread_ids = await delete_all_conversations(user.id)
    for tid in thread_ids:
        await reset_thread(tid)
    logger.info("Deleted %d conversations for user=%s", len(thread_ids), user.id)
    return {"status": "deleted_all", "count": len(thread_ids)}


@router.delete("/api/conversations/{thread_id}")
async def delete_conversation_endpoint(
    thread_id: str,
    user: AuthUser = Depends(get_current_user),
):
    deleted = await delete_conversation(thread_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    await reset_thread(thread_id)
    return {"status": "deleted", "thread_id": thread_id}


@router.post("/api/conversations/{thread_id}/clear")
async def clear_conversation_endpoint(
    thread_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """Clear all messages for a conversation while keeping the entry."""
    try:
        await assert_conversation_owner(thread_id, user.id)
    except ConversationAccessError:
        raise HTTPException(status_code=404, detail="会话不存在") from None
    await clear_messages(thread_id)
    await update_conversation_title(thread_id, "新对话")
    await reset_thread(thread_id)
    return {"status": "cleared", "thread_id": thread_id}


@router.get("/api/conversations/{thread_id}/messages")
async def get_messages_endpoint(
    thread_id: str,
    limit: int = 200,
    user: AuthUser = Depends(get_current_user),
):
    try:
        rows = await get_messages_for_user(thread_id, user.id, limit)
    except ConversationAccessError:
        raise HTTPException(status_code=404, detail="会话不存在") from None
    for row in rows:
        atts = normalize_stored_attachments(row.get("attachments"))
        for att in atts:
            resolved = resolve_attachment_url(att) or att.get("url") or ""
            att["url"] = resolved
            # 小红书卡片展示图与 url 同步，便于前端用 image_url / url 回退
            if att.get("kind") == "xhs_card" and resolved:
                att["image_url"] = resolved
        row["attachments"] = atts
    return rows

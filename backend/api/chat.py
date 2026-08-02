"""Chat / approve / health 路由。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from backend.api.deps import AuthUser, get_current_user
from backend.api.schemas import ApproveRequest, ChatRequest
from backend.common.concurrency import RunSlot, ServerBusyError, acquire_run_slot
from backend.common.oss import (
    CHAT_AUDIO_MAX_BYTES,
    CHAT_AUDIO_MIME_TYPES,
    CHAT_IMAGE_MAX_BYTES,
    CHAT_IMAGE_MIME_TYPES,
    build_file_url,
    put_object,
    resolve_attachment_url,
)
from backend.common.stream import request_cancel
from backend.db.database import (
    ConversationAccessError,
    assert_conversation_owner,
    get_conversation,
)
from backend.services.chat import (
    SSE_HEADERS,
    _run_config,
    prepare_chat_turn,
    stream_graph,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


async def _sse_with_slot(
    chunks: AsyncIterator[str],
    slot: RunSlot,
) -> AsyncIterator[str]:
    """确保并发槽在流结束 / 客户端断开 / 生成器未启动时都会释放。"""
    try:
        async for chunk in chunks:
            yield chunk
    finally:
        slot.release()


@router.post("/api/chat/upload")
async def upload_chat_attachment(
    file: UploadFile = File(...),
    _user: AuthUser = Depends(get_current_user),
):
    """上传聊天附件到 OSS（图片或 mp3），返回永久 url + object_key。"""
    file_type = (file.content_type or "").lower().strip()
    file_name = file.filename or "file"
    name_lower = file_name.lower()

    # 部分浏览器对 mp3 给 application/octet-stream，按扩展名兜底
    if file_type not in CHAT_IMAGE_MIME_TYPES and file_type not in CHAT_AUDIO_MIME_TYPES:
        if name_lower.endswith(".mp3"):
            file_type = "audio/mpeg"
        else:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported file type: {file_type or 'unknown'}",
            )

    is_audio = file_type in CHAT_AUDIO_MIME_TYPES
    max_bytes = CHAT_AUDIO_MAX_BYTES if is_audio else CHAT_IMAGE_MAX_BYTES

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    if len(content) > max_bytes:
        limit_label = "50MB" if is_audio else "10MB"
        kind = "audio" if is_audio else "image"
        raise HTTPException(
            status_code=400,
            detail=f"{kind} too large (max {limit_label})",
        )

    unique_name = f"{uuid.uuid4().hex[:12]}_{file_name}"
    object_key = f"chat-attachments/{unique_name}"

    await asyncio.to_thread(put_object, object_key, content, file_type)
    file_url = build_file_url(object_key)
    display_url = resolve_attachment_url(
        {"url": file_url, "object_key": object_key}
    ) or file_url

    logger.info(
        "Uploaded chat %s: %s -> %s",
        "audio" if is_audio else "image",
        file_name,
        object_key,
    )
    return {
        "url": file_url,
        "display_url": display_url,
        "object_key": object_key,
        "mime_type": file_type,
        "name": file_name,
        "file_size": len(content),
    }


class StopChatRequest(BaseModel):
    thread_id: str = Field(..., description="要停止生成的会话 thread_id")


async def _start_chat(
    *,
    thread_id: str,
    message: str,
    intent: str | None,
    attachments: list[dict] | None,
    request: Request,
    user_id: int,
    user_role: str | None = None,
    workspace_id: int | None = None,
    sales_workspace_id: int | None = None,
) -> StreamingResponse:
    milvus_collection: str | None = None
    from backend.db.workspace_store import (
        get_workspace_by_thread_for_user,
        get_workspace_for_user,
        touch_workspace,
    )
    from backend.db import sales_workspace_store as sales_store

    # 销售分析 thread：强制绑定销售工作区，禁止掉进共享库/文档 RAG
    if sales_workspace_id is None:
        sa_bound = await sales_store.get_workspace_by_thread_for_user(thread_id, user_id)
        if sa_bound:
            if (sa_bound.get("status") or "") != "active":
                raise HTTPException(
                    status_code=400,
                    detail="销售分析区已关闭或过期，请重新打开",
                )
            sales_workspace_id = int(sa_bound["id"])

    if sales_workspace_id is not None:
        sa = await sales_store.get_workspace_for_user(int(sales_workspace_id), user_id)
        if not sa:
            raise HTTPException(status_code=404, detail="销售分析区不存在或已过期")
        if (sa.get("thread_id") or "").strip() != (thread_id or "").strip():
            raise HTTPException(status_code=400, detail="会话与销售分析区不匹配")
        await sales_store.touch_workspace(int(sales_workspace_id), renew_ttl=True)
        # 销售模式与文档工作区互斥
        workspace_id = None

    # 工作区 thread 禁止无 workspace 配置进入共享库模式
    if workspace_id is None and sales_workspace_id is None:
        bound = await get_workspace_by_thread_for_user(thread_id, user_id)
        if bound:
            if (bound.get("status") or "") != "active":
                raise HTTPException(
                    status_code=400,
                    detail="工作区已关闭或过期，请在文档工作区重新打开",
                )
            workspace_id = int(bound["id"])

    if workspace_id is not None:
        ws = await get_workspace_for_user(int(workspace_id), user_id)
        if not ws:
            raise HTTPException(status_code=404, detail="工作区不存在或已过期")
        if (ws.get("thread_id") or "").strip() != (thread_id or "").strip():
            raise HTTPException(status_code=400, detail="会话与工作区不匹配")
        milvus_collection = (ws.get("milvus_collection") or "").strip()
        if not milvus_collection:
            raise HTTPException(status_code=500, detail="工作区索引配置异常")
        await touch_workspace(int(workspace_id), renew_ttl=True)

    try:
        run_slot = await acquire_run_slot()
    except ServerBusyError as e:
        raise HTTPException(status_code=503, detail=str(e) or "服务繁忙，请稍后重试。") from e

    try:
        input_data, config, is_first = await prepare_chat_turn(
            thread_id=thread_id,
            message=message,
            intent=intent,
            attachments=attachments,
            user_id=user_id,
            user_role=user_role,
            workspace_id=workspace_id,
            milvus_collection=milvus_collection,
            sales_workspace_id=sales_workspace_id,
        )
    except ConversationAccessError as e:
        run_slot.release()
        raise HTTPException(status_code=404, detail="会话不存在") from e
    except ValueError as e:
        run_slot.release()
        raise HTTPException(
            status_code=400,
            detail="消息或附件不能为空",
        ) from e
    except Exception:
        run_slot.release()
        raise

    return StreamingResponse(
        _sse_with_slot(
            stream_graph(
                input_data,
                config,
                thread_id,
                generate_title_flag=is_first,
                request=request,
                run_slot=run_slot,
            ),
            run_slot,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/api/chat")
async def chat_post(
    req: ChatRequest,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    """发送消息（支持图片/音频附件），SSE 流式回复。"""
    atts = [a.model_dump() for a in (req.attachments or [])]
    return await _start_chat(
        thread_id=req.thread_id,
        message=req.message,
        intent=req.intent,
        attachments=atts,
        request=request,
        user_id=user.id,
        user_role=user.role,
        workspace_id=req.workspace_id,
        sales_workspace_id=req.sales_workspace_id,
    )


@router.get("/api/chat")
async def chat_endpoint(
    request: Request,
    thread_id: str = Query(...),
    message: str = Query(...),
    intent: str | None = Query(
        None,
        description=(
            "可选：前端意图提示 chat|rag|media_gen|xhs_pack|image_edit|speech_recognize"
            "（软提示；产品模式由 agent 选工具）"
        ),
    ),
    user: AuthUser = Depends(get_current_user),
):
    """兼容旧版纯文本 GET。"""
    return await _start_chat(
        thread_id=thread_id,
        message=message,
        intent=intent,
        attachments=None,
        request=request,
        user_id=user.id,
        user_role=user.role,
    )


@router.post("/api/chat/stop")
async def chat_stop(
    req: StopChatRequest,
    user: AuthUser = Depends(get_current_user),
):
    """停止指定会话的进行中生成（取消 graph 任务 + 协作 Event）。"""
    try:
        await assert_conversation_owner(req.thread_id, user.id)
    except ConversationAccessError:
        # 新会话尚未落库时允许 stop；已存在但非本人则 404
        if await get_conversation(req.thread_id) is not None:
            raise HTTPException(status_code=404, detail="会话不存在") from None
    stopped = request_cancel(req.thread_id)
    return {"ok": True, "stopped": stopped}


@router.post("/api/approve")
async def approve_endpoint(
    req: ApproveRequest,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    try:
        await assert_conversation_owner(req.thread_id, user.id)
    except ConversationAccessError as e:
        raise HTTPException(status_code=404, detail="会话不存在") from e

    try:
        run_slot = await acquire_run_slot()
    except ServerBusyError as e:
        raise HTTPException(status_code=503, detail=str(e) or "服务繁忙，请稍后重试。") from e

    from backend.db.workspace_store import (
        get_workspace_by_thread_for_user,
        get_workspace_for_user,
    )
    from backend.db import sales_workspace_store as sales_store

    workspace_id = req.workspace_id
    sales_workspace_id = req.sales_workspace_id
    milvus_collection: str | None = None

    if sales_workspace_id is None:
        sa_bound = await sales_store.get_workspace_by_thread_for_user(
            req.thread_id, user.id
        )
        if sa_bound and (sa_bound.get("status") or "") == "active":
            sales_workspace_id = int(sa_bound["id"])
    if sales_workspace_id is not None:
        sa = await sales_store.get_workspace_for_user(int(sales_workspace_id), user.id)
        if not sa:
            raise HTTPException(status_code=404, detail="销售分析区不存在或已过期")
        if (sa.get("thread_id") or "").strip() != (req.thread_id or "").strip():
            raise HTTPException(status_code=400, detail="会话与销售分析区不匹配")
        workspace_id = None

    if workspace_id is None and sales_workspace_id is None:
        bound = await get_workspace_by_thread_for_user(req.thread_id, user.id)
        if bound and (bound.get("status") or "") == "active":
            workspace_id = int(bound["id"])
    if workspace_id is not None:
        ws = await get_workspace_for_user(int(workspace_id), user.id)
        if not ws:
            raise HTTPException(status_code=404, detail="工作区不存在或已过期")
        if (ws.get("thread_id") or "").strip() != (req.thread_id or "").strip():
            raise HTTPException(status_code=400, detail="会话与工作区不匹配")
        milvus_collection = (ws.get("milvus_collection") or "").strip() or None
        if not milvus_collection:
            raise HTTPException(status_code=500, detail="工作区索引配置异常")

    config = _run_config(
        req.thread_id,
        user_id=user.id,
        user_role=user.role,
        workspace_id=workspace_id,
        milvus_collection=milvus_collection,
        sales_workspace_id=sales_workspace_id,
        operation="resume",
    )
    resume_payload: dict = {
        "approved": req.approved,
        "reason": req.reason,
    }
    if req.approved and isinstance(req.edited_args, dict) and req.edited_args:
        resume_payload["edited_args"] = req.edited_args
    resume_cmd = Command(resume=resume_payload)

    return StreamingResponse(
        _sse_with_slot(
            stream_graph(
                resume_cmd,
                config,
                req.thread_id,
                request=request,
                run_slot=run_slot,
            ),
            run_slot,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/api/health")
async def health():
    return {"status": "ok"}

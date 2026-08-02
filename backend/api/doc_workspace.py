"""文档工作区：临时 RAG（独立 Milvus collection + PG 表），顶部材料 + Chat。"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from backend.api.deps import AuthUser, get_current_user
from backend.common.oss import build_file_url, put_object, resolve_attachment_url
from backend.common.stream import request_cancel
from backend.db import workspace_store
from backend.services.workspace_gc import purge_workspace
from backend.services.workspace_rag import (
    remove_workspace_file_index,
    run_workspace_parse_task,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/doc-workspace", tags=["doc-workspace"])

MAX_FILE_BYTES = 20 * 1024 * 1024
SUPPORTED_SUFFIXES = (".docx", ".txt", ".pdf")


def _is_supported(name: str) -> bool:
    lower = (name or "").lower()
    return any(lower.endswith(s) for s in SUPPORTED_SUFFIXES)


def _mime_for_name(file_name: str) -> str:
    lower = (file_name or "").lower()
    if lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if lower.endswith(".txt"):
        return "text/plain; charset=utf-8"
    if lower.endswith(".pdf"):
        return "application/pdf"
    return "application/octet-stream"


def _file_public(row: dict) -> dict:
    """对外文件信息：不回永久裸链，优先签名 URL。"""
    key = (row.get("object_key") or "").strip()
    signed = resolve_attachment_url(
        {"url": row.get("file_url") or "", "object_key": key}
    ) or ""
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "file_name": row["file_name"],
        "file_size": row["file_size"],
        "file_type": row["file_type"],
        "parse_status": row["parse_status"],
        "parse_error": row.get("parse_error") or "",
        "char_count": row.get("char_count") or 0,
        "created_at": row.get("created_at") or "",
        "display_url": signed,
    }


@router.post("/ensure")
async def ensure_workspace(user: AuthUser = Depends(get_current_user)):
    """获取或创建当前用户的活跃工作区（含绑定 thread_id）。"""
    existing = await workspace_store.get_latest_workspace_for_user(user.id)
    if existing:
        wid = int(existing["id"])
        await workspace_store.touch_workspace(wid, renew_ttl=True)
        existing = await workspace_store.get_workspace(wid) or existing
        files = await workspace_store.list_workspace_files(wid)
        return {
            "workspace": workspace_store.public_workspace_view(existing),
            "files": [_file_public(f) for f in files],
        }

    thread_id = f"ws-{user.id}-{uuid.uuid4().hex[:16]}"
    ws = await workspace_store.create_workspace(
        user_id=user.id,
        thread_id=thread_id,
        title="文档工作区",
    )
    logger.info(
        "doc-workspace created user=%s id=%s coll=%s",
        user.id,
        ws["id"],
        ws.get("milvus_collection"),
    )
    return {
        "workspace": workspace_store.public_workspace_view(ws),
        "files": [],
    }


@router.get("/{workspace_id}")
async def get_workspace_detail(
    workspace_id: int,
    user: AuthUser = Depends(get_current_user),
):
    ws = await workspace_store.get_workspace_for_user(workspace_id, user.id)
    if not ws:
        raise HTTPException(status_code=404, detail="工作区不存在或已过期")
    files = await workspace_store.list_workspace_files(workspace_id)
    return {
        "workspace": workspace_store.public_workspace_view(ws),
        "files": [_file_public(f) for f in files],
    }


@router.post("/{workspace_id}/upload")
async def upload_workspace_file(
    workspace_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
):
    ws = await workspace_store.get_workspace_for_user(workspace_id, user.id)
    if not ws:
        raise HTTPException(status_code=404, detail="工作区不存在或已过期")

    file_name = file.filename or "document.txt"
    if not _is_supported(file_name):
        raise HTTPException(status_code=400, detail="仅支持 DOCX / TXT / PDF")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail="文件过大（上限 20MB）")

    file_type = (file.content_type or "").strip() or _mime_for_name(file_name)
    unique_name = f"{uuid.uuid4().hex[:12]}_{file_name}"
    object_key = f"doc-analysis/{user.id}/ws{workspace_id}/{unique_name}"
    await asyncio.to_thread(put_object, object_key, content, file_type)
    file_url = build_file_url(object_key)

    row = await workspace_store.insert_workspace_file(
        workspace_id=workspace_id,
        file_name=file_name,
        file_url=file_url,
        object_key=object_key,
        file_size=len(content),
        file_type=file_type,
        parse_status=workspace_store.PARSE_PARSING,
    )

    background_tasks.add_task(
        run_workspace_parse_task,
        workspace_id=workspace_id,
        file_id=int(row["id"]),
        file_url=file_url,
        file_name=file_name,
        object_key=object_key,
    )

    return _file_public(row)


@router.delete("/{workspace_id}/files/{file_id}")
async def delete_workspace_file(
    workspace_id: int,
    file_id: int,
    user: AuthUser = Depends(get_current_user),
):
    ws = await workspace_store.get_workspace_for_user(workspace_id, user.id)
    if not ws:
        raise HTTPException(status_code=404, detail="工作区不存在或已过期")

    row = await workspace_store.get_workspace_file(file_id, workspace_id)
    if not row:
        raise HTTPException(status_code=404, detail="文件不存在")

    tid = (ws.get("thread_id") or "").strip()
    if tid:
        request_cancel(tid)

    await remove_workspace_file_index(
        workspace_id=workspace_id,
        file_id=file_id,
        file_name=row["file_name"],
        object_key=row.get("object_key") or "",
    )
    await workspace_store.delete_workspace_file_row(file_id, workspace_id)
    return {"ok": True}


@router.post("/{workspace_id}/clear-chat")
async def clear_workspace_chat(
    workspace_id: int,
    user: AuthUser = Depends(get_current_user),
):
    """清空工作区对话消息（PG messages + LangGraph checkpoint），保留已上传材料。"""
    from backend.common.checkpoint import reset_thread
    from backend.db.database import clear_messages, update_conversation_title

    ws = await workspace_store.get_workspace_for_user(workspace_id, user.id)
    if not ws:
        raise HTTPException(status_code=404, detail="工作区不存在或已过期")

    tid = (ws.get("thread_id") or "").strip()
    if not tid:
        raise HTTPException(status_code=500, detail="工作区会话未配置")

    request_cancel(tid)
    await clear_messages(tid)
    await reset_thread(tid)
    try:
        await update_conversation_title(tid, "文档工作区")
    except Exception:
        logger.exception("clear-chat update title failed ws=%s tid=%s", workspace_id, tid)
    await workspace_store.touch_workspace(workspace_id, renew_ttl=True)
    logger.info("doc-workspace chat cleared user=%s ws=%s tid=%s", user.id, workspace_id, tid)
    return {"ok": True, "thread_id": tid}


@router.post("/{workspace_id}/reset")
async def reset_workspace(
    workspace_id: int,
    user: AuthUser = Depends(get_current_user),
):
    """清空材料并销毁临时索引；保留同 thread 可继续聊（需重新上传）。"""
    ws = await workspace_store.get_workspace_for_user(workspace_id, user.id)
    if not ws:
        raise HTTPException(status_code=404, detail="工作区不存在或已过期")

    tid = (ws.get("thread_id") or "").strip()
    if tid:
        request_cancel(tid)

    files = await workspace_store.list_workspace_files(workspace_id)
    for f in files:
        await remove_workspace_file_index(
            workspace_id=workspace_id,
            file_id=int(f["id"]),
            file_name=f["file_name"],
            object_key=f.get("object_key") or "",
        )
        await workspace_store.delete_workspace_file_row(int(f["id"]), workspace_id)

    try:
        from backend.indexing.milvus_client import get_milvus_service

        coll = ws.get("milvus_collection") or workspace_store.milvus_collection_for_workspace(
            workspace_id
        )
        get_milvus_service(collection=coll).drop_collection()
    except Exception:
        logger.exception("reset drop collection failed ws=%s", workspace_id)

    return {"ok": True}


@router.delete("/{workspace_id}")
async def close_workspace(
    workspace_id: int,
    user: AuthUser = Depends(get_current_user),
):
    ws = await workspace_store.get_workspace(workspace_id)
    if not ws or int(ws.get("user_id") or 0) != int(user.id):
        raise HTTPException(status_code=404, detail="工作区不存在")
    if (ws.get("status") or "") not in ("active", "closed"):
        raise HTTPException(status_code=404, detail="工作区不存在")

    tid = (ws.get("thread_id") or "").strip()
    if tid:
        request_cancel(tid)

    result = await purge_workspace(workspace_id)
    return {"ok": True, "purged": result}

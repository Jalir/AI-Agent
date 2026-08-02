"""销售分析工作区：仅 Excel → 结构化表 + Chat 分析/出图/报告。"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from backend.api.deps import AuthUser, get_current_user
from backend.common.oss import build_file_url, delete_object, put_object, resolve_attachment_url
from backend.common.stream import request_cancel
from backend.config import settings
from backend.db import sales_workspace_store as store
from backend.services.sales_ingest import run_sales_parse_task
from backend.services.sales_query import list_workspace_tables
from backend.services.sales_workspace_gc import purge_sales_workspace

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sales-workspace", tags=["sales-workspace"])

MAX_FILE_BYTES = max(
    1_000_000,
    int(getattr(settings, "sales_max_file_bytes", 20 * 1024 * 1024) or 20 * 1024 * 1024),
)
SUPPORTED_SUFFIXES = (".xlsx",)


def _is_supported(name: str) -> bool:
    lower = (name or "").lower()
    return any(lower.endswith(s) for s in SUPPORTED_SUFFIXES)


def _file_public(row: dict) -> dict:
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
        "sheet_count": row.get("sheet_count") or 0,
        "row_count": row.get("row_count") or 0,
        "created_at": row.get("created_at") or "",
        "display_url": signed,
    }


@router.post("/ensure")
async def ensure_workspace(user: AuthUser = Depends(get_current_user)):
    existing = await store.get_latest_workspace_for_user(user.id)
    if existing:
        wid = int(existing["id"])
        await store.touch_workspace(wid, renew_ttl=True)
        existing = await store.get_workspace(wid) or existing
        files = await store.list_workspace_files(wid)
        tables = await list_workspace_tables(wid)
        return {
            "workspace": store.public_workspace_view(existing),
            "files": [_file_public(f) for f in files],
            "tables": tables,
        }

    thread_id = f"sa-{user.id}-{uuid.uuid4().hex[:16]}"
    ws = await store.create_workspace(
        user_id=user.id,
        thread_id=thread_id,
        title="销售分析",
    )
    logger.info("sales-workspace created user=%s id=%s", user.id, ws["id"])
    return {
        "workspace": store.public_workspace_view(ws),
        "files": [],
        "tables": [],
    }


@router.get("/{workspace_id}")
async def get_workspace_detail(
    workspace_id: int,
    user: AuthUser = Depends(get_current_user),
):
    ws = await store.get_workspace_for_user(workspace_id, user.id)
    if not ws:
        raise HTTPException(status_code=404, detail="销售分析区不存在或已过期")
    files = await store.list_workspace_files(workspace_id)
    tables = await list_workspace_tables(workspace_id)
    return {
        "workspace": store.public_workspace_view(ws),
        "files": [_file_public(f) for f in files],
        "tables": tables,
    }


@router.post("/{workspace_id}/upload")
async def upload_workspace_file(
    workspace_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
):
    ws = await store.get_workspace_for_user(workspace_id, user.id)
    if not ws:
        raise HTTPException(status_code=404, detail="销售分析区不存在或已过期")

    name = file.filename or "data.xlsx"
    if not _is_supported(name):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大（最大 {MAX_FILE_BYTES // (1024 * 1024)}MB）",
        )

    unique = f"{uuid.uuid4().hex[:12]}_{name}"
    object_key = f"sales-analysis/{user.id}/ws{workspace_id}/{unique}"
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    await asyncio.to_thread(put_object, object_key, content, mime)
    file_url = build_file_url(object_key)

    row = await store.insert_workspace_file(
        workspace_id,
        file_name=name,
        file_url=file_url,
        object_key=object_key,
        file_size=len(content),
        file_type=mime,
    )
    background_tasks.add_task(
        run_sales_parse_task,
        workspace_id,
        int(row["id"]),
        content,
    )
    return _file_public(row)


@router.delete("/{workspace_id}/files/{file_id}")
async def delete_workspace_file(
    workspace_id: int,
    file_id: int,
    user: AuthUser = Depends(get_current_user),
):
    ws = await store.get_workspace_for_user(workspace_id, user.id)
    if not ws:
        raise HTTPException(status_code=404, detail="销售分析区不存在或已过期")
    existing = await store.get_workspace_file(file_id, workspace_id)
    if not existing:
        raise HTTPException(status_code=404, detail="文件不存在")

    await store.delete_tables_for_file(file_id)
    deleted = await store.delete_workspace_file_row(file_id, workspace_id)
    key = (existing.get("object_key") or "").strip()
    if key:
        try:
            await asyncio.to_thread(delete_object, key)
        except Exception:
            logger.exception("sales file oss delete failed key=%s", key)
    return {"ok": True, "file": _file_public(deleted) if deleted else None}


@router.post("/{workspace_id}/clear-chat")
async def clear_workspace_chat(
    workspace_id: int,
    user: AuthUser = Depends(get_current_user),
):
    """清空销售分析区对话（PG messages + checkpoint），保留已上传 Excel 表数据。"""
    from backend.common.checkpoint import reset_thread
    from backend.db.database import clear_messages, update_conversation_title

    ws = await store.get_workspace_for_user(workspace_id, user.id)
    if not ws:
        raise HTTPException(status_code=404, detail="销售分析区不存在或已过期")

    tid = (ws.get("thread_id") or "").strip()
    if not tid:
        raise HTTPException(status_code=500, detail="销售分析区会话未配置")

    request_cancel(tid)
    await clear_messages(tid)
    await reset_thread(tid)
    try:
        await update_conversation_title(tid, "销售分析")
    except Exception:
        logger.exception("sales clear-chat update title failed ws=%s tid=%s", workspace_id, tid)
    await store.touch_workspace(workspace_id, renew_ttl=True)
    logger.info("sales-workspace chat cleared user=%s ws=%s tid=%s", user.id, workspace_id, tid)
    return {"ok": True, "thread_id": tid}


@router.post("/{workspace_id}/reset")
async def reset_workspace(
    workspace_id: int,
    user: AuthUser = Depends(get_current_user),
):
    ws = await store.get_workspace_for_user(workspace_id, user.id)
    if not ws:
        raise HTTPException(status_code=404, detail="销售分析区不存在或已过期")
    files = await store.list_workspace_files(workspace_id)
    for f in files:
        fid = int(f["id"])
        await store.delete_tables_for_file(fid)
        await store.delete_workspace_file_row(fid, workspace_id)
        key = (f.get("object_key") or "").strip()
        if key:
            try:
                await asyncio.to_thread(delete_object, key)
            except Exception:
                logger.exception("sales reset oss delete failed")
    tid = (ws.get("thread_id") or "").strip()
    if tid:
        request_cancel(tid)
    await store.touch_workspace(workspace_id, renew_ttl=True)
    return {"ok": True}


@router.delete("/{workspace_id}")
async def close_workspace(
    workspace_id: int,
    user: AuthUser = Depends(get_current_user),
):
    ws = await store.get_workspace_for_user(workspace_id, user.id)
    if not ws:
        raise HTTPException(status_code=404, detail="销售分析区不存在或已过期")
    return await purge_sales_workspace(workspace_id)

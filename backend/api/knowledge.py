"""知识库上传 / 列表 / 删除：全员可读，仅管理员可上传与删除。"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile

from backend.api.deps import AuthUser, get_current_user, require_admin
from backend.api.schemas import SaveFileRequest
from backend.common.errors import sanitize_public_text
from backend.common.oss import (
    build_file_url,
    delete_object,
    put_object,
    sign_put_url,
)
from backend.db.database import (
    delete_knowledge_file,
    get_knowledge_file,
    insert_knowledge_file,
    list_knowledge_files,
)
from backend.services.knowledge import PARSE_PARSING, run_parse_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/upload-signature")
async def get_upload_signature(
    file_name: str = Query(...),
    file_type: str = Query(...),
    _admin: AuthUser = Depends(require_admin),
):
    """Generate a presigned PUT URL for direct-to-OSS upload（仅管理员）。"""
    unique_name = f"{uuid.uuid4().hex[:12]}_{file_name}"
    object_key = f"knowledge-base/{unique_name}"

    upload_url = sign_put_url(object_key, file_type, expires=300)
    file_url = build_file_url(object_key)

    logger.info("Generated upload signature for object: %s", object_key)
    return {
        "upload_url": upload_url,
        "file_url": file_url,
        "object_key": object_key,
    }


@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    admin: AuthUser = Depends(require_admin),
):
    """上传到 OSS 并立即返回；文档解析在后台异步执行（仅管理员）。"""
    content = await file.read()
    file_type = file.content_type or "application/octet-stream"
    file_name = file.filename or "document"

    unique_name = f"{uuid.uuid4().hex[:12]}_{file_name}"
    object_key = f"knowledge-base/{unique_name}"

    await asyncio.to_thread(put_object, object_key, content, file_type)
    file_url = build_file_url(object_key)

    row = await insert_knowledge_file(
        file_name,
        file_url,
        len(content),
        file_type,
        object_key,
        user_id=admin.id,
        parse_status=PARSE_PARSING,
    )
    logger.info("Uploaded knowledge file: %s -> %s (id=%s)", file_name, object_key, row["id"])

    background_tasks.add_task(
        run_parse_task,
        file_id=row["id"],
        file_url=file_url,
        file_name=file_name,
        object_key=object_key,
    )

    return row


@router.post("/files")
async def save_file_record(
    req: SaveFileRequest,
    background_tasks: BackgroundTasks,
    admin: AuthUser = Depends(require_admin),
):
    """直传 OSS 后保存元数据，并后台解析（仅管理员）。"""
    row = await insert_knowledge_file(
        req.file_name,
        req.file_url,
        req.file_size,
        req.file_type,
        req.object_key,
        user_id=admin.id,
        parse_status=PARSE_PARSING,
    )
    logger.info("Saved knowledge file record: %s", row["id"])

    background_tasks.add_task(
        run_parse_task,
        file_id=row["id"],
        file_url=req.file_url,
        file_name=req.file_name,
        object_key=req.object_key,
    )

    return row


def _public_kb_row(row: dict | None) -> dict | None:
    """列表/详情对外：parse_error 再过安全闸（兼容旧库脏数据）。"""
    if not row:
        return row
    out = dict(row)
    pe = out.get("parse_error")
    if pe:
        out["parse_error"] = sanitize_public_text(
            str(pe), fallback="解析失败，请稍后重试或更换文件。"
        )
    return out


@router.get("/files")
async def list_file_records(
    limit: int = 100,
    offset: int = 0,
    _user: AuthUser = Depends(get_current_user),
):
    """列出全部知识库文件（登录用户可读）。"""
    rows = await list_knowledge_files(limit, offset)
    return [_public_kb_row(r) for r in rows]


@router.get("/files/{file_id}")
async def get_file_record(
    file_id: int,
    _user: AuthUser = Depends(get_current_user),
):
    """获取单个知识库文件（含解析状态）。"""
    record = await get_knowledge_file(file_id)
    if not record:
        return {"status": "not_found"}
    return _public_kb_row(record)


@router.delete("/files/{file_id}")
async def delete_file_record(
    file_id: int,
    _admin: AuthUser = Depends(require_admin),
):
    """删除知识库文件记录及其 OSS 对象（仅管理员）。"""
    record = await get_knowledge_file(file_id)
    if not record:
        raise HTTPException(status_code=404, detail="文件不存在")

    try:
        delete_object(record["object_key"])
        logger.info("Deleted OSS object: %s", record["object_key"])
    except Exception as e:
        logger.warning("Failed to delete OSS object %s: %s", record["object_key"], e)

    await delete_knowledge_file(file_id)
    logger.info("Deleted knowledge file record: %d", file_id)
    return {"status": "deleted", "id": file_id}

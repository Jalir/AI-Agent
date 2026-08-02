"""删除用户前的关联数据清理（会话 / 工作区 / OSS / checkpoint）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.common.audio_segment import user_transcribe_prefix
from backend.common.audio_trim import user_voice_clone_prefix
from backend.common.checkpoint import reset_thread
from backend.common.oss import delete_object, delete_prefix
from backend.db import sales_workspace_store, workspace_store
from backend.db.database import (
    delete_all_conversations_for_user,
    detach_knowledge_files_owner,
    list_message_attachment_keys_for_user,
    list_thread_ids_for_user,
)
from backend.db.voice_clone_store import delete_all_history
from backend.services.sales_workspace_gc import purge_sales_workspace
from backend.services.workspace_gc import purge_workspace

logger = logging.getLogger(__name__)


def _safe_delete_key(object_key: str) -> bool:
    key = (object_key or "").strip()
    if not key:
        return False
    try:
        delete_object(key)
        return True
    except Exception:
        logger.warning("user purge oss delete failed key=%s", key, exc_info=True)
        return False


async def _purge_doc_workspaces(user_id: int) -> dict[str, Any]:
    ids = await workspace_store.list_workspace_ids_for_user(user_id)
    results: list[dict[str, Any]] = []
    for wid in ids:
        try:
            results.append(await purge_workspace(wid))
        except Exception:
            logger.exception("user purge doc workspace failed user=%s ws=%s", user_id, wid)
            results.append({"workspace_id": wid, "ok": False})

    # 若 Milvus 失败导致行残留，强制删元数据，避免挡住删用户
    leftover = await workspace_store.list_workspace_ids_for_user(user_id)
    forced = 0
    for wid in leftover:
        try:
            files = await workspace_store.list_workspace_files(wid)
            for f in files:
                _safe_delete_key(str(f.get("object_key") or ""))
            if await workspace_store.delete_workspace_row(wid):
                forced += 1
        except Exception:
            logger.exception(
                "user purge force-delete doc workspace failed user=%s ws=%s",
                user_id,
                wid,
            )

    return {
        "count": len(ids),
        "results": results,
        "forced_rows": forced,
    }


async def _purge_sales_workspaces(user_id: int) -> dict[str, Any]:
    ids = await sales_workspace_store.list_workspace_ids_for_user(user_id)
    results: list[dict[str, Any]] = []
    for wid in ids:
        try:
            results.append(await purge_sales_workspace(wid))
        except Exception:
            logger.exception(
                "user purge sales workspace failed user=%s ws=%s", user_id, wid
            )
            results.append({"workspace_id": wid, "ok": False})

    leftover = await sales_workspace_store.list_workspace_ids_for_user(user_id)
    forced = 0
    for wid in leftover:
        try:
            files = await sales_workspace_store.list_workspace_files(wid)
            for f in files:
                _safe_delete_key(str(f.get("object_key") or ""))
            if await sales_workspace_store.delete_workspace_row(wid):
                forced += 1
        except Exception:
            logger.exception(
                "user purge force-delete sales workspace failed user=%s ws=%s",
                user_id,
                wid,
            )

    return {
        "count": len(ids),
        "results": results,
        "forced_rows": forced,
    }


async def _purge_voice_clone(user_id: int) -> dict[str, Any]:
    rows = await delete_all_history(user_id)
    oss_ok = 0
    for row in rows:
        if _safe_delete_key(str(row.get("object_key") or "")):
            oss_ok += 1
    prefix_deleted = await asyncio.to_thread(
        delete_prefix, user_voice_clone_prefix(user_id)
    )
    return {
        "history": len(rows),
        "history_oss": oss_ok,
        "prefix_deleted": prefix_deleted,
    }


async def _purge_conversations(user_id: int) -> dict[str, Any]:
    att_keys = await list_message_attachment_keys_for_user(user_id)
    att_ok = 0
    for key in att_keys:
        if _safe_delete_key(key):
            att_ok += 1

    thread_ids = await list_thread_ids_for_user(user_id)
    for tid in thread_ids:
        try:
            await reset_thread(tid)
        except Exception:
            logger.exception(
                "user purge reset_thread failed user=%s tid=%s", user_id, tid
            )

    deleted = await delete_all_conversations_for_user(user_id)
    return {
        "threads": len(deleted),
        "attachments": len(att_keys),
        "attachments_deleted": att_ok,
    }


async def purge_user_data(user_id: int) -> dict[str, Any]:
    """删除用户账号前清理其个人数据与外存资源。

    - 文档 / 销售工作区：OSS + Milvus/表数据 + 会话 checkpoint
    - 主会话列表：消息附件 OSS + checkpoint + DB
    - 声音克隆 / 转录：历史与用户前缀 OSS
    - 共享知识库：仅解除上传归属，不删除全员文件
    """
    uid = int(user_id)
    summary: dict[str, Any] = {"user_id": uid}

    summary["doc_workspaces"] = await _purge_doc_workspaces(uid)
    summary["sales_workspaces"] = await _purge_sales_workspaces(uid)
    summary["voice_clone"] = await _purge_voice_clone(uid)
    summary["conversations"] = await _purge_conversations(uid)

    # 前缀兜底：工作区/转录残留对象
    prefix_stats: dict[str, int] = {}
    for label, prefix in (
        ("doc_analysis", f"doc-analysis/{uid}/"),
        ("sales_analysis", f"sales-analysis/{uid}/"),
        ("transcribe", user_transcribe_prefix(uid)),
        ("voice_clone", user_voice_clone_prefix(uid)),
    ):
        try:
            prefix_stats[label] = await asyncio.to_thread(delete_prefix, prefix)
        except Exception:
            logger.exception("user purge prefix delete failed user=%s %s", uid, label)
            prefix_stats[label] = 0
    summary["oss_prefixes"] = prefix_stats

    try:
        summary["knowledge_detached"] = await detach_knowledge_files_owner(uid)
    except Exception:
        logger.exception("user purge detach knowledge failed user=%s", uid)
        summary["knowledge_detached"] = 0

    logger.info("user purge done user=%s summary=%s", uid, summary)
    return summary

"""文档工作区 GC：过期 / 关闭后硬删 OSS + PG + Milvus collection。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.common.checkpoint import reset_thread
from backend.common.oss import delete_object
from backend.config import settings
from backend.db import workspace_store
from backend.db.database import delete_conversation
from backend.indexing.milvus_client import get_milvus_service
from backend.indexing.workspace_postgres import delete_all_workspace_chunks

logger = logging.getLogger(__name__)


async def purge_workspace(workspace_id: int) -> dict[str, Any]:
    """硬清理单个工作区（幂等）。

    仅当 Milvus collection 确认不存在/已 drop 后才删除元数据行，
    避免孤儿 collection。
    """
    async with workspace_store.workspace_advisory_lock(
        workspace_id, blocking=True
    ) as locked:
        if not locked:
            return {
                "workspace_id": workspace_id,
                "ok": False,
                "reason": "lock_failed",
            }

        ws = await workspace_store.get_workspace(workspace_id)
        if not ws:
            return {"workspace_id": workspace_id, "ok": True, "skipped": True}

        # 先标 closed，阻断新的索引写入
        if (ws.get("status") or "") == "active":
            await workspace_store.mark_workspace_closed(workspace_id)

        files = await workspace_store.list_workspace_files(workspace_id)
        oss_ok = 0
        oss_fail = 0
        for f in files:
            key = (f.get("object_key") or "").strip()
            if not key:
                continue
            try:
                await asyncio.to_thread(delete_object, key)
                oss_ok += 1
            except Exception:
                oss_fail += 1
                logger.exception(
                    "workspace GC oss delete failed ws=%s key=%s",
                    workspace_id,
                    key,
                )

        collection = (
            (ws.get("milvus_collection") or "").strip()
            or workspace_store.milvus_collection_for_workspace(workspace_id)
        )
        milvus_ok = False
        try:
            milvus_ok = bool(
                await asyncio.to_thread(
                    get_milvus_service(collection=collection).drop_collection
                )
            )
        except Exception:
            logger.exception(
                "workspace GC milvus drop failed ws=%s coll=%s",
                workspace_id,
                collection,
            )
            milvus_ok = False

        if not milvus_ok:
            await workspace_store.bump_purge_fail(workspace_id)
            logger.error(
                "workspace GC aborted (keep row): milvus not cleared ws=%s coll=%s",
                workspace_id,
                collection,
            )
            return {
                "workspace_id": workspace_id,
                "ok": False,
                "reason": "milvus_drop_failed",
                "milvus_collection": collection,
                "oss_ok": oss_ok,
                "oss_fail": oss_fail,
            }

        chunks_deleted = 0
        try:
            chunks_deleted = await delete_all_workspace_chunks(workspace_id)
        except Exception:
            logger.exception("workspace GC pg chunks failed ws=%s", workspace_id)

        # 清会话与 checkpoint，避免泄漏进主对话列表
        thread_id = (ws.get("thread_id") or "").strip()
        user_id = int(ws.get("user_id") or 0)
        if thread_id and user_id:
            try:
                await delete_conversation(thread_id, user_id)
            except Exception:
                logger.exception(
                    "workspace GC delete conversation failed thread=%s", thread_id
                )
            try:
                await reset_thread(thread_id)
            except Exception:
                logger.exception(
                    "workspace GC reset_thread failed thread=%s", thread_id
                )

        row_deleted = await workspace_store.delete_workspace_row(workspace_id)
        logger.info(
            "workspace GC purged id=%s collection=%s files=%d oss_ok=%d oss_fail=%d "
            "chunks=%d milvus_ok=%s row_deleted=%s",
            workspace_id,
            collection,
            len(files),
            oss_ok,
            oss_fail,
            chunks_deleted,
            milvus_ok,
            row_deleted,
        )
        return {
            "workspace_id": workspace_id,
            "ok": True,
            "files": len(files),
            "oss_ok": oss_ok,
            "oss_fail": oss_fail,
            "chunks_deleted": chunks_deleted,
            "milvus_ok": milvus_ok,
            "row_deleted": row_deleted,
            "milvus_collection": collection,
        }


async def run_workspace_gc_once(*, limit: int | None = None) -> dict[str, Any]:
    """扫描过期/已关闭工作区并硬删一批（带全局 GC 锁）。"""
    async with workspace_store.gc_advisory_lock() as got:
        if not got:
            logger.info("workspace GC skipped: another worker holds the lock")
            return {"candidates": 0, "purged": 0, "skipped_lock": True}

        batch = max(
            1,
            min(
                int(
                    limit
                    if limit is not None
                    else settings.workspace_gc_batch_size or 20
                ),
                200,
            ),
        )
        candidates = await workspace_store.list_workspaces_for_gc(limit=batch)
        results: list[dict[str, Any]] = []
        for ws in candidates:
            wid = int(ws["id"])
            try:
                results.append(await purge_workspace(wid))
            except Exception:
                logger.exception("workspace GC purge failed id=%s", wid)
                results.append({"workspace_id": wid, "ok": False})

        purged = sum(1 for r in results if r.get("ok") and not r.get("skipped"))
        logger.info(
            "workspace GC round: candidates=%d purged=%d",
            len(candidates),
            purged,
        )
        return {
            "candidates": len(candidates),
            "purged": purged,
            "results": results,
        }


async def workspace_gc_loop(stop_event: asyncio.Event) -> None:
    """后台循环；interval=0 时不启动（由调用方判断）。"""
    interval = max(0, int(settings.workspace_gc_interval_sec or 0))
    if interval <= 0:
        logger.info("workspace GC disabled (WORKSPACE_GC_INTERVAL_SEC=0)")
        return

    logger.info(
        "workspace GC started: interval=%ss ttl_days=%s batch=%s",
        interval,
        workspace_store.workspace_ttl_days(),
        settings.workspace_gc_batch_size,
    )
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=min(30, interval))
        return
    except asyncio.TimeoutError:
        pass

    while not stop_event.is_set():
        try:
            await run_workspace_gc_once()
        except Exception:
            logger.exception("workspace GC round failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            continue

    logger.info("workspace GC stopped")

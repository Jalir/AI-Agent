"""销售分析工作区 GC：过期/关闭后硬删 OSS + PG 表数据 + 会话。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.common.checkpoint import reset_thread
from backend.common.oss import delete_object
from backend.config import settings
from backend.db import sales_workspace_store as store
from backend.db.database import delete_conversation

logger = logging.getLogger(__name__)


async def purge_sales_workspace(workspace_id: int) -> dict[str, Any]:
    async with store.sales_advisory_lock(workspace_id, blocking=True) as locked:
        if not locked:
            return {
                "workspace_id": workspace_id,
                "ok": False,
                "reason": "lock_failed",
            }

        ws = await store.get_workspace(workspace_id)
        if not ws:
            return {"workspace_id": workspace_id, "ok": True, "skipped": True}

        if (ws.get("status") or "") == "active":
            await store.mark_workspace_closed(workspace_id)

        files = await store.list_workspace_files(workspace_id)
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
                    "sales GC oss delete failed ws=%s key=%s",
                    workspace_id,
                    key,
                )

        thread_id = (ws.get("thread_id") or "").strip()
        user_id = int(ws.get("user_id") or 0)
        if thread_id:
            try:
                await reset_thread(thread_id)
            except Exception:
                logger.exception("sales GC reset_thread failed tid=%s", thread_id)
            if user_id:
                try:
                    await delete_conversation(thread_id, user_id)
                except Exception:
                    logger.exception(
                        "sales GC delete_conversation failed tid=%s", thread_id
                    )

        deleted = await store.delete_workspace_row(workspace_id)
        logger.info(
            "sales GC purged ws=%s deleted=%s oss_ok=%s oss_fail=%s",
            workspace_id,
            deleted,
            oss_ok,
            oss_fail,
        )
        return {
            "workspace_id": workspace_id,
            "ok": True,
            "deleted": deleted,
            "oss_ok": oss_ok,
            "oss_fail": oss_fail,
        }


async def run_sales_workspace_gc_once(*, limit: int | None = None) -> dict[str, Any]:
    async with store.gc_advisory_lock(blocking=False) as locked:
        if not locked:
            return {"ok": True, "skipped": True, "reason": "lock"}
        batch = (
            int(limit)
            if limit is not None
            else int(getattr(settings, "sales_workspace_gc_batch_size", None)
                     or settings.workspace_gc_batch_size
                     or 20)
        )
        items = await store.list_workspaces_for_gc(limit=max(1, batch))
        results = []
        for ws in items:
            try:
                results.append(await purge_sales_workspace(int(ws["id"])))
            except Exception:
                logger.exception("sales GC item failed ws=%s", ws.get("id"))
                results.append({"workspace_id": ws.get("id"), "ok": False})
        return {"ok": True, "count": len(results), "results": results}


async def sales_workspace_gc_loop(stop_event: asyncio.Event) -> None:
    interval = max(
        0,
        int(
            getattr(settings, "sales_workspace_gc_interval_sec", None)
            or settings.workspace_gc_interval_sec
            or 0
        ),
    )
    if interval <= 0:
        return
    logger.info(
        "Sales workspace GC loop start interval=%ss ttl_days=%s",
        interval,
        store.sales_ttl_days(),
    )
    while not stop_event.is_set():
        try:
            await run_sales_workspace_gc_once()
        except Exception:
            logger.exception("sales workspace GC loop error")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue

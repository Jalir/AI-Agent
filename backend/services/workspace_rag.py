"""文档工作区临时 RAG：独立 Milvus collection + workspace_document_chunks。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from backend.common.oss import delete_object, resolve_attachment_url
from backend.db import workspace_store
from backend.indexing.document_loader import DocumentLoader
from backend.indexing.milvus_client import get_milvus_service
from backend.indexing.workspace_postgres import (
    delete_workspace_chunks_by_file_id,
    init_workspace_document_chunks_table,
    insert_workspace_chunks,
)

logger = logging.getLogger(__name__)

_CHUNK_ID_MAX = 128


def chunk_id_prefix(workspace_id: int, file_id: int) -> str:
    return f"w{int(workspace_id)}|f{int(file_id)}|"


def _prefix_ids(
    chunks: list[dict],
    workspace_id: int,
    file_id: int,
) -> list[dict]:
    """chunk_id 带 workspace + file_id，避免同名文件互删/冲突。"""
    prefix = chunk_id_prefix(workspace_id, file_id)
    out: list[dict] = []
    for raw in chunks:
        c = dict(raw)
        c["file_id"] = int(file_id)
        c["ws_file_id"] = int(file_id)  # Milvus 动态字段，供按文件删除
        for key in ("chunk_id", "parent_chunk_id", "root_chunk_id"):
            val = str(c.get(key) or "")
            if not val:
                continue
            if val.startswith(prefix):
                new_val = val
            else:
                new_val = prefix + val
            if len(new_val) > _CHUNK_ID_MAX:
                digest = hashlib.md5(val.encode("utf-8")).hexdigest()[:12]
                new_val = f"{prefix}{digest}"[:_CHUNK_ID_MAX]
            c[key] = new_val
        out.append(c)
    return out


def _milvus_delete_file(collection: str, file_id: int, workspace_id: int) -> None:
    milvus = get_milvus_service(collection=collection)
    if not milvus.has_collection():
        return
    # 优先动态字段；失败再按 chunk_id 前缀
    try:
        milvus.delete(f"ws_file_id == {int(file_id)}")
        return
    except Exception:
        logger.warning(
            "milvus delete by ws_file_id failed, fallback prefix ws=%s file=%s",
            workspace_id,
            file_id,
            exc_info=True,
        )
    prefix = chunk_id_prefix(workspace_id, file_id)
    # like 在部分版本可用；否则跳过（整库 drop 时仍会清）
    try:
        milvus.delete(f'chunk_id like "{prefix}%"')
    except Exception:
        logger.exception(
            "milvus delete by chunk_id prefix failed ws=%s file=%s",
            workspace_id,
            file_id,
        )


async def index_workspace_file(
    *,
    workspace_id: int,
    file_id: int,
    file_url: str,
    file_name: str,
    object_key: str = "",
) -> dict[str, Any]:
    """解析并写入工作区临时索引（不进共享 knowledge_base）。"""
    from backend.indexing.embedding import embedding_service

    async with workspace_store.workspace_advisory_lock(
        workspace_id, blocking=False
    ) as locked:
        if not locked:
            raise ValueError("工作区正忙（清理或其它索引进行中），请稍后重试")

        if not await workspace_store.is_workspace_active(workspace_id):
            raise ValueError("工作区不存在、已过期或已关闭")

        ws = await workspace_store.get_workspace(workspace_id)
        if not ws:
            raise ValueError("工作区不存在")

        collection = (
            (ws.get("milvus_collection") or "").strip()
            or workspace_store.milvus_collection_for_workspace(workspace_id)
        )
        await init_workspace_document_chunks_table()

        fetch_url = (
            resolve_attachment_url({"url": file_url, "object_key": object_key})
            or file_url
        )

        loader = DocumentLoader()
        chunks = await asyncio.to_thread(
            loader.load_document,
            fetch_url,
            file_name,
            path_meta=file_url,
        )
        if not chunks:
            raise ValueError("文档无有效正文，无法建索引")

        chunks = _prefix_ids(chunks, workspace_id, file_id)

        # 同 file_id 重试：先清旧块
        await delete_workspace_chunks_by_file_id(workspace_id, file_id)
        milvus = get_milvus_service(collection=collection)
        await asyncio.to_thread(milvus.init_collection)
        try:
            await asyncio.to_thread(
                _milvus_delete_file, collection, file_id, workspace_id
            )
        except Exception:
            logger.exception(
                "workspace milvus delete-before-insert failed ws=%s file_id=%s",
                workspace_id,
                file_id,
            )

        l1_l2 = [c for c in chunks if c["chunk_level"] in (1, 2)]
        l3 = [c for c in chunks if c["chunk_level"] == 3]
        char_count = sum(
            len(str(c.get("text") or "")) for c in l1_l2 if c["chunk_level"] == 1
        )
        if char_count <= 0:
            char_count = sum(len(str(c.get("text") or "")) for c in chunks)

        pg_count = 0
        mv_count = 0
        try:
            pg_count = (
                await insert_workspace_chunks(workspace_id, l1_l2) if l1_l2 else 0
            )
            if l3:
                vectors = await asyncio.to_thread(
                    embedding_service.get_embeddings, [c["text"] for c in l3]
                )
                dense = vectors["dense"] if isinstance(vectors, dict) else vectors
                for chunk, vector in zip(l3, dense):
                    chunk["dense_vector"] = vector
                await asyncio.to_thread(milvus.insert, l3)
                mv_count = len(l3)
        except Exception:
            # 失败补偿：清掉本 file_id 半套索引
            logger.exception(
                "workspace index write failed, rolling back file_id=%s", file_id
            )
            try:
                await delete_workspace_chunks_by_file_id(workspace_id, file_id)
                _milvus_delete_file(collection, file_id, workspace_id)
            except Exception:
                logger.exception("workspace index rollback failed file_id=%s", file_id)
            raise

        # 写入后再确认工作区仍有效，避免与 purge 竞态留下孤儿
        if not await workspace_store.is_workspace_active(workspace_id):
            logger.warning(
                "workspace became inactive during index, rolling back ws=%s file=%s",
                workspace_id,
                file_id,
            )
            await delete_workspace_chunks_by_file_id(workspace_id, file_id)
            try:
                _milvus_delete_file(collection, file_id, workspace_id)
            except Exception:
                logger.exception("post-index rollback milvus failed")
            raise ValueError("工作区已关闭或过期，索引已撤销")

        logger.info(
            "workspace index done ws=%s file_id=%s l1l2=%s l3=%s collection=%s",
            workspace_id,
            file_id,
            pg_count,
            mv_count,
            collection,
        )
        return {
            "files": 1,
            "l1_l2_count": pg_count,
            "l3_count": mv_count,
            "char_count": char_count,
            "milvus_collection": collection,
        }


async def run_workspace_parse_task(
    *,
    workspace_id: int,
    file_id: int,
    file_url: str,
    file_name: str,
    object_key: str = "",
) -> None:
    await workspace_store.update_workspace_file_parse(
        file_id, parse_status=workspace_store.PARSE_PARSING
    )
    try:
        meta = await index_workspace_file(
            workspace_id=workspace_id,
            file_id=file_id,
            file_url=file_url,
            file_name=file_name,
            object_key=object_key,
        )
        # 文件行可能已被 purge CASCADE 删掉
        if not await workspace_store.get_workspace_file(file_id, workspace_id):
            logger.info(
                "workspace file row gone after index, skip status update id=%s",
                file_id,
            )
            return
        await workspace_store.update_workspace_file_parse(
            file_id,
            parse_status=workspace_store.PARSE_DONE,
            char_count=int(meta.get("char_count") or 0),
        )
    except Exception as e:
        logger.exception(
            "workspace parse failed ws=%s file_id=%s", workspace_id, file_id
        )
        try:
            if await workspace_store.get_workspace_file(file_id, workspace_id):
                await workspace_store.update_workspace_file_parse(
                    file_id,
                    parse_status=workspace_store.PARSE_FAILED,
                    parse_error=str(e)[:500],
                )
        except Exception:
            pass


async def remove_workspace_file_index(
    *,
    workspace_id: int,
    file_id: int,
    file_name: str = "",
    object_key: str = "",
) -> None:
    _ = file_name
    async with workspace_store.workspace_advisory_lock(
        workspace_id, blocking=True
    ) as locked:
        if not locked:
            logger.warning("remove file index lock failed ws=%s", workspace_id)
        await delete_workspace_chunks_by_file_id(workspace_id, file_id)
        ws = await workspace_store.get_workspace(workspace_id)
        collection = (ws or {}).get(
            "milvus_collection"
        ) or workspace_store.milvus_collection_for_workspace(workspace_id)
        try:
            _milvus_delete_file(collection, file_id, workspace_id)
        except Exception:
            logger.exception(
                "workspace milvus file delete failed ws=%s file_id=%s",
                workspace_id,
                file_id,
            )
        if object_key:
            try:
                delete_object(object_key)
            except Exception:
                logger.exception("workspace oss delete failed key=%s", object_key)


async def destroy_workspace_index(workspace_id: int) -> dict[str, Any]:
    from backend.services.workspace_gc import purge_workspace

    return await purge_workspace(workspace_id)

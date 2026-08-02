"""知识库解析编排：OSS 拉取 + 建索引 + 更新 parse_status。"""

from __future__ import annotations

import logging

from backend.common.errors import public_client_error
from backend.common.oss import sign_get_url
from backend.db.database import update_knowledge_file_parse_status

logger = logging.getLogger(__name__)

# parse_status: parsing | done | failed
PARSE_PARSING = "parsing"
PARSE_DONE = "done"
PARSE_FAILED = "failed"


async def index_remote_file(
    *,
    file_url: str,
    file_name: str,
    object_key: str,
) -> dict:
    """下载 OSS 文件（签名 URL）并执行 build_index，入库 file_path 用永久地址。"""
    from backend.indexing.document_loader import DocumentLoader
    from backend.indexing.indexing_pipeline import build_index

    if not DocumentLoader.is_supported_filename(file_name):
        return {"skipped": True, "reason": "不支持的文件类型"}

    download_url = sign_get_url(object_key, expires=600)
    return await build_index(
        download_url,
        filenames={download_url: file_name},
        path_meta={download_url: file_url},
    )


async def run_parse_task(
    *,
    file_id: int,
    file_url: str,
    file_name: str,
    object_key: str,
) -> None:
    """后台解析任务：成功 -> done，失败/跳过 -> failed。"""
    try:
        result = await index_remote_file(
            file_url=file_url,
            file_name=file_name,
            object_key=object_key,
        )
        if result.get("skipped"):
            reason = public_client_error(
                result.get("reason") or "skipped",
                kind="parse",
                fallback="不支持的文件类型或已跳过解析。",
            )
            await update_knowledge_file_parse_status(file_id, PARSE_FAILED, reason)
            logger.warning("Parse skipped for file %s (#%s): %s", file_name, file_id, result)
            return

        if result.get("error"):
            logger.error(
                "Parse failed for file %s (#%s): %s", file_name, file_id, result["error"]
            )
            await update_knowledge_file_parse_status(
                file_id,
                PARSE_FAILED,
                public_client_error(result["error"], kind="parse"),
            )
            return

        await update_knowledge_file_parse_status(file_id, PARSE_DONE)
        logger.info("Parsed knowledge file %s (#%s): %s", file_name, file_id, result)
    except Exception as e:
        logger.exception("Parse task failed for file %s (#%s)", file_name, file_id)
        try:
            await update_knowledge_file_parse_status(
                file_id,
                PARSE_FAILED,
                public_client_error(e, kind="parse"),
            )
        except Exception:
            logger.exception("Failed to update parse_status for file #%s", file_id)

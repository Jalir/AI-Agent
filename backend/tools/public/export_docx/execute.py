"""export_docx 执行器。"""

from __future__ import annotations

import asyncio
import logging

from backend.common.errors import tool_user_error
from backend.common.stream import emit_file, emit_status
from backend.common.tool_outcome import ensure_action_hint
from backend.tools.context import ToolExecContext
from backend.tools.public.export_docx.tool import (
    export_docx_to_oss,
    format_export_result,
)

logger = logging.getLogger(__name__)


async def execute(ctx: ToolExecContext) -> str:
    await emit_status(ctx.thread_id, "正在生成文档…")
    content = str(ctx.arg("content") or "")
    filename = str(ctx.arg("filename") or "")
    try:
        meta = await asyncio.to_thread(export_docx_to_oss, content, filename)
        await emit_file(ctx.thread_id, meta)
        return format_export_result(meta)
    except ValueError as e:
        return ensure_action_hint(tool_user_error("导出", e))
    except Exception as e:
        logger.exception("export_docx failed")
        return ensure_action_hint(tool_user_error("导出", e))

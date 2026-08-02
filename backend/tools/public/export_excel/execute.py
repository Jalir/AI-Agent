"""export_excel 执行器。"""

from __future__ import annotations

import asyncio
import logging

from backend.common.errors import tool_user_error
from backend.common.stream import emit_file, emit_status
from backend.common.tool_outcome import ensure_action_hint
from backend.tools.context import ToolExecContext
from backend.tools.public.export_excel.tool import (
    export_excel_to_oss,
    format_export_excel_result,
)

logger = logging.getLogger(__name__)


async def execute(ctx: ToolExecContext) -> str:
    await emit_status(ctx.thread_id, "正在生成表格…")
    rows = ctx.arg("rows")
    columns = ctx.arg("columns")
    filename = str(ctx.arg("filename") or "")
    sheet_name = str(ctx.arg("sheet_name") or "Sheet1")
    try:
        meta = await asyncio.to_thread(
            export_excel_to_oss,
            rows,
            columns=columns,
            filename=filename,
            sheet_name=sheet_name,
        )
        await emit_file(ctx.thread_id, meta)
        return format_export_excel_result(meta)
    except ValueError as e:
        return ensure_action_hint(tool_user_error("导出", e))
    except Exception as e:
        logger.exception("export_excel failed")
        return ensure_action_hint(tool_user_error("导出", e))

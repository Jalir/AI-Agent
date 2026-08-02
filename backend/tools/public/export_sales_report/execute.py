"""export_sales_report 执行器：复用 docx 导出链路。"""

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
    conf = (ctx.config or {}).get("configurable") if ctx.config else None
    if not isinstance(conf, dict) or conf.get("sales_workspace_id") is None:
        return ensure_action_hint("当前不在销售分析区，无法导出销售报告。")

    await emit_status(ctx.thread_id, "正在生成销售分析报告…")
    content = str(ctx.arg("content") or "")
    filename = str(ctx.arg("filename") or "销售分析报告.docx").strip()
    if not filename.lower().endswith(".docx"):
        filename = f"{filename}.docx" if filename else "销售分析报告.docx"
    try:
        meta = await asyncio.to_thread(
            export_docx_to_oss, content, filename, style="report"
        )
        await emit_file(ctx.thread_id, meta)
        return format_export_result(meta)
    except ValueError as e:
        return ensure_action_hint(tool_user_error("导出报告", e))
    except Exception as e:
        logger.exception("export_sales_report failed")
        return ensure_action_hint(tool_user_error("导出报告", e))

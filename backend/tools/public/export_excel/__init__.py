"""导出 Excel 表格供前端下载的技能。"""

from __future__ import annotations

from typing import Any

from backend.tools.public.export_excel.tool import (
    TOOL_NAME,
    export_excel,
    export_excel_to_oss,
    format_export_excel_result,
)

TOOL = export_excel
# 导出文件：需 HITL 审批
REQUIRES_APPROVAL = True
APPROVAL_LABEL = "导出为 Excel 表格"
MAX_CALLS_PER_TURN = 2


def approval_question(tool_args: Any) -> str:
    name = ""
    row_hint = ""
    if isinstance(tool_args, dict):
        name = str(tool_args.get("filename") or "").strip()
        rows = tool_args.get("rows")
        if isinstance(rows, list):
            row_hint = f"，共 {len(rows)} 行"
        elif isinstance(rows, str) and rows.strip():
            row_hint = ""
    if name:
        return f"即将把数据整理成「{name}」{row_hint}并提供下载，是否继续？"
    return f"即将把数据整理成 Excel 表格{row_hint}并提供下载，是否继续？"


__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "APPROVAL_LABEL",
    "MAX_CALLS_PER_TURN",
    "approval_question",
    "export_excel",
    "export_excel_to_oss",
    "format_export_excel_result",
]

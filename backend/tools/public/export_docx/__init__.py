"""导出 Word 文档供前端下载的技能。"""

from __future__ import annotations

from typing import Any

from backend.tools.public.export_docx.tool import (
    TOOL_NAME,
    export_docx,
    export_docx_to_oss,
    format_export_result,
)

TOOL = export_docx
# 导出文件：需 HITL 审批
REQUIRES_APPROVAL = True
APPROVAL_LABEL = "导出为 Word 文档"
MAX_CALLS_PER_TURN = 2


def approval_question(tool_args: Any) -> str:
    name = ""
    if isinstance(tool_args, dict):
        name = str(tool_args.get("filename") or "").strip()
    if name:
        return f"即将把内容整理成「{name}」并提供下载，是否继续？"
    return "即将把内容整理成 Word 文档并提供下载，是否继续？"


__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "APPROVAL_LABEL",
    "MAX_CALLS_PER_TURN",
    "approval_question",
    "export_docx",
    "export_docx_to_oss",
    "format_export_result",
]

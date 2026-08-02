"""导出销售分析 Word 报告。"""

from __future__ import annotations

from backend.tools.public.export_sales_report.execute import execute
from backend.tools.public.export_sales_report.tool import (
    TOOL_NAME,
    approval_question,
    export_sales_report,
)

TOOL = export_sales_report
REQUIRES_APPROVAL = True
APPROVAL_LABEL = "导出销售分析报告"
MAX_CALLS_PER_TURN = 2

__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "APPROVAL_LABEL",
    "MAX_CALLS_PER_TURN",
    "approval_question",
    "execute",
]

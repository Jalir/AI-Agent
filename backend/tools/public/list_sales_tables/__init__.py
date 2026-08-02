"""列出销售分析区数据表。"""

from __future__ import annotations

from backend.tools.public.list_sales_tables.execute import execute
from backend.tools.public.list_sales_tables.tool import TOOL_NAME, list_sales_tables

TOOL = list_sales_tables
REQUIRES_APPROVAL = False
MAX_CALLS_PER_TURN = 1

__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "MAX_CALLS_PER_TURN",
    "execute",
]

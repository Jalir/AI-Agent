"""查询销售分析区结构化数据。"""

from __future__ import annotations

from backend.tools.public.query_sales_data.execute import execute
from backend.tools.public.query_sales_data.tool import TOOL_NAME, query_sales_data

TOOL = query_sales_data
REQUIRES_APPROVAL = False
MAX_CALLS_PER_TURN = 5

__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "MAX_CALLS_PER_TURN",
    "execute",
]

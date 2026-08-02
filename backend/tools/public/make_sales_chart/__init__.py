"""生成销售图表（ECharts）。"""

from __future__ import annotations

from backend.tools.public.make_sales_chart.execute import execute
from backend.tools.public.make_sales_chart.tool import TOOL_NAME, make_sales_chart

TOOL = make_sales_chart
REQUIRES_APPROVAL = False
MAX_CALLS_PER_TURN = 4

__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "MAX_CALLS_PER_TURN",
    "execute",
]

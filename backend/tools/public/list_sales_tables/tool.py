"""列出销售分析工作区中的结构化表。"""

from __future__ import annotations

from langchain_core.tools import tool

TOOL_NAME = "list_sales_tables"


@tool(TOOL_NAME)
async def list_sales_tables() -> str:
    """列出销售区表（ID、列名、行数、列样例/年月取值）。每问最多 1 次。
    过滤时优先用返回的 samples / year_months，勿臆造年份。
    """
    return "list_sales_tables"

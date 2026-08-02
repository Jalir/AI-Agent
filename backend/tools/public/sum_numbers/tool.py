"""对数字数组精确求和。"""

from __future__ import annotations

from langchain_core.tools import tool

TOOL_NAME = "sum_numbers"


@tool(TOOL_NAME)
async def sum_numbers(numbers_json: str = "", column: str = "") -> str:
    """对少量数字精确求和（禁止心算）。仅用于聚合结果的二次汇总（如把已按月合计的两三个数再相加）。
    表内全量合计请用 query_sales_data 的 aggregations_json（fn=sum），勿对明细预览列求和。

    Args:
        numbers_json: JSON 数字数组，例如 [12.5, 30, 7.8]
        column: 本轮 query 聚合结果中的数值列名（与 numbers_json 二选一，优先 column）
    """
    _ = (numbers_json, column)
    return "sum_numbers"

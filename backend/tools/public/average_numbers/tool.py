"""对数字数组精确求平均。"""

from __future__ import annotations

from langchain_core.tools import tool

TOOL_NAME = "average_numbers"


@tool(TOOL_NAME)
async def average_numbers(numbers_json: str = "", column: str = "") -> str:
    """对少量数字精确求平均（禁止心算）。仅用于聚合结果的二次计算。
    表内全量均值请用 query_sales_data 的 aggregations_json（fn=avg），勿对明细预览列求平均。

    Args:
        numbers_json: JSON 数字数组，例如 [12.5, 30, 7.8]
        column: 本轮 query 聚合结果中的数值列名（与 numbers_json 二选一，优先 column）
    """
    _ = (numbers_json, column)
    return "average_numbers"

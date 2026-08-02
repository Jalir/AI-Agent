"""白名单查询销售结构化表。"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

TOOL_NAME = "query_sales_data"


@tool(TOOL_NAME)
async def query_sales_data(
    table_id: int,
    filters_json: str = "[]",
    group_by_json: str = "[]",
    aggregations_json: str = "[]",
    columns_json: str = "[]",
    order_by: str = "",
    order_desc: bool = False,
    limit: int = 200,
    time_grain: str = "",
) -> str:
    """查询销售表（每问最多 1 次）。分析/合计/对比时必须在本工具内聚合，禁止先查明细再 sum_numbers。
    优先传 aggregations_json（fn=sum/avg）+ 按需 group_by_json，对全部匹配行汇总后再返回；
    按交易时间等日期列汇总时必须传 time_grain（month/day/year），否则每个时间戳各成一组。
    仅当用户明确要逐行明细时才不传 aggregations。列/过滤不能唯一对应时先问用户。
    出图用 make_sales_chart(use_last_query=true)。

    Args:
        table_id: 表 ID（先 list_sales_tables）
        filters_json: [{"column","op","value"}]，op=eq/ne/contains/gte/lte/gt/lt/in
        group_by_json: 分组列名 JSON 数组，如 ["交易时间"]；与 aggregations 一起用于报告维度
        aggregations_json: [{"column","fn","alias"}]，fn=sum/avg/count/min/max；要总额/均值时必填
        columns_json: 仅明细模式需要的返回列；聚合模式可忽略
        order_by: 排序列
        order_desc: 是否降序
        limit: 行数上限（明细预览用；聚合结果通常远小于此）
        time_grain: 对 group_by 中可解析的日期值归一化：month|day|year；
            按月/按日/按年统计时必填（支持 2025-02-03 18:52:04、2025/02/03、2025年2月 等年在前格式）
    """
    _ = (
        table_id,
        filters_json,
        group_by_json,
        aggregations_json,
        columns_json,
        order_by,
        order_desc,
        limit,
        time_grain,
    )
    return "query_sales_data"


def _parse_json_list(raw: Any, *, name: str) -> list:
    import json

    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return raw
    text = str(raw).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{name} 不是合法 JSON：{e}") from e
    if not isinstance(data, list):
        raise ValueError(f"{name} 必须是 JSON 数组")
    return data

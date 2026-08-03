"""根据查询结果生成 ECharts 配置并推送到前端。"""

from __future__ import annotations

from langchain_core.tools import tool

TOOL_NAME = "make_sales_chart"


@tool(TOOL_NAME)
async def make_sales_chart(
    table_id: int = 0,
    chart_type: str = "bar",
    title: str = "",
    category_column: str = "",
    value_column: str = "",
    use_last_query: bool = True,
    filters_json: str = "[]",
    group_by_json: str = "[]",
    aggregations_json: str = "[]",
    series_name: str = "",
    limit: int = 50,
    time_grain: str = "",
) -> str:
    """生成销售图表（bar/hbar/line/pie）。默认 use_last_query=true 复用本轮 query 的 rows，勿再查库。
    category_column/value_column 必填且须为结果列；一句话多图先拆清定义再分别调用。仅分析/出图时不要 export。
    商品名等长类目优先用 hbar（横向柱），避免底部名称被挤掉。

    Args:
        table_id: 表 ID（仅 use_last_query=false 时需要）
        chart_type: bar | hbar | line | pie（hbar=横向柱，适合长类目名）
        title: 标题
        category_column: 类目列（必填）
        value_column: 数值列（必填）
        use_last_query: 默认 true
        filters_json: 仅 use_last_query=false 时使用
        group_by_json: 仅 use_last_query=false 时使用
        aggregations_json: 仅 use_last_query=false 时使用
        series_name: 系列名
        limit: 最多绘制行数（仅重新查库时）
        time_grain: 仅 use_last_query=false 时使用；month|day|year
    """
    _ = (
        table_id,
        chart_type,
        title,
        category_column,
        value_column,
        use_last_query,
        filters_json,
        group_by_json,
        aggregations_json,
        series_name,
        limit,
        time_grain,
    )
    return "make_sales_chart"

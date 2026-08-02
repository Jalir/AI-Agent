"""make_sales_chart 执行器。"""

from __future__ import annotations

import logging
import uuid

from backend.common.stream import emit_chart, emit_status
from backend.common.tool_outcome import (
    ToolAction,
    ensure_action_hint,
    format_tool_outcome,
)
from backend.services.sales_query import chart_from_query_result, query_sales_table
from backend.tools.context import ToolExecContext
from backend.tools.public.query_sales_data.execute import _sales_workspace_id
from backend.tools.public.query_sales_data.tool import _parse_json_list

logger = logging.getLogger(__name__)


def _as_bool(raw, default: bool = True) -> bool:
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


async def execute(ctx: ToolExecContext) -> str:
    workspace_id = _sales_workspace_id(ctx)
    if workspace_id is None:
        return ensure_action_hint("当前不在销售分析区，无法生成图表。")

    chart_type = str(ctx.arg("chart_type") or "bar")
    title = str(ctx.arg("title") or "").strip()
    category_column = str(ctx.arg("category_column") or "").strip()
    value_column = str(ctx.arg("value_column") or "").strip()
    series_name = str(ctx.arg("series_name") or "").strip()
    use_last = _as_bool(ctx.arg("use_last_query", True), default=True)

    if not category_column or not value_column:
        return format_tool_outcome(
            headline="生成图表缺少列映射",
            action=ToolAction.ASK_USER,
            detail=(
                "请先向用户确认类目列与数值列对应表中的哪一列，"
                "以及过滤条件；一句话多图请拆开分别确认后再出图。"
                "禁止猜测列名。"
            ),
        )

    await emit_status(ctx.thread_id, "正在生成图表…")
    last = ctx.sales_last_query if isinstance(ctx.sales_last_query, dict) else None
    used_cache = False
    try:
        if use_last and last and last.get("rows"):
            used_cache = True
            result = {
                "mode": last.get("mode"),
                "columns": last.get("columns") or [],
                "rows": list(last.get("rows") or []),
                "evidence": last.get("evidence") or {},
            }
        else:
            if use_last:
                return format_tool_outcome(
                    headline="尚无可用的查询结果可出图",
                    action=ToolAction.RETRY,
                    detail=(
                        "请先调用一次 query_sales_data 拉齐数据，"
                        "再 make_sales_chart(use_last_query=true)。"
                        "（此类失败可在 query 后用相同参数重试）"
                    ),
                )
            try:
                table_id = int(ctx.arg("table_id"))
                filters = _parse_json_list(ctx.arg("filters_json"), name="filters_json")
                group_by = _parse_json_list(
                    ctx.arg("group_by_json"), name="group_by_json"
                )
                aggregations = _parse_json_list(
                    ctx.arg("aggregations_json"), name="aggregations_json"
                )
                limit = int(ctx.arg("limit") or 50)
                time_grain = str(ctx.arg("time_grain") or "").strip()
            except (TypeError, ValueError) as e:
                return ensure_action_hint(str(e))
            if not aggregations:
                group_by = group_by or [category_column]
                aggregations = [
                    {
                        "column": value_column,
                        "fn": "sum",
                        "alias": value_column,
                    }
                ]
            result = await query_sales_table(
                workspace_id,
                table_id=table_id,
                filters=filters,
                group_by=[str(x) for x in group_by],
                aggregations=aggregations,
                limit=limit,
                time_grain=time_grain or None,
            )

        chart = chart_from_query_result(
            result,
            chart_type=chart_type,
            title=title,
            category_column=category_column,
            value_column=value_column,
            series_name=series_name,
        )
    except ValueError as e:
        return ensure_action_hint(str(e))
    except Exception:
        logger.exception("make_sales_chart failed")
        return ensure_action_hint("生成图表失败，请检查列名与过滤条件。")

    chart_id = uuid.uuid4().hex[:12]
    await emit_chart(
        ctx.thread_id,
        {
            "chart_id": chart_id,
            "title": title or chart["option"].get("title", {}).get("text") or "图表",
            "option": chart["option"],
            "evidence": chart.get("evidence") or {},
        },
    )
    ev = chart.get("evidence") or {}
    reused = "复用上次查询" if used_cache else "重新查询"
    return (
        f"已生成图表「{title or chart_id}」（类型 {chart_type}，{reused}，"
        f"匹配 {ev.get('matched_rows', '?')} 行）。"
        "请基于查询证据撰写销售反馈；用户可在前端下载 PNG。"
        "若需保存完整分析报告，请再调用 export_sales_report。"
    )

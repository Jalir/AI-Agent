"""query_sales_data 执行器。"""

from __future__ import annotations

import json
import logging

from backend.common.stream import emit_status
from backend.common.tool_outcome import ensure_action_hint
from backend.services.sales_query import query_sales_table
from backend.tools.context import ToolExecContext
from backend.tools.public._sales_reuse import build_column_arrays
from backend.tools.public.query_sales_data.tool import _parse_json_list

logger = logging.getLogger(__name__)


def _sales_workspace_id(ctx: ToolExecContext) -> int | None:
    conf = (ctx.config or {}).get("configurable") if ctx.config else None
    if not isinstance(conf, dict):
        return None
    raw = conf.get("sales_workspace_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


async def execute(ctx: ToolExecContext) -> str:
    workspace_id = _sales_workspace_id(ctx)
    if workspace_id is None:
        return ensure_action_hint("当前不在销售分析区，无法查询销售数据。")

    try:
        table_id = int(ctx.arg("table_id"))
    except (TypeError, ValueError):
        return ensure_action_hint("请提供有效的 table_id（先调用 list_sales_tables）。")

    try:
        filters = _parse_json_list(ctx.arg("filters_json"), name="filters_json")
        group_by = _parse_json_list(ctx.arg("group_by_json"), name="group_by_json")
        aggregations = _parse_json_list(
            ctx.arg("aggregations_json"), name="aggregations_json"
        )
        columns = _parse_json_list(ctx.arg("columns_json"), name="columns_json")
        limit = int(ctx.arg("limit") or 200)
        order_by = str(ctx.arg("order_by") or "").strip()
        order_desc = bool(ctx.arg("order_desc"))
        time_grain = str(ctx.arg("time_grain") or "").strip()
    except ValueError as e:
        return ensure_action_hint(str(e))

    await emit_status(ctx.thread_id, "正在查询销售数据…")
    try:
        result = await query_sales_table(
            workspace_id,
            table_id=table_id,
            filters=filters,
            group_by=[str(x) for x in group_by],
            aggregations=aggregations,
            columns=[str(x) for x in columns],
            order_by=order_by or None,
            order_desc=order_desc,
            limit=limit,
            time_grain=time_grain or None,
        )
    except ValueError as e:
        return ensure_action_hint(str(e))
    except Exception:
        logger.exception("query_sales_data failed")
        return ensure_action_hint("查询失败，请检查表 ID 与过滤条件后重试。")

    # 控制回灌给模型的体积
    rows = result.get("rows") or []
    columns = [str(c) for c in (result.get("columns") or [])]
    preview = rows[:80]
    arrays = build_column_arrays(preview, columns)
    payload = {
        "mode": result.get("mode"),
        "columns": columns,
        "rows": preview,
        "row_preview_count": len(preview),
        "arrays": arrays,
        "evidence": result.get("evidence"),
        "note": (
            "aggregate=已对匹配行汇总；detail=明细预览（可能截断）。"
            "数字须引用本结果，勿编造。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False)

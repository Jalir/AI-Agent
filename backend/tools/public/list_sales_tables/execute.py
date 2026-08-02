"""list_sales_tables 执行器。"""

from __future__ import annotations

import json
import logging

from backend.common.stream import emit_status
from backend.common.tool_outcome import ensure_action_hint
from backend.services.sales_query import list_workspace_tables
from backend.tools.context import ToolExecContext

logger = logging.getLogger(__name__)


async def execute(ctx: ToolExecContext) -> str:
    conf = (ctx.config or {}).get("configurable") if ctx.config else None
    raw = conf.get("sales_workspace_id") if isinstance(conf, dict) else None
    try:
        workspace_id = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        workspace_id = None
    if workspace_id is None:
        return ensure_action_hint("当前不在销售分析区，无法列出数据表。")

    await emit_status(ctx.thread_id, "正在查看已上传的销售表…")
    try:
        tables = await list_workspace_tables(workspace_id)
    except Exception:
        logger.exception("list_sales_tables failed")
        return ensure_action_hint("列出数据表失败，请稍后重试。")

    if not tables:
        return ensure_action_hint("尚未解析出数据表，请先上传一行表头的 Excel（.xlsx）。")

    slim = []
    for t in tables:
        slim.append(
            {
                "table_id": t["table_id"],
                "sheet_name": t["sheet_name"],
                "row_count": t["row_count"],
                "columns": t.get("columns") or [],
                "warnings": t.get("warnings") or [],
            }
        )
    return (
        "销售分析区数据表如下（请用 table_id 查询，数字以查询结果为准，勿编造）：\n"
        + json.dumps(slim, ensure_ascii=False)
        + "\n（内部提示）过滤月份/城市时优先用 columns.samples 与 year_months 中的真实取值；"
        "用户只说「7月」且 year_months 有多个年份时先问清；唯一则可直接用。"
        "按日期/时间列做月日年汇总时 query 须带 time_grain=month|day|year。"
        "出图前须用真实列名填 category_column/value_column；禁止猜列。"
    )

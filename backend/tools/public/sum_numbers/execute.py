"""sum_numbers 执行器。"""

from __future__ import annotations

import json

from backend.common.tool_outcome import ensure_action_hint
from backend.tools.context import ToolExecContext
from backend.tools.public._number_array import format_number, parse_number_array
from backend.tools.public._sales_reuse import extract_column_numbers


async def execute(ctx: ToolExecContext) -> str:
    column = str(ctx.arg("column") or "").strip()
    try:
        if column:
            nums = extract_column_numbers(ctx.sales_last_query, column)
        else:
            nums = parse_number_array(ctx.arg("numbers_json"), name="numbers_json")
    except ValueError as e:
        return ensure_action_hint(str(e))

    total = sum(nums)
    payload = {
        "operation": "sum",
        "count": len(nums),
        "column": column or None,
        "result": format_number(total),
        "note": "结果由工具精确计算，请直接采用，勿再心算改写。",
    }
    return json.dumps(payload, ensure_ascii=False)

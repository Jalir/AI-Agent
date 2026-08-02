"""write_xhs_copy 执行器。"""

from __future__ import annotations

from backend.common.stream import emit_status
from backend.common.tool_outcome import format_arg_failure
from backend.tools.context import ToolExecContext
from backend.tools.public.write_xhs_copy.schema import validate_write_xhs_copy_args
from backend.tools.public.write_xhs_copy.tool import run_write_xhs_copy


async def execute(ctx: ToolExecContext) -> str:
    model, err = validate_write_xhs_copy_args(ctx.args)
    if err or model is None:
        return format_arg_failure("文案", err or "文案参数不合规")
    await emit_status(ctx.thread_id, "正在写文案…")
    return await run_write_xhs_copy(
        model.material, style=model.style, count=model.count
    )

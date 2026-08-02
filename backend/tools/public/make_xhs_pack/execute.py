"""make_xhs_pack 执行器。"""

from __future__ import annotations

from backend.common.stream import emit_status
from backend.common.tool_outcome import format_arg_failure
from backend.tools.context import ToolExecContext
from backend.tools.public.make_xhs_pack.schema import validate_make_xhs_pack_args
from backend.tools.public.make_xhs_pack.tool import run_make_xhs_pack


async def execute(ctx: ToolExecContext) -> str:
    model, err = validate_make_xhs_pack_args(ctx.args)
    if err or model is None:
        return format_arg_failure("图文包", err or "图文包参数不合规")
    await emit_status(ctx.thread_id, "正在生成…")
    return await run_make_xhs_pack(
        model.items,
        style=model.style,
        with_image=model.with_image,
        thread_id=ctx.thread_id,
    )

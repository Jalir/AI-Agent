"""generate_image 执行器。"""

from __future__ import annotations

import asyncio
import logging

from backend.common.errors import tool_user_error
from backend.common.stream import emit_file, emit_status
from backend.common.tool_outcome import ensure_action_hint, format_arg_failure
from backend.tools.context import ToolExecContext
from backend.tools.public.generate_image.schema import validate_generate_image_args
from backend.tools.public.generate_image.tool import (
    format_image_result,
    generate_image_to_urls,
)

logger = logging.getLogger(__name__)


async def execute(ctx: ToolExecContext) -> str:
    model, err = validate_generate_image_args(ctx.args)
    if err or model is None:
        return format_arg_failure("生图", err or "生图参数不合规")
    await emit_status(ctx.thread_id, "正在生成图片…")
    try:
        metas = await asyncio.to_thread(
            generate_image_to_urls,
            model.prompt,
            size=model.size,
            n=model.n,
        )
        for meta in metas:
            await emit_file(ctx.thread_id, meta)
        return format_image_result(metas)
    except ValueError as e:
        return ensure_action_hint(tool_user_error("生图", e))
    except Exception as e:
        logger.exception("generate_image failed")
        return ensure_action_hint(tool_user_error("生图", e))

"""image_edit 执行器。"""

from __future__ import annotations

import asyncio
import logging

from backend.common.errors import tool_user_error
from backend.common.stream import emit_file, emit_status
from backend.common.tool_outcome import ensure_action_hint
from backend.tools.context import ToolExecContext
from backend.tools.public.image_edit.tool import (
    format_image_edit_result,
    image_edit_to_urls,
)

logger = logging.getLogger(__name__)


async def execute(ctx: ToolExecContext) -> str:
    await emit_status(ctx.thread_id, "正在编辑图片…")
    prompt = str(ctx.arg("prompt") or "")
    image = str(ctx.arg("image") or "")
    image2 = str(ctx.arg("image2") or "")
    image3 = str(ctx.arg("image3") or "")
    try:
        metas = await asyncio.to_thread(
            image_edit_to_urls,
            prompt,
            image=image,
            image2=image2,
            image3=image3,
        )
        for meta in metas:
            await emit_file(ctx.thread_id, meta)
        return format_image_edit_result(metas)
    except ValueError as e:
        return ensure_action_hint(tool_user_error("图像编辑", e))
    except Exception as e:
        logger.exception("image_edit failed")
        return ensure_action_hint(tool_user_error("图像编辑", e))

"""speech_recognize 执行器（识别后自动导出 docx）。"""

from __future__ import annotations

import asyncio
import logging
import time

from backend.common.errors import tool_user_error
from backend.common.stream import emit_file, emit_status
from backend.common.tool_outcome import ensure_action_hint
from backend.tools.context import ToolExecContext
from backend.tools.public.export_docx import export_docx_to_oss
from backend.tools.public.speech_recognize.tool import (
    format_speech_recognize_result,
    speech_recognize_text,
)

logger = logging.getLogger(__name__)


async def execute(ctx: ToolExecContext) -> str:
    await emit_status(ctx.thread_id, "正在识别…")
    audio_url = str(ctx.arg("audio_url") or "")
    prompt = str(ctx.arg("prompt") or "")
    try:
        # ASR / OSS 为同步阻塞 I/O，必须离开事件循环，否则会卡死全站 SSE
        full_text = await asyncio.to_thread(
            speech_recognize_text, audio_url, prompt=prompt
        )
        await emit_status(ctx.thread_id, "正在生成文档…")
        meta = await asyncio.to_thread(
            export_docx_to_oss,
            full_text,
            f"录音转写_{time.strftime('%Y%m%d_%H%M%S')}.docx",
        )
        await emit_file(ctx.thread_id, meta)
        return format_speech_recognize_result(full_text, has_docx=True)
    except ValueError as e:
        return ensure_action_hint(tool_user_error("录音识别", e))
    except Exception as e:
        logger.exception("speech_recognize failed")
        return ensure_action_hint(tool_user_error("录音识别", e))

"""send_email 执行器。"""

from __future__ import annotations

import logging

from backend.common.stream import emit_status
from backend.common.tool_outcome import format_arg_failure
from backend.db.auth_store import get_user_by_id
from backend.tools.context import ToolExecContext
from backend.tools.gated.email.schema import validate_send_email_args
from backend.tools.gated.email.tool import run_send_email

logger = logging.getLogger(__name__)


async def execute(ctx: ToolExecContext) -> str:
    model, err = validate_send_email_args(ctx.args)
    if err or model is None:
        return format_arg_failure("邮件", err or "邮件参数不合规")
    sender_username = ""
    sender_email = None
    if ctx.user_id is not None:
        try:
            user = await get_user_by_id(ctx.user_id)
        except Exception:
            logger.exception("load sender user failed")
            user = None
        if user:
            sender_username = str(user.get("username") or "")
            sender_email = user.get("email")
    await emit_status(ctx.thread_id, "正在发送邮件…")
    return await run_send_email(
        model,
        sender_username=sender_username,
        sender_email=sender_email,
    )

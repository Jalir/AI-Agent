"""发送邮件 tool（参数校验 + 占位投递；发件人由服务端注入）。"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from backend.tools.gated.email.schema import SendEmailArgs, validate_send_email_args

logger = logging.getLogger(__name__)

TOOL_NAME = "send_email"


async def run_send_email(
    args: dict[str, Any] | SendEmailArgs,
    *,
    sender_username: str = "",
    sender_email: str | None = None,
) -> str:
    """校验并「发送」邮件。

    当前为占位投递（无 SMTP）；发件人身份来自当前登录用户，禁止由模型指定 from。
    """
    if isinstance(args, SendEmailArgs):
        model = args
    else:
        model, err = validate_send_email_args(args)
        if err or model is None:
            return err or "邮件参数不合规"

    sender_label = (sender_username or "").strip() or "当前用户"
    sender_mail = (sender_email or "").strip()
    if sender_mail:
        from_line = f"{sender_label} <{sender_mail}>（系统代发）"
    else:
        from_line = f"{sender_label}（系统代发；账号未绑定发件邮箱）"

    # TODO: 接入 SMTP / 第三方 API；接入前保持占位成功文案供联调 HITL
    logger.info(
        "send_email stub to=%s subject=%r from=%s",
        model.to,
        model.subject[:80],
        from_line,
    )
    return (
        f"邮件已成功发送至 {model.to}，主题：{model.subject}。"
        f"发送身份：{from_line}。"
        "（当前为占位投递，尚未连接真实邮件服务）"
    )


@tool(TOOL_NAME, args_schema=SendEmailArgs)
async def send_email(to: str, subject: str, body: str) -> str:
    """发送邮件（需用户确认）。仅用户明确要求发出时调用；无邮箱先 resolve_recipient。发件人由系统注入。"""
    return await run_send_email(
        {"to": to, "subject": subject, "body": body},
    )

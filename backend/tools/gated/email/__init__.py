"""邮件发送技能。"""

from __future__ import annotations

from typing import Any

from backend.common.permissions import EMAIL_SEND
from backend.tools.gated.email.schema import (
    SendEmailArgs,
    draft_from_args,
    validate_send_email_args,
)
from backend.tools.gated.email.tool import TOOL_NAME, run_send_email, send_email

TOOL = send_email
REQUIRES_APPROVAL = True
REQUIRED_PERMISSIONS = (EMAIL_SEND,)
APPROVAL_LABEL = "发送邮件"
MAX_CALLS_PER_TURN = 2


def approval_question(tool_args: Any) -> str:
    draft = draft_from_args(tool_args)
    to = draft["to"]
    subject = draft["subject"]
    if to and subject:
        return f"即将向 {to} 发送邮件「{subject}」，请核对后确认。"
    if to:
        return f"即将向 {to} 发送邮件，请核对后确认。"
    return "即将发送邮件，请核对后确认。"


def approval_payload(tool_args: Any) -> dict[str, Any]:
    """HITL interrupt 附加字段：草稿预览 + 可编辑。"""
    draft = draft_from_args(tool_args)
    return {
        "draft": draft,
        "editable": True,
        "fields": ["to", "subject", "body"],
    }


__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "REQUIRED_PERMISSIONS",
    "APPROVAL_LABEL",
    "MAX_CALLS_PER_TURN",
    "approval_question",
    "approval_payload",
    "send_email",
    "run_send_email",
    "SendEmailArgs",
    "validate_send_email_args",
    "draft_from_args",
]

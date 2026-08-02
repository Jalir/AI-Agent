"""收件人解析技能（只读，无需审批）。"""

from __future__ import annotations

from backend.common.permissions import EMAIL_RESOLVE
from backend.tools.gated.resolve_recipient.tool import (
    TOOL_NAME,
    resolve_recipient,
    run_resolve_recipient,
)

TOOL = resolve_recipient
REQUIRES_APPROVAL = False
REQUIRED_PERMISSIONS = (EMAIL_RESOLVE,)
APPROVAL_LABEL = "查找收件人"
MAX_CALLS_PER_TURN = 4

__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "REQUIRED_PERMISSIONS",
    "APPROVAL_LABEL",
    "MAX_CALLS_PER_TURN",
    "resolve_recipient",
    "run_resolve_recipient",
]

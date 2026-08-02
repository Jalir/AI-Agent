"""批量小红书图文包技能（编排 write + image，按序推卡片）。"""

from __future__ import annotations

from typing import Any

from backend.tools.public.make_xhs_pack.schema import (
    MakeXhsPackArgs,
    validate_make_xhs_pack_args,
)
from backend.tools.public.make_xhs_pack.tool import (
    TOOL_NAME,
    format_pack_result,
    make_xhs_pack,
    run_make_xhs_pack,
)

TOOL = make_xhs_pack
# 可能批量生图：走 HITL
REQUIRES_APPROVAL = True
APPROVAL_LABEL = "批量生成小红书图文"
MAX_CALLS_PER_TURN = 2


def approval_question(tool_args: Any) -> str:
    model, _ = validate_make_xhs_pack_args(tool_args if isinstance(tool_args, dict) else {})
    if model is None:
        return "即将批量生成小红书图文，是否继续？"
    kind = "图文" if model.with_image else "文案"
    return f"即将按顺序生成 {len(model.items)} 条小红书{kind}，是否继续？"


__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "APPROVAL_LABEL",
    "MAX_CALLS_PER_TURN",
    "approval_question",
    "MakeXhsPackArgs",
    "validate_make_xhs_pack_args",
    "make_xhs_pack",
    "run_make_xhs_pack",
    "format_pack_result",
]

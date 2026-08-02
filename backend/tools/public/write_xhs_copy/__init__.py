"""小红书文案生成技能。"""

from backend.tools.public.write_xhs_copy.schema import (
    WriteXhsCopyArgs,
    validate_write_xhs_copy_args,
)
from backend.tools.public.write_xhs_copy.tool import (
    TOOL_NAME,
    generate_xhs_posts,
    run_write_xhs_copy,
    write_xhs_copy,
)

TOOL = write_xhs_copy
# 纯文本改写：跳过 HITL
REQUIRES_APPROVAL = False
MAX_CALLS_PER_TURN = 2

__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "MAX_CALLS_PER_TURN",
    "WriteXhsCopyArgs",
    "validate_write_xhs_copy_args",
    "write_xhs_copy",
    "run_write_xhs_copy",
    "generate_xhs_posts",
]

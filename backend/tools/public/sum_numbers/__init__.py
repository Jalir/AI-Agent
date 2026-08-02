"""数字数组求和。"""

from __future__ import annotations

from backend.tools.public.sum_numbers.execute import execute
from backend.tools.public.sum_numbers.tool import TOOL_NAME, sum_numbers

TOOL = sum_numbers
REQUIRES_APPROVAL = False
MAX_CALLS_PER_TURN = 2

__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "MAX_CALLS_PER_TURN",
    "execute",
]

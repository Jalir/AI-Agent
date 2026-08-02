"""数字数组求平均。"""

from __future__ import annotations

from backend.tools.public.average_numbers.execute import execute
from backend.tools.public.average_numbers.tool import TOOL_NAME, average_numbers

TOOL = average_numbers
REQUIRES_APPROVAL = False
MAX_CALLS_PER_TURN = 2

__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "MAX_CALLS_PER_TURN",
    "execute",
]

"""图像编辑技能（SiliconFlow Qwen-Image-Edit，返回图片 URL）。"""

from backend.tools.public.image_edit.tool import (
    TOOL_NAME,
    format_image_edit_result,
    image_edit,
    image_edit_to_urls,
)

TOOL = image_edit
REQUIRES_APPROVAL = False
APPROVAL_LABEL = "图像编辑"
MAX_CALLS_PER_TURN = 4

__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "APPROVAL_LABEL",
    "MAX_CALLS_PER_TURN",
    "image_edit",
    "image_edit_to_urls",
    "format_image_edit_result",
]

"""通用文生图技能（OpenAI 兼容接口，返回图片 URL）。"""

from backend.tools.public.generate_image.schema import (
    GenerateImageArgs,
    validate_generate_image_args,
)
from backend.tools.public.generate_image.tool import (
    TOOL_NAME,
    format_image_result,
    generate_image,
    generate_image_to_urls,
)

TOOL = generate_image
# 计费副作用：默认跳过 HITL（需要审批时改为 True 并配置 APPROVAL_LABEL）
REQUIRES_APPROVAL = False
APPROVAL_LABEL = "生成图片"
MAX_CALLS_PER_TURN = 4

__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "APPROVAL_LABEL",
    "MAX_CALLS_PER_TURN",
    "GenerateImageArgs",
    "validate_generate_image_args",
    "generate_image",
    "generate_image_to_urls",
    "format_image_result",
]

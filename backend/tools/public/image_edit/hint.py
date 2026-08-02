"""图像编辑产品模式。"""

from __future__ import annotations

import re

from backend.common.tool_outcome import INTERNAL_HINT_PREFIX
from backend.tools.hints import HintBuildContext, ProductHint

_UTTERANCE = re.compile(
    r"(?:图像编辑|图片编辑|改图|修图|编辑图片|编辑这[张幅]图|"
    r"(?:把|将).{0,12}(?:图|照片).{0,16}(?:改|编|换成|变成))",
    re.IGNORECASE,
)


def _build_agent_hint(ctx: HintBuildContext) -> str | None:
    refs = (ctx.image_urls or [])[:3]
    if not refs:
        return (
            f"{INTERNAL_HINT_PREFIX}用户已选择「图像编辑」，但本轮没有可用参考图。"
            "不要调用工具；用自然语言请用户上传 1～3 张图片，并说明想怎么改。"
        )
    keys = ("image", "image2", "image3")
    named = [f"{keys[i]}={u}" for i, u in enumerate(refs)]
    return (
        f"{INTERNAL_HINT_PREFIX}用户已选择「图像编辑」。"
        f"建议调用 image_edit，参考图 URL 已就绪：{'；'.join(named)}。"
        "prompt 用用户的编辑描述；禁止改用 generate_image；禁止搜知识库。"
    )


PRODUCT_HINT = ProductHint(
    id="image_edit",
    route_intent="media_gen",
    priority=20,
    utterance_re=_UTTERANCE,
    needs_images=True,
    router_extra=(
        "【提示】用户点击了图像编辑：intent=media_gen；"
        "需要参考图 + 编辑描述，不要走知识库检索。"
    ),
    status="正在准备图像编辑…",
    agent_status="正在编辑…",
    build_agent_hint=_build_agent_hint,
)

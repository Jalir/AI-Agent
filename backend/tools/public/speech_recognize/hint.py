"""录音识别产品模式。"""

from __future__ import annotations

import re

from backend.common.tool_outcome import INTERNAL_HINT_PREFIX
from backend.tools.hints import HintBuildContext, ProductHint

_UTTERANCE = re.compile(
    r"(?:录音识别|语音识别|语音转文字|音频转写|听写|转写(?:这段)?(?:录音|音频)|"
    r"(?:把|将).{0,12}(?:录音|音频|语音).{0,16}(?:转|识别|听写))",
    re.IGNORECASE,
)


def _build_agent_hint(ctx: HintBuildContext) -> str | None:
    refs = (ctx.audio_urls or [])[:1]
    if not refs:
        return (
            f"{INTERNAL_HINT_PREFIX}用户已选择「录音识别」，但本轮没有可用音频。"
            "不要调用工具；用自然语言请用户上传 mp3（不超过 1 小时、50MB），"
            "并可说明希望如何转写。"
        )
    return (
        f"{INTERNAL_HINT_PREFIX}用户已选择「录音识别」。"
        f"建议调用 speech_recognize，audio_url 已就绪：{refs[0]}。"
        "prompt 用用户的识别要求（若用户未特别说明可留空走默认转写）；"
        "禁止搜知识库；禁止生图/图像编辑；"
        "工具会返回最多 2000 字预览并自动生成完整 docx，"
        "请按工具结果展示预览并提示下载，不要再调用 export_docx，不要复述全文。"
    )


PRODUCT_HINT = ProductHint(
    id="speech_recognize",
    route_intent="chat",
    priority=10,
    utterance_re=_UTTERANCE,
    needs_audio=True,
    router_extra=(
        "【提示】用户点击了录音识别：intent=chat；"
        "需要音频附件转写，不要走知识库检索或生图。"
    ),
    status="正在准备录音识别…",
    agent_status="正在识别…",
    build_agent_hint=_build_agent_hint,
)

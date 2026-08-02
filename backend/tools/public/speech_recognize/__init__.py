"""录音识别技能（Qwen3-Omni，audio_url + prompt）。"""

from backend.tools.public.speech_recognize.tool import (
    PREVIEW_MAX_CHARS,
    TOOL_NAME,
    format_speech_recognize_result,
    speech_recognize,
    speech_recognize_text,
    truncate_transcript,
)

TOOL = speech_recognize
REQUIRES_APPROVAL = False
APPROVAL_LABEL = "录音识别"
MAX_CALLS_PER_TURN = 2

__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "APPROVAL_LABEL",
    "MAX_CALLS_PER_TURN",
    "PREVIEW_MAX_CHARS",
    "speech_recognize",
    "speech_recognize_text",
    "truncate_transcript",
    "format_speech_recognize_result",
]

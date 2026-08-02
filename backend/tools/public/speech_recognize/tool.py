"""录音识别：SiliconFlow Qwen3-Omni，按 audio_url + prompt 转写。"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin

import requests
from langchain_core.tools import tool

from backend.config import settings

logger = logging.getLogger(__name__)

TOOL_NAME = "speech_recognize"
# 聊天预览字数上限；全文走 docx 下载
PREVIEW_MAX_CHARS = 2000

_DEFAULT_PROMPT = (
    "转录这个音频的内容，不要总结，不要添加标题，不要添加任何解释，只返回文本。"
)


def _chat_completions_endpoint() -> str:
    base = (settings.asr_base_url or "").rstrip("/") + "/"
    return urljoin(base, "chat/completions")


def truncate_transcript(text: str, *, max_chars: int = PREVIEW_MAX_CHARS) -> str:
    """预览截断：超过 max_chars 用省略号。"""
    s = (text or "").strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "…"


def speech_recognize_text(
    audio_url: str,
    *,
    prompt: str = "",
) -> str:
    """请求 Omni 多模态接口，返回完整转写文本。"""
    url = (audio_url or "").strip()
    if not url:
        raise ValueError("音频 URL 为空，请先上传 mp3 文件。")

    text = (prompt or "").strip() or _DEFAULT_PROMPT
    model = (settings.asr_model or "").strip()
    if not model:
        raise ValueError(
            "未配置 ASR_MODEL，无法识别录音。请在环境变量中设置模型 ID。"
        )
    api_key = (settings.asr_api_key or "").strip()
    if not api_key:
        raise ValueError("未配置录音识别 API Key（ASR_API_KEY）。")
    base = (settings.asr_base_url or "").strip()
    if not base:
        raise ValueError("未配置录音识别接口地址（ASR_BASE_URL）。")

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "audio_url",
                        "audio_url": {"url": url},
                    },
                    {
                        "type": "text",
                        "text": text,
                    },
                ],
            }
        ],
    }

    endpoint = _chat_completions_endpoint()
    timeout = float(settings.asr_timeout_sec or 600.0)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    logger.info(
        "speech_recognize request: model=%s audio=%r prompt=%r",
        model,
        url[:120],
        text[:120],
    )
    t0 = time.perf_counter()
    resp = requests.request(
        "POST", endpoint, json=payload, headers=headers, timeout=timeout
    )
    try:
        body = resp.json()
    except Exception:
        body = {"raw": (resp.text or "")[:500]}
    if resp.status_code >= 400:
        err = body.get("error") if isinstance(body, dict) else None
        detail = ""
        if isinstance(err, dict):
            detail = str(err.get("message") or err)
        elif err:
            detail = str(err)
        else:
            detail = str(body)[:400]
        raise RuntimeError(f"录音识别接口 HTTP {resp.status_code}: {detail}")

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "speech_recognize done: model=%s http=%d elapsed_ms=%d",
        model,
        resp.status_code,
        elapsed_ms,
    )

    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"录音识别接口未返回 choices: {str(body)[:400]}")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise RuntimeError(f"录音识别接口 message 无效: {str(body)[:400]}")
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
        content = "".join(parts)
    result = str(content or "").strip()
    if not result:
        raise RuntimeError(f"录音识别接口返回空内容: {str(body)[:400]}")
    return result


def format_speech_recognize_result(full_text: str, *, has_docx: bool = True) -> str:
    """给 agent 的工具结果：仅含截断预览 + 下载提示。"""
    full = (full_text or "").strip()
    total = len(full)
    preview = truncate_transcript(full)
    truncated = total > PREVIEW_MAX_CHARS
    head = (
        f"录音转写预览（全文 {total} 字，聊天仅展示前 {PREVIEW_MAX_CHARS} 字）："
        if truncated
        else "录音转写结果："
    )
    docx_bit = (
        "完整转写已生成 Word 文档，界面会显示下载卡片。"
        "请把下面预览原文原样展示给用户（不要补全被截断部分），"
        "并用一两句告知可点击下方卡片下载完整 docx；不要粘贴 URL 或 Markdown 链接。"
        if has_docx
        else "请把下面预览原文原样展示给用户。"
    )
    return f"{head}\n{preview}\n\n{docx_bit}"


@tool(TOOL_NAME)
def speech_recognize(audio_url: str, prompt: str = "") -> str:
    """将录音（mp3）转写为文字。

    Args:
        audio_url: 音频公开 URL（必填）
        prompt: 识别指令，空=默认转写
    """
    try:
        text = speech_recognize_text(audio_url, prompt=prompt)
    except ValueError as e:
        from backend.common.errors import tool_user_error

        return tool_user_error("录音识别", e)
    except Exception as e:
        logger.exception("speech_recognize failed")
        from backend.common.errors import tool_user_error

        return tool_user_error("录音识别", e)
    # 无 tools_node 特判时仍截断，避免把超长全文塞进对话
    return format_speech_recognize_result(text, has_docx=False)

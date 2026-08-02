"""独立音频转写：上传 MP3 → 静音切段 ASR → SSE 进度。

对象落在 transcribe/{user_id}/ 下，接口校验 URL 归属。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api.deps import AuthUser, get_current_user
from backend.common.audio_segment import (
    cleanup_segment_objects,
    prepare_transcribe_segments,
    user_transcribe_prefix,
)
from backend.common.oss import (
    CHAT_AUDIO_MAX_BYTES,
    CHAT_AUDIO_MIME_TYPES,
    build_file_url,
    put_object,
    resolve_attachment_url,
)
from backend.services.chat import SSE_HEADERS
from backend.tools.public.speech_recognize.tool import speech_recognize_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["transcribe"])


class TranscribeRequest(BaseModel):
    audio_url: str = Field(..., description="已上传音频的公开 URL")


def _assert_owned_audio_url(audio_url: str, user_id: int) -> None:
    """仅允许使用当前用户目录下的转写音频。"""
    path = unquote(urlparse((audio_url or "").strip()).path or "")
    marker = f"/{user_transcribe_prefix(user_id)}"
    alt = user_transcribe_prefix(user_id)
    if marker not in path and not path.lstrip("/").startswith(alt):
        raise HTTPException(
            status_code=403,
            detail="无权使用该音频，请重新上传。",
        )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _join_segment_texts(parts: list[str]) -> str:
    """拼接分段文本：中文紧挨，其它用空格。"""
    out: list[str] = []
    for raw in parts:
        s = (raw or "").strip()
        if not s:
            continue
        if not out:
            out.append(s)
            continue
        prev = out[-1]
        # 两端都像中日韩时不加空格
        if _cjk_end(prev) and _cjk_start(s):
            out.append(s)
        else:
            out.append(" " + s)
    return "".join(out).strip()


def _cjk_start(s: str) -> bool:
    if not s:
        return False
    o = ord(s[0])
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0x3040 <= o <= 0x30FF
        or 0xAC00 <= o <= 0xD7AF
    )


def _cjk_end(s: str) -> bool:
    if not s:
        return False
    o = ord(s[-1])
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0x3040 <= o <= 0x30FF
        or 0xAC00 <= o <= 0xD7AF
    )


async def _stream_transcribe(
    *,
    audio_url: str,
    user_id: int,
    request: Request,
) -> AsyncIterator[str]:
    cleanup_keys: list[str] = []
    try:
        yield _sse(
            {
                "type": "status",
                "phase": "preparing",
                "message": "正在分析音频…",
                "percent": 2,
            }
        )

        try:
            duration, segments, cleanup_keys = await asyncio.to_thread(
                prepare_transcribe_segments,
                audio_url,
                user_id=user_id,
            )
        except ValueError as e:
            yield _sse({"type": "error", "detail": str(e)})
            return
        except Exception:
            logger.exception("transcribe segment prepare failed user=%s", user_id)
            yield _sse(
                {
                    "type": "error",
                    "detail": "音频分析失败，请确认已安装 ffmpeg，且文件为有效 MP3。",
                }
            )
            return

        if await request.is_disconnected():
            return

        total = max(1, len(segments))
        yield _sse(
            {
                "type": "status",
                "phase": "segmented",
                "message": (
                    f"已分成 {total} 段，开始识别…"
                    if total > 1
                    else "开始识别…"
                ),
                "total": total,
                "duration_sec": duration,
                "percent": 8,
            }
        )

        parts: list[str] = []
        for i, seg in enumerate(segments):
            if await request.is_disconnected():
                logger.info("transcribe client disconnected user=%s", user_id)
                return

            # 准备 8% + 识别过程占 8%→96%
            base = 8 + int(i * 88 / total)
            yield _sse(
                {
                    "type": "status",
                    "phase": "recognizing",
                    "message": f"正在识别第 {i + 1}/{total} 段…",
                    "current": i + 1,
                    "total": total,
                    "percent": base,
                    "segment_start": seg.start_sec,
                    "segment_end": seg.end_sec,
                }
            )

            try:
                chunk_text = await asyncio.to_thread(
                    speech_recognize_text, seg.url
                )
            except ValueError as e:
                yield _sse({"type": "error", "detail": str(e)})
                return
            except Exception:
                logger.exception(
                    "transcribe segment ASR failed user=%s idx=%s",
                    user_id,
                    seg.index,
                )
                yield _sse(
                    {
                        "type": "error",
                        "detail": (
                            f"第 {i + 1}/{total} 段识别失败，请稍后重试。"
                            "若反复失败，可尝试更清晰的录音或较短片段。"
                        ),
                        "current": i + 1,
                        "total": total,
                        "text": _join_segment_texts(parts),
                    }
                )
                return

            chunk_text = (chunk_text or "").strip()
            if chunk_text:
                parts.append(chunk_text)
            joined = _join_segment_texts(parts)
            percent = 8 + int((i + 1) * 88 / total)
            yield _sse(
                {
                    "type": "progress",
                    "current": i + 1,
                    "total": total,
                    "percent": min(96, percent),
                    "segment_text": chunk_text,
                    "text": joined,
                    "message": f"已完成 {i + 1}/{total} 段",
                }
            )

        final_text = _join_segment_texts(parts)
        if not final_text:
            yield _sse(
                {
                    "type": "error",
                    "detail": "转写结果为空，请换一段音频重试。",
                }
            )
            return

        yield _sse(
            {
                "type": "done",
                "text": final_text,
                "total": total,
                "duration_sec": duration,
                "percent": 100,
                "message": "转写完成",
            }
        )
    finally:
        if cleanup_keys:
            await asyncio.to_thread(cleanup_segment_objects, cleanup_keys)


@router.post("/transcribe/upload")
async def upload_audio(
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
):
    """上传 MP3 到用户隔离目录 transcribe/{user_id}/。"""
    file_type = (file.content_type or "").lower().strip()
    file_name = file.filename or "audio.mp3"
    name_lower = file_name.lower()

    if file_type not in CHAT_AUDIO_MIME_TYPES:
        if name_lower.endswith(".mp3"):
            file_type = "audio/mpeg"
        else:
            raise HTTPException(status_code=400, detail="仅支持 MP3 音频")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    if len(content) > CHAT_AUDIO_MAX_BYTES:
        raise HTTPException(status_code=400, detail="audio too large (max 50MB)")

    unique_name = f"{uuid.uuid4().hex[:12]}_{file_name}"
    object_key = f"{user_transcribe_prefix(user.id)}{unique_name}"
    await asyncio.to_thread(put_object, object_key, content, file_type)
    file_url = build_file_url(object_key)
    display_url = resolve_attachment_url(
        {"url": file_url, "object_key": object_key}
    ) or file_url

    logger.info("transcribe upload user=%s -> %s", user.id, object_key)
    return {
        "url": file_url,
        "display_url": display_url,
        "object_key": object_key,
        "mime_type": file_type,
        "name": file_name,
        "file_size": len(content),
    }


@router.post("/transcribe")
async def transcribe_audio(
    body: TranscribeRequest,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    """分段转写已上传音频，SSE 推送进度与逐步拼接的全文。"""
    audio_url = (body.audio_url or "").strip()
    if not audio_url:
        raise HTTPException(status_code=400, detail="audio_url 不能为空")
    _assert_owned_audio_url(audio_url, user.id)

    return StreamingResponse(
        _stream_transcribe(
            audio_url=audio_url,
            user_id=user.id,
            request=request,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )

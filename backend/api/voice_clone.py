"""声音克隆：参考音转写 + SiliconFlow TTS（references 动态音色）。

上传 / 截取对象均落在 voice-clone/{user_id}/ 下，接口校验 URL 归属。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import requests
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.api.deps import AuthUser, get_current_user
from backend.common.audio_trim import (
    TRANSCRIBE_MAX_SEC,
    prepare_audio_for_transcribe,
    user_voice_clone_prefix,
)
from backend.common.oss import (
    CHAT_AUDIO_MAX_BYTES,
    CHAT_AUDIO_MIME_TYPES,
    build_file_url,
    delete_object,
    put_object,
    resolve_attachment_url,
)
from backend.config import settings
from backend.db import voice_clone_store
from backend.tools.public.speech_recognize.tool import speech_recognize_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice-clone", tags=["voice-clone"])

MOSS_MODEL = "fnlp/MOSS-TTSD-v0.5"
COSY_MODEL = "FunAudioLLM/CosyVoice2-0.5B"
ALLOWED_MODELS = frozenset({MOSS_MODEL, COSY_MODEL})


class TranscribeRequest(BaseModel):
    audio_url: str = Field(..., description="参考音频的公开 URL")


class SynthesizeRequest(BaseModel):
    audio_url: str = Field(..., description="参考音频的公开 URL")
    reference_text: str = Field(..., description="参考音频原文（与音频内容一致）")
    input: str = Field(..., description="要用克隆音色复述的文案")
    model: str | None = Field(None, description="TTS 模型；空则用默认")
    speed: float = Field(1.0, ge=0.25, le=4.0, description="语速 0.25–4.0")
    ref_file_name: str = Field("", description="参考音文件名（写入历史）")


def _history_public(row: dict) -> dict:
    """附带可播放 display_url。"""
    object_key = (row.get("object_key") or "").strip()
    audio_url = (row.get("audio_url") or "").strip()
    display_url = (
        resolve_attachment_url({"url": audio_url, "object_key": object_key})
        or audio_url
    )
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "speak_text": row.get("speak_text") or "",
        "model": row.get("model") or "",
        "speed": float(row.get("speed") or 1.0),
        "ref_file_name": row.get("ref_file_name") or "",
        "audio_url": audio_url,
        "display_url": display_url,
        "object_key": object_key,
        "file_size": int(row.get("file_size") or 0),
        "created_at": row.get("created_at"),
    }


def _safe_delete_oss(object_key: str) -> None:
    key = (object_key or "").strip()
    if not key:
        return
    try:
        delete_object(key)
    except Exception:
        logger.exception("voice-clone delete oss failed: %s", key)


def _tts_endpoint() -> str:
    base = (settings.tts_base_url or "").rstrip("/") + "/"
    return urljoin(base, "audio/speech")


def _resolve_model(raw: str | None) -> str:
    model = (raw or settings.tts_default_model or MOSS_MODEL).strip()
    if model not in ALLOWED_MODELS:
        allowed = ", ".join(sorted(ALLOWED_MODELS))
        raise HTTPException(
            status_code=400,
            detail=f"不支持的模型：{model}。可选：{allowed}",
        )
    return model


def _assert_owned_audio_url(audio_url: str, user_id: int) -> None:
    """仅允许使用当前用户目录下的声音克隆音频。"""
    path = unquote(urlparse((audio_url or "").strip()).path or "")
    marker = f"/{user_voice_clone_prefix(user_id)}"
    alt = user_voice_clone_prefix(user_id)
    if marker not in path and not path.lstrip("/").startswith(alt):
        raise HTTPException(
            status_code=403,
            detail="无权使用该音频，请重新上传参考音。",
        )


@router.post("/upload")
async def upload_reference(
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
):
    """上传参考 MP3 到用户隔离目录 voice-clone/{user_id}/ref/。"""
    file_type = (file.content_type or "").lower().strip()
    file_name = file.filename or "ref.mp3"
    name_lower = file_name.lower()

    if file_type not in CHAT_AUDIO_MIME_TYPES:
        if name_lower.endswith(".mp3"):
            file_type = "audio/mpeg"
        else:
            raise HTTPException(status_code=400, detail="仅支持 MP3 参考音频")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    if len(content) > CHAT_AUDIO_MAX_BYTES:
        raise HTTPException(status_code=400, detail="audio too large (max 50MB)")

    unique_name = f"{uuid.uuid4().hex[:12]}_{file_name}"
    object_key = f"{user_voice_clone_prefix(user.id)}ref/{unique_name}"
    await asyncio.to_thread(put_object, object_key, content, file_type)
    file_url = build_file_url(object_key)
    display_url = resolve_attachment_url(
        {"url": file_url, "object_key": object_key}
    ) or file_url

    logger.info("voice-clone upload user=%s -> %s", user.id, object_key)
    return {
        "url": file_url,
        "display_url": display_url,
        "object_key": object_key,
        "mime_type": file_type,
        "name": file_name,
        "file_size": len(content),
    }


@router.post("/transcribe")
async def transcribe_reference(
    body: TranscribeRequest,
    user: AuthUser = Depends(get_current_user),
):
    """转写参考音频，结果作为 references[].text。

    超过 20 秒的音频只截取前 20 秒再识别，避免长音频耗尽额度；
    返回的 audio_url 为实际用于识别的地址（可能已截取），合成时请用同一 URL。
    """
    audio_url = (body.audio_url or "").strip()
    if not audio_url:
        raise HTTPException(status_code=400, detail="audio_url 不能为空")
    _assert_owned_audio_url(audio_url, user.id)

    try:
        asr_url, truncated, duration = await asyncio.to_thread(
            prepare_audio_for_transcribe, audio_url, user_id=user.id
        )
        text = await asyncio.to_thread(speech_recognize_text, asr_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        logger.exception("voice-clone transcribe failed user=%s", user.id)
        raise HTTPException(
            status_code=502,
            detail=(
                "参考音转写失败，请稍后重试。"
                f"建议使用 8–10 秒、单人清晰的 mp3；超过 {int(TRANSCRIBE_MAX_SEC)} 秒将只识别前段。"
            ),
        ) from None

    return {
        "text": text,
        "audio_url": asr_url,
        "truncated": truncated,
        "duration_sec": duration,
        "used_sec": TRANSCRIBE_MAX_SEC if truncated else duration,
    }


@router.get("/history")
async def list_clone_history(user: AuthUser = Depends(get_current_user)):
    """当前用户的合成历史（新→旧）。"""
    rows = await voice_clone_store.list_history(user.id, limit=50)
    return {"items": [_history_public(r) for r in rows]}


@router.delete("/history/{item_id}")
async def delete_clone_history_item(
    item_id: int,
    user: AuthUser = Depends(get_current_user),
):
    row = await voice_clone_store.delete_history(item_id, user.id)
    if not row:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    _safe_delete_oss(str(row.get("object_key") or ""))
    return {"ok": True}


@router.delete("/history")
async def clear_clone_history(user: AuthUser = Depends(get_current_user)):
    rows = await voice_clone_store.delete_all_history(user.id)
    for row in rows:
        _safe_delete_oss(str(row.get("object_key") or ""))
    return {"ok": True, "deleted": len(rows)}


@router.post("/synthesize")
async def synthesize_clone(
    body: SynthesizeRequest,
    user: AuthUser = Depends(get_current_user),
):
    """用参考音 + 原文克隆音色；结果写入 OSS + DB，返回历史条目 JSON。"""
    audio_url = (body.audio_url or "").strip()
    reference_text = (body.reference_text or "").strip()
    speak_text = (body.input or "").strip()
    if not audio_url:
        raise HTTPException(status_code=400, detail="audio_url 不能为空")
    if not reference_text:
        raise HTTPException(
            status_code=400,
            detail="reference_text 不能为空（须与参考音频内容一致）",
        )
    if not speak_text:
        raise HTTPException(status_code=400, detail="input 不能为空（要复述的文案）")
    _assert_owned_audio_url(audio_url, user.id)

    model = _resolve_model(body.model)
    api_key = (settings.tts_api_key or "").strip()
    base = (settings.tts_base_url or "").strip()
    if not api_key or not base:
        raise HTTPException(
            status_code=503,
            detail="未配置 TTS 接口（TTS_BASE_URL / TTS_API_KEY）",
        )

    speed = float(body.speed)
    payload: dict[str, Any] = {
        "model": model,
        "input": speak_text,
        "response_format": "mp3",
        "stream": False,
        "speed": speed,
        "references": [
            {
                "audio": audio_url,
                "text": reference_text,
            }
        ],
    }
    if model == COSY_MODEL:
        payload["voice"] = ""

    endpoint = _tts_endpoint()
    timeout = float(settings.tts_timeout_sec or 180.0)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    logger.info(
        "voice-clone synthesize user=%s model=%s speed=%s input_len=%d",
        user.id,
        model,
        speed,
        len(speak_text),
    )

    try:
        resp = await asyncio.to_thread(
            requests.post,
            endpoint,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException:
        logger.exception("voice-clone synthesize request failed user=%s", user.id)
        raise HTTPException(
            status_code=502,
            detail="语音合成请求失败，请稍后重试。",
        ) from None

    content_type = (resp.headers.get("Content-Type") or "").lower()
    if resp.status_code >= 400:
        detail = ""
        try:
            err_body = resp.json()
            err = err_body.get("error") if isinstance(err_body, dict) else None
            if isinstance(err, dict):
                detail = str(err.get("message") or err)
            elif err:
                detail = str(err)
            else:
                detail = str(err_body)[:400]
        except Exception:
            detail = (resp.text or "")[:400]
        logger.warning(
            "voice-clone synthesize HTTP %s user=%s: %s",
            resp.status_code,
            user.id,
            detail,
        )
        raise HTTPException(
            status_code=502,
            detail=detail or f"语音合成失败（HTTP {resp.status_code}）",
        )

    audio_bytes = resp.content or b""
    if not audio_bytes:
        raise HTTPException(status_code=502, detail="语音合成返回空音频")

    if "application/json" in content_type or audio_bytes[:1] == b"{":
        try:
            err_body = resp.json()
            detail = str(err_body)[:400]
        except Exception:
            detail = "语音合成返回非音频内容"
        raise HTTPException(status_code=502, detail=detail)

    object_key = (
        f"{user_voice_clone_prefix(user.id)}"
        f"results/{uuid.uuid4().hex[:16]}.mp3"
    )
    try:
        await asyncio.to_thread(put_object, object_key, audio_bytes, "audio/mpeg")
    except Exception:
        logger.exception("voice-clone save result oss failed user=%s", user.id)
        raise HTTPException(status_code=502, detail="保存合成音频失败") from None

    file_url = build_file_url(object_key)
    try:
        row = await voice_clone_store.insert_history(
            user_id=user.id,
            speak_text=speak_text,
            model=model,
            speed=speed,
            ref_file_name=(body.ref_file_name or "").strip(),
            audio_url=file_url,
            object_key=object_key,
            file_size=len(audio_bytes),
        )
    except Exception:
        _safe_delete_oss(object_key)
        logger.exception("voice-clone save history db failed user=%s", user.id)
        raise HTTPException(status_code=502, detail="保存合成历史失败") from None

    return _history_public(row)

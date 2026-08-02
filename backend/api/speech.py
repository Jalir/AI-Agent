"""实时语音：下发 NLS Token + AppKey（前端直连阿里云 WebSocket）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from backend.api.deps import AuthUser, get_current_user
from backend.common.nls_token import get_nls_credentials

logger = logging.getLogger(__name__)

router = APIRouter(tags=["speech"])


@router.get("/api/speech/token")
async def speech_token(_user: AuthUser = Depends(get_current_user)):
    """返回短时 Token，供浏览器直连阿里云实时语音识别。"""
    try:
        creds = get_nls_credentials()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception:
        logger.exception("NLS token failed")
        raise HTTPException(status_code=502, detail="获取语音 Token 失败，请稍后重试。") from None

    return {
        "token": creds.token,
        "app_key": creds.app_key,
        "gateway_url": creds.gateway_url,
        "expire_time": creds.expire_time,
    }

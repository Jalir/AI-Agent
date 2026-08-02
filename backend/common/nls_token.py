"""阿里云智能语音交互：CreateToken（HMAC-SHA1）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

_META_HOST = "https://nls-meta.cn-shanghai.aliyuncs.com/"
_DEFAULT_GATEWAY = "wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1"

# 进程内缓存，过期前 60s 刷新
_cached: dict[str, Any] = {"token": "", "expire_time": 0}


@dataclass(frozen=True)
class NlsCredentials:
    token: str
    app_key: str
    gateway_url: str
    expire_time: int


def _encode_text(text: str | bytes) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    return quote(text, safe="~")


def _encode_dict(params: dict[str, str]) -> str:
    items = sorted(params.items(), key=lambda x: x[0])
    return "&".join(f"{_encode_text(k)}={_encode_text(v)}" for k, v in items)


def _ak_pair() -> tuple[str, str]:
    ak = (settings.nls_access_key_id or settings.oss_access_key_id or "").strip()
    sk = (settings.nls_access_key_secret or settings.oss_access_key_secret or "").strip()
    return ak, sk


def create_token(access_key_id: str, access_key_secret: str) -> tuple[str, int]:
    """调用 CreateToken，返回 (token_id, expire_unix)."""
    parameters = {
        "AccessKeyId": access_key_id,
        "Action": "CreateToken",
        "Format": "JSON",
        "RegionId": "cn-shanghai",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": str(uuid.uuid1()),
        "SignatureVersion": "1.0",
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "Version": "2019-02-28",
    }
    query_string = _encode_dict(parameters)
    string_to_sign = "GET&" + _encode_text("/") + "&" + _encode_text(query_string)
    digest = hmac.new(
        (access_key_secret + "&").encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    signature = _encode_text(base64.b64encode(digest))
    url = f"{_META_HOST}?Signature={signature}&{query_string}"

    with httpx.Client(timeout=15.0) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        logger.warning("NLS CreateToken HTTP %s: %s", resp.status_code, resp.text[:300])
        raise RuntimeError("获取语音 Token 失败")

    data = resp.json()
    token_obj = data.get("Token") if isinstance(data, dict) else None
    if not isinstance(token_obj, dict) or not token_obj.get("Id"):
        logger.warning("NLS CreateToken unexpected body: %s", str(data)[:300])
        raise RuntimeError("获取语音 Token 失败")

    expire = int(token_obj.get("ExpireTime") or 0)
    return str(token_obj["Id"]), expire


def get_nls_credentials(*, force_refresh: bool = False) -> NlsCredentials:
    app_key = (settings.nls_app_key or "").strip()
    if not app_key:
        raise RuntimeError("未配置 NLS_APP_KEY")

    ak, sk = _ak_pair()
    if not ak or not sk:
        raise RuntimeError("未配置阿里云 AccessKey（NLS_ACCESS_KEY_* 或 OSS_ACCESS_KEY_*）")

    gateway = (settings.nls_gateway_url or _DEFAULT_GATEWAY).strip().rstrip("?")
    now = int(time.time())
    cached_token = str(_cached.get("token") or "")
    cached_exp = int(_cached.get("expire_time") or 0)
    if (
        not force_refresh
        and cached_token
        and cached_exp > now + 60
    ):
        return NlsCredentials(
            token=cached_token,
            app_key=app_key,
            gateway_url=gateway,
            expire_time=cached_exp,
        )

    token, expire_time = create_token(ak, sk)
    _cached["token"] = token
    _cached["expire_time"] = expire_time
    logger.info("NLS token refreshed, expire_time=%s", expire_time)
    return NlsCredentials(
        token=token,
        app_key=app_key,
        gateway_url=gateway,
        expire_time=expire_time,
    )

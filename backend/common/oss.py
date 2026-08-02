"""阿里云 OSS 公共封装（知识库 / 聊天附件共用）。"""

from __future__ import annotations

import oss2

from backend.config import settings

# 聊天图片：单张上限、允许的 MIME
CHAT_IMAGE_MAX_BYTES = 10 * 1024 * 1024
CHAT_IMAGE_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/gif",
    }
)

# 聊天音频（录音识别）：单文件上限 50MB，时长由前端校验 ≤1 小时
CHAT_AUDIO_MAX_BYTES = 50 * 1024 * 1024
CHAT_AUDIO_MIME_TYPES = frozenset(
    {
        "audio/mpeg",
        "audio/mp3",
        "audio/x-mpeg",
        "audio/x-mp3",
    }
)


def get_oss_bucket() -> oss2.Bucket:
    auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
    return oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket_name)


def build_file_url(object_key: str) -> str:
    endpoint = settings.oss_endpoint.replace("https://", "").replace("http://", "")
    return f"https://{settings.oss_bucket_name}.{endpoint}/{object_key}"


def sign_get_url(object_key: str, *, expires: int = 3600) -> str:
    """私有桶场景：生成短期可读 URL（前端回显 / 模型拉图）。"""
    return get_oss_bucket().sign_url("GET", object_key, expires=expires)


def sign_put_url(object_key: str, content_type: str, *, expires: int = 300) -> str:
    headers = {"Content-Type": content_type}
    return get_oss_bucket().sign_url("PUT", object_key, expires=expires, headers=headers)


def put_object(object_key: str, content: bytes, content_type: str) -> None:
    get_oss_bucket().put_object(
        object_key, content, headers={"Content-Type": content_type}
    )


def delete_object(object_key: str) -> None:
    get_oss_bucket().delete_object(object_key)


def delete_prefix(prefix: str) -> int:
    """删除指定前缀下全部对象，返回删除个数（尽力而为）。"""
    key_prefix = (prefix or "").strip()
    if not key_prefix:
        return 0
    bucket = get_oss_bucket()
    deleted = 0
    for obj in oss2.ObjectIterator(bucket, prefix=key_prefix):
        try:
            bucket.delete_object(obj.key)
            deleted += 1
        except Exception:
            continue
    return deleted


def resolve_attachment_url(att: dict, *, expires: int = 3600) -> str:
    """优先用 object_key 签出可读 URL，否则回退永久 url。"""
    object_key = (att.get("object_key") or "").strip()
    if object_key:
        try:
            return sign_get_url(object_key, expires=expires)
        except Exception:
            pass
    return str(att.get("url") or "").strip()

"""对外文案安全边界：完整异常只进日志，客户端永不出现密钥/签名 URL/堆栈。"""

from __future__ import annotations

import re
from typing import Any

# ---- 敏感片段：出现即视为不可对用户展示 ----
_SENSITIVE_RE = re.compile(
    r"(?:"
    r"https?://\S+"
    r"|OSSAccessKeyId\s*="
    r"|Signature\s*="
    r"|Expires\s*="
    r"|AccessKey(?:Id|Secret)\s*[=:]"
    r"|sk-[A-Za-z0-9]{8,}"
    r"|(?:api[_-]?key|secret|token|password|passwd|authorization)\s*[:=]\s*\S+"
    r"|Bearer\s+\S+"
    r"|-----BEGIN[A-Z ]*PRIVATE KEY-----"
    r"|traceback\s*\(most recent call last\)"
    r"|File\s+\"[^\"]+\.py\""
    r"|[A-Za-z]:\\[^\s]+"  # Windows 绝对路径
    r"|/(?:home|Users|var|tmp|opt)/[^\s]+"
    r")",
    re.IGNORECASE,
)

# 成功路径里若被误拼进密钥，做就地打码（比整段替换更温和）
_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"https?://[^\s\]\)\'\"<>]+(?:OSSAccessKeyId|Signature|Expires)=[^\s\]\)\'\"<>]+",
            re.IGNORECASE,
        ),
        "[签名链接已隐藏]",
    ),
    (re.compile(r"OSSAccessKeyId=[^&\s\"']+", re.IGNORECASE), "OSSAccessKeyId=[已隐藏]"),
    (re.compile(r"Signature=[^&\s\"']+", re.IGNORECASE), "Signature=[已隐藏]"),
    (re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"), "sk-[已隐藏]"),
    (re.compile(r"Bearer\s+\S+", re.IGNORECASE), "Bearer [已隐藏]"),
    (
        re.compile(
            r"(?:api[_-]?key|access[_-]?key[_-]?secret|secret|password)\s*[:=]\s*\S+",
            re.IGNORECASE,
        ),
        "[凭证已隐藏]",
    ),
]

_DEFAULT = "操作失败，请稍后重试。"

_KIND_FALLBACK: dict[str, str] = {
    "chat": "生成失败，请稍后重试。",
    "tool": "操作失败，请稍后重试。",
    "upload": "上传失败，请检查文件后重试。",
    "parse": "解析失败，请稍后重试或更换文件。",
    "image": "图片处理失败，请稍后重试。",
    "audio": "录音识别失败，请稍后重试。",
    "export": "导出失败，请稍后重试。",
    "xhs": "图文生成失败，请稍后重试。",
}


def looks_sensitive(text: str | None) -> bool:
    """是否含 URL/密钥/堆栈/本机路径等不应给前端的内容。"""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_SENSITIVE_RE.search(raw))


def redact_secrets(text: str | None) -> str:
    """就地打码敏感片段；用于流式正文等可能误带签名的文本。"""
    s = text if text is not None else ""
    if not s:
        return ""
    for pattern, repl in _REDACT_PATTERNS:
        s = pattern.sub(repl, s)
    return s


def is_safe_user_message(text: str | None, *, max_len: int = 160) -> bool:
    """短、无敏感、无明显堆栈特征的校验文案，允许直接展示。"""
    t = (text or "").strip()
    if not t or len(t) > max_len:
        return False
    if looks_sensitive(t):
        return False
    low = t.lower()
    if any(
        x in low
        for x in (
            "traceback",
            'file "',
            "line ",
            "exception",
            "errno",
            ".py",
            "http/",
            "status code",
            "request id",
        )
    ):
        return False
    return True


def sanitize_public_text(
    text: str | None,
    *,
    fallback: str | None = None,
) -> str:
    """对外字符串总闸：敏感则整段替换，否则打码后返回。"""
    fb = fallback or _DEFAULT
    raw = (text or "").strip()
    if not raw:
        return fb
    if looks_sensitive(raw) and not is_safe_user_message(raw):
        # 整段都是错误堆栈/带 URL → 不给用户看
        return fb
    redacted = redact_secrets(raw)
    if looks_sensitive(redacted):
        return fb
    return redacted


def public_client_error(
    exc: BaseException | str | None,
    *,
    kind: str = "chat",
    fallback: str | None = None,
) -> str:
    """异常 → 可展示短句。ValueError 且文案安全时保留（如「请先上传」）。"""
    fb = fallback or _KIND_FALLBACK.get(kind, _DEFAULT)
    raw = str(exc or "").strip()
    if not raw:
        return fb

    # 业务校验（ValueError）且文案干净 → 优先保留
    if isinstance(exc, ValueError) and is_safe_user_message(raw):
        if "api_key" in raw.lower() or "未配置" in raw:
            return fb
        return raw

    low = raw.lower()

    if "image_url" in low or (
        "download" in low and ("403" in raw or "expired" in low or "signature" in low)
    ):
        return "生成失败：对话中的历史图片链接已失效，请新开对话或重新上传后再试。"
    if "audio" in low and ("403" in raw or "download" in low or "expired" in low):
        return _KIND_FALLBACK["audio"]
    if "timeout" in low or "timed out" in low:
        return "请求超时，请稍后重试。"
    if "rate" in low and "limit" in low:
        return "请求过于频繁，请稍后再试。"

    if is_safe_user_message(raw) and not looks_sensitive(raw):
        if any(
            raw.startswith(p)
            for p in (
                "失败",
                "生成失败",
                "上传失败",
                "解析失败",
                "导出失败",
                "识别失败",
                "生图失败",
                "图像编辑失败",
                "录音识别失败",
                "文案生成失败",
                "操作失败",
                "内容为空",
                "素材为空",
                "请先",
                "请提供",
                "至少需要",
                "不支持",
            )
        ):
            if "api_key" in low or "未配置" in raw:
                return fb
            return raw

    return fb


def tool_user_error(
    label: str,
    exc: BaseException | str | None = None,
) -> str:
    """工具失败专用：给 ToolMessage / agent，禁止拼原始异常。"""
    kind_map = {
        "生图": "image",
        "图像编辑": "image",
        "录音识别": "audio",
        "导出": "export",
        "文案": "xhs",
        "图文": "xhs",
    }
    kind = "tool"
    for k, v in kind_map.items():
        if k in (label or ""):
            kind = v
            break
    msg = public_client_error(exc, kind=kind, fallback=f"{label}失败，请稍后重试。")
    raw = str(exc or "").strip()
    if (
        isinstance(exc, ValueError)
        and is_safe_user_message(raw)
        and msg == raw
        and label
        and not raw.startswith(label)
    ):
        return f"{label}失败：{msg}"
    return msg


def http_public_detail(detail: Any, *, fallback: str = "请求失败，请稍后重试。") -> str:
    """HTTPException.detail → 安全字符串。"""
    if detail is None:
        return fallback
    if isinstance(detail, (list, dict)):
        # FastAPI 校验错误结构，给固定文案
        return "请求参数无效，请检查后重试。"
    return sanitize_public_text(str(detail), fallback=fallback)

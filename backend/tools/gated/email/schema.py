"""send_email 入参校验。"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

# 常见占位 / 假邮箱域名（模型爱瞎编这些）
_PLACEHOLDER_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "test.com",
        "test.org",
        "localhost",
        "email.com",
        "domain.com",
        "xxx.com",
        "foo.com",
        "bar.com",
        "placeholder.com",
        "invalid.com",
    }
)

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def is_valid_email_format(value: str) -> bool:
    email = normalize_email(value)
    if not email or len(email) > 254:
        return False
    return bool(_EMAIL_RE.match(email))


def is_placeholder_email(value: str) -> bool:
    email = normalize_email(value)
    if not email or "@" not in email:
        return True
    domain = email.rsplit("@", 1)[-1]
    if domain in _PLACEHOLDER_DOMAINS:
        return True
    # 单段域名、明显假地址
    if "." not in domain:
        return True
    local = email.split("@", 1)[0]
    if local in {"test", "xxx", "foo", "bar", "placeholder", "someone", "anybody"}:
        return True
    return False


class SendEmailArgs(BaseModel):
    to: str = Field(..., description="真实收件人邮箱")
    subject: str = Field(..., description="邮件主题")
    body: str = Field(..., description="邮件正文")

    @field_validator("to")
    @classmethod
    def _check_to(cls, v: str) -> str:
        email = normalize_email(v)
        if not is_valid_email_format(email):
            raise ValueError("收件人邮箱格式无效")
        if is_placeholder_email(email):
            raise ValueError(
                "收件人疑似占位邮箱，请先 resolve_recipient 或向用户索要真实地址"
            )
        return email

    @field_validator("subject")
    @classmethod
    def _check_subject(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("邮件主题不能为空")
        if len(s) > 200:
            raise ValueError("邮件主题过长（最多 200 字）")
        return s

    @field_validator("body")
    @classmethod
    def _check_body(cls, v: str) -> str:
        b = (v or "").strip()
        if not b:
            raise ValueError("邮件正文不能为空")
        if len(b) > 20000:
            raise ValueError("邮件正文过长（最多 20000 字）")
        return b


def validate_send_email_args(args: Any) -> tuple[SendEmailArgs | None, str | None]:
    """校验 tool args；成功返回 (model, None)，失败返回 (None, error_msg)。"""
    if not isinstance(args, dict):
        return None, "邮件参数无效"
    try:
        model = SendEmailArgs.model_validate(args)
        return model, None
    except Exception as e:
        # pydantic ValidationError 信息较冗长，取首条
        msg = str(e)
        if hasattr(e, "errors"):
            try:
                errs = e.errors()  # type: ignore[attr-defined]
                if errs:
                    loc = ".".join(str(x) for x in errs[0].get("loc", ()))
                    detail = str(errs[0].get("msg") or msg)
                    if detail.startswith("Value error, "):
                        detail = detail[len("Value error, ") :]
                    msg = f"{loc}: {detail}" if loc else detail
            except Exception:
                pass
        return None, f"邮件参数不合规：{msg}"


def draft_from_args(args: Any) -> dict[str, str]:
    if not isinstance(args, dict):
        return {"to": "", "subject": "", "body": ""}
    return {
        "to": str(args.get("to") or "").strip(),
        "subject": str(args.get("subject") or "").strip(),
        "body": str(args.get("body") or "").strip(),
    }

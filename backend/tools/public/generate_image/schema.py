"""generate_image 入参校验（含尺寸/张数软归一）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from backend.tools.public.write_xhs_copy.normalize import clamp_int

ImageSize = Literal[
    "",
    "1024x1024",
    "1024x1792",
    "1792x1024",
    "512x512",
    "768x768",
    "1K",
    "2K",
]

_ALLOWED_SIZES = frozenset(
    {
        "",
        "1024x1024",
        "1024x1792",
        "1792x1024",
        "512x512",
        "768x768",
        "1K",
        "2K",
    }
)

_SIZE_ALIASES: dict[str, str] = {
    "square": "1024x1024",
    "正方形": "1024x1024",
    "方图": "1024x1024",
    "竖图": "1024x1792",
    "竖版": "1024x1792",
    "portrait": "1024x1792",
    "横图": "1792x1024",
    "横版": "1792x1024",
    "landscape": "1792x1024",
    "1k": "1K",
    "2k": "2K",
}


def _normalize_size(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if s in _ALLOWED_SIZES:
        return s
    key = s.lower().replace("×", "x").replace(" ", "")
    if key in _SIZE_ALIASES:
        return _SIZE_ALIASES[key]
    if key in {x.lower() for x in _ALLOWED_SIZES if x}:
        for a in _ALLOWED_SIZES:
            if a.lower() == key:
                return a
    # 未知尺寸软落到服务默认，避免无谓失败
    return ""


class GenerateImageArgs(BaseModel):
    prompt: str = Field(..., description="画面描述")
    size: ImageSize = Field(default="", description="尺寸，空=默认")
    n: int = Field(default=1, ge=1, le=4, description="张数 1～4")

    @field_validator("prompt")
    @classmethod
    def _check_prompt(cls, v: str) -> str:
        text = (v or "").strip()
        if not text:
            raise ValueError("画面描述不能为空")
        return text

    @field_validator("size", mode="before")
    @classmethod
    def _size_before(cls, v: Any) -> str:
        return _normalize_size(v)

    @field_validator("n", mode="before")
    @classmethod
    def _n_before(cls, v: Any) -> int:
        return clamp_int(v, lo=1, hi=4, default=1)


def validate_generate_image_args(
    args: Any,
) -> tuple[GenerateImageArgs | None, str | None]:
    if not isinstance(args, dict):
        return None, "生图参数无效"
    try:
        return GenerateImageArgs.model_validate(args), None
    except Exception as e:
        msg = _first_error(e)
        return None, f"生图参数不合规：{msg}"


def _first_error(e: Exception) -> str:
    msg = str(e)
    if hasattr(e, "errors"):
        try:
            errs = e.errors()  # type: ignore[attr-defined]
            if errs:
                loc = ".".join(str(x) for x in errs[0].get("loc", ()))
                detail = str(errs[0].get("msg") or msg)
                if detail.startswith("Value error, "):
                    detail = detail[len("Value error, ") :]
                return f"{loc}: {detail}" if loc else detail
        except Exception:
            pass
    return msg

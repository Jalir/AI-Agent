"""小红书文案：工具入参 + 结构化输出。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from backend.tools.public.write_xhs_copy.normalize import (
    clamp_int,
    normalize_xhs_style,
)

XhsStyle = Literal["种草", "测评", "清单", "干货"]


class WriteXhsCopyArgs(BaseModel):
    material: str = Field(..., description="素材或知识库摘要")
    style: XhsStyle = Field(default="种草", description="风格：种草/测评/清单/干货")
    count: int = Field(default=1, ge=1, le=5, description="篇数 1～5")

    @field_validator("material")
    @classmethod
    def _check_material(cls, v: str) -> str:
        text = (v or "").strip()
        if not text:
            raise ValueError("素材不能为空")
        return text

    @field_validator("style", mode="before")
    @classmethod
    def _style_before(cls, v: Any) -> str:
        return normalize_xhs_style(v)

    @field_validator("count", mode="before")
    @classmethod
    def _count_before(cls, v: Any) -> int:
        return clamp_int(v, lo=1, hi=5, default=1)


class XhsPost(BaseModel):
    title: str = Field(default="", description="笔记标题")
    body: str = Field(default="", description="笔记正文")
    tags: list[str] = Field(default_factory=list, description="话题标签")
    image_prompt: str = Field(
        default="",
        description="可选配图画面描述，供 generate_image 使用",
    )
    notes: str = Field(default="", description="内部备注（素材不足等）")


class XhsCopyResult(BaseModel):
    posts: list[XhsPost] = Field(default_factory=list)


def validate_write_xhs_copy_args(
    args: Any,
) -> tuple[WriteXhsCopyArgs | None, str | None]:
    if not isinstance(args, dict):
        return None, "文案参数无效"
    try:
        return WriteXhsCopyArgs.model_validate(args), None
    except Exception as e:
        msg = _first_error(e)
        return None, f"文案参数不合规：{msg}"


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

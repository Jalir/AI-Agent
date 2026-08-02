"""make_xhs_pack 入参校验（含 items / style 软归一）。"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.tools.public.write_xhs_copy.normalize import normalize_xhs_style
from backend.tools.public.write_xhs_copy.schema import XhsStyle

_MAX_ITEMS = 10


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("0", "false", "no", "否", "n"):
        return False
    if s in ("1", "true", "yes", "是", "y"):
        return True
    return default


def _coerce_items(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        text = v.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return _coerce_items(parsed)
            except Exception:
                pass
        # 空行 / --- / 编号列表软拆分
        parts = re.split(r"\n\s*---\s*\n|\n{2,}", text)
        out = [p.strip() for p in parts if p.strip()]
        if len(out) <= 1:
            numbered = re.split(r"\n\s*(?=\d+[\.、\)）]\s*)", text)
            out = [p.strip() for p in numbered if p.strip()]
        return out[:_MAX_ITEMS] if out else [text]
    if isinstance(v, list):
        out: list[str] = []
        for x in v:
            s = str(x).strip() if x is not None else ""
            if s:
                out.append(s)
            if len(out) >= _MAX_ITEMS:
                break
        return out
    s = str(v).strip()
    return [s] if s else []


class MakeXhsPackArgs(BaseModel):
    items: list[str] = Field(
        ...,
        min_length=1,
        max_length=_MAX_ITEMS,
        description="素材列表，一项一条，1～10 条",
    )
    style: XhsStyle = Field(default="种草", description="风格：种草/测评/清单/干货")
    with_image: bool = Field(default=True, description="是否配图")

    @field_validator("items", mode="before")
    @classmethod
    def _items_before(cls, v: Any) -> list[str]:
        return _coerce_items(v)

    @field_validator("items")
    @classmethod
    def _check_items(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("素材列表不能为空；请每项放一条摘要")
        return v[:_MAX_ITEMS]

    @field_validator("style", mode="before")
    @classmethod
    def _style_before(cls, v: Any) -> str:
        return normalize_xhs_style(v)

    @field_validator("with_image", mode="before")
    @classmethod
    def _with_image_before(cls, v: Any) -> bool:
        return _as_bool(v, True)


def validate_make_xhs_pack_args(
    args: Any,
) -> tuple[MakeXhsPackArgs | None, str | None]:
    if not isinstance(args, dict):
        return None, "图文包参数无效"
    try:
        return MakeXhsPackArgs.model_validate(args), None
    except Exception as e:
        msg = _first_error(e)
        return None, f"图文包参数不合规：{msg}"


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

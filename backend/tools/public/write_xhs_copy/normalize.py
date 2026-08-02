"""小红书相关入参软归一（容错：近义/夹杂 → 规范值）。"""

from __future__ import annotations

from typing import Any

_ALLOWED_STYLES = ("种草", "测评", "清单", "干货")

_STYLE_ALIASES: dict[str, str] = {
    "种草风": "种草",
    "种草向": "种草",
    "安利": "种草",
    "推荐": "种草",
    "测评风": "测评",
    "评测": "测评",
    "横评": "测评",
    "清单风": "清单",
    "列表": "清单",
    "合集": "清单",
    "干货风": "干货",
    "教程": "干货",
    "科普": "干货",
}


def normalize_xhs_style(value: Any, *, default: str = "种草") -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    if raw in _ALLOWED_STYLES:
        return raw
    if raw in _STYLE_ALIASES:
        return _STYLE_ALIASES[raw]
    for key in _ALLOWED_STYLES:
        if key in raw:
            return key
    for alias, canon in _STYLE_ALIASES.items():
        if alias in raw:
            return canon
    # 未知风格软落到默认，避免无谓失败
    return default


def clamp_int(value: Any, *, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))

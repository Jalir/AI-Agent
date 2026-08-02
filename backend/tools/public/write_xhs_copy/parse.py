"""健壮解析 LLM 输出的 JSON（业界常用：软解析 → 转义修复 → json_repair）。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def extract_json_text(raw: str) -> str:
    text = (raw or "").strip()
    text = _FENCE_RE.sub("", text).strip()
    if text.startswith("{") or text.startswith("["):
        return text
    start_obj, start_arr = text.find("{"), text.find("[")
    starts = [i for i in (start_obj, start_arr) if i >= 0]
    if not starts:
        return text
    start = min(starts)
    end_obj, end_arr = text.rfind("}"), text.rfind("]")
    end = max(end_obj, end_arr)
    if end > start:
        return text[start : end + 1]
    return text[start:]


def _normalize_quotes(text: str) -> str:
    return (
        text.replace("\ufeff", "")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )


def escape_controls_in_strings(text: str) -> str:
    """把 JSON 字符串字面量里的裸 \\n/\\t/控制符转成合法转义。"""
    out: list[str] = []
    in_str = False
    escape = False
    for ch in text:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\" and in_str:
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            out.append(ch)
            continue
        if in_str and ord(ch) < 32:
            mapping = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
            out.append(mapping.get(ch, f"\\u{ord(ch):04x}"))
            continue
        out.append(ch)
    return "".join(out)


def loads_llm_json(raw: str) -> Any:
    """多层兜底解析；尽量不抛，抛则说明彻底不可修。"""
    text = extract_json_text(raw)
    text = _normalize_quotes(text)
    errors: list[str] = []

    for label, candidate in (
        ("strict_false", text),
        ("escaped_controls", escape_controls_in_strings(text)),
    ):
        try:
            return json.loads(candidate, strict=False)
        except json.JSONDecodeError as e:
            errors.append(f"{label}: {e}")

    try:
        from json_repair import loads as repair_loads

        repaired = repair_loads(escape_controls_in_strings(text))
        logger.info("llm json repaired via json_repair")
        return repaired
    except Exception as e:
        errors.append(f"json_repair: {e}")

    raise ValueError("无法解析模型 JSON：" + " | ".join(errors[:3]))

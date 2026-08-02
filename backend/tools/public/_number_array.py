"""销售/通用：解析数字 JSON 数组并格式化结果。"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any


def parse_number_array(raw: Any, *, name: str = "numbers_json") -> list[Decimal]:
    """把 LLM 传入的 JSON 数组解析为 Decimal 列表。"""
    if raw is None or raw == "":
        raise ValueError(f"请提供 {name}：JSON 数字数组，例如 [1, 2.5, 3]")
    if isinstance(raw, list):
        data = raw
    else:
        text = str(raw).strip()
        if not text:
            raise ValueError(f"请提供 {name}：JSON 数字数组，例如 [1, 2.5, 3]")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"{name} 不是合法 JSON：{e}") from e
    if not isinstance(data, list):
        raise ValueError(f"{name} 必须是 JSON 数组，例如 [1, 2.5, 3]")
    if not data:
        raise ValueError(f"{name} 不能为空数组")

    nums: list[Decimal] = []
    for i, item in enumerate(data):
        if isinstance(item, bool) or item is None:
            raise ValueError(f"{name}[{i}] 不是有效数字：{item!r}")
        try:
            if isinstance(item, Decimal):
                nums.append(item)
            elif isinstance(item, (int, float)):
                nums.append(Decimal(str(item)))
            else:
                text = str(item).strip().replace(",", "")
                if not text:
                    raise ValueError(f"{name}[{i}] 为空")
                nums.append(Decimal(text))
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"{name}[{i}] 不是有效数字：{item!r}") from e
    return nums


def format_number(value: Decimal) -> str | int | float:
    """尽量返回干净 JSON 友好数值（去掉无意义尾零）。"""
    normalized = value.normalize() if value == value.to_integral() else +value
    # 整数用 int，否则用不带多余零的字符串再转 float/保持精确字符串
    if normalized == normalized.to_integral():
        as_int = int(normalized)
        # JSON 安全范围内用 int
        if -9007199254740991 <= as_int <= 9007199254740991:
            return as_int
        return str(as_int)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    try:
        return float(text)
    except ValueError:
        return text

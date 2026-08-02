"""销售分析：复用本轮 query_sales_data 结果。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.services.sales_query import _to_number
from backend.tools.public._number_array import parse_number_array


def build_column_arrays(
    rows: list[dict[str, Any]],
    columns: list[str],
    *,
    max_len: int = 500,
) -> dict[str, list[float]]:
    """从结果行抽出偏数值列，便于 sum_numbers / 模型直接复用。"""
    arrays: dict[str, list[float]] = {}
    if not rows:
        return arrays
    n = len(rows)
    for col in columns:
        vals: list[float] = []
        for r in rows:
            num = _to_number(r.get(col) if isinstance(r, dict) else None)
            if num is not None:
                vals.append(float(num))
        # 至少一半单元格可解析为数字才视为数值列
        if vals and len(vals) * 2 >= n:
            arrays[str(col)] = vals[:max_len]
    return arrays


def extract_column_numbers(
    last_query: dict[str, Any] | None,
    column: str,
) -> list[Decimal]:
    """从上次查询结果取某一列数字（优先 arrays）。"""
    col = (column or "").strip()
    if not col:
        raise ValueError("请提供 column（结果中的数值列名）")
    if not isinstance(last_query, dict) or not last_query.get("rows"):
        raise ValueError(
            "本轮尚无可用的查询结果。请先调用一次 query_sales_data，"
            "或直接把数字数组传入 numbers_json。"
        )

    arrays = last_query.get("arrays")
    if isinstance(arrays, dict) and col in arrays:
        return parse_number_array(arrays[col], name=f"arrays[{col}]")

    rows = last_query.get("rows") or []
    cols = [str(c) for c in (last_query.get("columns") or [])]
    if cols and col not in cols:
        # 行里可能仍有该键（明细模式）
        if not any(isinstance(r, dict) and col in r for r in rows):
            opts = "、".join(cols) if cols else "（无列）"
            raise ValueError(f"结果中无列「{col}」，可选：{opts}")

    nums: list[Decimal] = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        raw = r.get(col)
        num = _to_number(raw)
        if num is None:
            raise ValueError(f"行 {i} 的「{col}」不是有效数字：{raw!r}")
        nums.append(Decimal(str(num)))
    if not nums:
        raise ValueError(f"列「{col}」没有可汇总的数字")
    return nums

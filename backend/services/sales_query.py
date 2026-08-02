"""销售表白名单查询 / 聚合 / ECharts option 构建。"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from backend.config import settings
from backend.db import sales_workspace_store as store

_ALLOWED_OPS = frozenset({"eq", "ne", "contains", "gte", "lte", "gt", "lt", "in"})
_ALLOWED_AGGS = frozenset({"sum", "avg", "count", "min", "max"})
_TIME_GRAINS = frozenset({"year", "month", "day"})
# 年在前：YYYY-MM[-DD][ time] / YYYY/M/D / YYYY年M月[D日]；不含有歧义的 D/M/YYYY
_DATE_YMD_RE = re.compile(
    r"^(?P<y>\d{4})[-/年](?P<m>\d{1,2})"
    r"(?:[-/月](?P<d>\d{1,2}))?"
    r"(?:日)?"
    r"(?:[T\s].*)?$",
)
_MONTH_ONLY_RE = re.compile(r"^(?P<m2>\d{1,2})\s*月$")
_MONTH_RE = re.compile(
    r"(?P<y>\d{4})[-/年](?P<m>\d{1,2})|"
    r"(?P<m2>\d{1,2})\s*月|"
    r"(?P<ym>\d{6})",
)


def _default_limit() -> int:
    return max(1, int(getattr(settings, "sales_query_default_limit", 200) or 200))


def _max_limit() -> int:
    return max(1, int(getattr(settings, "sales_query_max_limit", 2000) or 2000))


def _to_number(val: Any) -> float | None:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "").replace("¥", "").replace("￥", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_date_parts(val: Any) -> tuple[int, int, int | None] | None:
    """解析年在前的日期，返回 (year, month, day|None)；失败返回 None。

    兼容：datetime/date、YYYY-MM-DD[ time]、YYYY/M/D、YYYY年M月[D日]、YYYYMM。
    不处理有歧义的 D/M/YYYY（如 03/02/2025）。
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return (val.year, val.month, val.day)
    if isinstance(val, date):
        return (val.year, val.month, val.day)

    s = str(val).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{6}", s):
        y, m = int(s[:4]), int(s[4:6])
        if 1 <= m <= 12:
            return (y, m, None)
        return None

    m = _DATE_YMD_RE.match(s)
    if m:
        y, mo = int(m.group("y")), int(m.group("m"))
        if not (1 <= mo <= 12):
            return None
        d_raw = m.group("d")
        day = int(d_raw) if d_raw else None
        if day is not None and not (1 <= day <= 31):
            return None
        return (y, mo, day)

    # 宽松回退：字符串中嵌套「年-月」片段（过滤 / year_months 画像用）
    m = _MONTH_RE.search(s)
    if not m:
        return None
    if m.group("ym"):
        ym = m.group("ym")
        mo = int(ym[4:6])
        if 1 <= mo <= 12:
            return (int(ym[:4]), mo, None)
        return None
    if m.group("y") and m.group("m"):
        mo = int(m.group("m"))
        if 1 <= mo <= 12:
            return (int(m.group("y")), mo, None)
        return None
    return None


def format_time_grain(val: Any, grain: str) -> str | None:
    """按 year/month/day 归一化；无法解析或缺分量时返回 None。"""
    g = (grain or "").strip().lower()
    if g not in _TIME_GRAINS:
        return None
    parts = parse_date_parts(val)
    if not parts:
        return None
    y, mo, day = parts
    if y <= 0:
        return None
    if g == "year":
        return f"{y:04d}"
    if g == "month":
        return f"{y:04d}-{mo:02d}"
    if day is None:
        return None
    return f"{y:04d}-{mo:02d}-{day:02d}"


def parse_year_month(val: Any) -> str | None:
    """归一化为 YYYY-MM；仅「x月」时为 --MM；失败返回 None。"""
    formatted = format_time_grain(val, "month")
    if formatted:
        return formatted
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    m = _MONTH_ONLY_RE.match(s)
    if m:
        mo = int(m.group("m2"))
        if 1 <= mo <= 12:
            return f"--{mo:02d}"
    m = _MONTH_RE.search(s)
    if m and m.group("m2"):
        mo = int(m.group("m2"))
        if 1 <= mo <= 12:
            return f"--{mo:02d}"
    return None


def _group_cell_value(cell: Any, time_grain: str | None) -> str:
    """分组键：有 time_grain 且可解析则用归一化值，否则原字符串。"""
    if time_grain:
        keyed = format_time_grain(cell, time_grain)
        if keyed:
            return keyed
    if cell is None:
        return ""
    return str(cell)


def _match_filter(cell: Any, op: str, expected: Any) -> bool:
    if op == "eq":
        if expected is None:
            return cell is None or str(cell).strip() == ""
        # 月份友好比较
        em = parse_year_month(expected)
        cm = parse_year_month(cell)
        if em and cm:
            if em.startswith("--"):
                return cm.endswith(em[1:])  # --03 vs 2024-03
            if cm.startswith("--"):
                return em.endswith(cm[1:])
            return cm == em
        return str(cell).strip() == str(expected).strip()
    if op == "ne":
        return not _match_filter(cell, "eq", expected)
    if op == "contains":
        return str(expected).strip().lower() in str(cell or "").lower()
    if op == "in":
        values = expected if isinstance(expected, list) else [expected]
        return any(_match_filter(cell, "eq", v) for v in values)
    num = _to_number(cell)
    exp = _to_number(expected)
    if num is None or exp is None:
        # 回退字符串比较
        a, b = str(cell or ""), str(expected or "")
        if op == "gte":
            return a >= b
        if op == "lte":
            return a <= b
        if op == "gt":
            return a > b
        if op == "lt":
            return a < b
        return False
    if op == "gte":
        return num >= exp
    if op == "lte":
        return num <= exp
    if op == "gt":
        return num > exp
    if op == "lt":
        return num < exp
    return False


def _validate_filters(columns: set[str], filters: list[dict]) -> list[dict]:
    out: list[dict] = []
    for f in filters or []:
        if not isinstance(f, dict):
            continue
        col = str(f.get("column") or "").strip()
        op = str(f.get("op") or "eq").strip().lower()
        if col not in columns:
            raise ValueError(f"未知列：{col}")
        if op not in _ALLOWED_OPS:
            raise ValueError(f"不支持的操作：{op}")
        out.append({"column": col, "op": op, "value": f.get("value")})
    return out


def _apply_filters(rows: list[dict], filters: list[dict]) -> list[dict]:
    if not filters:
        return list(rows)
    matched: list[dict] = []
    for data in rows:
        ok = True
        for f in filters:
            if not _match_filter(data.get(f["column"]), f["op"], f["value"]):
                ok = False
                break
        if ok:
            matched.append(data)
    return matched


def _year_months_in_column(rows: list[dict], column: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for data in rows:
        ym = parse_year_month(data.get(column))
        if not ym or ym.startswith("--") or ym in seen:
            continue
        seen.add(ym)
        out.append(ym)
    out.sort()
    return out


def _resolve_ym_value(expected: Any, available: list[str]) -> Any:
    """按表内真实 YYYY-MM 纠正年份猜测：唯一同月则采用，多个则抛错请用户确认。"""
    want = parse_year_month(expected)
    if not want or want.startswith("--"):
        return expected
    if want in available:
        return expected
    month = want[5:7]
    candidates = [ym for ym in available if ym.endswith(f"-{month}")]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ValueError(
            "请向用户确认月份对应哪一年："
            f"过滤值 {want!r} 在表中无匹配，同月可选 {candidates}。"
        )
    return expected


def _resolve_month_filters(
    filters: list[dict],
    rows: list[dict],
) -> tuple[list[dict], list[str]]:
    """0 命中时，用表内年月取值纠正 eq/in 的月份过滤（跟数据走）。"""
    notes: list[str] = []
    resolved: list[dict] = []
    for f in filters:
        op = f.get("op") or "eq"
        col = f["column"]
        val = f.get("value")
        if op not in {"eq", "in"}:
            resolved.append(f)
            continue
        available = _year_months_in_column(rows, col)
        if not available:
            resolved.append(f)
            continue
        if op == "eq":
            new_val = _resolve_ym_value(val, available)
            if new_val != val:
                notes.append(f"{col}: {val!r}→{new_val!r}")
            resolved.append({**f, "value": new_val})
            continue
        # in
        values = val if isinstance(val, list) else [val]
        new_values = []
        changed = False
        for item in values:
            nv = _resolve_ym_value(item, available)
            if nv != item:
                changed = True
            new_values.append(nv)
        if changed:
            notes.append(f"{col}: {values!r}→{new_values!r}")
        resolved.append({**f, "value": new_values})
    return resolved, notes


def _profile_columns(
    rows: list[dict],
    columns: list[dict],
    *,
    max_samples: int = 12,
) -> list[dict]:
    """为 list 补充样例 / 年月取值，减轻模型瞎猜年份。"""
    out: list[dict] = []
    for c in columns or []:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        samples: list[str] = []
        seen: set[str] = set()
        for data in rows:
            raw = data.get(name)
            if raw is None or raw == "":
                continue
            text = str(raw).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            samples.append(text)
            if len(samples) >= max_samples:
                break
        item: dict[str, Any] = {
            "name": name,
            "type": c.get("type"),
            "samples": samples,
        }
        yms = _year_months_in_column(rows, name)
        if yms:
            item["year_months"] = yms[:24]
        out.append(item)
    return out


async def list_workspace_tables(workspace_id: int) -> list[dict]:
    tables = await store.list_tables(workspace_id)
    out: list[dict] = []
    for t in tables:
        table_id = int(t["id"])
        row_count = int(t.get("row_count") or 0)
        sample_n = max(1, min(row_count or 1, 500))
        raw_rows = await store.fetch_table_rows(table_id, offset=0, limit=sample_n)
        data_rows = [
            item.get("data") if isinstance(item.get("data"), dict) else {}
            for item in raw_rows
        ]
        cols = _profile_columns(data_rows, t.get("columns") or [])
        out.append(
            {
                "table_id": table_id,
                "file_id": t["file_id"],
                "sheet_name": t["sheet_name"],
                "columns": cols,
                "row_count": row_count,
                "warnings": t.get("warnings") or [],
            }
        )
    return out


async def query_sales_table(
    workspace_id: int,
    *,
    table_id: int,
    filters: list[dict] | None = None,
    group_by: list[str] | None = None,
    aggregations: list[dict] | None = None,
    columns: list[str] | None = None,
    order_by: str | None = None,
    order_desc: bool = False,
    limit: int | None = None,
    time_grain: str | None = None,
) -> dict[str, Any]:
    meta = await store.get_table_for_workspace(table_id, workspace_id)
    if not meta:
        raise ValueError("数据表不存在或不属于当前工作区")

    col_meta = meta.get("columns") or []
    col_names = {str(c.get("name")) for c in col_meta if c.get("name")}
    validated_filters = _validate_filters(col_names, filters or [])

    grain: str | None = None
    if time_grain is not None and str(time_grain).strip():
        grain = str(time_grain).strip().lower()
        if grain not in _TIME_GRAINS:
            raise ValueError(
                f"不支持的 time_grain：{time_grain!r}，可选 year / month / day"
            )

    gb = [str(c).strip() for c in (group_by or []) if str(c).strip()]
    for c in gb:
        if c not in col_names:
            raise ValueError(f"未知分组列：{c}")

    aggs: list[dict] = []
    for a in aggregations or []:
        if not isinstance(a, dict):
            continue
        fn = str(a.get("fn") or "").strip().lower()
        col = str(a.get("column") or "").strip()
        alias = str(a.get("alias") or f"{fn}_{col}").strip()[:64]
        if fn not in _ALLOWED_AGGS:
            raise ValueError(f"不支持的聚合：{fn}")
        if fn != "count" and col not in col_names:
            raise ValueError(f"未知聚合列：{col}")
        aggs.append({"fn": fn, "column": col, "alias": alias or f"{fn}_{col}"})

    select_cols = [str(c).strip() for c in (columns or []) if str(c).strip()]
    for c in select_cols:
        if c not in col_names:
            raise ValueError(f"未知列：{c}")

    lim = int(limit) if limit is not None else _default_limit()
    lim = max(1, min(lim, _max_limit()))

    # 拉取全表再内存过滤（第一期表规模有上限；避免模型拼 SQL）
    total = await store.count_table_rows(table_id)
    raw_rows = await store.fetch_table_rows(table_id, offset=0, limit=max(total, 1))
    all_data = [
        item.get("data") if isinstance(item.get("data"), dict) else {}
        for item in raw_rows
    ]
    matched = _apply_filters(all_data, validated_filters)
    filter_notes: list[str] = []
    # 模型猜错年份导致 0 行：按表内真实年月纠正（唯一同月则采用，多个则请用户确认）
    if not matched and validated_filters:
        rewritten, filter_notes = _resolve_month_filters(validated_filters, all_data)
        if filter_notes:
            validated_filters = rewritten
            matched = _apply_filters(all_data, validated_filters)

    evidence = {
        "table_id": table_id,
        "sheet_name": meta.get("sheet_name") or "",
        "filters": validated_filters,
        "matched_rows": len(matched),
        "source_rows": total,
    }
    if filter_notes:
        evidence["filter_resolved"] = filter_notes
    if grain:
        evidence["time_grain"] = grain

    if aggs or gb:
        buckets: dict[tuple, dict[str, Any]] = {}
        stats: dict[tuple, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        counts: dict[tuple, int] = defaultdict(int)

        for row in matched:
            key = tuple(_group_cell_value(row.get(c), grain) for c in gb)
            counts[key] += 1
            if key not in buckets:
                buckets[key] = {c: key[i] for i, c in enumerate(gb)}
            for a in aggs:
                if a["fn"] == "count":
                    continue
                num = _to_number(row.get(a["column"]))
                if num is not None:
                    stats[key][a["alias"]].append(num)

        result_rows: list[dict] = []
        for key, base in buckets.items():
            row = dict(base)
            for a in aggs:
                alias = a["alias"]
                if a["fn"] == "count":
                    row[alias] = counts[key]
                else:
                    vals = stats[key].get(alias) or []
                    if not vals:
                        row[alias] = None
                    elif a["fn"] == "sum":
                        row[alias] = round(sum(vals), 6)
                    elif a["fn"] == "avg":
                        row[alias] = round(sum(vals) / len(vals), 6)
                    elif a["fn"] == "min":
                        row[alias] = min(vals)
                    elif a["fn"] == "max":
                        row[alias] = max(vals)
            result_rows.append(row)

        if order_by:
            ob = str(order_by).strip()
            result_rows.sort(
                key=lambda r: (r.get(ob) is None, r.get(ob)),
                reverse=bool(order_desc),
            )
        result_rows = result_rows[:lim]
        out_columns = list(gb) + [a["alias"] for a in aggs]
        return {
            "mode": "aggregate",
            "columns": out_columns,
            "rows": result_rows,
            "evidence": evidence,
        }

    # 明细
    if select_cols:
        detail = [{c: r.get(c) for c in select_cols} for r in matched]
        out_columns = select_cols
    else:
        detail = matched
        out_columns = [str(c.get("name")) for c in col_meta if c.get("name")]

    if order_by:
        ob = str(order_by).strip()
        if ob in col_names:
            detail.sort(
                key=lambda r: (r.get(ob) is None, r.get(ob)),
                reverse=bool(order_desc),
            )
    detail = detail[:lim]
    return {
        "mode": "detail",
        "columns": out_columns,
        "rows": detail,
        "evidence": evidence,
    }


def build_echarts_option(
    *,
    chart_type: str,
    title: str,
    categories: list[Any],
    values: list[Any],
    series_name: str = "数值",
) -> dict[str, Any]:
    ctype = (chart_type or "bar").strip().lower()
    if ctype not in {"bar", "line", "pie"}:
        ctype = "bar"
    cats = ["" if c is None else str(c) for c in categories]
    nums: list[float] = []
    for v in values:
        n = _to_number(v)
        nums.append(0.0 if n is None else float(n))

    if ctype == "pie":
        return {
            "title": {"text": title or "图表", "left": "center"},
            "tooltip": {"trigger": "item"},
            "series": [
                {
                    "name": series_name or "数值",
                    "type": "pie",
                    "radius": "60%",
                    "data": [
                        {"name": cats[i] if i < len(cats) else str(i), "value": nums[i]}
                        for i in range(len(nums))
                    ],
                }
            ],
        }

    return {
        "title": {"text": title or "图表", "left": "center"},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "3%", "right": "4%", "bottom": "8%", "containLabel": True},
        "xAxis": {"type": "category", "data": cats},
        "yAxis": {"type": "value"},
        "series": [
            {
                "name": series_name or "数值",
                "type": ctype,
                "data": nums,
                "smooth": ctype == "line",
            }
        ],
    }


def chart_from_query_result(
    result: dict[str, Any],
    *,
    chart_type: str = "bar",
    title: str = "",
    category_column: str = "",
    value_column: str = "",
    series_name: str = "",
) -> dict[str, Any]:
    rows = result.get("rows") or []
    cols = [str(c) for c in (result.get("columns") or [])]
    if not rows:
        raise ValueError(
            "没有可绘制的数据行（过滤后无匹配行）。"
            "请向用户确认过滤条件是否与表内取值一致后再出图。"
        )

    cat_col = (category_column or "").strip()
    val_col = (value_column or "").strip()
    if not cat_col or not val_col:
        opts = "、".join(cols) if cols else "（无列）"
        raise ValueError(
            "请指定 category_column 与 value_column，无法自动猜测。"
            f"当前结果可选列：{opts}"
        )
    if cat_col not in cols:
        raise ValueError(
            f"请指定有效的 category_column：结果中无「{cat_col}」，"
            f"可选：{'、'.join(cols)}"
        )
    if val_col not in cols:
        raise ValueError(
            f"请指定有效的 value_column：结果中无「{val_col}」，"
            f"可选：{'、'.join(cols)}"
        )

    cats = [r.get(cat_col) for r in rows]
    vals = [r.get(val_col) for r in rows]
    option = build_echarts_option(
        chart_type=chart_type,
        title=title or f"{val_col} by {cat_col}",
        categories=cats,
        values=vals,
        series_name=series_name or val_col,
    )
    return {
        "option": option,
        "category_column": cat_col,
        "value_column": val_col,
        "row_count": len(rows),
        "evidence": result.get("evidence") or {},
    }

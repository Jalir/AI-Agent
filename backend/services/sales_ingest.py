"""销售 Excel 解析入库：第一期仅支持一行表头的明细表。"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from backend.config import settings
from backend.db import sales_workspace_store as store

logger = logging.getLogger(__name__)

_UNNAMED_RE = re.compile(r"^Unnamed", re.I)


def _max_sheets() -> int:
    return max(1, int(getattr(settings, "sales_max_sheets", 20) or 20))


def _max_rows() -> int:
    return max(100, int(getattr(settings, "sales_max_rows_per_sheet", 50_000) or 50_000))


def _normalize_col_name(raw: Any, idx: int) -> str:
    text = str(raw).strip() if raw is not None else ""
    if not text or _UNNAMED_RE.match(text) or text.lower() == "nan":
        return f"列{idx + 1}"
    return text[:120]


def _cell_to_jsonable(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    if hasattr(val, "item"):
        try:
            return val.item()
        except Exception:
            pass
    if isinstance(val, (str, int, float, bool)):
        return val
    return str(val)


def _infer_col_type(series_values: list[Any]) -> str:
    non_null = [v for v in series_values if v is not None and str(v).strip() != ""]
    if not non_null:
        return "string"
    num_ok = 0
    date_ok = 0
    for v in non_null[:80]:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            num_ok += 1
            continue
        if isinstance(v, (datetime, date)):
            date_ok += 1
            continue
        s = str(v).strip()
        if re.fullmatch(r"-?\d+(\.\d+)?", s.replace(",", "")):
            num_ok += 1
            continue
        if re.search(r"\d{4}[-/年]\d{1,2}", s) or re.fullmatch(r"\d{1,2}\s*月", s):
            date_ok += 1
    n = len(non_null[:80])
    if date_ok >= max(1, int(n * 0.5)):
        return "date"
    if num_ok >= max(1, int(n * 0.6)):
        return "number"
    return "string"


def parse_excel_bytes(content: bytes) -> list[dict[str, Any]]:
    """解析 xlsx → [{sheet_name, columns, rows, warnings}]。"""
    import pandas as pd

    if not content:
        raise ValueError("空文件")

    max_sheets = _max_sheets()
    max_rows = _max_rows()
    bio = io.BytesIO(content)

    try:
        xl = pd.ExcelFile(bio, engine="openpyxl")
    except Exception as e:
        raise ValueError(f"无法读取 Excel：{e}") from e

    sheet_names = list(xl.sheet_names or [])
    if not sheet_names:
        raise ValueError("工作簿中没有工作表")
    if len(sheet_names) > max_sheets:
        raise ValueError(f"工作表过多（最多 {max_sheets} 个）")

    results: list[dict[str, Any]] = []
    for sheet in sheet_names:
        warnings: list[str] = []
        try:
            df = pd.read_excel(xl, sheet_name=sheet, header=0, dtype=object)
        except Exception as e:
            warnings.append(f"工作表「{sheet}」读取失败：{e}")
            continue

        if df is None or df.empty:
            warnings.append(f"工作表「{sheet}」无数据行，已跳过")
            continue

        # 去全空行
        df = df.dropna(how="all")
        if df.empty:
            warnings.append(f"工作表「{sheet}」无有效数据，已跳过")
            continue

        if len(df) > max_rows:
            warnings.append(f"超过 {max_rows} 行，已截断")
            df = df.iloc[:max_rows].copy()

        # 第一期：拒绝明显多级/空表头（pandas 会生成大量 Unnamed）
        raw_cells = list(df.columns)
        bad = 0
        for c in raw_cells:
            if c is None or (isinstance(c, float) and math.isnan(c)):
                bad += 1
            else:
                s = str(c).strip()
                if not s or _UNNAMED_RE.match(s) or s.lower() == "nan":
                    bad += 1
        if raw_cells and bad / max(1, len(raw_cells)) > 0.4:
            warnings.append(
                f"工作表「{sheet}」表头不规范（疑似多行表头或空表头），已跳过。"
                "第一期仅支持一行表头的明细表。"
            )
            # 若所有表都被跳过，外层会统一报错
            continue

        col_names: list[str] = []
        seen: dict[str, int] = {}
        for i, c in enumerate(df.columns):
            name = _normalize_col_name(c, i)
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 1
            col_names.append(name)
        df.columns = col_names

        sample_by_col: dict[str, list[Any]] = {c: [] for c in col_names}
        row_payloads: list[dict[str, Any]] = []
        for idx, (_, series) in enumerate(df.iterrows()):
            data: dict[str, Any] = {}
            empty = True
            for c in col_names:
                v = _cell_to_jsonable(series.get(c))
                data[c] = v
                if v is not None and str(v).strip() != "":
                    empty = False
                if len(sample_by_col[c]) < 80:
                    sample_by_col[c].append(v)
            if empty:
                continue
            row_payloads.append({"row_idx": idx, "data": data})

        if not row_payloads:
            warnings.append(f"工作表「{sheet}」无有效数据行，已跳过")
            continue

        columns = [
            {
                "name": c,
                "type": _infer_col_type(sample_by_col[c]),
                "index": i,
            }
            for i, c in enumerate(col_names)
        ]
        results.append(
            {
                "sheet_name": str(sheet)[:255],
                "columns": columns,
                "rows": row_payloads,
                "warnings": warnings,
            }
        )

    if not results:
        raise ValueError("未能解析出任何有效数据表（请确认首行为表头且有数据行）")
    return results


async def index_sales_file(
    *,
    workspace_id: int,
    file_id: int,
    content: bytes,
) -> dict[str, Any]:
    """解析并写入 sales_tables / sales_table_rows。"""
    async with store.sales_advisory_lock(workspace_id, blocking=True) as locked:
        if not locked:
            raise RuntimeError("无法获取工作区锁")

        ws = await store.get_workspace(workspace_id)
        if not ws or (ws.get("status") or "") != "active":
            raise RuntimeError("工作区不可用")

        await store.delete_tables_for_file(file_id)

        sheets = await asyncio.to_thread(parse_excel_bytes, content)
        total_rows = 0
        table_ids: list[int] = []
        all_warnings: list[str] = []

        for sheet in sheets:
            tid = await store.insert_table(
                workspace_id=workspace_id,
                file_id=file_id,
                sheet_name=sheet["sheet_name"],
                columns=sheet["columns"],
                warnings=sheet.get("warnings") or [],
            )
            n = await store.insert_table_rows(tid, sheet["rows"])
            total_rows += n
            table_ids.append(tid)
            all_warnings.extend(sheet.get("warnings") or [])

        return {
            "sheet_count": len(table_ids),
            "row_count": total_rows,
            "table_ids": table_ids,
            "warnings": all_warnings,
        }


async def run_sales_parse_task(
    workspace_id: int,
    file_id: int,
    content: bytes,
) -> None:
    await store.update_workspace_file_parse(
        file_id, parse_status=store.PARSE_PARSING
    )
    try:
        result = await index_sales_file(
            workspace_id=workspace_id,
            file_id=file_id,
            content=content,
        )
        await store.update_workspace_file_parse(
            file_id,
            parse_status=store.PARSE_DONE,
            sheet_count=int(result["sheet_count"]),
            row_count=int(result["row_count"]),
        )
        await store.touch_workspace(workspace_id, renew_ttl=True)
        logger.info(
            "sales parse ok ws=%s file=%s sheets=%s rows=%s",
            workspace_id,
            file_id,
            result["sheet_count"],
            result["row_count"],
        )
    except Exception as e:
        logger.exception("sales parse failed ws=%s file=%s", workspace_id, file_id)
        try:
            await store.delete_tables_for_file(file_id)
        except Exception:
            logger.exception("sales parse rollback tables failed file=%s", file_id)
        err = str(e).strip() or "解析失败"
        await store.update_workspace_file_parse(
            file_id,
            parse_status=store.PARSE_FAILED,
            parse_error=err[:2000],
        )

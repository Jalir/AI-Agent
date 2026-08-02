"""工具执行节点：门禁 + 统一分发（业务逻辑在各 tools/*/execute）。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Mapping

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from backend.common.errors import redact_secrets, tool_user_error
from backend.common.stream import emit_status, is_cancel_requested, thread_id_from_config
from backend.common.tool_outcome import ensure_action_hint
from backend.db.rbac_store import get_permissions_for_role
from backend.graph.guard import (
    evaluate_call,
    is_ephemeral_tool_failure,
    looks_like_failure,
    record_failure,
    record_gate_deny,
    record_success,
)
from backend.tools import dispatch_tool, tool_missing_permissions
from backend.tools.context import ToolExecContext

logger = logging.getLogger(__name__)


def _as_counts(raw: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def _as_fps(raw: Any) -> set[str]:
    if not raw:
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {str(x) for x in raw if str(x).strip()}
    return set()


def _cfg_user(config: RunnableConfig | None) -> tuple[int | None, str]:
    if not config:
        return None, "user"
    conf = config.get("configurable") or {}
    uid_raw = conf.get("user_id")
    try:
        uid = int(uid_raw) if uid_raw is not None else None
    except (TypeError, ValueError):
        uid = None
    role = str(conf.get("user_role") or "user").strip().lower() or "user"
    return uid, role


_WORKSPACE_ALLOWED_TOOLS = frozenset(
    {"search_knowledge_base", "export_docx", "export_excel"}
)
_SALES_ALLOWED_TOOLS = frozenset(
    {
        "list_sales_tables",
        "query_sales_data",
        "make_sales_chart",
        "export_sales_report",
        "export_excel",
        "sum_numbers",
        "average_numbers",
    }
)


async def tools_node(state: Mapping[str, Any], config: RunnableConfig) -> dict:
    """执行工具调用；门禁拦截 / 失败熔断写入 state。"""
    tid = thread_id_from_config(config)
    last_msg = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", None) or []
    conf = (config or {}).get("configurable") or {}
    workspace_mode = conf.get("workspace_id") is not None
    sales_mode = conf.get("sales_workspace_id") is not None

    counts = _as_counts(state.get("tool_call_counts"))
    fail_fps = _as_fps(state.get("tool_fail_fps"))
    tools_blocked = bool(state.get("tools_blocked"))
    consecutive = int(state.get("consecutive_tool_failures") or 0)
    sales_last = state.get("sales_last_query")
    if not isinstance(sales_last, dict):
        sales_last = None

    results: list[ToolMessage] = []
    for call in tool_calls:
        if is_cancel_requested(tid):
            logger.info("Tools node cancelled before call thread=%s", tid)
            raise asyncio.CancelledError()
        name = str(call.get("name") or "")
        args = call.get("args") or {}
        if not isinstance(args, dict):
            args = {"_raw": args}
        call_id = call.get("id", "")

        _, role = _cfg_user(config)
        try:
            perms = await get_permissions_for_role(role)
        except Exception:
            logger.exception("load permissions for tool gate failed")
            perms = frozenset()
        missing = tool_missing_permissions(name, perms)
        if missing:
            deny_perm = ensure_action_hint(
                f"无权限：缺少 {', '.join(sorted(missing))}。"
                "请用自然语言说明局限，并建议用户联系管理员开通权限。"
            )
            logger.info(
                "Tool permission blocked: name=%s missing=%s", name, sorted(missing)
            )
            results.append(ToolMessage(content=deny_perm, tool_call_id=call_id))
            counts, fail_list, consecutive, tools_blocked = record_gate_deny(
                name=name or "unknown",
                args=args,
                counts=counts,
                fail_fps=fail_fps,
                consecutive_failures=consecutive,
                tools_blocked=tools_blocked,
                reason=deny_perm,
            )
            fail_fps = set(fail_list)
            continue

        if sales_mode and name not in _SALES_ALLOWED_TOOLS:
            deny_sa = ensure_action_hint(
                "当前为销售分析区，仅支持查询已上传 Excel、出图与导出报告/表格。"
            )
            logger.info("Tool blocked in sales mode: name=%s", name)
            results.append(ToolMessage(content=deny_sa, tool_call_id=call_id))
            counts, fail_list, consecutive, tools_blocked = record_gate_deny(
                name=name or "unknown",
                args=args,
                counts=counts,
                fail_fps=fail_fps,
                consecutive_failures=consecutive,
                tools_blocked=tools_blocked,
                reason=deny_sa,
            )
            fail_fps = set(fail_list)
            continue

        if workspace_mode and name not in _WORKSPACE_ALLOWED_TOOLS:
            deny_ws = ensure_action_hint(
                "当前为文档工作区，仅支持检索本次上传材料与导出文档/表格。"
            )
            logger.info("Tool blocked in workspace mode: name=%s", name)
            results.append(ToolMessage(content=deny_ws, tool_call_id=call_id))
            counts, fail_list, consecutive, tools_blocked = record_gate_deny(
                name=name or "unknown",
                args=args,
                counts=counts,
                fail_fps=fail_fps,
                consecutive_failures=consecutive,
                tools_blocked=tools_blocked,
                reason=deny_ws,
            )
            fail_fps = set(fail_list)
            continue

        deny = evaluate_call(
            name=name,
            args=args,
            counts=counts,
            fail_fps=fail_fps,
            tools_blocked=tools_blocked,
            consecutive_failures=consecutive,
        )
        if deny is not None:
            deny = ensure_action_hint(deny)
            logger.info("Tool guard blocked: name=%s msg=%s", name, deny[:100])
            results.append(ToolMessage(content=deny, tool_call_id=call_id))
            counts, fail_list, consecutive, tools_blocked = record_gate_deny(
                name=name or "unknown",
                args=args,
                counts=counts,
                fail_fps=fail_fps,
                consecutive_failures=consecutive,
                tools_blocked=tools_blocked,
                reason=deny,
            )
            fail_fps = set(fail_list)
            continue

        await emit_status(tid, "正在处理…")
        uid, role = _cfg_user(config)
        try:
            result_s = str(
                await dispatch_tool(
                    ToolExecContext(
                        name=name,
                        args=args,
                        thread_id=tid,
                        config=config,
                        user_id=uid,
                        role=role,
                        sales_last_query=sales_last,
                    )
                )
            )
            result_s = redact_secrets(result_s)
            if looks_like_failure(result_s):
                result_s = ensure_action_hint(result_s)
        except Exception as e:
            logger.exception("tool execution crashed: %s", name)
            result_s = ensure_action_hint(tool_user_error("操作", e))

        results.append(ToolMessage(content=result_s, tool_call_id=call_id))

        if looks_like_failure(result_s):
            ephemeral = is_ephemeral_tool_failure(result_s)
            counts, fail_list, consecutive, tools_blocked = record_failure(
                name=name or "unknown",
                args=args,
                counts=counts,
                fail_fps=fail_fps,
                consecutive_failures=consecutive,
                fingerprint=not ephemeral,
            )
            fail_fps = set(fail_list)
        else:
            counts, consecutive = record_success(name=name or "unknown", counts=counts)
            if name == "query_sales_data":
                try:
                    parsed = json.loads(result_s)
                except (TypeError, json.JSONDecodeError):
                    parsed = None
                if isinstance(parsed, dict) and isinstance(parsed.get("rows"), list):
                    sales_last = parsed

    return {
        "messages": results,
        "tool_call_counts": counts,
        "tool_fail_fps": sorted(fail_fps),
        "tools_blocked": tools_blocked,
        "consecutive_tool_failures": consecutive,
        "sales_last_query": sales_last,
    }

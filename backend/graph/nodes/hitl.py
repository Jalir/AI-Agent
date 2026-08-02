"""Human-in-the-loop 审批节点与条件边。

审批（导出/发邮件等）：气泡内确认；可带草稿编辑；取消 → cancel 节点结束。
澄清（证据不足/条件不清）：走 clarify 节点，用户补充后作为新一轮对话再进 LLM。

审批文案由各 tools/{public,gated}/<name>/ 导出 APPROVAL_LABEL / approval_question / approval_payload，
hitl 不再为每个工具写死分支。
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.graph import END
from langgraph.types import interrupt

from backend.common.permissions import EMAIL_SEND
from backend.common.stream import get_token_queue, thread_id_from_config
from backend.db.rbac_store import role_has_permission
from backend.tools import (
    APPROVAL_LABELS,
    APPROVAL_PAYLOAD_BUILDERS,
    APPROVAL_QUESTION_BUILDERS,
    SAFE_TOOL_NAMES,
)
from backend.tools.gated.email.schema import validate_send_email_args


def _user_question(tool_name: str, tool_args: Any) -> str:
    """优先用技能包自定义 builder；否则用标签默认句。"""
    builder = APPROVAL_QUESTION_BUILDERS.get(tool_name)
    if builder is not None:
        try:
            text = str(builder(tool_args) or "").strip()
            if text:
                return text
        except Exception:
            # 自定义问句失败时回落默认，避免卡死审批
            pass
    label = APPROVAL_LABELS.get(tool_name) or "执行该操作"
    return f"需要您确认：是否{label}？"


def _extra_payload(tool_name: str, tool_args: Any) -> dict[str, Any]:
    builder = APPROVAL_PAYLOAD_BUILDERS.get(tool_name)
    if builder is None:
        return {}
    try:
        extra = builder(tool_args)
        return dict(extra) if isinstance(extra, dict) else {}
    except Exception:
        return {}


def _merge_edited_args(original: Any, edited: Any) -> dict[str, Any]:
    base = dict(original) if isinstance(original, dict) else {}
    if not isinstance(edited, dict):
        return base
    for key, value in edited.items():
        if value is None:
            continue
        if isinstance(value, str):
            base[key] = value
        else:
            base[key] = value
    return base


def _with_updated_tool_args(last_msg: Any, new_args: dict[str, Any]) -> AIMessage | None:
    """按 message id 替换首个 tool_call 的 args（add_messages 同 id 覆盖）。"""
    tool_calls = list(getattr(last_msg, "tool_calls", None) or [])
    if not tool_calls:
        return None
    first = dict(tool_calls[0])
    first["args"] = new_args
    tool_calls[0] = first
    msg_id = getattr(last_msg, "id", None)
    return AIMessage(
        content=getattr(last_msg, "content", "") or "",
        tool_calls=tool_calls,
        id=msg_id,
    )


async def approval_node(
    state: Mapping[str, Any],
    *,
    config: RunnableConfig,
) -> dict:
    """敏感操作暂停，等待用户在气泡中确认、编辑或取消。"""
    last_msg = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", None) or []

    if not tool_calls:
        return {"approval_decision": ""}

    call = tool_calls[0]
    tool_name = call.get("name", "unknown")
    tool_args = call.get("args", {})
    if not isinstance(tool_args, dict):
        tool_args = {}

    # 无发信权限或参数不合规：跳过 HITL，进 tools 返回结构化错误（避免无效审批卡）
    if tool_name == "send_email":
        conf = (config or {}).get("configurable") or {}
        role = str(conf.get("user_role") or "user").strip().lower() or "user"
        if not await role_has_permission(role, EMAIL_SEND):
            return {"approval_decision": "approved"}
        _, err = validate_send_email_args(tool_args)
        if err:
            return {"approval_decision": "approved"}

    payload: dict[str, Any] = {
        "question": _user_question(tool_name, tool_args),
        "action": tool_name,
        "kind": "approval",
        "args": tool_args,
    }
    payload.update(_extra_payload(tool_name, tool_args))

    token = var_child_runnable_config.set(config)
    try:
        user_response = interrupt(payload)
    finally:
        var_child_runnable_config.reset(token)

    if not isinstance(user_response, dict):
        user_response = {"approved": bool(user_response), "reason": ""}

    if not bool(user_response.get("approved", False)):
        return {"approval_decision": "rejected"}

    edited = user_response.get("edited_args")
    if isinstance(edited, dict) and edited:
        merged = _merge_edited_args(tool_args, edited)
        # 编辑后仍不合规：带着新参数进 tools，由执行层返回错误
        updated = _with_updated_tool_args(last_msg, merged)
        if updated is not None:
            return {
                "approval_decision": "approved",
                "messages": [updated],
            }

    return {"approval_decision": "approved"}


async def cancel_node(state: Mapping[str, Any], config: RunnableConfig) -> dict:
    """用户取消敏感操作：固定文案结束，不调用 LLM。"""
    text = "任务已取消"
    tid = thread_id_from_config(config)
    queue = get_token_queue(tid)
    if queue is not None:
        await queue.put(text)
    return {
        "messages": [AIMessage(content=text)],
        "approval_decision": "",
    }


def should_use_tools(state: Mapping[str, Any]) -> Literal["approval", "tools", END]:
    last_msg = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", None) or []
    if not tool_calls:
        return END
    # 熔断后仍可能偶发 tool_calls：进 tools 由门禁直接拒绝并回 ToolMessage，避免裸 tool_calls 挂起
    if bool(state.get("tools_blocked")):
        return "tools"
    if all((c.get("name") or "") in SAFE_TOOL_NAMES for c in tool_calls):
        return "tools"
    return "approval"


def after_approval(state: Mapping[str, Any]) -> Literal["tools", "cancelled"]:
    if (state.get("approval_decision") or "") == "rejected":
        return "cancelled"
    return "tools"

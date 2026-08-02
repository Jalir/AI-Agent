"""工具结果分流：给 agent 的结构化内部提示（勿对用户复述原文）。

action:
  retry    — 可改参再调（同参禁止；最多再试 1 次）
  ask_user — 缺用户信息，禁止再调工具，自然语言补问
  degrade  — 部分成功，勿整包重跑，简要告知用户
  fatal    — 无法继续，自然语言致歉收场
"""

from __future__ import annotations

from enum import Enum

# 内部提示前缀（全项目统一）
INTERNAL_HINT_MARK = "（内部提示）"
INTERNAL_HINT_PREFIX = "（内部提示，勿对用户复述）"

# 写入 agent system prompt 的 action 行为摘要（单一来源）
AGENT_ACTION_RULES = (
    "工具结果若含「（内部提示）action=…」：严格按 action 行动，且勿向用户复述该提示——"
    "retry=改参后再调（最多 1 次，禁止同错参）；ask_user=停工具、自然语言补问；"
    "degrade=勿整包重跑，简要告知结果；fatal/熔断/达上限=停工具、自然语言收场。"
)

# 成功结果勿当失败熔断的文案头
_SUCCESS_PREFIXES = ("已按顺序生成", "已生成", "表格「", "文档「", "已完成图像编辑")


class ToolAction(str, Enum):
    RETRY = "retry"
    ASK_USER = "ask_user"
    DEGRADE = "degrade"
    FATAL = "fatal"


_ACTION_HINTS: dict[ToolAction, str] = {
    ToolAction.RETRY: (
        "请根据错误改参数后最多再调用 1 次；"
        "禁止用完全相同的错误参数重试；勿向用户复述本提示。"
    ),
    ToolAction.ASK_USER: (
        "禁止再调用工具；用自然语言向用户补问缺失信息，勿提工具或参数名。"
    ),
    ToolAction.DEGRADE: (
        "勿整包重跑或逐条补调；用一两句自然语言告知用户结果与局限即可。"
    ),
    ToolAction.FATAL: (
        "禁止再调用工具；用自然语言致歉并给出可执行的下一步建议。"
    ),
}


def classify_arg_error(detail: str) -> ToolAction:
    """根据校验文案判断：缺信息 → 问用户；其余可改参重试。"""
    text = (detail or "").strip()
    if not text:
        return ToolAction.RETRY
    ask_markers = (
        "不能为空",
        "为空",
        "请提供",
        "请先",
        "请指定",
        "请确认",
        "请向用户确认",
        "素材列表不能为空",
        "画面描述不能为空",
        "素材不能为空",
        "未上传",
        "缺少",
        "没有可绘制",
        "无匹配行",
        "无法自动猜测",
    )
    if any(m in text for m in ask_markers):
        return ToolAction.ASK_USER
    return ToolAction.RETRY


def format_tool_outcome(
    *,
    headline: str,
    action: ToolAction,
    detail: str = "",
    extra_hint: str = "",
) -> str:
    """拼 ToolMessage 正文：headline 供门禁识别，后附内部 action。"""
    head = (headline or "").strip() or "操作失败"
    body = (detail or "").strip()
    line = f"{head}：{body}" if body and body not in head else head
    hint = _ACTION_HINTS.get(action, _ACTION_HINTS[ToolAction.FATAL])
    if extra_hint:
        hint = f"{hint}{extra_hint}"
    return f"{line}\n{INTERNAL_HINT_MARK}action={action.value}：{hint}"


def format_arg_failure(label: str, detail: str) -> str:
    """参数校验失败 → 带分流的 ToolMessage。"""
    msg = (detail or "").strip() or f"{label}参数不合规"
    # 统一带「参数不合规」前缀，便于 looks_like_failure
    if "参数不合规" not in msg and "参数无效" not in msg:
        headline = f"{label}参数不合规"
        detail_s = msg
    else:
        headline = msg.split("：", 1)[0] if "：" in msg else msg
        detail_s = msg.split("：", 1)[1] if "：" in msg else ""
    action = classify_arg_error(msg)
    return format_tool_outcome(headline=headline, action=action, detail=detail_s)


def format_tool_user_message(headline: str, *, ask: str) -> str:
    """工具成功结果：headline + 对用户话术指引 + 禁贴 URL / 禁提实现。"""
    return (
        f"{headline}"
        f"{ask}"
        "不要粘贴 URL、Markdown 链接或文件名超链接，也不要提工具或系统实现。"
    )


def looks_like_success_result(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and any(t.startswith(p) for p in _SUCCESS_PREFIXES)


def looks_like_tool_outcome_failure(text: str) -> bool:
    """是否为带分流的失败/需收场结果（degrade 成功头不算）。"""
    t = (text or "").strip()
    if not t:
        return False
    if looks_like_success_result(t):
        return False
    mark = f"{INTERNAL_HINT_MARK}action="
    if mark in t[:240]:
        if "action=degrade" in t[:240]:
            return False
        return True
    return False


def ensure_action_hint(text: str, *, default_action: ToolAction | None = None) -> str:
    """若尚未带 action 提示，为失败类结果补上分流指令。"""
    t = (text or "").strip()
    mark = f"{INTERNAL_HINT_MARK}action="
    if not t or mark in t:
        return t
    if t.startswith("已") and "失败" not in t[:20]:
        return t
    action = default_action
    if action is None:
        if any(x in t for x in ("未配置", "无权限", "已熔断", "已达")):
            action = ToolAction.FATAL
        elif classify_arg_error(t) == ToolAction.ASK_USER:
            action = ToolAction.ASK_USER
        elif "超时" in t or "繁忙" in t or "频繁" in t:
            action = ToolAction.RETRY
        else:
            action = ToolAction.RETRY
    hint = _ACTION_HINTS[action]
    return f"{t}\n{INTERNAL_HINT_MARK}action={action.value}：{hint}"

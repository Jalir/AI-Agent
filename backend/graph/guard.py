"""Agent 工具调用安全门禁：次数上限、失败指纹去重、连续失败熔断。

与具体业务 tool 解耦；在 tools_node 入口统一拦截。
上限来自各技能包 MAX_CALLS_PER_TURN → tools.TOOL_MAX_CALLS；未声明则用全局默认。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Mapping

from backend.common.tool_outcome import (
    INTERNAL_HINT_PREFIX,
    ToolAction,
    format_tool_outcome,
    looks_like_success_result,
    looks_like_tool_outcome_failure,
)
from backend.config import settings

logger = logging.getLogger(__name__)

_FAIL_PREFIXES = (
    "失败",
    "文案生成失败",
    "生图失败",
    "图像编辑失败",
    "录音识别失败",
    "导出失败",
    "参数不合规",
    "生图参数不合规",
    "文案参数不合规",
    "图文包参数不合规",
    "邮件参数不合规",
    "生图参数无效",
    "文案参数无效",
    "图文包参数无效",
    "无权限：",
    "未知工具",
    "已达调用上限",
    "相同请求此前已失败",
    "工具调用已熔断",
    "素材列表为空",
)

BLOCKED_HINT = (
    f"{INTERNAL_HINT_PREFIX}工具调用已熔断或达上限。"
    "禁止再调用任何工具；请仅用自然语言向用户说明进展或局限，并给出可执行的下一步建议。"
)

RECURSION_USER_MSG = (
    "这轮处理步骤过多，已自动中止，避免反复重试。"
    "请简化需求后重试，或换一种说法说明你想要的结果。"
)


def tool_fingerprint(name: str, args: Any) -> str:
    try:
        payload = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        payload = str(args)
    raw = f"{name}|{payload}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def max_for_tool(name: str) -> int:
    try:
        from backend.tools import TOOL_MAX_CALLS

        if name in TOOL_MAX_CALLS:
            return int(TOOL_MAX_CALLS[name])
    except Exception:
        pass
    return max(1, int(settings.tool_max_calls_per_name or 3))


def total_calls(counts: Mapping[str, Any] | None) -> int:
    if not counts:
        return 0
    n = 0
    for v in counts.values():
        try:
            n += int(v)
        except (TypeError, ValueError):
            continue
    return n


def looks_like_failure(result: str) -> bool:
    text = (result or "").strip()
    if not text:
        return False
    # 部分成功 / degrade 不算失败，避免误熔断
    if looks_like_success_result(text):
        return False
    head = text[:80]
    if any(head.startswith(p) for p in _FAIL_PREFIXES):
        return True
    return looks_like_tool_outcome_failure(text)


def fresh_guard_state() -> dict[str, Any]:
    """每轮用户消息进入 intent_router 时重置工具闸。

    不清理 sales_last_query：改图类型（如「改成饼图」）需复用上一轮查询缓存。
    清空会话 / reset_thread 时随 checkpoint 一起消失。
    """
    return {
        "tool_call_counts": {},
        "tool_fail_fps": [],
        "tools_blocked": False,
        "consecutive_tool_failures": 0,
    }


def is_ephemeral_tool_failure(result: str) -> bool:
    """环境未就绪类失败：计次但不记失败指纹，避免 query 之后同参出图被误杀。"""
    text = (result or "").strip()
    if not text:
        return False
    markers = (
        "尚无本轮查询结果",
        "尚无可用的查询结果",
        "尚无本轮查询结果可出图",
    )
    return any(m in text for m in markers)


def evaluate_call(
    *,
    name: str,
    args: Any,
    counts: dict[str, int],
    fail_fps: set[str],
    tools_blocked: bool,
    consecutive_failures: int,
) -> str | None:
    """若应拦截则返回给 ToolMessage 的说明；允许执行则返回 None。"""
    if tools_blocked:
        return (
            "工具调用已熔断（连续失败或达上限）。请勿再调用任何工具，"
            "直接用自然语言回复用户。"
        )

    max_total = max(1, int(settings.tool_max_total_calls or 12))
    if total_calls(counts) >= max_total:
        return (
            f"已达本轮工具调用总上限（{max_total}）。"
            "请勿再调用工具，直接根据已有结果回复用户。"
        )

    per_max = max_for_tool(name)
    used = int(counts.get(name) or 0)
    if used >= per_max:
        return (
            f"工具「{name}」本轮已达调用上限（{per_max}）。"
            "请勿再次调用该工具，直接回复用户。"
        )

    fp = tool_fingerprint(name, args)
    if fp in fail_fps:
        return format_tool_outcome(
            headline="相同请求此前已失败，禁止重复执行",
            action=ToolAction.ASK_USER,
            extra_hint="请勿再用相同参数调用；若缺信息则自然语言补问，否则说明局限并结束本轮工具调用。",
        )

    max_fail = max(1, int(settings.tool_max_consecutive_failures or 2))
    if consecutive_failures >= max_fail:
        return format_tool_outcome(
            headline=f"连续失败已达 {max_fail} 次，工具调用已熔断",
            action=ToolAction.FATAL,
            extra_hint="请勿再调用工具，直接用自然语言回复用户。",
        )

    return None


def bump_count(name: str, counts: dict[str, int]) -> dict[str, int]:
    next_counts = dict(counts)
    key = name or "unknown"
    next_counts[key] = int(next_counts.get(key) or 0) + 1
    return next_counts


def record_success(
    *,
    name: str,
    counts: dict[str, int],
) -> tuple[dict[str, int], int]:
    """成功：计数 +1，连续失败清零。"""
    return bump_count(name, counts), 0


def record_failure(
    *,
    name: str,
    args: Any,
    counts: dict[str, int],
    fail_fps: set[str],
    consecutive_failures: int,
    fingerprint: bool = True,
) -> tuple[dict[str, int], list[str], int, bool]:
    """真实执行失败：计数 +1；默认识指纹；连续失败 +1，必要时熔断。"""
    next_counts = bump_count(name, counts)
    fps = set(fail_fps)
    if fingerprint:
        fps.add(tool_fingerprint(name or "unknown", args))
    consec = int(consecutive_failures) + (1 if fingerprint else 0)
    max_fail = max(1, int(settings.tool_max_consecutive_failures or 2))
    max_total = max(1, int(settings.tool_max_total_calls or 12))
    blocked = consec >= max_fail or total_calls(next_counts) >= max_total
    if blocked:
        logger.warning(
            "Tool guard tripped: name=%s consec=%d total=%d",
            name,
            consec,
            total_calls(next_counts),
        )
    return next_counts, sorted(fps), consec, blocked


def record_gate_deny(
    *,
    name: str,
    args: Any,
    counts: dict[str, int],
    fail_fps: set[str],
    consecutive_failures: int,
    tools_blocked: bool,
    reason: str,
) -> tuple[dict[str, int], list[str], int, bool]:
    """门禁拦截：计次；同参失败去重记指纹；达总上限则熔断。不抬高「连续执行失败」。"""
    next_counts = bump_count(name, counts)
    fps = set(fail_fps)
    # 已失败指纹 / 已熔断：继续记指纹，防止同参空转
    if "此前已失败" in reason or "已熔断" in reason:
        fps.add(tool_fingerprint(name or "unknown", args))
    max_total = max(1, int(settings.tool_max_total_calls or 12))
    blocked = bool(tools_blocked) or total_calls(next_counts) >= max_total
    if blocked and not tools_blocked:
        logger.warning(
            "Tool guard blocked by limit: name=%s total=%d",
            name,
            total_calls(next_counts),
        )
    return next_counts, sorted(fps), int(consecutive_failures), blocked

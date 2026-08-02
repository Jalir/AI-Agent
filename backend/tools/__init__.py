"""Agent 可 bind 的技能包。

目录约定：
  tools/public/<name>/  — 普通技能（登录即可，一般无需权限码）
  tools/gated/<name>/   — 受控技能（须声明 REQUIRED_PERMISSIONS）

新增技能：
  1. 按是否要权限选 public 或 gated
  2. 提供 __init__.py，导出：
       TOOL / TOOL_NAME / REQUIRES_APPROVAL
       REQUIRED_PERMISSIONS / APPROVAL_*（可选）
       execute(ctx)          — 可选，自定义执行（副作用/校验）；缺省走 tool.ainvoke
       PRODUCT_HINT          — 可选，ProductHint 产品模式声明
       MAX_CALLS_PER_TURN    — 本轮该工具调用上限（供 guard；未声明则用全局默认）
  3. 重启服务后自动进入注册表
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Awaitable, Callable, Collection, Iterable
from pathlib import Path
from typing import Any

from backend.tools.context import ToolExecContext
from backend.tools.hints import ProductHint

logger = logging.getLogger(__name__)

TOOLS: list = []
TOOL_BY_NAME: dict[str, object] = {}
SAFE_TOOL_NAMES: frozenset[str] = frozenset()
TOOL_REQUIRED_PERMISSIONS: dict[str, frozenset[str]] = {}
TOOL_GROUPS: dict[str, str] = {}
APPROVAL_LABELS: dict[str, str] = {}
APPROVAL_QUESTION_BUILDERS: dict[str, Callable[[Any], str]] = {}
APPROVAL_PAYLOAD_BUILDERS: dict[str, Callable[[Any], dict]] = {}
# name → async (ToolExecContext) -> str
TOOL_EXECUTE: dict[str, Callable[[ToolExecContext], Awaitable[str]]] = {}
# product hint id → ProductHint
PRODUCT_HINTS: dict[str, ProductHint] = {}
# name → 本轮调用上限（供 guard；由各技能包 MAX_CALLS_PER_TURN 填充）
TOOL_MAX_CALLS: dict[str, int] = {}

_package_dir = Path(__file__).parent
_TOOL_GROUPS = ("public", "gated")
_safe: set[str] = set()

ToolHandler = Callable[[ToolExecContext], Awaitable[str]]


def _normalize_permissions(raw: Any) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        code = raw.strip()
        return frozenset({code}) if code else frozenset()
    if isinstance(raw, Iterable):
        out: set[str] = set()
        for item in raw:
            code = str(item or "").strip()
            if code:
                out.add(code)
        return frozenset(out)
    return frozenset()


def _load_group(group: str) -> None:
    group_dir = _package_dir / group
    if not group_dir.is_dir():
        return
    for child in sorted(group_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("_") or child.name.startswith("."):
            continue
        if not (child / "__init__.py").is_file():
            continue
        mod_name = child.name
        try:
            module = importlib.import_module(f".{group}.{mod_name}", package=__name__)
        except Exception:
            logger.exception("Failed to load tool package: %s.%s", group, mod_name)
            continue

        tool = getattr(module, "TOOL", None)
        if tool is None or not hasattr(tool, "name"):
            logger.warning(
                "Tool package %s.%s missing TOOL export, skipped", group, mod_name
            )
            continue

        name = str(getattr(module, "TOOL_NAME", None) or tool.name)
        if tool not in TOOLS:
            TOOLS.append(tool)
        TOOL_BY_NAME[name] = tool
        TOOL_GROUPS[name] = group

        requires_approval = bool(getattr(module, "REQUIRES_APPROVAL", True))
        if not requires_approval:
            _safe.add(name)

        required = _normalize_permissions(
            getattr(module, "REQUIRED_PERMISSIONS", ())
        )
        TOOL_REQUIRED_PERMISSIONS[name] = required
        if group == "gated" and not required:
            logger.warning(
                "Gated tool %s.%s has empty REQUIRED_PERMISSIONS "
                "(will bind for all logged-in users)",
                group,
                mod_name,
            )

        label = getattr(module, "APPROVAL_LABEL", None)
        if isinstance(label, str) and label.strip():
            APPROVAL_LABELS[name] = label.strip()

        builder = getattr(module, "approval_question", None)
        if callable(builder):
            APPROVAL_QUESTION_BUILDERS[name] = builder  # type: ignore[assignment]

        payload_builder = getattr(module, "approval_payload", None)
        if callable(payload_builder):
            APPROVAL_PAYLOAD_BUILDERS[name] = payload_builder  # type: ignore[assignment]

        # 优先 __init__.execute，其次同包 execute.execute
        handler = getattr(module, "execute", None)
        if not callable(handler):
            try:
                exec_mod = importlib.import_module(
                    f".{group}.{mod_name}.execute", package=__name__
                )
                handler = getattr(exec_mod, "execute", None)
            except ModuleNotFoundError:
                handler = None
            except Exception:
                logger.exception(
                    "Failed to load execute for %s.%s", group, mod_name
                )
                handler = None
        if callable(handler):
            TOOL_EXECUTE[name] = handler  # type: ignore[assignment]

        max_calls = getattr(module, "MAX_CALLS_PER_TURN", None)
        if max_calls is not None:
            try:
                TOOL_MAX_CALLS[name] = max(1, int(max_calls))
            except (TypeError, ValueError):
                pass

        # PRODUCT_HINT：__init__ 或 hint.PRODUCT_HINT
        hint = getattr(module, "PRODUCT_HINT", None)
        if hint is None:
            try:
                hint_mod = importlib.import_module(
                    f".{group}.{mod_name}.hint", package=__name__
                )
                hint = getattr(hint_mod, "PRODUCT_HINT", None)
            except ModuleNotFoundError:
                hint = None
            except Exception:
                logger.exception("Failed to load hint for %s.%s", group, mod_name)
                hint = None
        if isinstance(hint, ProductHint):
            if hint.id in PRODUCT_HINTS:
                logger.warning(
                    "Duplicate PRODUCT_HINT id=%s from %s.%s (overwriting)",
                    hint.id,
                    group,
                    mod_name,
                )
            PRODUCT_HINTS[hint.id] = hint


for _group in _TOOL_GROUPS:
    _load_group(_group)

SAFE_TOOL_NAMES = frozenset(_safe)


def tool_missing_permissions(
    tool_name: str,
    user_permissions: Collection[str] | None,
) -> frozenset[str]:
    """返回调用该 tool 仍缺少的权限码；空集表示允许。"""
    required = TOOL_REQUIRED_PERMISSIONS.get(tool_name) or frozenset()
    if not required:
        return frozenset()
    have = frozenset(user_permissions or ())
    return required - have


def filter_tools_for_permissions(
    user_permissions: Collection[str] | None,
    *,
    tools: list | None = None,
) -> list:
    """按权限裁剪可 bind 的工具列表（服务端过滤，不交给 LLM 判断）。"""
    source = tools if tools is not None else TOOLS
    have = frozenset(user_permissions or ())
    allowed: list = []
    for tool in source:
        name = str(getattr(tool, "name", "") or "")
        missing = tool_missing_permissions(name, have)
        if missing:
            continue
        allowed.append(tool)
    return allowed


async def dispatch_tool(ctx: ToolExecContext) -> str:
    """统一执行：有自定义 execute 则用之，否则 tool.ainvoke。"""
    from backend.common.errors import tool_user_error
    from backend.common.tool_outcome import ensure_action_hint

    handler = TOOL_EXECUTE.get(ctx.name)
    if handler is not None:
        return str(await handler(ctx))

    tool_fn = TOOL_BY_NAME.get(ctx.name)
    if tool_fn is None:
        return ensure_action_hint("未知操作，请稍后重试。")
    try:
        return str(await tool_fn.ainvoke(ctx.args))  # type: ignore[union-attr]
    except NotImplementedError:
        return str(tool_fn.invoke(ctx.args))  # type: ignore[union-attr]
    except Exception as e:
        logger.exception("tool ainvoke failed: %s", ctx.name)
        return ensure_action_hint(tool_user_error("操作", e))


logger.info(
    "Loaded %d tool(s): %s (safe=%s, handlers=%s, product_hints=%s, max_calls=%s)",
    len(TOOLS),
    [t.name for t in TOOLS],
    sorted(SAFE_TOOL_NAMES),
    sorted(TOOL_EXECUTE),
    sorted(PRODUCT_HINTS),
    TOOL_MAX_CALLS,
)

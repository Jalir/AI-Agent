"""产品模式 hint：由各技能包声明，agent / intent_router 只消费注册表。"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HintBuildContext:
    """构建 agent 内部提示时的只读上下文。"""

    user_text: str = ""
    client_hint: str = ""
    audio_urls: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    suggested_kb_query: str = ""
    suggested_kb_top_k: int | None = None
    export_confirm: bool = False


@dataclass(frozen=True)
class ProductHint:
    """前端/话语触发的产品模式（不等于图分支 intent）。"""

    id: str
    # 映射到图分支：chat | rag | media_gen
    route_intent: str
    # 匹配优先级，越小越先匹配（互斥）
    priority: int = 100
    # 用户话正则（可选）
    utterance_re: re.Pattern[str] | None = None
    # 命中时是否要求本轮有音频 / 图片（话语匹配用；client 按钮可放宽）
    needs_audio: bool = False
    needs_images: bool = False
    # 导出确认短句时不触发（如批量图文）
    skip_on_export_confirm: bool = False
    # intent_router 附加给分类 LLM 的提示
    router_extra: str = ""
    # 路由后状态文案
    status: str = ""
    # agent 注入的内部提示；返回 None/"" 表示本轮不注入
    build_agent_hint: Callable[[HintBuildContext], str | None] | None = None
    # 匹配本 hint 后的状态文案（agent 侧）
    agent_status: str = ""


def match_product_hint(
    *,
    client_hint: str = "",
    user_text: str = "",
    audio_urls: list[str] | None = None,
    image_urls: list[str] | None = None,
    export_confirm: bool = False,
    hints: dict[str, ProductHint] | None = None,
) -> ProductHint | None:
    """按优先级选出唯一产品 hint（client 按钮优先于话语）。"""
    from backend.tools import PRODUCT_HINTS

    registry = hints if hints is not None else PRODUCT_HINTS
    ordered = sorted(registry.values(), key=lambda h: (h.priority, h.id))
    client = (client_hint or "").strip().lower()
    if client and client in registry:
        return registry[client]

    text = user_text or ""
    audios = audio_urls or []
    images = image_urls or []
    for h in ordered:
        if h.skip_on_export_confirm and export_confirm:
            continue
        if h.utterance_re is None or not h.utterance_re.search(text):
            continue
        if h.needs_audio and not audios:
            continue
        if h.needs_images and not images:
            continue
        return h
    return None


def client_hint_ids(hints: dict[str, ProductHint] | None = None) -> frozenset[str]:
    from backend.tools import PRODUCT_HINTS

    registry = hints if hints is not None else PRODUCT_HINTS
    return frozenset(registry.keys())


def map_client_to_route(
    client: str | None,
    *,
    graph_intents: frozenset[str],
    hints: dict[str, ProductHint] | None = None,
) -> tuple[str | None, str]:
    """前端 hint → (forced 图意图或 None, product_hint id)。"""
    from backend.tools import PRODUCT_HINTS

    registry = hints if hints is not None else PRODUCT_HINTS
    c = (client or "").strip().lower().replace("-", "_")
    if not c:
        return None, ""
    if c in registry:
        h = registry[c]
        return h.route_intent, h.id
    if c in graph_intents:
        return c, ""
    return None, ""

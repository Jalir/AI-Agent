"""LangGraph 编排层：State 定义、节点接线、编译。

节点实现在 backend.graph.nodes；可 bind 的技能在 backend.tools/{public,gated}/<name>/。

主路径：intent_router → agent ⇄ approval? ⇄ tools
知识库检索为 tools.search_kb（内部含纠错流水线）。
安全闸：recursion_limit（全图）+ tools_node 门禁（次数/指纹/熔断）。

图在应用 lifespan 里 compile_graph(checkpointer) 后才可用；
模块导出的 graph 是代理，避免 import 时绑定到 None。
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from typing_extensions import NotRequired
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from backend.graph.nodes import (
    after_approval,
    agent_node,
    approval_node,
    cancel_node,
    clarify_node,
    intent_router_node,
    route_by_intent,
    should_use_tools,
    tools_node,
)


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    intent: NotRequired[str]
    # 前端意图提示（一次性）；xhs_pack 等产品模式留给 agent，不作为图分支
    client_intent: NotRequired[str]
    # 本轮用户音频附件 URL（一次性；录音识别用）
    pending_audio_urls: NotRequired[list[str]]
    # 意图理解无法可靠归一查询时的反问文案（intent=clarify）
    clarify_question: NotRequired[str]
    # 路由给 agent 的建议检索词 / top_k（一次性，不强制）
    suggested_kb_query: NotRequired[str]
    suggested_kb_top_k: NotRequired[int | None]
    # 审批结果：approved | rejected（一次性）
    approval_decision: NotRequired[str]
    # ---- 工具安全闸（每轮用户消息在 intent_router 重置）----
    tool_call_counts: NotRequired[dict[str, int]]
    tool_fail_fps: NotRequired[list[str]]
    tools_blocked: NotRequired[bool]
    consecutive_tool_failures: NotRequired[int]
    # 销售分析：本轮最近一次 query_sales_data 结果（供出图/数组汇总复用）
    sales_last_query: NotRequired[dict[str, Any] | None]


# START → intent_router ┬─ clarify → END
#                       └─ agent ⇄ approval? ┬─ tools → agent
#                                            └─ cancelled → END

_builder = StateGraph(AgentState)
_builder.add_node("intent_router", intent_router_node)
_builder.add_node("agent", agent_node)
_builder.add_node("clarify", clarify_node)
_builder.add_node("approval", approval_node)
_builder.add_node("cancelled", cancel_node)
_builder.add_node("tools", tools_node)

_builder.add_edge(START, "intent_router")
_builder.add_conditional_edges(
    "intent_router",
    route_by_intent,
    {
        "agent": "agent",
        "clarify": "clarify",
    },
)
_builder.add_edge("clarify", END)
_builder.add_conditional_edges(
    "agent",
    should_use_tools,
    {"approval": "approval", "tools": "tools", END: END},
)
_builder.add_conditional_edges(
    "approval",
    after_approval,
    {"tools": "tools", "cancelled": "cancelled"},
)
_builder.add_edge("cancelled", END)
_builder.add_edge("tools", "agent")


class _GraphProxy:
    """转发到 lifespan 里 compile 出的 CompiledStateGraph。"""

    __slots__ = ("_compiled",)

    def __init__(self) -> None:
        self._compiled: Any = None

    def bind(self, compiled: Any) -> None:
        self._compiled = compiled

    @property
    def ready(self) -> bool:
        return self._compiled is not None

    def __getattr__(self, name: str) -> Any:
        if self._compiled is None:
            raise RuntimeError(
                "Graph not initialized; ensure compile_graph() ran in app lifespan"
            )
        return getattr(self._compiled, name)


graph = _GraphProxy()


def compile_graph(checkpointer: Any) -> Any:
    """用给定 checkpointer 编译图并绑定到模块级 graph 代理。"""
    compiled = _builder.compile(checkpointer=checkpointer)
    graph.bind(compiled)
    return compiled


__all__ = ["AgentState", "compile_graph", "graph"]

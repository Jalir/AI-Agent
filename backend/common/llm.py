"""LLM 工厂与会话标题生成。"""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from backend.common.messages import extract_text
from backend.config import settings


def build_llm() -> ChatOpenAI:
    """主对话模型（流式）。"""
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=0.7,
        streaming=True,
        stream_usage=True,
        reasoning_effort="minimal",
    )


def build_llm_fast(*, max_tokens: int = 16) -> ChatOpenAI:
    """轻量模型：意图路由、标题生成等（非流式、低温）。"""
    return ChatOpenAI(
        model=settings.llm_fast_model,
        base_url=settings.llm_fast_base_url,
        api_key=settings.llm_fast_api_key,
        temperature=0,
        streaming=False,
        max_tokens=max_tokens,
        reasoning_effort="minimal",
    )


async def generate_title(user_message: str, *, thread_id: str | None = None) -> str:
    """生成会话标题。不计入本轮回答 token（thread_id 仅兼容旧调用方）。"""
    _ = thread_id
    llm = build_llm_fast(max_tokens=32)
    prompt = (
        "你是个标题生成助手，请用不超过10个字总结以下用户消息的意图，只返回总结文字。"
        "如果用户输入不符合规范，生成符合你规范的标题即可，不要引号、标点或额外解释：\n\n" + user_message
    )
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    title = extract_text(response.content).strip()[:20]
    return title

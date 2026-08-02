"""小红书文案生成：内部专用 prompt + LLM，与主 agent / 出图解耦。"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from backend.config import settings
from backend.tools.public.write_xhs_copy.parse import loads_llm_json
from backend.tools.public.write_xhs_copy.prompt import XHS_SYSTEM_PROMPT
from backend.tools.public.write_xhs_copy.schema import (
    WriteXhsCopyArgs,
    XhsCopyResult,
    XhsPost,
)

logger = logging.getLogger(__name__)

TOOL_NAME = "write_xhs_copy"

_REPAIR_HINT = (
    "上次输出不是合法 JSON。请只输出一个 JSON 对象，不要 Markdown 代码块，"
    "正文换行必须写成 \\n 转义，不要在字符串里直接回车。"
)


def _build_writer_llm(*, temperature: float = 0.8) -> ChatOpenAI:
    """文案用主模型，非流式。"""
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=temperature,
        streaming=False,
        max_tokens=4096,
        reasoning_effort="minimal",
    )


def _parse_result(raw: str) -> XhsCopyResult:
    data: Any = loads_llm_json(raw)
    if isinstance(data, list):
        data = {"posts": data}
    if not isinstance(data, dict):
        raise ValueError("模型返回非对象 JSON")
    posts_raw = data.get("posts")
    if posts_raw is None and any(k in data for k in ("title", "body")):
        posts_raw = [data]
    if not isinstance(posts_raw, list):
        raise ValueError("缺少 posts 数组")
    posts: list[XhsPost] = []
    for item in posts_raw:
        if not isinstance(item, dict):
            continue
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r"[,，\s#]+", tags) if t.strip()]
        posts.append(
            XhsPost(
                title=str(item.get("title") or "").strip(),
                body=str(item.get("body") or "").strip(),
                tags=[str(t).lstrip("#").strip() for t in tags if str(t).strip()],
                image_prompt=str(item.get("image_prompt") or "").strip(),
                notes=str(item.get("notes") or "").strip(),
            )
        )
    if not posts:
        raise ValueError("未解析到任何文案")
    return XhsCopyResult(posts=posts)


def format_xhs_for_agent(result: XhsCopyResult) -> str:
    """格式化给主 agent：可直接展示给用户，并提示可选出图。"""
    parts: list[str] = []
    for i, post in enumerate(result.posts, start=1):
        head = f"【笔记 {i}】" if len(result.posts) > 1 else "【小红书笔记】"
        lines = [head, f"标题：{post.title}", "", post.body]
        if post.tags:
            lines.append("")
            lines.append("标签：" + " ".join(f"#{t}" for t in post.tags))
        if post.image_prompt:
            lines.append("")
            lines.append(f"（配图提示，仅内部）image_prompt={post.image_prompt}")
        if post.notes:
            lines.append(f"（备注，勿原样对用户念）{post.notes}")
        parts.append("\n".join(lines))
    footer = (
        "\n\n请将以上文案用友好中文整理后展示给用户（可微调排版，勿改核心信息）。"
        "用户未要求配图时不要调用生图；若要求配图，可用各篇的 image_prompt 调用 generate_image。"
        "不要提工具名或 JSON。"
    )
    return "\n\n---\n\n".join(parts) + footer


def _message_text(resp: Any) -> str:
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content
    return str(content or "")


async def generate_xhs_posts(
    material: str,
    style: str = "种草",
    count: int = 1,
) -> XhsCopyResult:
    """生成结构化小红书文案；失败抛异常（供复合 tool 使用）。"""
    text = (material or "").strip()
    if not text:
        raise ValueError("素材为空")

    try:
        n = max(1, min(int(count or 1), 5))
    except (TypeError, ValueError):
        n = 1
    style_s = (style or "种草").strip() or "种草"

    human = (
        f"风格：{style_s}\n篇数：{n}\n"
        f"素材（用户原文或资料摘要，请严格依据）：\n{text}"
    )
    messages: list = [
        SystemMessage(content=XHS_SYSTEM_PROMPT),
        HumanMessage(content=human),
    ]

    raw = _message_text(await _build_writer_llm().ainvoke(messages))
    try:
        result = _parse_result(raw)
    except Exception as parse_err:
        logger.warning(
            "write_xhs_copy parse failed, internal repair once: %s",
            parse_err,
        )
        repair_messages = messages + [
            HumanMessage(content=f"模型上次输出摘录：\n{(raw or '')[:1200]}\n\n{_REPAIR_HINT}")
        ]
        raw2 = _message_text(
            await _build_writer_llm(temperature=0.3).ainvoke(repair_messages)
        )
        result = _parse_result(raw2)

    result.posts = result.posts[:n]
    return result


async def run_write_xhs_copy(
    material: str,
    style: str = "种草",
    count: int = 1,
) -> str:
    try:
        result = await generate_xhs_posts(material, style=style, count=count)
        return format_xhs_for_agent(result)
    except Exception as e:
        logger.exception("write_xhs_copy failed after internal repair")
        from backend.common.errors import public_client_error

        return (
            f"{public_client_error(e, kind='xhs')}。"
            "请勿再次调用本工具；请直接向用户致歉并建议缩短素材或减少篇数后重试。"
        )


@tool(TOOL_NAME, args_schema=WriteXhsCopyArgs)
async def write_xhs_copy(
    material: str,
    style: str = "种草",
    count: int = 1,
) -> str:
    """改写小红书笔记（标题/正文/标签），不出图。需查库时先检索再写入 material。"""
    return await run_write_xhs_copy(material, style=style, count=count)

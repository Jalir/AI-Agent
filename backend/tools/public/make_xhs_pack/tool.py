"""批量小红书图文包：并发写文案 + 可选生图，完成一条即推一条卡片。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.tools import tool

from backend.common.stream import emit_status, emit_xhs_card
from backend.common.tool_outcome import (
    INTERNAL_HINT_MARK,
    ToolAction,
    format_tool_outcome,
    format_tool_user_message,
)
from backend.tools.public.generate_image.tool import generate_image_to_urls
from backend.tools.public.make_xhs_pack.schema import MakeXhsPackArgs
from backend.tools.public.write_xhs_copy import generate_xhs_posts

logger = logging.getLogger(__name__)

TOOL_NAME = "make_xhs_pack"
# 同包内并发度：避免打爆生图/文案 API
_PACK_CONCURRENCY = 3


def format_pack_result(
    ok: int,
    total: int,
    with_image: bool,
    *,
    image_fail: int = 0,
    copy_fail: int = 0,
) -> str:
    img = "（含配图）" if with_image else "（仅文案）"
    base = format_tool_user_message(
        f"已按顺序生成 {ok}/{total} 条小红书图文{img}，界面会按序号展示卡片。",
        ask=(
            "请用一两句友好中文告知用户查看下方卡片即可；"
            "不要粘贴全文或 Markdown 图链，也不要再逐条调用生图/文案工具。"
        ),
    )
    if ok <= 0:
        return format_tool_outcome(
            headline="图文包生成失败",
            action=ToolAction.FATAL,
            detail="全部条目未能生成文案",
            extra_hint="可建议用户缩短素材或减少条数后重试。",
        )
    if copy_fail > 0 or image_fail > 0:
        parts: list[str] = []
        if copy_fail:
            parts.append(f"{copy_fail} 条文案失败")
        if image_fail:
            parts.append(f"{image_fail} 条配图失败")
        # 保留成功头（门禁不当失败）+ 统一 degrade action 行
        return (
            f"{base}\n"
            f"{INTERNAL_HINT_MARK}action={ToolAction.DEGRADE.value}："
            f"{'、'.join(parts)}已在对应卡片 error 中体现；"
            "勿整包重跑；用自然语言简要告知即可。"
        )
    return base


async def _build_one_card(
    index: int,
    material: str,
    *,
    style: str,
    want_image: bool,
    sem: asyncio.Semaphore,
) -> dict[str, Any]:
    """生成单条卡片（文案必做，配图可选）；局部失败不抛出。"""
    async with sem:
        title = ""
        body = ""
        tags: list[str] = []
        image_url = ""
        error = ""
        copy_ok = False
        image_fail = False

        try:
            result = await generate_xhs_posts(material, style=style, count=1)
            post = result.posts[0]
            title = post.title
            body = post.body
            tags = list(post.tags or [])
            image_prompt = (post.image_prompt or "").strip()
            copy_ok = True
            if want_image:
                prompt = image_prompt or f"{title}\n{(body or '')[:240]}"
                prompt = f"{prompt}。竖构图，画面比例 9:16，小红书竖屏封面。"
                try:
                    metas = await asyncio.to_thread(
                        generate_image_to_urls, prompt, n=1
                    )
                    image_url = str((metas[0] or {}).get("url") or "").strip()
                    if not image_url:
                        error = "生图未返回 URL"
                        image_fail = True
                except Exception as e:
                    logger.exception("make_xhs_pack image failed index=%s", index)
                    from backend.common.errors import public_client_error

                    error = public_client_error(e, kind="image")
                    image_fail = True
        except Exception as e:
            logger.exception("make_xhs_pack copy failed index=%s", index)
            from backend.common.errors import public_client_error

            error = public_client_error(e, kind="xhs")
            title = title or f"第 {index} 条"
            body = body or (material[:200] + ("…" if len(material) > 200 else ""))

        return {
            "index": index,
            "title": title,
            "body": body,
            "tags": tags,
            "image_url": image_url,
            "error": error,
            "_copy_ok": copy_ok,
            "_image_fail": image_fail,
        }


async def run_make_xhs_pack(
    items: list[str] | Any,
    style: str = "种草",
    with_image: bool = True,
    *,
    thread_id: str | None = None,
) -> str:
    materials = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not materials:
        return format_tool_outcome(
            headline="图文包参数不合规",
            action=ToolAction.ASK_USER,
            detail="素材列表为空，请先提供多条知识/菜品摘要",
        )

    total = len(materials)
    style_s = (style or "种草").strip() or "种草"
    want_image = bool(with_image)
    await emit_status(thread_id, f"正在生成图文…")

    sem = asyncio.Semaphore(_PACK_CONCURRENCY)
    tasks = [
        asyncio.create_task(
            _build_one_card(
                i,
                material,
                style=style_s,
                want_image=want_image,
                sem=sem,
            )
        )
        for i, material in enumerate(materials, start=1)
    ]

    # 谁先完成谁先推 SSE；前端按 index 排序展示，无需等整包
    ok = 0
    copy_fail = 0
    image_fail = 0
    for done in asyncio.as_completed(tasks):
        card = await done
        if card.pop("_copy_ok", False):
            ok += 1
        else:
            copy_fail += 1
        if card.pop("_image_fail", False):
            image_fail += 1
        await emit_xhs_card(thread_id, card)

    return format_pack_result(
        ok,
        total,
        want_image,
        image_fail=image_fail,
        copy_fail=copy_fail,
    )


@tool(TOOL_NAME, args_schema=MakeXhsPackArgs)
async def make_xhs_pack(
    items: list[str],
    style: str = "种草",
    with_image: bool = True,
) -> str:
    """批量小红书图文（每条一文案+可选配图）。多条一次传 items；勿用 generate_image 画合集。"""
    return await run_make_xhs_pack(items, style=style, with_image=with_image)

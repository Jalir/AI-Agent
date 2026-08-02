"""聊天编排：历史灌入、SSE 流式跑图、开场标题。"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, AsyncIterator

from starlette.requests import Request

from backend.common.checkpoint import reset_thread
from backend.common.errors import public_client_error, redact_secrets, sanitize_public_text
from backend.common.llm import generate_title
from backend.common.messages import (
    build_user_content,
    content_audio_urls,
    extract_text,
    normalize_attachments,
)
from backend.common.concurrency import RunSlot
from backend.common.stream import (
    STREAM_DONE,
    begin_run,
    bind_run_task,
    end_run,
    get_active_run,
)
from backend.common.usage import pop_usage_snapshot, register_usage, unregister_usage
from backend.db.database import (
    get_messages,
    insert_message,
    update_conversation_title,
    upsert_conversation_and_count_user_messages,
)
from backend.config import settings
from backend.graph import graph
from backend.graph.guard import RECURSION_USER_MSG
from backend.graph.nodes import CLIENT_INTENT_SET

logger = logging.getLogger(__name__)

STOPPED_REPLY = "已停止回答"


def _run_config(
    thread_id: str,
    *,
    user_id: int | None = None,
    user_role: str | None = None,
    workspace_id: int | None = None,
    milvus_collection: str | None = None,
    sales_workspace_id: int | None = None,
    operation: str = "chat",
) -> dict:
    """带全局步数上限的 runnable config；可选注入当前用户（供工具鉴权/发件人）。

    同时写入 LangSmith 友好的 run_name / tags / metadata，便于按会话与场景过滤。
    """
    role = (str(user_role).strip().lower() if user_role else "") or "user"
    op = (operation or "chat").strip().lower() or "chat"
    if sales_workspace_id is not None:
        mode = "sales"
    elif workspace_id is not None:
        mode = "workspace"
    else:
        mode = "main"

    configurable: dict = {"thread_id": thread_id}
    if user_id is not None:
        configurable["user_id"] = int(user_id)
    if user_role:
        configurable["user_role"] = role
    if workspace_id is not None:
        configurable["workspace_id"] = int(workspace_id)
    coll = (milvus_collection or "").strip()
    if coll:
        configurable["milvus_collection"] = coll
    if sales_workspace_id is not None:
        configurable["sales_workspace_id"] = int(sales_workspace_id)

    metadata: dict[str, Any] = {
        "app": "langgraph-demo",
        "mode": mode,
        "operation": op,
        "thread_id": thread_id,
        "user_role": role,
        "langsmith_project": (settings.langsmith_project or "langgraph-demo").strip(),
    }
    if user_id is not None:
        metadata["user_id"] = int(user_id)
    if workspace_id is not None:
        metadata["workspace_id"] = int(workspace_id)
    if sales_workspace_id is not None:
        metadata["sales_workspace_id"] = int(sales_workspace_id)
    if coll:
        metadata["milvus_collection"] = coll

    tags = [
        mode,
        op,
        f"role:{role}",
        f"thread:{thread_id}",
    ]
    if sales_workspace_id is not None:
        tags.append(f"sales_ws:{int(sales_workspace_id)}")
    if workspace_id is not None:
        tags.append(f"doc_ws:{int(workspace_id)}")

    return {
        "configurable": configurable,
        "recursion_limit": max(8, int(settings.graph_recursion_limit or 28)),
        "run_name": f"{mode}_{op}",
        "tags": tags,
        "metadata": metadata,
    }

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse(data: object) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def seed_history_if_new(thread_id: str) -> None:
    """若图状态尚无 messages，则从 PostgreSQL 灌入；已有状态则跳过 DB。"""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph.aget_state(config)

    # 热路径：checkpoint 已有历史则不再查 messages 表
    if snapshot.values and snapshot.values.get("messages"):
        return

    rows = await get_messages(thread_id)

    if not rows:
        # 新线程无需预置 SystemMessage；agent 每轮注入统一 _AGENT_SYSTEM
        logger.info("New thread %s has no history; skip seed", thread_id)
        return

    msgs = []
    for r in rows:
        if r["role"] == "user":
            content = build_user_content(
                r.get("content") or "",
                r.get("attachments") or [],
                for_llm=True,
            )
            msgs.append(HumanMessage(content=content))
        elif r["role"] == "system":
            # 历史 system 仍灌入；agent 会用当前 _AGENT_SYSTEM 覆盖首条
            msgs.append(SystemMessage(content=r["content"]))
        else:
            msgs.append(AIMessage(content=r["content"]))
    await graph.aupdate_state(config, {"messages": msgs})
    logger.info("Seeded %d messages for thread %s", len(msgs), thread_id)


async def _generate_and_persist_title(thread_id: str, user_text: str) -> str:
    """Call LLM for title and persist to DB. Runs concurrently with graph stream."""
    title = await generate_title(user_text, thread_id=thread_id)
    await update_conversation_title(thread_id, title)
    logger.info("Generated title for thread %s: %s", thread_id, title)
    return title


async def _client_disconnected(request: Request | None) -> bool:
    if request is None:
        return False
    try:
        return await request.is_disconnected()
    except Exception:
        return False


async def _cancel_task(task: asyncio.Task | None) -> None:
    """取消后台任务并等待其退出，吞掉 CancelledError，避免泄漏。"""
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _sync_stopped_assistant(thread_id: str, config: dict) -> None:
    """停止后重建 checkpoint，避免半截 tool_calls / 节点状态污染下一轮。

    消息已落库，故 reset 后从 DB 回灌即可与前端展示一致。
    """
    _ = config
    try:
        await reset_thread(thread_id)
        await seed_history_if_new(thread_id)
    except Exception:
        logger.warning(
            "Failed to resync graph state after stop thread=%s",
            thread_id,
            exc_info=True,
        )


async def stream_graph(
    input_data: Any,
    config: dict,
    thread_id: str,
    *,
    generate_title_flag: bool = False,
    request: Request | None = None,
    run_slot: RunSlot | None = None,
) -> AsyncIterator[str]:
    """SSE generator: token 队列流式输出 + interrupt 检测 + 标题并发。

    用户停止 / 断连时：取消 graph 任务、落库已输出内容、回收队列，不空等跑完。
    """
    assistant_parts: list[str] = []
    assistant_files: list[dict] = []
    assistant_xhs_cards: list[dict] = []
    assistant_charts: list[dict] = []
    title_sent = False
    stream_error: str | None = None
    usage_payload: dict | None = None
    user_stopped = False
    natural_end = False
    persisted = False

    active = await begin_run(thread_id)
    run_id = active.run_id
    token_queue = active.queue
    cancel_event = active.cancel
    register_usage(thread_id, run_id)

    title_task: asyncio.Task | None = None
    if generate_title_flag:
        user_text = ""
        if isinstance(input_data, dict):
            raw = input_data.get("messages", [{}])[0].get("content", "")
            user_text = extract_text(raw)
        if user_text:
            title_task = asyncio.create_task(_generate_and_persist_title(thread_id, user_text))

    async def _run_graph() -> None:
        nonlocal stream_error
        try:
            await graph.ainvoke(input_data, config)
        except asyncio.CancelledError:
            logger.info("Graph task cancelled for thread %s", thread_id)
            raise
        except Exception as exc:
            # interrupt() 会以 GraphInterrupt 冒泡；ainvoke 通常已吞掉，
            # 若仍漏出则不当作失败，留给下方 aget_state 检测 interrupts。
            from langgraph.errors import GraphBubbleUp, GraphRecursionError

            if isinstance(exc, GraphBubbleUp):
                logger.info("Graph interrupted for thread %s", thread_id)
            elif isinstance(exc, GraphRecursionError):
                logger.warning(
                    "Graph recursion limit hit for thread %s: %s",
                    thread_id,
                    exc,
                )
                # 友好收场，不走 error 事件
                await token_queue.put(RECURSION_USER_MSG)
                stream_error = None
            else:
                # 完整异常只落日志；SSE 只回安全文案
                stream_error = public_client_error(exc, kind="chat")
                logger.exception("Graph invoke failed for thread %s", thread_id)
        finally:
            with suppress(Exception):
                await token_queue.put(STREAM_DONE)

    graph_task = asyncio.create_task(_run_graph())
    bind_run_task(thread_id, run_id, graph_task)

    async def _should_stop() -> bool:
        return cancel_event.is_set() or await _client_disconnected(request)

    async def _persist_assistant(token_total: int = 0) -> None:
        nonlocal persisted, assistant_parts
        if persisted:
            return
        if user_stopped and not assistant_parts and not assistant_files and not assistant_xhs_cards and not assistant_charts:
            assistant_parts.append(STOPPED_REPLY)
        if not (assistant_parts or assistant_files or assistant_xhs_cards or assistant_charts):
            return
        persisted = True
        full_text = "".join(assistant_parts)
        persist_atts = list(assistant_files) + list(assistant_xhs_cards) + list(assistant_charts)
        try:
            await insert_message(
                thread_id,
                "assistant",
                full_text,
                token_total=token_total,
                attachments=persist_atts or None,
            )
        except Exception:
            logger.exception("Failed to persist assistant message for thread %s", thread_id)
            persisted = False

    try:
        while True:
            if await _should_stop():
                user_stopped = True
                logger.info("Stop detected for thread %s (cancel/disconnect)", thread_id)
                break

            # 标题若先完成，立刻推给前端
            if title_task and title_task.done() and not title_sent:
                try:
                    t = title_task.result()
                    yield sse({"type": "title", "title": t})
                    title_sent = True
                except Exception:
                    pass

            try:
                item = await asyncio.wait_for(token_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                if await _should_stop():
                    user_stopped = True
                    break
                if graph_task.done() and token_queue.empty():
                    natural_end = True
                    break
                continue

            if item is STREAM_DONE:
                natural_end = True
                break
            # 进度状态（intent / 检索 / 重排等）
            if isinstance(item, dict) and item.get("type") == "status":
                status = (item.get("content") or "").strip()
                if status:
                    yield sse({"type": "status", "content": status})
                continue
            # 导出文件（Word 等）：推给前端下载卡片
            if isinstance(item, dict) and item.get("type") == "file":
                url = str(item.get("url") or "").strip()
                if url:
                    meta = {
                        "name": str(item.get("name") or "download.docx"),
                        "url": url,
                        "object_key": str(item.get("object_key") or ""),
                        "mime_type": str(item.get("mime_type") or ""),
                        "file_size": int(item.get("file_size") or 0),
                    }
                    assistant_files.append(meta)
                    yield sse({"type": "file", **meta})
                continue
            # 销售分析 ECharts
            if isinstance(item, dict) and item.get("type") == "chart":
                option = item.get("option")
                if isinstance(option, dict) and option:
                    chart = {
                        "kind": "chart",
                        "chart_id": str(item.get("chart_id") or ""),
                        "title": str(item.get("title") or ""),
                        "option": option,
                        "evidence": item.get("evidence")
                        if isinstance(item.get("evidence"), dict)
                        else {},
                        "mime_type": "application/x-echarts",
                        "name": str(item.get("title") or "chart") or "chart",
                        "url": "",
                    }
                    assistant_charts.append(chart)
                    yield sse(
                        {
                            "type": "chart",
                            "chart_id": chart["chart_id"],
                            "title": chart["title"],
                            "option": chart["option"],
                            "evidence": chart["evidence"],
                        }
                    )
                continue
            # 有序小红书图文卡片
            if isinstance(item, dict) and item.get("type") == "xhs_card":
                try:
                    index = int(item.get("index") or 0)
                except (TypeError, ValueError):
                    index = 0
                if index > 0:
                    tags = item.get("tags") or []
                    if not isinstance(tags, list):
                        tags = [str(tags)]
                    card = {
                        "kind": "xhs_card",
                        "index": index,
                        "title": str(item.get("title") or ""),
                        "body": str(item.get("body") or ""),
                        "tags": [str(t) for t in tags if str(t).strip()],
                        "image_url": str(item.get("image_url") or "").strip(),
                        "error": (
                            sanitize_public_text(
                                str(item.get("error") or "").strip(),
                                fallback="生成失败，请稍后重试。",
                            )
                            if str(item.get("error") or "").strip()
                            else ""
                        ),
                        "mime_type": "application/x-xhs-card",
                        "name": f"xhs_{index}",
                        "url": str(item.get("image_url") or "").strip(),
                    }
                    assistant_xhs_cards.append(card)
                    yield sse({"type": "xhs_card", **{k: card[k] for k in (
                        "index", "title", "body", "tags", "image_url", "error"
                    )}})
                continue
            if isinstance(item, str) and item:
                safe_text = redact_secrets(item)
                assistant_parts.append(safe_text)
                yield sse({"type": "text", "content": safe_text})
    except asyncio.CancelledError:
        user_stopped = True
        logger.info("SSE generator cancelled for thread %s", thread_id)
    finally:
        # aclose / 中途退出且非正常读完 → 视为用户停止
        if cancel_event.is_set() or user_stopped or not natural_end:
            user_stopped = True

        # 关键：取消图任务，不再空等跑完（避免断连后继续烧 token / 占内存）
        # 若已被同 thread 新 run 取代，只取消本轮 task，勿清后来者的注册
        still_owner = False
        cur = get_active_run(thread_id)
        if cur is not None and cur.run_id == run_id:
            still_owner = True
            await _cancel_task(graph_task)
            end_run(thread_id, run_id)
        else:
            # 已被新 run 替换：只取消本轮自己的 task，勿清注册表
            await _cancel_task(graph_task)

        usage_payload = pop_usage_snapshot(thread_id, run_id)
        if usage_payload is None:
            unregister_usage(thread_id, run_id)

        token_total = int((usage_payload or {}).get("total_tokens") or 0)
        # 断连/aclose 时后续代码可能不跑：停止路径必须在 finally 落库
        if user_stopped and still_owner:
            await _persist_assistant(token_total)
            await _sync_stopped_assistant(thread_id, config)
        elif user_stopped and (
            assistant_parts or assistant_files or assistant_xhs_cards or assistant_charts
        ):
            # 被同 thread 新 run 顶替：只落已输出内容，不写「已停止」占位、不 reset
            await _persist_assistant(token_total)

        if run_slot is not None:
            run_slot.release()

    # 用户已停止：尽量补一条 stopped（若连接仍在），然后收工
    if user_stopped:
        stopped_text = "".join(assistant_parts) or STOPPED_REPLY
        # 思考阶段停下时前端可能还没收到正文；若仍连着则推占位文案
        if stopped_text == STOPPED_REPLY and not assistant_files and not assistant_xhs_cards and not assistant_charts:
            with suppress(Exception):
                yield sse({"type": "text", "content": STOPPED_REPLY})
        with suppress(Exception):
            yield sse({"type": "done", "title": None, "stopped": True})
        logger.info(
            "Stopped stream thread=%s chars=%d",
            thread_id,
            len("".join(assistant_parts)),
        )
        return

    # 回退：队列没收到 token 时，从最终 state 取 AI 回复
    if not assistant_parts and not stream_error:
        snapshot = await graph.aget_state(config)
        msgs = (snapshot.values or {}).get("messages") or []
        if msgs:
            last = msgs[-1]
            text = extract_text(getattr(last, "content", ""))
            if text and getattr(last, "type", "") == "ai":
                safe_text = redact_secrets(text)
                assistant_parts.append(safe_text)
                yield sse({"type": "text", "content": safe_text})
                logger.info("Fallback yielded full AI message for thread %s", thread_id)
                token_total = int((usage_payload or {}).get("total_tokens") or 0)
                await _persist_assistant(token_total)

    if stream_error:
        # stream_error 已是 public_client_error 文案，不再拼接原始异常
        yield sse({"type": "error", "content": stream_error})

    token_total = int((usage_payload or {}).get("total_tokens") or 0)
    await _persist_assistant(token_total)

    snapshot = await graph.aget_state(config)
    if snapshot.tasks:
        for task in snapshot.tasks:
            if task.interrupts:
                val = task.interrupts[0].value
                logger.info("Interrupt for thread %s: %s", thread_id, val)
                if token_total > 0:
                    yield sse({"type": "usage", "total_tokens": token_total})
                yield sse({"type": "status", "content": "等待确认…"})
                yield sse({"type": "interrupt", "data": val})
                return

    title: str | None = None
    if title_task:
        try:
            title = await title_task
            if not title_sent:
                yield sse({"type": "title", "title": title})
                title_sent = True
        except Exception as e:
            logger.warning("Title generation failed: %s", e)

    if token_total > 0:
        logger.info("Usage thread=%s total_tokens=%s", thread_id, token_total)
        yield sse({"type": "usage", "total_tokens": token_total})

    yield sse({"type": "done", "title": title if not title_sent else None})


async def prepare_chat_turn(
    *,
    thread_id: str,
    message: str,
    intent: str | None,
    attachments: list[dict] | None,
    user_id: int,
    user_role: str | None = None,
    workspace_id: int | None = None,
    milvus_collection: str | None = None,
    sales_workspace_id: int | None = None,
) -> tuple[dict, dict, bool]:
    """落库用户消息并组装 graph 输入。

    Returns:
        (input_data, config, is_first_user_turn)

    Raises:
        ValueError: 消息与附件皆空。
        ConversationAccessError: thread 不属于该用户。
    """
    text = (message or "").strip()
    atts = normalize_attachments(attachments)
    if not text and not atts:
        raise ValueError("message or attachments required")

    config = _run_config(
        thread_id,
        user_id=user_id,
        user_role=user_role,
        workspace_id=workspace_id,
        milvus_collection=milvus_collection,
        sales_workspace_id=sales_workspace_id,
    )

    # 单事务：upsert 会话 + 统计历史 user 条数（本轮写入之前）
    if sales_workspace_id is not None:
        conv_title = "销售分析"
    elif workspace_id is not None:
        conv_title = "文档工作区"
    else:
        conv_title = "新对话"
    user_count = await upsert_conversation_and_count_user_messages(
        thread_id, user_id=user_id, title=conv_title
    )
    is_first = user_count == 0

    await seed_history_if_new(thread_id)

    # DB 存永久 url + object_key；图输入用签过名的可读 URL
    await insert_message(thread_id, "user", text, attachments=atts)
    llm_content = build_user_content(text, atts, for_llm=True)
    logger.info(
        "Chat turn thread=%s text=%r attachments=%d multimodal=%s",
        thread_id,
        text[:80],
        len(atts),
        isinstance(llm_content, list),
    )
    input_data: dict = {"messages": [{"role": "user", "content": llm_content}]}
    audio_urls = content_audio_urls(llm_content)
    if audio_urls:
        input_data["pending_audio_urls"] = audio_urls

    if intent:
        normalized = intent.strip().lower().replace("-", "_")
        if normalized in CLIENT_INTENT_SET:
            input_data["client_intent"] = normalized
        else:
            logger.warning("Ignoring invalid intent: %r", intent)

    return input_data, config, is_first

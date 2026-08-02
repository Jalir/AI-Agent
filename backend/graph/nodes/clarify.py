"""意图不明时的澄清节点：直接反问用户，不进入检索。"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from backend.common.stream import emit_status, get_token_queue, thread_id_from_config

logger = logging.getLogger(__name__)

_DEFAULT_CLARIFY = "您的问题里有不太确定的地方，能再具体说说想查什么吗？"


async def clarify_node(state: Mapping[str, Any], config: RunnableConfig) -> dict:
    """把路由给出的澄清问句流式返回，结束本轮。"""
    tid = thread_id_from_config(config)
    text = (state.get("clarify_question") or "").strip() or _DEFAULT_CLARIFY
    logger.info("Clarify branch: %r", text[:120])
    await emit_status(tid, "正在确认…")
    queue = get_token_queue(tid)
    if queue is not None:
        await queue.put(text)
    return {
        "messages": [AIMessage(content=text)],
        "client_intent": "",
        "clarify_question": text,
        "pending_audio_urls": [],
    }

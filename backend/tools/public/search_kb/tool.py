"""对外暴露的 LangChain tool：search_knowledge_base。"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from backend.tools.public.search_kb.critique import _MAX_TOP_K
from backend.tools.public.search_kb.pipeline import run_search_kb
from backend.tools.public.search_kb.retrieve import RAG_TOP_K

logger = logging.getLogger(__name__)

TOOL_NAME = "search_knowledge_base"


@tool(TOOL_NAME)
async def search_knowledge_base(query: str, top_k: int = RAG_TOP_K) -> str:
    """检索文档片段。工作区=本次上传材料；主对话=共享知识库。
    回答/总结/报表/建议/行动项前须先检索；无依据则说明「材料未提及」，勿编造。

    Args:
        query: 可检索的独立中文问句（纠错、消指代后）
        top_k: 返回条数，默认 5，最大 30
    """
    k = max(1, min(int(top_k or RAG_TOP_K), _MAX_TOP_K))
    q = (query or "").strip()
    logger.info("tool search_knowledge_base query=%r top_k=%d", q[:80], k)
    return await run_search_kb(q, k)

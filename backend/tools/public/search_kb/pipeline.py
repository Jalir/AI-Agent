"""知识库检索流水线：混合检索 + 证据纠错（扩召回 / 重写）+ L2 扩展。

供 search_knowledge_base tool 内部调用；纠错是函数步骤，不是图节点 / 外层 tool。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from backend.common.stream import emit_status, make_sync_status_emitter
from backend.tools.public.search_kb.critique import (
    _MAX_TOP_K,
    clamp_top_k,
    format_judgment_for_agent,
    judge_evidence,
    rewrite_query,
)
from backend.tools.public.search_kb.retrieve import (
    RAG_TOP_K,
    expand_hits_to_l2,
    extract_rag_query,
    format_rag_context,
    hybrid_retrieve,
)

logger = logging.getLogger(__name__)


def _hit_id_fingerprint(hits: list[dict] | None) -> list[str]:
    keys: list[str] = []
    for h in hits or []:
        cid = str(h.get("chunk_id") or "").strip()
        if cid:
            keys.append(f"id:{cid}")
        else:
            text = str(h.get("text") or "").strip()[:120]
            if text:
                keys.append(f"tx:{text}")
    return sorted(set(keys))


def _evidence_unchanged(prev_ids: list[str] | None, hits: list[dict] | None) -> bool:
    prev = [str(x) for x in (prev_ids or []) if str(x).strip()]
    if not prev:
        return False
    return _hit_id_fingerprint(hits) == sorted(set(prev))


async def _retrieve(
    query_text: str,
    top_k: int,
    dense_text: str | None,
    *,
    post_expand: bool,
    on_status: Callable[[str], None] | None,
    thread_id: str | None,
    milvus_collection: str | None = None,
) -> tuple[list[dict], str]:
    await emit_status(thread_id, "正在查询…")

    def _do() -> list[dict]:
        return hybrid_retrieve(
            query_text,
            top_k,
            dense_text,
            on_status,
            relax_relative_filter=post_expand,
            milvus_collection=milvus_collection,
        )

    try:
        hits = await asyncio.to_thread(_do)
        await emit_status(thread_id, "正在整理…")
        context = format_rag_context(hits)
        logger.info(
            "search_kb retrieve: hits=%d query=%r top_k=%d post_expand=%s",
            len(hits),
            query_text[:80],
            top_k,
            post_expand,
        )
        return hits, context
    except Exception:
        logger.exception("search_kb hybrid search failed")
        await emit_status(thread_id, "正在重试…")
        return [], "（知识库检索失败，请告知用户稍后重试）"


async def run_search_kb(
    query: str,
    top_k: int = RAG_TOP_K,
    *,
    thread_id: str | None = None,
    workspace_id: int | None = None,
    milvus_collection: str | None = None,
) -> str:
    """
    执行检索 + 至多一轮 expand / 至多一轮 rewrite，再 L2 扩展，返回给 LLM 的文本。

    workspace_id / milvus_collection：文档工作区临时 RAG；未传则走共享知识库。
    """
    query_text = extract_rag_query(query) or (query or "").strip()
    top_k = max(1, min(int(top_k or RAG_TOP_K), _MAX_TOP_K))

    if not query_text:
        return "（未提供有效检索词，未检索到相关内容）"

    on_status = make_sync_status_emitter(thread_id)
    dense_text: str | None = None
    rewrite_used = False
    expand_used = False
    rewrite_strategy = ""
    prev_hit_ids: list[str] = []
    judgment: dict[str, Any] = {}
    standalone = query_text
    source_label = "工作区材料" if workspace_id is not None else "知识库"

    # ---- 首轮检索 ----
    hits, context = await _retrieve(
        query_text,
        top_k,
        dense_text,
        post_expand=False,
        on_status=on_status,
        thread_id=thread_id,
        milvus_collection=milvus_collection,
    )

    # ---- 纠错循环：expand / rewrite 各最多一次 ----
    for _ in range(2):
        await emit_status(thread_id, "正在核对…")

        if (expand_used or rewrite_used) and _evidence_unchanged(prev_hit_ids, hits):
            judgment = {
                "relevance": "medium",
                "answerable": True,
                "ambiguity": "none",
                "issues": [],
                "need_expand_topk": False,
                "suggested_top_k": None,
                "need_rewrite": False,
                "rewrite_strategy": None,
                "reason": "纠错后证据未变，跳过复评",
            }
            break

        judgment = dict(
            await judge_evidence(
                question=standalone,
                rag_context=context,
                allow_rewrite=not rewrite_used,
                allow_expand=not expand_used,
                current_top_k=top_k,
                thread_id=thread_id,
            )
        )

        need_expand = bool(judgment.get("need_expand_topk")) and not expand_used
        need_rewrite = bool(judgment.get("need_rewrite")) and not rewrite_used

        if need_expand:
            await emit_status(thread_id, "正在补充…")
            prev_hit_ids = _hit_id_fingerprint(hits)
            top_k = clamp_top_k(judgment.get("suggested_top_k"), current=top_k)
            expand_used = True
            hits, context = await _retrieve(
                query_text,
                top_k,
                dense_text,
                post_expand=True,
                on_status=on_status,
                thread_id=thread_id,
                milvus_collection=milvus_collection,
            )
            continue

        if need_rewrite:
            await emit_status(thread_id, "正在调整…")
            strategy = (judgment.get("rewrite_strategy") or "step_back").strip().lower()
            if strategy not in ("step_back", "hyde"):
                strategy = "step_back"
            rewrite_strategy = strategy
            prev_hit_ids = _hit_id_fingerprint(hits)
            rewritten = await rewrite_query(
                question=standalone,
                strategy=strategy,  # type: ignore[arg-type]
                judgment_reason=str(judgment.get("reason") or ""),
                thread_id=thread_id,
            )
            rewrite_used = True
            if strategy == "hyde":
                dense_text = rewritten
                # BM25 仍用原独立查询
            else:
                query_text = rewritten
                dense_text = None
            hits, context = await _retrieve(
                query_text,
                top_k,
                dense_text,
                post_expand=False,
                on_status=on_status,
                thread_id=thread_id,
                milvus_collection=milvus_collection,
            )
            continue

        break

    # ---- L2 父块扩展（给主模型阅读）----
    if hits:
        await emit_status(thread_id, "正在整理…")
        try:
            expanded = await expand_hits_to_l2(hits, workspace_id=workspace_id)
            context = format_rag_context(expanded)
            logger.info(
                "search_kb L2 expand: l3=%d → context_chars=%d workspace=%s",
                len(hits),
                len(context),
                workspace_id,
            )
        except Exception:
            logger.exception("search_kb L2 expand failed, use L3 context")

    hint = format_judgment_for_agent(
        judgment,  # type: ignore[arg-type]
        rewrite_used=rewrite_used,
        expand_used=expand_used,
    )
    parts = [
        f"【检索查询】{standalone}",
        f"【最终 top_k】{top_k}",
        f"【检索范围】{source_label}",
    ]
    if rewrite_used and rewrite_strategy:
        parts.append(f"【纠错】已做查询重写（{rewrite_strategy}）")
    if expand_used:
        parts.append("【纠错】已扩大召回")
    if hint:
        parts.append(hint)
    parts.append(f"【检索到的{source_label}内容】")
    parts.append(context)
    parts.append(
        "请严格依据以上内容回答或继续调用其它工具；证据不足时说明局限，禁止编造。"
    )
    await emit_status(thread_id, "查询完成…")
    return "\n".join(parts)

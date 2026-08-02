"""RAG 检索：查询清洗、混合召回、Rerank 精排、上下文格式化。"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from backend.tools.public.search_kb.critique import _DEFAULT_TOP_K

logger = logging.getLogger(__name__)

RAG_TOP_K = _DEFAULT_TOP_K

# 用户常带的意图前缀，会污染 embedding / BM25，检索前剥掉
_RAG_QUERY_PREFIX_RE = re.compile(
    r"^\s*(?:请)?(?:帮我)?"
    r"(?:"
    r"(?:从)?(?:知识库|文档|资料|数据库)(?:中|里)?(?:检索|查询|搜索|查找|查|问答)?"
    r"|"
    r"(?:检索|查询|搜索|查找|查)(?:一下)?(?:知识库|文档|资料|数据库)"
    r")"
    r"[：:，,\s的]*",
    re.IGNORECASE,
)


def extract_rag_query(user_text: str) -> str:
    """从用户话术中提取真实检索词，去掉「知识库检索：」等前缀。"""
    text = (user_text or "").strip()
    if not text:
        return ""
    cleaned = _RAG_QUERY_PREFIX_RE.sub("", text, count=1).strip()
    return cleaned or text


def clean_chunk_text(text: str) -> str:
    """清洗检索 chunk：去控制字符、压缩空白，保留段落换行；不截断。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = text.replace("\u00ad", "").replace("\xa0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\n(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=[a-zA-Z,])\n(?=[a-zA-Z])", " ", text)
    return text.strip()


def format_rag_context(results: list[dict]) -> str:
    """将命中整理为供 LLM 阅读的上下文（减负：不展示得分/层级）。"""
    if not results:
        return "（未检索到相关内容）"

    parts: list[str] = []
    for idx, res in enumerate(results, start=1):
        filename = res.get("filename") or "未知文件"
        page_num = res.get("page_number", "N/A")
        text = clean_chunk_text(str(res.get("text") or ""))
        parts.append(f"[{idx}] 来源={filename} | 页码={page_num}\n{text}")
    return "\n\n".join(parts)


def filter_by_relative_score(
    hits: list[dict],
    *,
    margin: float = 0.4,
    min_keep: int = 1,
) -> list[dict]:
    """
    相对 top1 过滤：保留 score >= top1_score - margin。

    不依赖绝对分数量纲；至少保留 min_keep 条（默认 1，即最高分）。
    """
    if not hits:
        return []
    min_keep = max(1, min(int(min_keep), len(hits)))

    scores = [float(h.get("score") or 0.0) for h in hits]
    top1 = max(scores)
    floor = top1 - float(margin)

    kept = [h for h, s in zip(hits, scores) if s >= floor]
    if len(kept) < min_keep:
        # 已按分数降序时取前 min_keep；否则按分排序再取
        ordered = sorted(hits, key=lambda x: float(x.get("score") or 0.0), reverse=True)
        kept = ordered[:min_keep]

    logger.info(
        "Relative filter: in=%d top1=%.4f floor=%.4f margin=%.3f kept=%d",
        len(hits),
        top1,
        floor,
        margin,
        len(kept),
    )
    return kept


def _recall_k(top_k: int) -> int:
    """混合检索多召回条数，供 Rerank 精排。"""
    from backend.config import settings

    mult = max(1, int(settings.rerank_recall_multiplier or 4))
    cap = max(top_k, int(settings.rerank_max_recall or 50))
    return min(cap, max(top_k * mult, 20))


async def expand_hits_to_l2(
    hits: list[dict],
    *,
    workspace_id: int | None = None,
) -> list[dict]:
    """
    L3 命中 → 按 parent_chunk_id 批量拉 L2，按 L2 去重后供 LLM 阅读。

    性能：一次 ANY 查询；保留首个（Rerank 分更高）子块的 score。
    L2 缺失（旧索引）时回退该条 L3。
    workspace_id：走工作区临时表，与共享 document_chunks 隔离。
    """
    if not hits:
        return []

    from backend.config import settings

    if not settings.rag_expand_to_l2:
        return hits

    parent_ids: list[str] = []
    seen_parents: set[str] = set()
    for h in hits:
        pid = str(h.get("parent_chunk_id") or "").strip()
        if pid and pid not in seen_parents:
            seen_parents.add(pid)
            parent_ids.append(pid)

    if not parent_ids:
        return hits

    try:
        if workspace_id is not None:
            from backend.indexing.workspace_postgres import (
                query_workspace_l2_by_chunk_ids,
            )

            l2_map = await query_workspace_l2_by_chunk_ids(
                int(workspace_id), parent_ids
            )
        else:
            from backend.indexing.postgres_store import query_l2_by_chunk_ids

            l2_map = await query_l2_by_chunk_ids(parent_ids)
    except Exception:
        logger.exception("L2 parent expand failed, fallback to L3 hits")
        return hits

    out: list[dict] = []
    used_l2: set[str] = set()
    l2_hits = 0
    l3_fallback = 0

    for h in hits:
        pid = str(h.get("parent_chunk_id") or "").strip()
        if pid and pid in used_l2:
            continue

        l2 = l2_map.get(pid) if pid else None
        if l2:
            item = dict(h)
            item["text"] = l2.get("text") or item.get("text") or ""
            item["l3_chunk_id"] = h.get("chunk_id")
            item["chunk_id"] = l2.get("chunk_id") or pid
            item["parent_chunk_id"] = l2.get("parent_chunk_id") or ""
            item["root_chunk_id"] = l2.get("root_chunk_id") or h.get("root_chunk_id") or ""
            item["chunk_level"] = 2
            item["chunk_idx"] = l2.get("chunk_idx", h.get("chunk_idx", 0))
            if l2.get("filename"):
                item["filename"] = l2["filename"]
            if l2.get("page_number") is not None:
                item["page_number"] = l2["page_number"]
            used_l2.add(pid)
            out.append(item)
            l2_hits += 1
        else:
            out.append(h)
            l3_fallback += 1
            if pid:
                used_l2.add(pid)

    logger.info(
        "Parent expand L3→L2: in=%d parents=%d l2=%d l3_fallback=%d out=%d",
        len(hits),
        len(parent_ids),
        l2_hits,
        l3_fallback,
        len(out),
    )
    return out


def hybrid_retrieve(
    query_text: str,
    top_k: int = RAG_TOP_K,
    dense_text: str | None = None,
    on_status: Callable[[str], None] | None = None,
    *,
    relax_relative_filter: bool = False,
    milvus_collection: str | None = None,
) -> list[dict]:
    """同步：embedding → Milvus Dense+BM25 混合检索 → 可选 BGE Rerank。

    query_text：BM25 / Rerank 查询；dense_text：可选，HyDE 假设段落仅用于 dense。
    返回的仍是 L3；调用方再用 expand_hits_to_l2 异步回查父块。
    on_status：可选进度回调（线程安全由调用方保证）。
    relax_relative_filter：expand 后再检索时为 True——跳过相对分过滤，
    避免 top1 极高时把扩召回结果又砍回原集合（Rerank 的 top_k 仍生效）。
    milvus_collection：指定集合（工作区临时 RAG）；默认共享 embeddings_collection。
    """
    from backend.config import settings
    from backend.indexing.embedding import embedding_service
    from backend.indexing.milvus_client import get_milvus_service

    def _status(msg: str) -> None:
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    top_k = max(1, int(top_k))
    embed_src = (dense_text or query_text or "").strip() or query_text
    query_embeddings = embedding_service.get_embeddings([embed_src])
    dense_vector = query_embeddings["dense"][0]
    coll = (milvus_collection or "").strip() or None
    service = get_milvus_service(collection=coll) if coll else get_milvus_service()

    use_rerank = bool(settings.rerank_enabled)
    milvus_k = _recall_k(top_k) if use_rerank else top_k

    hits = service.hybrid_search(
        query_text=query_text,
        dense_vector=dense_vector,
        top_k=milvus_k,
        filter_expr="",
    )

    if not use_rerank or not hits:
        return hits[:top_k]

    try:
        from backend.tools.public.search_kb.reranker import rerank_service

        _status("正在筛选…")
        # Rerank 用真实问题（或 Step-back 后的 query），不用 HyDE 段落
        ranked = rerank_service.rerank(
            query_text,
            hits,
            top_k=top_k,
            score_threshold=settings.rerank_score_threshold_value,
        )
        logger.info(
            "Rerank: candidates=%d → kept=%d (top_k=%d) scores=%s",
            len(hits),
            len(ranked),
            top_k,
            [round(float(x.get("score") or 0.0), 4) for x in ranked[:8]],
        )
        if settings.rerank_relative_filter and ranked:
            if relax_relative_filter:
                logger.info(
                    "Relative filter skipped (post-expand): keep rerank top_k=%d",
                    len(ranked),
                )
            else:
                ranked = filter_by_relative_score(
                    ranked,
                    margin=float(settings.rerank_score_margin or 0.4),
                    min_keep=1,
                )
        return ranked
    except Exception:
        logger.exception("Rerank failed, fallback to RRF top_k")
        return hits[:top_k]

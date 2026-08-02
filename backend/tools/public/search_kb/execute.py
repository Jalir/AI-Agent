"""search_knowledge_base 执行器。"""

from __future__ import annotations

from backend.tools.context import ToolExecContext
from backend.tools.public.search_kb.critique import _MAX_TOP_K
from backend.tools.public.search_kb.pipeline import run_search_kb
from backend.tools.public.search_kb.retrieve import RAG_TOP_K


async def execute(ctx: ToolExecContext) -> str:
    query = str(ctx.arg("query") or "").strip()
    try:
        top_k = int(ctx.arg("top_k") or RAG_TOP_K)
    except (TypeError, ValueError):
        top_k = RAG_TOP_K
    top_k = max(1, min(top_k, _MAX_TOP_K))

    workspace_id: int | None = None
    milvus_collection: str | None = None
    conf = (ctx.config or {}).get("configurable") if ctx.config else None
    if isinstance(conf, dict):
        raw_ws = conf.get("workspace_id")
        if raw_ws is not None and str(raw_ws).strip() != "":
            try:
                workspace_id = int(raw_ws)
            except (TypeError, ValueError):
                workspace_id = None
        coll = str(conf.get("milvus_collection") or "").strip()
        if coll:
            milvus_collection = coll

    # 工作区模式禁止静默回落共享库
    if workspace_id is not None and not milvus_collection:
        return (
            "（工作区索引配置缺失，已中止检索，避免误查共享知识库。"
            "请重新打开文档工作区后再试。）"
        )

    return await run_search_kb(
        query,
        top_k,
        thread_id=ctx.thread_id,
        workspace_id=workspace_id,
        milvus_collection=milvus_collection,
    )

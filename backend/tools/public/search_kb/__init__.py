"""知识库检索技能：tool + 内部检索/纠错流水线。

目录约定（新增技能可照抄）：
  tools/public/<skill_name>/   # 普通技能
  tools/gated/<skill_name>/    # 需权限技能
    __init__.py   # 导出 TOOL / TOOL_NAME / REQUIRES_APPROVAL [/ REQUIRED_PERMISSIONS]
    tool.py       # @tool 定义
    …             # 本技能私有实现（如 critique / pipeline）
"""

from backend.tools.public.search_kb.critique import _MAX_TOP_K
from backend.tools.public.search_kb.pipeline import run_search_kb
from backend.tools.public.search_kb.retrieve import RAG_TOP_K, extract_rag_query
from backend.tools.public.search_kb.tool import TOOL_NAME, search_knowledge_base

TOOL = search_knowledge_base
# 只读检索：跳过 HITL 审批
REQUIRES_APPROVAL = False
MAX_CALLS_PER_TURN = 4

__all__ = [
    "TOOL",
    "TOOL_NAME",
    "REQUIRES_APPROVAL",
    "MAX_CALLS_PER_TURN",
    "RAG_TOP_K",
    "_MAX_TOP_K",
    "extract_rag_query",
    "run_search_kb",
    "search_knowledge_base",
]

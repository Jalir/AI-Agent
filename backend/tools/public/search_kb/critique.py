"""
RAG 纠错模块（独立于主对话 LLM）。

职责：
1. 对检索证据做结构化评判：相关性 / 可回答性 / 歧义
2. 证据不足时，由 FAST 模型在 Step-back 与 HyDE 中二选一
3. 生成对应的重写检索文本（本模块只产出文本，不直接调 Milvus）
"""

from __future__ import annotations

import json
import logging
import re
from typing import Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage

from backend.common.llm import build_llm_fast
from backend.common.messages import extract_text
from backend.common.usage import record_llm_usage

logger = logging.getLogger(__name__)

RewriteStrategy = Literal["step_back", "hyde"]

_JUDGE_SYSTEM = """你是 RAG 证据评审器。根据「用户问题」与「检索到的知识库内容」，做结构化判断。
只输出一个 JSON 对象，不要 markdown、不要解释、不要代码块。

字段（全部必填）：
{
  "relevance": "high" | "medium" | "low",
  "answerable": true | false,
  "ambiguity": "none" | "partial" | "high",
  "issues": ["insufficient_evidence" | "low_relevance" | "ambiguous_query" | "partial_coverage" | "noise"],
  "need_expand_topk": true | false,
  "suggested_top_k": number | null,
  "need_rewrite": true | false,
  "rewrite_strategy": "step_back" | "hyde" | null,
  "reason": "不超过40字的中文理由"
}

判定规则：
- answerable=true：证据足以直接回答核心问题（允许诚实说明「仅找到 N 条」——仅当已扩大召回后仍不足，或明显库中没有更多）
- need_expand_topk=true：相关内容方向对，但条数不够（如用户要10道、当前只召回5条；或 hit 数顶满了当前 top_k）→ 应加大 top_k 再搜；suggested_top_k 建议为用户期望数量或当前 top_k 的 2 倍（整数）
- need_rewrite=true：查询跑题/歧义/表述导致检不准，重写查询有望改善（与 expand 不同）
- need_expand_topk 与 need_rewrite 不要同时为 true：优先 expand（先加召回量）；仅当明显是查询质量问题才 rewrite
- need_rewrite=false 且无需 expand：证据已够用，或扩大/重写都无益
- rewrite_strategy：仅当 need_rewrite=true 时必选其一：
  - step_back：问题过细、术语偏门、条件过多 → 抽象成更高层问题再检索
  - hyde：表述口语/意图模糊，或需要「像文档一样的段落」才能对齐向量 → 生成假设性答案段落再检索
- 禁止同时选两种重写策略；不确定时优先 step_back
"""


_REWRITE_STEP_BACK = """你是查询改写器（Step-back）。
把用户问题改写成更抽象、覆盖面更广、适合知识库检索的短查询。
只输出改写后的查询文本，不要引号、不要解释。"""

_REWRITE_HYDE = """你是 HyDE 假设文档生成器。
根据用户问题，写一段 80～180 字、像知识库正文的中文段落（假设性答案/说明），用于向量检索。
不要提问、不要元说明、不要 markdown。只输出该段落。"""


class RagJudgment(TypedDict):
    relevance: str
    answerable: bool
    ambiguity: str
    issues: list[str]
    need_expand_topk: bool
    suggested_top_k: int | None
    need_rewrite: bool
    rewrite_strategy: str | None
    reason: str


_DEFAULT_TOP_K = 5
_MAX_TOP_K = 30


def _build_critique_llm(*, max_tokens: int = 400):
    """纠错专用 FAST LLM（比意图分类允许更长 JSON）。"""
    return build_llm_fast(max_tokens=max_tokens)


def _parse_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        return {}
    # 去掉可能的 ```json 包裹
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}


def count_rag_hits(rag_context: str) -> int:
    """粗估检索条数（按 [1] [2] … 标题行）。"""
    if not rag_context or "未检索到相关内容" in rag_context:
        return 0
    return len(re.findall(r"^\[\d+\]\s", rag_context, flags=re.MULTILINE))


def clamp_top_k(value: int | None, *, current: int) -> int:
    """建议 top_k 收敛到 (current, MAX]；至少比当前大。"""
    try:
        k = int(value) if value is not None else current * 2
    except (TypeError, ValueError):
        k = current * 2
    k = max(k, current + 1, current * 2)
    return min(k, _MAX_TOP_K)


def _default_judgment(
    *,
    reason: str,
    need_rewrite: bool = False,
    need_expand_topk: bool = False,
    suggested_top_k: int | None = None,
) -> RagJudgment:
    return {
        "relevance": "low",
        "answerable": False,
        "ambiguity": "none",
        "issues": ["insufficient_evidence"],
        "need_expand_topk": need_expand_topk,
        "suggested_top_k": suggested_top_k,
        "need_rewrite": need_rewrite,
        "rewrite_strategy": "step_back" if need_rewrite else None,
        "reason": reason,
    }


def normalize_judgment(
    data: dict,
    *,
    allow_rewrite: bool,
    allow_expand: bool,
    current_top_k: int,
    hit_count: int,
) -> RagJudgment:
    """收敛模型输出；禁止阶段对应动作时强制关掉。"""
    relevance = str(data.get("relevance") or "low").lower()
    if relevance not in ("high", "medium", "low"):
        relevance = "low"

    ambiguity = str(data.get("ambiguity") or "none").lower()
    if ambiguity not in ("none", "partial", "high"):
        ambiguity = "none"

    issues_raw = data.get("issues") or []
    if not isinstance(issues_raw, list):
        issues_raw = [str(issues_raw)]
    allowed_issues = {
        "insufficient_evidence",
        "low_relevance",
        "ambiguous_query",
        "partial_coverage",
        "noise",
    }
    issues = [str(x) for x in issues_raw if str(x) in allowed_issues]

    answerable = bool(data.get("answerable"))
    need_expand = bool(data.get("need_expand_topk")) and allow_expand and not answerable
    need_rewrite = bool(data.get("need_rewrite")) and allow_rewrite and not answerable

    # 二者互斥：优先扩召回
    if need_expand and need_rewrite:
        need_rewrite = False

    # 启发式：命中顶满 top_k 且不可答 → 即使模型漏标也扩一轮
    if (
        allow_expand
        and not answerable
        and not need_rewrite
        and hit_count > 0
        and hit_count >= current_top_k
        and current_top_k < _MAX_TOP_K
    ):
        need_expand = True
        if "partial_coverage" not in issues:
            issues.append("partial_coverage")

    suggested: int | None = None
    if need_expand:
        suggested = clamp_top_k(data.get("suggested_top_k"), current=current_top_k)

    strategy: str | None = None
    if need_rewrite:
        s = str(data.get("rewrite_strategy") or "step_back").strip().lower().replace("-", "_")
        strategy = s if s in ("step_back", "hyde") else "step_back"

    if need_expand:
        default_reason = f"召回不足，扩大 top_k→{suggested}"
    elif need_rewrite:
        default_reason = "证据不足需重写"
    else:
        default_reason = "评审完成"
    reason = str(data.get("reason") or "").strip()[:80] or default_reason

    return {
        "relevance": relevance,
        "answerable": answerable,
        "ambiguity": ambiguity,
        "issues": issues,
        "need_expand_topk": need_expand,
        "suggested_top_k": suggested,
        "need_rewrite": need_rewrite,
        "rewrite_strategy": strategy,
        "reason": reason,
    }


async def judge_evidence(
    *,
    question: str,
    rag_context: str,
    allow_rewrite: bool,
    allow_expand: bool = True,
    current_top_k: int = _DEFAULT_TOP_K,
    thread_id: str | None = None,
) -> RagJudgment:
    """结构化评判证据；复评阶段关闭对应 allow_*。"""
    hit_count = count_rag_hits(rag_context)
    empty = hit_count == 0
    if empty:
        # 空结果：优先改写查询；若已改写过再考虑扩大无意义
        return _default_judgment(
            reason="检索结果为空",
            need_rewrite=allow_rewrite,
            need_expand_topk=False,
        )

    llm = _build_critique_llm(max_tokens=400)
    flags = []
    if allow_expand:
        flags.append("可选择扩大 top_k 再搜一轮")
    if allow_rewrite:
        flags.append("可选择一次查询重写")
    if not flags:
        flags.append("禁止再纠错，只能判定能否回答")
    phase = "；".join(flags)
    user_payload = (
        f"【阶段】{phase}\n"
        f"【当前 top_k】{current_top_k}\n"
        f"【当前命中条数】{hit_count}\n\n"
        f"【用户问题】\n{question}\n\n"
        f"【检索到的知识库内容】\n{rag_context[:6000]}"
    )
    try:
        resp = await llm.ainvoke(
            [
                SystemMessage(content=_JUDGE_SYSTEM),
                HumanMessage(content=user_payload),
            ]
        )
        raw = extract_text(resp.content)
        record_llm_usage(
            thread_id,
            "rag_judge",
            resp,
            prompt_text=_JUDGE_SYSTEM + "\n" + user_payload,
            completion_text=raw,
        )
        data = _parse_json_object(raw)
        judgment = normalize_judgment(
            data,
            allow_rewrite=allow_rewrite,
            allow_expand=allow_expand,
            current_top_k=current_top_k,
            hit_count=hit_count,
        )
        logger.info(
            "RAG judge: answerable=%s expand=%s top_k=%s rewrite=%s strategy=%s reason=%r",
            judgment["answerable"],
            judgment["need_expand_topk"],
            judgment["suggested_top_k"],
            judgment["need_rewrite"],
            judgment["rewrite_strategy"],
            judgment["reason"],
        )
        return judgment
    except Exception:
        logger.exception("RAG judge failed")
        return _default_judgment(reason="评审失败，跳过纠错", need_rewrite=False)


async def rewrite_query(
    *,
    question: str,
    strategy: RewriteStrategy,
    judgment_reason: str = "",
    thread_id: str | None = None,
) -> str:
    """按选定策略生成重写检索文本。失败则回退原问题。"""
    if strategy == "hyde":
        system = _REWRITE_HYDE
        max_tokens = 280
    else:
        system = _REWRITE_STEP_BACK
        max_tokens = 120

    llm = _build_critique_llm(max_tokens=max_tokens)
    human = f"【用户问题】\n{question}"
    if judgment_reason:
        human += f"\n\n【评审理由】\n{judgment_reason}"
    try:
        resp = await llm.ainvoke(
            [
                SystemMessage(content=system),
                HumanMessage(content=human),
            ]
        )
        text = extract_text(resp.content).strip()
        text = text.strip("\"'` \n")
        record_llm_usage(
            thread_id,
            "rag_rewrite",
            resp,
            prompt_text=system + "\n" + human,
            completion_text=text,
        )
        if not text:
            logger.warning("RAG rewrite empty, fallback to original question")
            return question
        logger.info("RAG rewrite strategy=%s preview=%r", strategy, text[:120])
        return text
    except Exception:
        logger.exception("RAG rewrite failed, fallback to original")
        return question


def format_judgment_for_agent(
    judgment: RagJudgment | None,
    *,
    rewrite_used: bool,
    expand_used: bool = False,
) -> str:
    """把评审结论压缩成给主 LLM 的短提示。"""
    if not judgment:
        return ""
    lines = [
        "【证据评审】",
        f"相关性={judgment.get('relevance')}；可回答={judgment.get('answerable')}；"
        f"歧义={judgment.get('ambiguity')}；理由={judgment.get('reason')}",
    ]
    issues = judgment.get("issues") or []
    if issues:
        lines.append(f"问题标记={','.join(issues)}")
    if expand_used or rewrite_used:
        bits = []
        if expand_used:
            bits.append("扩大召回")
        if rewrite_used:
            bits.append("查询重写")
        lines.append(f"已完成{'+'.join(bits)}与复评；若仍不足，请如实说明知识库局限，禁止编造。")
    elif not judgment.get("answerable"):
        lines.append("证据可能不足：仅依据检索内容回答，禁止编造未出现的信息。")
    return "\n".join(lines)
